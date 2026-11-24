import json

import pytest

from coglossa.llm.types import PermanentBackendError
from coglossa.schema import (
    Agreement,
    Candidate,
    Chunk,
    DocumentProfile,
    FlagCode,
    Genre,
    GlossaryEntry,
    Guide,
    PipelineProfile,
    ReconciledSpan,
    Reliability,
)
from coglossa.stages.context import StageContext
from coglossa.stages.reconcile import ReconcileResult
from coglossa.stages.score import score_chunk, span_source_text
from coglossa.stages.translate import build_chunk_inputs
from coglossa.testing.fake_scorer import FakeScorer
from tests.conftest import BASE_CONFIG, make_segments

PROFILE = DocumentProfile(
    source_lang="fr",
    target_lang="en",
    mixed_language=False,
    genre=Genre.NEWS,
    tone_register="plain",
    word_count=6,
    pipeline_profile=PipelineProfile.SHORT,
)
GUIDE = Guide(summary="s", style_guide="g")


@pytest.fixture
def scored_config(make_config):
    models = {
        **BASE_CONFIG["models"],
        "model-c": {"backend": "fake", "context_window": 32768},
        "model-d": {"backend": "fake", "context_window": 32768},
    }
    return make_config(
        models=models,
        roles={
            **BASE_CONFIG["roles"],
            "judge": {"model": "model-d"},  # independent of translators and reviewer
            "roundtrip": {"model": "model-c"},
        },
    )


def _ci():
    segs = make_segments("Il pleut.", "Le chat dort.", "Demain.")
    return build_chunk_inputs([Chunk(id="c1", segment_ids=["s1", "s2", "s3"])], segs)[0]


def _reconciled(texts=("It rains. The cat sleeps.", "Tomorrow.")):
    spans = [
        ReconciledSpan(
            start="s1", end="s2", text=texts[0], agreement=Agreement.MINOR, chosen_from="merged"
        ),
        ReconciledSpan(
            start="s3", end="s3", text=texts[1], agreement=Agreement.AGREE, chosen_from="literal"
        ),
    ]
    return ReconcileResult(spans=spans, engine_error=None, errors=[])


def _cands():
    out = []
    for name in ("literal", "contextual"):
        for sid in ("s1", "s2", "s3"):
            out.append(
                Candidate(
                    chunk_id="c1",
                    translator=name,
                    model="m",
                    start=sid,
                    end=sid,
                    text=f"{name}-{sid}",
                )
            )
    return out


def _ctx(runner):
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


def test_span_source_text():
    assert span_source_text(_ci(), "s1", "s2") == "Il pleut. Le chat dort."


async def test_full_scoring(scored_config, make_runner):
    scorer = FakeScorer()
    result = await score_chunk(
        _ctx(make_runner(scored_config)),
        scorer,
        PROFILE,
        GUIDE,
        _ci(),
        _cands(),
        _reconciled(),
        tier=Reliability.A,
        diverse=True,
    )
    assert result.run_errors == []
    assert [s.reliability for s in result.scores] == [Reliability.A, Reliability.A]
    assert all(s.meaning is not None and s.meaning > 70 for s in result.scores)
    assert result.back_translations == ["[fr] It rains. The cat sleeps.", "[fr] Tomorrow."]
    assert result.judge_errors == [[], []]
    first_qe, second_qe = scorer.qe_calls
    assert [(i.source, i.reference) for i in first_qe] == [
        ("Il pleut. Le chat dort.", None),
        ("Demain.", None),
    ]
    assert [(i.source, i.hypothesis, i.reference) for i in second_qe] == [
        ("", "[fr] It rains. The cat sleeps.", "Il pleut. Le chat dort."),
        ("", "[fr] Tomorrow.", "Demain."),
    ]
    final_pairs, rt_pairs = scorer.similarity_calls
    # 2 (source, final) pairs + 1 candidate pair per span (literal vs contextual)
    assert len(final_pairs) == 4
    assert final_pairs[2].a == "literal-s1 literal-s2"
    assert final_pairs[2].b == "contextual-s1 contextual-s2"
    assert len(rt_pairs) == 2
    assert result.scores[0].signals["agreement"] == pytest.approx((0.7 + 0.8) / 2)


