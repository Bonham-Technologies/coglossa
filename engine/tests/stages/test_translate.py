import json

import pytest

from coglossa.llm.types import PermanentBackendError
from coglossa.schema import (
    Chunk,
    DocumentProfile,
    Genre,
    GlossaryEntry,
    Guide,
    PipelineProfile,
    SpanText,
)
from coglossa.stages.context import StageContext
from coglossa.stages.translate import (
    CONTEXT_CHARS,
    build_chunk_inputs,
    check_coverage,
    drop_repeated_spans,
    looks_cut_off,
    translate_chunk,
)
from tests.conftest import BASE_CONFIG, make_segments

PROFILE = DocumentProfile(
    source_lang="de",
    target_lang="en",
    mixed_language=False,
    genre=Genre.TECHNICAL,
    tone_register="dry",
    word_count=4,
    pipeline_profile=PipelineProfile.SHORT,
)
GUIDE = Guide(
    summary="s",
    style_guide="g",
    glossary=[
        GlossaryEntry(term="Gestalt", rendering="form"),
        GlossaryEntry(term="Angst", rendering="dread"),
    ],
)


def _spans(*ranges):
    return [SpanText(start=a, end=b, text="t") for a, b in ranges]


IDS = ["s1", "s2", "s3"]


@pytest.mark.parametrize(
    ("ranges", "error"),
    [
        ([("s1", "s1"), ("s2", "s3")], None),
        ([("s1", "s3")], None),
        ([("s1", "s1"), ("s9", "s9")], "unknown segment id"),
        ([("s2", "s1")], "before start"),
        ([("s1", "s1"), ("s3", "s3")], "expected a span starting at s2"),
        ([("s1", "s2"), ("s2", "s3")], "expected a span starting at s3"),
        ([("s1", "s2")], "s3..s3 not covered"),
        ([("s1", "s3"), ("s3", "s3")], "after the last segment"),
        ([], "s1..s3 not covered"),
    ],
)
def test_check_coverage(ranges, error):
    errors = check_coverage(_spans(*ranges), IDS)
    if error is None:
        assert errors == []
    else:
        assert len(errors) == 1 and error in errors[0]


def _texts(*items):
    return [SpanText(start=a, end=b, text=t) for a, b, t in items]


def test_spans_repeated_after_full_coverage_are_dropped():
    # CGL-21: a model wrote the whole list twice; a blank span after the last is harmless too
    once = [("s1", "s1", "A"), ("s2", "s3", "BC")]
    assert drop_repeated_spans(_texts(*once, *once), IDS) == _texts(*once)
    assert drop_repeated_spans(_texts(*once, ("s4", "s4", " ")), IDS) == _texts(*once)


@pytest.mark.parametrize(
    "extra",
    [
        ("s1", "s1", "A, said again differently"),  # new text: a restart, not a repeat
        ("s2", "s2", "BC"),  # same text under another range
    ],
)
def test_spans_after_full_coverage_with_anything_new_are_kept(extra):
    spans = _texts(("s1", "s1", "A"), ("s2", "s3", "BC"), extra)
    assert drop_repeated_spans(spans, IDS) == spans
    assert "after the last segment" in check_coverage(spans, IDS)[0]


@pytest.mark.parametrize(
    ("text", "cut"),
    [
        ("Though ", True),  # a model cut off where it meant to open a quotation
        ("हो। ", True),
        ("Done.\u00a0", True),
        ("Done.\n", False),  # a line break is not where a quotation mark cut a text
        ("A whole sentence.", False),
        ("", False),
        ("   ", False),  # blank is a different problem
    ],
)
def test_looks_cut_off(text, cut):
    assert looks_cut_off(text) is cut


def test_spans_before_full_coverage_are_never_dropped():
    spans = _texts(("s1", "s1", "A"), ("s1", "s1", "A"), ("s2", "s3", "BC"))
    assert drop_repeated_spans(spans, IDS) == spans


def test_build_chunk_inputs_adds_neighbour_context():
    segs = make_segments("A" * 1000, "B", "C" * 1000)
    chunks = [
        Chunk(id="c1", segment_ids=["s1"]),
        Chunk(id="c2", segment_ids=["s2"]),
        Chunk(id="c3", segment_ids=["s3"]),
    ]
    inputs = build_chunk_inputs(chunks, segs)
    assert inputs[0].context_before == "" and inputs[0].context_after == "B"
    assert inputs[1].context_before == "A" * CONTEXT_CHARS
    assert inputs[1].context_after == "C" * CONTEXT_CHARS
    assert inputs[2].context_after == ""
    assert [s.id for s in inputs[1].segments] == ["s2"]


def _ci(*texts):
    segs = make_segments(*texts)
    return build_chunk_inputs([Chunk(id="c1", segment_ids=[s.id for s in segs])], segs)[0]


