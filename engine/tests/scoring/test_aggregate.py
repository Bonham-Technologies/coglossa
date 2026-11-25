import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from coglossa.config import ScoringConfig
from coglossa.schema import FlagCode, Genre, JudgeError, Reliability
from coglossa.scoring.aggregate import (
    SIGNALS,
    SpanEvidence,
    document_notices,
    model_diversity,
    normalize_qe,
    normalize_sim,
    score_span,
)
from coglossa.scoring.literals import literal_check
from coglossa.scoring.numbers import NumberMismatch
from coglossa.scoring.rules import GuideViolation
from coglossa.scoring.untranslated import Untranslated
from tests.conftest import BASE_CONFIG, PRE_CALIBRATION_THRESHOLDS

CFG = ScoringConfig(thresholds=PRE_CALIBRATION_THRESHOLDS)
A, B, C = Reliability.A, Reliability.B, Reliability.C


def _ev(**kw):
    base = dict(
        source_text="Il pleut.",
        final_text="It rains.",
        genre=Genre.NEWS,
        qe_raw=2.0,
        xsim_cos=0.9,
        roundtrip_qe_raw=2.0,
        roundtrip_cos=0.9,
        judge_errors=[],
        agreement=1.0,
        length_ratio=1.0,
    )
    return SpanEvidence(**{**base, **kw})


def _err(category="accuracy/mistranslation", severity="major", text="x"):
    return JudgeError(category=category, severity=severity, excerpt="", explanation=text)


def test_normalisers():
    assert normalize_qe(None) is None and normalize_sim(None, 0.5) is None
    assert normalize_qe(0) == 1.0 and normalize_qe(25) == 0.0 and normalize_qe(30) == 0.0
    assert normalize_qe(2.0) == pytest.approx(0.92)
    assert normalize_sim(0.9, 0.5) == pytest.approx(0.8)
    assert normalize_sim(0.2, 0.5) == 0.0 and normalize_sim(1.0, 0.5) == 1.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_raw_values_are_null(bad):
    assert normalize_qe(bad) is None and normalize_sim(bad, 0.5) is None
    score, _ = score_span(_ev(qe_raw=bad, xsim_cos=bad), tier=A, cfg=CFG, diverse=True)
    assert score.signals["qe"] is None and score.signals["xsim"] is None


def test_all_good_signals_tier_a():
    score, flags = score_span(_ev(), tier=A, cfg=CFG, diverse=True)
    # .35*.92 + .10*.8 + .20*.86 + .30*1 + .05*1 = .924
    assert score.meaning == pytest.approx(92.4)
    assert score.style == 100.0
    assert score.reliability is A
    assert set(score.signals) == set(SIGNALS)
    assert score.signals["roundtrip"] == pytest.approx(0.86)
    assert flags == []


def test_literary_genre_halves_roundtrip_weight():
    score, _ = score_span(
        _ev(genre=Genre.POETRY, roundtrip_qe_raw=25.0, roundtrip_cos=0.0),
        tier=A,
        cfg=CFG,
        diverse=True,
    )
    # roundtrip signal 0 with weight .10 (halved): (.322 + .08 + 0 + .3 + .05) / .90
    assert score.meaning == pytest.approx(round(100 * 0.752 / 0.90, 1))


def test_missing_signals_renormalise_and_downgrade():
    ev = _ev(qe_raw=None, xsim_cos=None, roundtrip_qe_raw=None, roundtrip_cos=None, agreement=None)
    score, _ = score_span(ev, tier=A, cfg=CFG, diverse=True)
    assert score.meaning == 100.0  # judge only
    assert score.reliability is B  # 3 model signals missing


def test_nothing_available():
    ev = _ev(
        qe_raw=None,
        xsim_cos=None,
        roundtrip_qe_raw=None,
        roundtrip_cos=None,
        judge_errors=None,
        agreement=None,
    )
    score, flags = score_span(ev, tier=B, cfg=CFG, diverse=True)
    assert (score.meaning, score.style, score.reliability) == (None, None, Reliability.UNSCORED)
    assert flags == []


