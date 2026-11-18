import json

from coglossa.schema import (
    Agreement,
    Candidate,
    Chunk,
    DocumentProfile,
    Genre,
    Guide,
    PipelineProfile,
)
from coglossa.stages.context import StageContext
from coglossa.stages.reconcile import reconcile_chunk
from coglossa.stages.translate import build_chunk_inputs
from tests.conftest import make_segments

PROFILE = DocumentProfile(
    source_lang="es",
    target_lang="en",
    mixed_language=False,
    genre=Genre.NEWS,
    tone_register="neutral",
    word_count=2,
    pipeline_profile=PipelineProfile.SHORT,
)
GUIDE = Guide(summary="s", style_guide="g")


def _ci():
    segs = make_segments("Hola.", "Adiós.")
    return build_chunk_inputs([Chunk(id="c1", segment_ids=["s1", "s2"])], segs)[0]


def _cands(*names):
    return [
        Candidate(chunk_id="c1", translator=n, model="m", start=s, end=s, text=f"{n}-{s}")
        for n in names
        for s in ("s1", "s2")
    ]


def _ctx(runner):
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


async def test_reviewer_output_is_used(make_runner, fake_backend):
    runner = make_runner()
    result = await reconcile_chunk(
        _ctx(runner), PROFILE, GUIDE, _ci(), _cands("literal", "contextual")
    )
    assert result.engine_error is None and result.errors == []
    assert [(s.start, s.text, s.chosen_from) for s in result.spans] == [
        ("s1", "literal-s1", "literal"),
        ("s2", "literal-s2", "literal"),
    ]
    payload = json.loads(fake_backend.requests[0].messages[1].content.split("INPUT_JSON:", 1)[1])
    assert [c["translator"] for c in payload["candidates"]] == ["literal", "contextual"]
    assert payload["candidates"][1]["spans"][0] == {
        "start": "s1",
        "end": "s1",
        "text": "contextual-s1",
        "notes": "",
    }
    assert runner.log.records[0].role == "reviewer"
    assert "literal, contextual" in fake_backend.requests[0].messages[0].content


async def test_reviewer_failure_falls_back_to_contextual(make_runner, fake_backend):
    fake_backend.script("reconcile", "garbage", "garbage")
    result = await reconcile_chunk(
        _ctx(make_runner()), PROFILE, GUIDE, _ci(), _cands("literal", "contextual")
    )
    assert [(s.text, s.agreement, s.chosen_from) for s in result.spans] == [
        ("contextual-s1", Agreement.UNKNOWN, "contextual"),
        ("contextual-s2", Agreement.UNKNOWN, "contextual"),
    ]
    assert result.engine_error.startswith("review failed")
    assert [(e.stage, e.role, e.chunk_id) for e in result.errors] == [
        ("reconcile", "reviewer", "c1")
    ]


async def test_fallback_uses_first_available_when_no_contextual(make_runner, fake_backend):
    fake_backend.script("reconcile", "garbage", "garbage")
    result = await reconcile_chunk(_ctx(make_runner()), PROFILE, GUIDE, _ci(), _cands("literal"))
    assert {s.chosen_from for s in result.spans} == {"literal"}


async def test_no_candidates_yields_empty_flagged_spans(make_runner, fake_backend):
    result = await reconcile_chunk(_ctx(make_runner()), PROFILE, GUIDE, _ci(), [])
    assert [(s.start, s.text, s.chosen_from) for s in result.spans] == [
        ("s1", "", "none"),
        ("s2", "", "none"),
    ]
    assert result.engine_error == "no translator produced output for this passage"
    assert fake_backend.requests == []


async def test_reviewer_text_cut_off_is_repaired(make_runner, fake_backend):
    def span(sid, text):
        return {
            "start": sid,
            "end": sid,
            "text": text,
            "agreement": "agree",
            "chosen_from": "literal",
        }

    fake_backend.script(
        "reconcile", json.dumps({"spans": [span("s1", "Hello "), span("s2", "Bye.")]})
    )
    runner = make_runner()
    result = await reconcile_chunk(
        _ctx(runner), PROFILE, GUIDE, _ci(), _cands("literal", "contextual")
    )
    assert result.engine_error is None
    assert len([r for r in runner.log.records if r.role == "reviewer"]) == 2


async def test_reviewer_spans_written_twice_are_accepted(make_runner, fake_backend):
    def span(sid):
        return {
            "start": sid,
            "end": sid,
            "text": sid,
            "agreement": "agree",
            "chosen_from": "literal",
        }

    once = [span("s1"), span("s2")]
    fake_backend.script("reconcile", json.dumps({"spans": once + once}))
    runner = make_runner()
    result = await reconcile_chunk(
        _ctx(runner), PROFILE, GUIDE, _ci(), _cands("literal", "contextual")
    )
    assert [s.text for s in result.spans] == ["s1", "s2"]
    assert len([r for r in runner.log.records if r.role == "reviewer"]) == 1


async def test_reviewer_coverage_errors_are_repaired(make_runner, fake_backend):
    bad = {
        "spans": [
            {
                "start": "s1",
                "end": "s1",
                "text": "x",
                "agreement": "agree",
                "chosen_from": "literal",
            }
        ]
    }
    fake_backend.script("reconcile", json.dumps(bad))
    result = await reconcile_chunk(
        _ctx(make_runner()), PROFILE, GUIDE, _ci(), _cands("literal", "contextual")
    )
    assert result.engine_error is None
    assert len(result.spans) == 2