def _payloads(fake_backend):
    return [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1]) for r in fake_backend.requests
    ]


def _ctx(runner):
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


async def test_runs_every_translator_with_role_specific_payloads(
    make_config, make_runner, fake_backend
):
    models = {
        **BASE_CONFIG["models"],
        "model-b": {"backend": "fake", "context_window": 32768, "name": "served-b"},
    }
    runner = make_runner(make_config(models=models))
    ci = _ci("Die Gestalt ist klar.", "Gut.")
    candidates, errors = await translate_chunk(_ctx(runner), PROFILE, GUIDE, ci)
    assert errors == []
    assert [(c.translator, c.model, c.start, c.text) for c in candidates] == [
        ("literal", "model-a", "s1", "[en] Die Gestalt ist klar."),
        ("literal", "model-a", "s2", "[en] Gut."),
        ("contextual", "served-b", "s1", "[en] Die Gestalt ist klar."),
        ("contextual", "served-b", "s2", "[en] Gut."),
    ]
    by_name = {p["translator"]: p for p in _payloads(fake_backend)}
    literal, contextual = by_name["literal"], by_name["contextual"]
    assert literal["context_before"] == "" and literal["context_after"] == ""
    assert literal["guide"] == {
        "glossary": [{"term": "Gestalt", "rendering": "form", "notes": "", "locked": False}]
    }
    assert contextual["guide"]["style_guide"] == "g"
    assert [g["term"] for g in contextual["guide"]["glossary"]] == ["Gestalt"]
    assert {r.role for r in runner.log.records} == {"translator:literal", "translator:contextual"}


async def test_extra_translators_run_too(make_config, make_runner):
    roles = {
        **BASE_CONFIG["roles"],
        "extra_translators": [{"name": "second", "prompt": "contextual", "model": "model-a"}],
    }
    runner = make_runner(make_config(roles=roles))
    candidates, _ = await translate_chunk(_ctx(runner), PROFILE, GUIDE, _ci("Eins."))
    assert sorted({c.translator for c in candidates}) == ["contextual", "literal", "second"]


async def test_bad_coverage_is_repaired(make_runner, fake_backend):
    bad = json.dumps({"spans": [{"start": "s1", "end": "s1", "text": "only one"}]})
    fake_backend.script("translate", bad)
    runner = make_runner()
    candidates, errors = await translate_chunk(_ctx(runner), PROFILE, GUIDE, _ci("A.", "B."))
    assert errors == []
    assert len([c for c in candidates if c.translator == "literal"]) == 2
    assert len(runner.log.records) == 3  # literal twice (repair) + contextual once


async def test_a_reply_written_twice_is_accepted_without_repair(make_runner, fake_backend):
    once = [{"start": "s1", "end": "s1", "text": "A"}, {"start": "s2", "end": "s2", "text": "B"}]
    fake_backend.script("translate", json.dumps({"spans": once + once}))
    runner = make_runner()
    candidates, errors = await translate_chunk(_ctx(runner), PROFILE, GUIDE, _ci("A.", "B."))
    assert errors == []
    assert [(c.start, c.text) for c in candidates if c.translator == "literal"] == [
        ("s1", "A"),
        ("s2", "B"),
    ]
    assert len(runner.log.records) == 2  # no repair turn


async def test_a_text_cut_off_at_a_quotation_mark_is_repaired(make_runner, fake_backend):
    cut = [
        {"start": "s1", "end": "s1", "text": "Though "},
        {"start": "s2", "end": "s2", "text": "B"},
    ]
    fake_backend.script("translate", json.dumps({"spans": cut}))
    runner = make_runner()
    candidates, errors = await translate_chunk(_ctx(runner), PROFILE, GUIDE, _ci("A.", "B."))
    assert errors == []
    assert "Though " not in [c.text for c in candidates]
    (repair,) = [r.messages[3].content for r in fake_backend.requests if len(r.messages) == 4]
    assert "spans[0]: the text ends with a space, as if it was cut off" in repair
    assert "quotation marks" in repair


async def test_failed_translator_is_recorded_not_fatal(make_runner, fake_backend):
    fake_backend.script("translate", PermanentBackendError("model gone"))
    candidates, errors = await translate_chunk(_ctx(make_runner()), PROFILE, GUIDE, _ci("A."))
    assert {c.translator for c in candidates} == {"contextual"}
    assert [(e.stage, e.chunk_id, e.role) for e in errors] == [
        ("translate", "c1", "translator:literal")
    ]
    assert "model gone" in errors[0].message


async def test_programming_errors_propagate(make_runner, fake_backend):
    fake_backend.script("translate", RuntimeError("bug"))
    with pytest.raises(RuntimeError, match="bug"):
        await translate_chunk(_ctx(make_runner()), PROFILE, GUIDE, _ci("A."))