def test_agreement_alone_never_produces_a_meaning_score():
    ev = _ev(
        qe_raw=None,
        xsim_cos=None,
        roundtrip_qe_raw=None,
        roundtrip_cos=None,
        judge_errors=None,
        agreement=1.0,
    )
    score, _ = score_span(ev, tier=A, cfg=CFG, diverse=True)
    assert score.meaning is None and score.signals["agreement"] == 1.0
    assert score.reliability is Reliability.UNSCORED


def test_blank_translation_is_unscored():
    score, flags = score_span(_ev(final_text="  "), tier=A, cfg=CFG, diverse=True)
    assert score.signals == dict.fromkeys(SIGNALS)
    assert score.reliability is Reliability.UNSCORED and flags == []


def test_low_meaning_threshold_uses_tier_and_judge_explanation():
    ev = _ev(qe_raw=12.0, xsim_cos=0.7, judge_errors=[_err(text="'pleut' means rains, not cries")])
    score, flags = score_span(ev, tier=C, cfg=CFG, diverse=True)
    assert score.meaning < 75
    [flag] = [f for f in flags if f.code is FlagCode.LOW_MEANING]
    assert "below the 75 threshold" in flag.explanation
    assert "means rains" in flag.explanation
    assert flag.evidence["threshold"] == 75.0


def test_threshold_text_does_not_round_a_failing_score_up():
    s = ScoringConfig.model_validate({"thresholds": {"meaning": 92.5}})
    score, flags = score_span(_ev(), tier=A, cfg=s, diverse=True)
    assert score.meaning == pytest.approx(92.4)
    [flag] = flags
    assert flag.explanation.startswith("Meaning score 92.4 is below the 92.5 threshold.")
    assert flag.explanation.endswith(" Compare the translation with the source carefully.")


def test_style_flag_without_judge_explanation_uses_style_wording():
    errors = [_err(category="fluency", severity="major", text="")]
    _, flags = score_span(_ev(judge_errors=errors), tier=A, cfg=CFG, diverse=True)
    [flag] = [f for f in flags if f.code is FlagCode.STYLE_DRIFT]
    assert flag.explanation == (
        "Style score 50.0 is below the 60 threshold. "
        "Review the wording and register of this passage."
    )


def test_critical_accuracy_error_is_hard_trigger():
    # perfect model signals keep meaning at exactly the threshold (70.0, not below)
    ev = _ev(
        qe_raw=0.0,
        xsim_cos=1.0,
        roundtrip_qe_raw=0.0,
        roundtrip_cos=1.0,
        judge_errors=[_err(severity="critical", text="negation reversed")],
    )
    score, flags = score_span(ev, tier=A, cfg=CFG, diverse=True)
    assert score.meaning == 70.0
    assert [f.code for f in flags] == [FlagCode.LOW_MEANING]
    assert "critical" in flags[0].explanation.lower()


def test_style_drift_threshold_and_critical():
    major = [_err(category="fluency", severity="major")]
    score, flags = score_span(_ev(judge_errors=major), tier=A, cfg=CFG, diverse=True)
    assert score.style == 50.0 and FlagCode.STYLE_DRIFT in [f.code for f in flags]
    crit = [_err(category="locale", severity="critical")]
    _, flags = score_span(_ev(judge_errors=crit), tier=A, cfg=CFG, diverse=True)
    assert FlagCode.STYLE_DRIFT in [f.code for f in flags]


def test_locked_term_violation_flags_only_locked():
    locked = GuideViolation("Gestalt", "form", "glossary", True)
    unlocked = GuideViolation("Angst", "dread", "glossary", False)
    _, flags = score_span(
        _ev(guide_signal=0.0, guide_violations=(locked, unlocked)), tier=A, cfg=CFG, diverse=True
    )
    [flag] = flags
    assert flag.code is FlagCode.TERMINOLOGY_VIOLATION
    assert "Gestalt" in flag.explanation and "form" in flag.explanation
    assert flag.evidence["violations"] == [locked.as_evidence()]


def test_number_mismatch_is_a_hard_trigger_that_leaves_the_scores_alone():
    mismatch = NumberMismatch(source=("1,200",), target=("1 300",))
    clean_score, clean_flags = score_span(_ev(), tier=A, cfg=CFG, diverse=True)
    score, flags = score_span(_ev(number_mismatch=mismatch), tier=A, cfg=CFG, diverse=True)
    assert score == clean_score and clean_flags == []
    [flag] = flags
    assert flag.code is FlagCode.NUMBER_MISMATCH
    assert flag.explanation == mismatch.explanation()
    assert flag.evidence == {"source": ["1,200"], "target": ["1 300"]}