async def test_blank_span_not_sent_and_unscored(scored_config, make_runner):
    scorer = FakeScorer()
    result = await score_chunk(
        _ctx(make_runner(scored_config)),
        scorer,
        PROFILE,
        GUIDE,
        _ci(),
        _cands(),
        _reconciled(("It rains. The cat sleeps.", " ")),
        tier=Reliability.A,
        diverse=True,
    )
    assert len(scorer.qe_calls[0]) == 1
    assert result.scores[1].reliability is Reliability.UNSCORED
    assert result.flags[1] == [] and result.back_translations[1] is None


async def test_scorer_failure_is_one_run_error_and_judge_still_counts(scored_config, make_runner):
    scorer = FakeScorer(fail=True)
    result = await score_chunk(
        _ctx(make_runner(scored_config)),
        scorer,
        PROFILE,
        GUIDE,
        _ci(),
        _cands(),
        _reconciled(),
        tier=Reliability.A,
        diverse=True,
    )
    assert [(e.stage, e.role, e.chunk_id) for e in result.run_errors] == [("score", "scorer", "c1")]
    s = result.scores[0]
    assert (s.signals["qe"], s.signals["xsim"], s.signals["roundtrip"]) == (None, None, None)
    assert s.signals["judge"] == 1.0 and s.meaning is not None
    assert s.reliability is Reliability.B
    # the roundtrip round is skipped once the first round failed
    assert len(scorer.qe_calls) == 1 and len(scorer.similarity_calls) == 1


async def test_no_scorer_no_roles(make_config, make_runner):
    result = await score_chunk(
        _ctx(make_runner(make_config())),
        None,
        PROFILE,
        GUIDE,
        _ci(),
        _cands(),
        _reconciled(),
        tier=Reliability.A,
        diverse=False,
    )
    assert result.run_errors == []
    assert all(s.meaning is None for s in result.scores)
    assert result.back_translations == [None, None]
    assert result.judge_errors == [None, None]  # the judge did not run


async def test_locked_term_violation_flagged(scored_config, make_runner):
    guide = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="tomcat", locked=True)],
    )
    result = await score_chunk(
        _ctx(make_runner(scored_config)),
        FakeScorer(),
        PROFILE,
        guide,
        _ci(),
        _cands(),
        _reconciled(),
        tier=Reliability.A,
        diverse=True,
    )
    assert FlagCode.TERMINOLOGY_VIOLATION in [f.code for f in result.flags[0]]
    assert result.scores[0].signals["guide_check"] == 0.0


async def test_changed_number_is_flagged_and_a_reordered_date_is_not(make_config, make_runner):
    segs = make_segments("Il pleut.", "Le chat a 12 ans.", "Le 17.11.2026.")
    ci = build_chunk_inputs([Chunk(id="c1", segment_ids=["s1", "s2", "s3"])], segs)[0]
    result = await score_chunk(
        _ctx(make_runner(make_config())),
        None,
        PROFILE,
        GUIDE,
        ci,
        _cands(),
        _reconciled(("It rains. The cat is 13.", "On 2026-11-17.")),
        tier=Reliability.A,
        diverse=False,
    )
    [flag] = [f for f in result.flags[0] if f.code is FlagCode.NUMBER_MISMATCH]
    assert flag.evidence == {"source": ["12"], "target": ["13"]}
    assert "12" in flag.explanation and "13" in flag.explanation
    assert result.flags[1] == []
    assert all(s.meaning is None for s in result.scores)  # no scorer or judge: still checked


async def test_a_passage_left_untranslated_is_flagged_and_scores_are_unchanged(
    make_config, make_runner
):
    segs = make_segments("Il pleut.", "Le chat dort sur le canapé.", "Demain.")
    ci = build_chunk_inputs([Chunk(id="c1", segment_ids=["s1", "s2", "s3"])], segs)[0]
    copied = "Il pleut. Le chat dort sur le canapé."

    async def scored(texts):
        return await score_chunk(
            _ctx(make_runner(make_config())),
            None,
            PROFILE,
            GUIDE,
            ci,
            _cands(),
            _reconciled(texts),
            tier=Reliability.A,
            diverse=False,
        )

    result = await scored((copied, "Tomorrow."))
    [flag] = result.flags[0]
    assert flag.code is FlagCode.UNTRANSLATED
    assert flag.evidence == {"rule": "copied", "source": copied, "translation": copied}
    assert result.flags[1] == []
    clean = await scored(("It rains. The cat sleeps on the sofa.", "Tomorrow."))
    assert clean.flags[0] == [] and clean.scores == result.scores


