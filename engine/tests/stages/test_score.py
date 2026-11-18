from coglossa.schema import Agreement, Candidate, FlagCode, ReconciledSpan, Reliability
from coglossa.stages.score import SIGNALS, competing_candidates, initial_flags, unscored

POS = {"s1": 0, "s2": 1, "s3": 2}


def _cand(name, start, end):
    return Candidate(chunk_id="c1", translator=name, model="m", start=start, end=end, text=name)


def _span(**kw):
    base = dict(start="s1", end="s1", text="t", agreement=Agreement.AGREE, chosen_from="literal")
    return ReconciledSpan(**{**base, **kw})


def test_unscored_has_every_signal_null():
    score = unscored()
    assert score.signals == dict.fromkeys(SIGNALS)
    assert score.meaning is None and score.reliability is Reliability.UNSCORED


def test_competing_candidates_overlap():
    cands = [_cand("a", "s1", "s1"), _cand("b", "s2", "s3"), _cand("c", "s3", "s3")]
    assert [c.translator for c in competing_candidates(cands, "s2", "s2", POS)] == ["b"]
    assert [c.translator for c in competing_candidates(cands, "s1", "s2", POS)] == ["a", "b"]


def test_no_flags_for_clean_agreement():
    assert initial_flags(_span(), engine_error=None, competing=[]) == []


def test_all_flags():
    span = _span(
        agreement=Agreement.MAJOR, rationale="literal drops the negation", cultural_or_wordplay=True
    )
    flags = initial_flags(
        span, engine_error="review failed: boom", competing=[_cand("literal", "s1", "s1")]
    )
    assert [f.code for f in flags] == [
        FlagCode.ENGINE_ERROR,
        FlagCode.AGENT_DISAGREEMENT,
        FlagCode.CULTURAL_OR_WORDPLAY,
    ]
    assert flags[0].evidence["error"] == "review failed: boom"
    assert flags[1].explanation == "literal drops the negation"
    assert flags[1].evidence["candidates"] == [
        {"translator": "literal", "model": "m", "text": "literal"}
    ]


def test_flag_explanations_have_plain_language_defaults():
    flags = initial_flags(
        _span(agreement=Agreement.MAJOR, cultural_or_wordplay=True), engine_error=None, competing=[]
    )
    assert all(f.explanation for f in flags)