def test_number_mismatch_is_raised_without_any_model_signal():
    mismatch = NumberMismatch(source=("2004",), target=())
    bare = _ev(
        qe_raw=None,
        xsim_cos=None,
        roundtrip_qe_raw=None,
        roundtrip_cos=None,
        judge_errors=None,
        agreement=None,
        number_mismatch=mismatch,
    )
    score, flags = score_span(bare, tier=A, cfg=CFG, diverse=True)
    assert score.meaning is None and [f.code for f in flags] == [FlagCode.NUMBER_MISMATCH]


def test_literal_mismatch_is_a_hard_trigger_that_leaves_the_scores_alone():
    changed = literal_check("See https://example.com/a", "Voir https://example.com/b")
    clean_score, clean_flags = score_span(_ev(), tier=A, cfg=CFG, diverse=True)
    score, flags = score_span(_ev(literal_mismatch=changed), tier=A, cfg=CFG, diverse=True)
    assert score == clean_score and clean_flags == []
    [flag] = flags
    assert flag.code is FlagCode.LITERAL_MISMATCH
    assert flag.explanation == changed.explanation()
    assert flag.evidence == changed.as_evidence()
    bare = _ev(
        qe_raw=None,
        xsim_cos=None,
        roundtrip_qe_raw=None,
        roundtrip_cos=None,
        judge_errors=None,
        agreement=None,
        literal_mismatch=changed,
    )
    score, flags = score_span(bare, tier=A, cfg=CFG, diverse=True)
    assert score.meaning is None and [f.code for f in flags] == [FlagCode.LITERAL_MISMATCH]


def test_untranslated_is_a_hard_trigger_that_leaves_the_scores_alone():
    copied = Untranslated(rule="copied", source="Il pleut.", translation="Il pleut.")
    clean_score, clean_flags = score_span(_ev(), tier=A, cfg=CFG, diverse=True)
    score, flags = score_span(_ev(untranslated=copied), tier=A, cfg=CFG, diverse=True)
    assert score == clean_score and clean_flags == []
    [flag] = flags
    assert flag.code is FlagCode.UNTRANSLATED
    assert flag.explanation == copied.explanation()
    assert flag.evidence == {"rule": "copied", "source": "Il pleut.", "translation": "Il pleut."}


def test_untranslated_is_raised_without_any_model_signal():
    wrong = Untranslated(
        rule="script",
        source="It rains.",
        translation="it rains all day long here",
        script="Latn",
        expected=("Ethi",),
        language="am",
    )
    bare = _ev(
        qe_raw=None,
        xsim_cos=None,
        roundtrip_qe_raw=None,
        roundtrip_cos=None,
        judge_errors=None,
        agreement=None,
        untranslated=wrong,
    )
    score, flags = score_span(bare, tier=A, cfg=CFG, diverse=True)
    assert score.meaning is None and [f.code for f in flags] == [FlagCode.UNTRANSLATED]
    assert flags[0].evidence["expected"] == ["Ethi"]


@pytest.mark.parametrize(
    ("ratio", "with_omission", "flagged"),
    [
        (0.3, True, True),
        (2.5, True, True),
        (1.0, True, False),
        (0.3, False, False),
        (None, True, False),
    ],
)
def test_possible_omission(ratio, with_omission, flagged):
    errors = (
        [_err(category="accuracy/omission", severity="minor", text="drops 'demain'")]
        if with_omission
        else []
    )
    _, flags = score_span(
        _ev(length_ratio=ratio, judge_errors=errors), tier=A, cfg=CFG, diverse=True
    )
    assert (FlagCode.POSSIBLE_OMISSION in [f.code for f in flags]) is flagged


def test_no_diversity_downgrades_but_c_stays_c():
    assert score_span(_ev(), tier=A, cfg=CFG, diverse=False)[0].reliability is B
    assert score_span(_ev(), tier=C, cfg=CFG, diverse=False)[0].reliability is C