async def test_a_copy_within_one_language_is_not_untranslated(make_config, make_runner):
    # en-US to en-GB: the profile's source language is the target's, so a copy is expected
    segs = make_segments("It rains.", "The cat sleeps on the sofa.", "Tomorrow.")
    ci = build_chunk_inputs([Chunk(id="c1", segment_ids=["s1", "s2", "s3"])], segs)[0]
    copied = "It rains. The cat sleeps on the sofa."
    result = await score_chunk(
        _ctx(make_runner(make_config())),
        None,
        PROFILE.model_copy(update={"source_lang": "en-US"}),
        GUIDE,
        ci,
        _cands(),
        _reconciled((copied, "Tomorrow.")),
        tier=Reliability.A,
        diverse=False,
    )
    assert result.flags == [[], []]


async def test_terms_the_guide_keeps_as_they_are_are_not_untranslated(make_config, make_runner):
    term = "mise en place de la cuisine"
    segs = make_segments("Il pleut.", "Le chat dort.", term)
    ci = build_chunk_inputs([Chunk(id="c1", segment_ids=["s1", "s2", "s3"])], segs)[0]
    kept = Guide(summary="s", style_guide="g", glossary=[GlossaryEntry(term=term, rendering=term)])

    async def flags(guide):
        result = await score_chunk(
            _ctx(make_runner(make_config())),
            None,
            PROFILE,
            guide,
            ci,
            _cands(),
            _reconciled(("It rains. The cat sleeps.", term)),
            tier=Reliability.A,
            diverse=False,
        )
        return [[f.code for f in span] for span in result.flags]

    assert await flags(GUIDE) == [[], [FlagCode.UNTRANSLATED]]
    assert await flags(kept) == [[], []]


async def test_the_numbers_check_reads_each_side_in_its_language(make_config, make_runner):
    # French writes 1.500 for fifteen hundred; the English 1.5 is a thousandfold change (CGL-114)
    segs = make_segments("Il pleut.", "Il coûte 1.500 euros.", "Demain 1.500.")
    ci = build_chunk_inputs([Chunk(id="c1", segment_ids=["s1", "s2", "s3"])], segs)[0]
    result = await score_chunk(
        _ctx(make_runner(make_config())),
        None,
        PROFILE,
        GUIDE,
        ci,
        _cands(),
        _reconciled(("It rains. It costs 1.5 euros.", "Tomorrow 1,500.")),
        tier=Reliability.A,
        diverse=False,
    )
    [flag] = [f for f in result.flags[0] if f.code is FlagCode.NUMBER_MISMATCH]
    assert flag.evidence == {"source": ["1.500"], "target": ["1.5"]}
    assert result.flags[1] == []


async def test_over_long_span_is_not_sent_and_short_span_stays_aligned(scored_config, make_runner):
    long_text = " ".join(["It rains"] * 200)  # 1799 chars: over both the QE and similarity budgets
    scorer = FakeScorer(qe_overrides={"Tomorrow.": 5.0})
    result = await score_chunk(
        _ctx(make_runner(scored_config)),
        scorer,
        PROFILE,
        GUIDE,
        _ci(),
        _cands(),
        _reconciled((long_text, "Tomorrow.")),
        tier=Reliability.A,
        diverse=True,
    )
    assert result.run_errors == []
    long_score, short_score = result.scores
    assert (long_score.signals["qe"], long_score.signals["xsim"]) == (None, None)
    assert long_score.signals["roundtrip"] is None  # back-translation is over budget too
    assert long_score.signals["judge"] == 1.0 and long_score.meaning is not None
    assert short_score.signals["qe"] == pytest.approx(1 - 5.0 / 25)
    assert short_score.signals["xsim"] == pytest.approx(0.8)
    assert short_score.signals["roundtrip"] is not None
    sent_hypotheses = [i.hypothesis for call in scorer.qe_calls for i in call]
    assert long_text not in sent_hypotheses and "Tomorrow." in sent_hypotheses
    sent_sides = [side for call in scorer.similarity_calls for p in call for side in (p.a, p.b)]
    assert all(len(side) <= 800 for side in sent_sides)


async def test_budgets_are_configurable_and_apply_to_candidate_pairs(make_config, make_runner):
    cfg = make_config(scoring={"qe_max_chars": 30, "sim_max_chars": 12})
    scorer = FakeScorer()
    result = await score_chunk(
        _ctx(make_runner(cfg)),
        scorer,
        PROFILE,
        GUIDE,
        _ci(),
        _cands(),
        _reconciled(),
        tier=Reliability.A,
        diverse=True,
    )
    # span 1: source+final = 23+25 chars > 30; span 2: "Demain." + "Tomorrow." fits
    [qe_call] = scorer.qe_calls
    assert [i.hypothesis for i in qe_call] == ["Tomorrow."]
    # every candidate pair has a side over 12 chars; so does span 1's source
    [sim_call] = scorer.similarity_calls
    assert [(p.a, p.b) for p in sim_call] == [("Demain.", "Tomorrow.")]
    first, second = result.scores
    assert first.signals["qe"] is None and first.signals["xsim"] is None
    assert first.signals["agreement"] == pytest.approx(0.7)  # reviewer rating only
    assert second.signals["qe"] == pytest.approx(0.92)
    assert second.signals["agreement"] == pytest.approx(1.0)  # no candidate pair fits


async def test_scorer_returning_the_wrong_number_of_values_is_a_failure(scored_config, make_runner):
    class ShortScorer(FakeScorer):
        async def qe(self, items):
            return (await super().qe(items))[:-1]

    result = await score_chunk(
        _ctx(make_runner(scored_config)),
        ShortScorer(),
        PROFILE,
        GUIDE,
        _ci(),
        _cands(),
        _reconciled(),
        tier=Reliability.A,
        diverse=True,
    )
    assert [e.message for e in result.run_errors] == ["scorer returned 1 values for 2 inputs"]
    assert all(s.signals["qe"] is None and s.signals["xsim"] is not None for s in result.scores)


@pytest.mark.parametrize(("supports_qe", "supports_similarity"), [(False, True), (True, False)])
async def test_unsupported_scorer_calls_are_skipped_without_error(
    scored_config, make_runner, supports_qe, supports_similarity
):
    scorer = FakeScorer(supports_qe=supports_qe, supports_similarity=supports_similarity)
    result = await score_chunk(
        _ctx(make_runner(scored_config)),
        scorer,
        PROFILE,
        GUIDE,
        _ci(),
        _cands(),
        _reconciled(),
        tier=Reliability.A,
        diverse=True,
    )
    assert result.run_errors == []
    assert (scorer.qe_calls == []) is not supports_qe
    assert (scorer.similarity_calls == []) is not supports_similarity
    for s in result.scores:
        assert (s.signals["qe"] is not None) is supports_qe
        assert (s.signals["xsim"] is not None) is supports_similarity
        assert s.signals["roundtrip"] is not None  # the other model still scores the roundtrip


async def test_scorer_without_supports_attributes_is_assumed_capable(scored_config, make_runner):
    class Minimal:  # a plugin scorer that only implements the calls
        async def qe(self, items):
            return [0.5] * len(items)

        async def similarity(self, pairs):
            return [0.5] * len(pairs)

    result = await score_chunk(
        _ctx(make_runner(scored_config)),
        Minimal(),
        PROFILE,
        GUIDE,
        _ci(),
        _cands(),
        _reconciled(),
        tier=Reliability.A,
        diverse=True,
    )
    assert result.run_errors == []
    assert all(s.signals["qe"] is not None and s.signals["xsim"] is not None for s in result.scores)


def _three_spans(first=" "):
    """s1 (blank by default), s2 and s3 as separate spans with different agreement."""
    spans = [
        ReconciledSpan(
            start="s1", end="s1", text=first, agreement=Agreement.MAJOR, chosen_from="literal"
        ),
        ReconciledSpan(
            start="s2",
            end="s2",
            text="The cat sleeps.",
            agreement=Agreement.MINOR,
            chosen_from="merged",
        ),
        ReconciledSpan(
            start="s3",
            end="s3",
            text="Tomorrow.",
            agreement=Agreement.AGREE,
            chosen_from="literal",
        ),
    ]
    return ReconcileResult(spans=spans, engine_error=None, errors=[])


async def _score_three(cfg, make_runner, scorer, reconciled=None):
    return await score_chunk(
        _ctx(make_runner(cfg)),
        scorer,
        PROFILE,
        GUIDE,
        _ci(),
        _cands(),
        reconciled or _three_spans(),
        tier=Reliability.A,
        diverse=True,
    )