def test_document_notices():
    [notice] = document_notices(C, "yo", "en")
    assert notice.code is FlagCode.LOW_RELIABILITY_LANGUAGE
    assert "yo→en" in notice.explanation and "less reliable" in notice.explanation
    assert document_notices(A, "fr", "en") == []


@pytest.mark.parametrize("tier", [A, C])
def test_unscored_document_notice_replaces_tier_notice(tier):
    [notice] = document_notices(tier, "yo", "en", scored=False)
    assert notice.code is FlagCode.UNSCORED
    assert notice.explanation == (
        "No scores were computed: no scorer or judge was configured or reachable. "
        "Review every passage."
    )


@pytest.mark.parametrize(("tier", "scored"), [(A, True), (C, True), (A, False)])
def test_parts_of_the_file_left_out_come_first_and_once(tier, scored):
    """CGL-87: the ingestor's notices are document notices, ahead of the scoring ones."""
    left_out = "Some parts of this file couldn't be read, so they aren't translated: 1 chart."
    notices = document_notices(tier, "yo", "en", scored=scored, unread=[left_out, left_out])
    assert [(n.code, n.explanation) for n in notices[:1]] == [(FlagCode.UNREAD_CONTENT, left_out)]
    assert all(n.code is not FlagCode.UNREAD_CONTENT for n in notices[1:])
    assert len(notices) == (1 if tier is A and scored else 2)


def test_model_diversity(make_config):
    roles = BASE_CONFIG["roles"]  # translators model-a/model-b, reviewer model-a
    models = {
        **BASE_CONFIG["models"],
        "model-c": {"backend": "fake", "context_window": 32768},
        "model-d": {"backend": "fake", "context_window": 32768},
    }

    def diverse(judge, roundtrip):
        extra = {}
        if judge:
            extra["judge"] = {"model": judge}
        if roundtrip:
            extra["roundtrip"] = {"model": roundtrip}
        return model_diversity(make_config(models=models, roles={**roles, **extra}))

    assert model_diversity(make_config()) is False
    assert diverse("model-d", "model-c") is True
    assert diverse("model-c", "model-c") is True  # judge and roundtrip may share a model
    assert diverse("model-b", "model-c") is False  # judge shares a translator's model
    assert diverse("model-a", "model-c") is False  # judge is the reviewer's model
    assert diverse("model-d", "model-b") is False  # roundtrip shares a translator's model
    assert diverse(None, "model-c") is False
    assert diverse("model-d", None) is False


sev = st.sampled_from(["minor", "major", "critical"])
cat = st.sampled_from(["accuracy/mistranslation", "accuracy/omission", "fluency", "locale"])
maybe = lambda s: st.one_of(st.none(), s)  # noqa: E731


@settings(max_examples=200, deadline=None)
@given(
    qe=maybe(st.floats(0, 25)),
    cos=maybe(st.floats(-1, 1)),
    rq=maybe(st.floats(0, 25)),
    rc=maybe(st.floats(-1, 1)),
    agree=maybe(st.floats(0, 1)),
    errors=maybe(st.lists(st.tuples(cat, sev), max_size=5)),
    tier=st.sampled_from([A, B, C]),
    extra=sev,
)
def test_scores_bounded_and_critical_error_never_helps(qe, cos, rq, rc, agree, errors, tier, extra):
    judge = None if errors is None else [_err(c, s) for c, s in errors]
    ev = _ev(
        qe_raw=qe,
        xsim_cos=cos,
        roundtrip_qe_raw=rq,
        roundtrip_cos=rc,
        agreement=agree,
        judge_errors=judge,
    )
    score, _ = score_span(ev, tier=tier, cfg=CFG, diverse=True)
    for value in (score.meaning, score.style):
        assert value is None or 0.0 <= value <= 100.0
    if judge is not None:
        worse = _ev(
            qe_raw=qe,
            xsim_cos=cos,
            roundtrip_qe_raw=rq,
            roundtrip_cos=rc,
            agreement=agree,
            judge_errors=[*judge, _err("accuracy/omission", extra)],
        )
        worse_score, _ = score_span(worse, tier=tier, cfg=CFG, diverse=True)
        assert worse_score.meaning <= score.meaning