async def test_signals_stay_with_their_span_after_a_blank_first_span(scored_config, make_runner):
    scorer = FakeScorer(
        qe_overrides={"The cat sleeps.": 5.0, "Tomorrow.": 10.0},
        similarity_overrides={
            ("Le chat dort.", "The cat sleeps."): 0.6,
            ("Demain.", "Tomorrow."): 0.95,
            ("literal-s2", "contextual-s2"): 0.7,
            ("literal-s3", "contextual-s3"): 1.0,
        },
    )
    result = await _score_three(scored_config, make_runner, scorer)
    assert result.run_errors == []
    blank, cat, tomorrow = result.scores
    assert blank.reliability is Reliability.UNSCORED
    assert set(blank.signals.values()) == {None}
    assert cat.signals["qe"] == pytest.approx(0.8)
    assert cat.signals["xsim"] == pytest.approx(0.2)
    assert cat.signals["agreement"] == pytest.approx((0.7 + 0.4) / 2)
    assert tomorrow.signals["qe"] == pytest.approx(0.6)
    assert tomorrow.signals["xsim"] == pytest.approx(0.9)
    assert tomorrow.signals["agreement"] == pytest.approx(1.0)
    assert result.back_translations == [None, "[fr] The cat sleeps.", "[fr] Tomorrow."]
    assert result.judge_errors == [None, [], []]  # the blank span was not judged


async def test_qe_failure_keeps_similarity(scored_config, make_runner):
    result = await _score_three(scored_config, make_runner, FakeScorer(fail_qe=True))
    assert [(e.stage, e.role) for e in result.run_errors] == [("score", "scorer")]
    for score in result.scores[1:]:
        assert score.signals["qe"] is None
        assert score.signals["xsim"] == pytest.approx(0.8)


async def test_similarity_failure_keeps_qe(scored_config, make_runner):
    result = await _score_three(scored_config, make_runner, FakeScorer(fail_similarity=True))
    assert [(e.stage, e.role) for e in result.run_errors] == [("score", "scorer")]
    for score in result.scores[1:]:
        assert score.signals["qe"] == pytest.approx(0.92)
        assert score.signals["xsim"] is None


async def test_roundtrip_failure_only_loses_the_roundtrip_signal(
    scored_config, make_runner, fake_backend
):
    fake_backend.script("back_translate", PermanentBackendError("down"))
    result = await _score_three(scored_config, make_runner, FakeScorer())
    assert [(e.stage, e.role) for e in result.run_errors] == [("roundtrip", "roundtrip")]
    for score in result.scores[1:]:
        assert score.signals["roundtrip"] is None
        assert score.signals["qe"] is not None and score.signals["xsim"] is not None
    assert result.back_translations == [None, None, None]


async def test_blank_span_is_sent_nowhere(scored_config, make_runner, fake_backend):
    scorer = FakeScorer()
    await _score_three(scored_config, make_runner, scorer)
    sent = [side for call in scorer.similarity_calls for p in call for side in (p.a, p.b)]
    assert "Il pleut." not in sent and "literal-s1" not in sent and "contextual-s1" not in sent
    assert all(i.source != "Il pleut." for call in scorer.qe_calls for i in call)
    assert all(i.reference != "Il pleut." for call in scorer.qe_calls for i in call)
    payloads = {
        p["task"]: p
        for p in (
            json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])
            for r in fake_backend.requests
        )
    }
    for task in ("judge", "back_translate"):
        assert [(s["start"], s["text"]) for s in payloads[task]["spans"]] == [
            ("s2", "The cat sleeps."),
            ("s3", "Tomorrow."),
        ]


async def test_non_finite_scorer_values_become_null(scored_config, make_runner):
    nan = float("nan")
    scorer = FakeScorer(
        qe_overrides={"The cat sleeps.": nan},
        similarity_overrides={("literal-s2", "contextual-s2"): float("inf")},
    )
    result = await _score_three(scored_config, make_runner, scorer)
    cat, tomorrow = result.scores[1:]
    assert cat.signals["qe"] is None and cat.signals["xsim"] == pytest.approx(0.8)
    assert cat.signals["agreement"] == pytest.approx(0.7)  # the inf candidate pair is ignored
    assert tomorrow.signals["qe"] == pytest.approx(0.92)
    assert result.run_errors == []


async def test_empty_back_translation_is_reported_as_none(scored_config, make_runner, fake_backend):
    fake_backend.script(
        "back_translate",
        json.dumps(
            {
                "spans": [
                    {"start": "s2", "end": "s2", "text": ""},
                    {"start": "s3", "end": "s3", "text": "Demain."},
                ]
            }
        ),
    )
    result = await _score_three(scored_config, make_runner, FakeScorer())
    assert result.back_translations == [None, None, "Demain."]
    assert result.scores[1].signals["roundtrip"] is None
    assert result.scores[2].signals["roundtrip"] is not None
