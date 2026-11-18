import json

from coglossa.llm.types import ContextOverflowError, OutputTruncatedError
from coglossa.schema import (
    Agreement,
    Chunk,
    DocumentProfile,
    FlagCode,
    Genre,
    Guide,
    PipelineProfile,
    Reliability,
    Span,
)
from coglossa.stages.chunk import (
    ChunkOutcome,
    engine_error_outcome,
    merge_outcomes,
    process_chunk,
    split_chunk_input,
)
from coglossa.stages.context import StageContext
from coglossa.stages.score import unscored
from coglossa.stages.translate import build_chunk_inputs
from tests.conftest import make_segments

PROFILE = DocumentProfile(
    source_lang="fr",
    target_lang="en",
    mixed_language=False,
    genre=Genre.OTHER,
    tone_register="neutral",
    word_count=8,
    pipeline_profile=PipelineProfile.STANDARD,
)
GUIDE = Guide(summary="", style_guide="")
TEXTS = ("Il pleut.", "Le chat dort.", "Demain, le soleil.", "Fin.")


def _ci(texts=TEXTS):
    segments = make_segments(*texts)
    chunk = Chunk(id="c1", segment_ids=[s.id for s in segments])
    return build_chunk_inputs([chunk], segments)[0]


def _ctx(make_runner):
    runner = make_runner()
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


async def _run(make_runner, ci, hook=None):
    return await process_chunk(
        _ctx(make_runner),
        None,
        PROFILE,
        GUIDE,
        ci,
        tier=Reliability.A,
        diverse=False,
        on_stage=hook,
    )


def _tasks(fake_backend):
    return [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["task"]
        for r in fake_backend.requests
    ]


async def test_normal_chunk(make_runner, fake_backend):
    seen = []

    async def hook(stage):
        seen.append(stage)

    out = await _run(make_runner, _ci(), hook)
    assert isinstance(out, ChunkOutcome)
    assert out.chunk_id == "c1"
    assert [s.id for s in out.spans] == ["c1.1", "c1.2", "c1.3", "c1.4"]
    assert [s.text for s in out.spans] == [f"[en] {t}" for t in TEXTS]
    assert out.proposed_terms == [[], [], [], []]
    assert out.errors == [] and out.engine_error is False
    assert {c.translator for c in out.candidates} == {"literal", "contextual"}
    assert seen == ["translate", "reconcile"]


async def test_translator_overflow_splits_the_chunk(make_runner, fake_backend):
    fake_backend.script("translate", ContextOverflowError("maximum context length exceeded"))
    seen = []

    async def hook(stage):
        seen.append(stage)

    out = await _run(make_runner, _ci(), hook)
    assert [(s.id, s.start, s.end) for s in out.spans] == [
        ("c1.1", "s1", "s1"),
        ("c1.2", "s2", "s2"),
        ("c1.3", "s3", "s3"),
        ("c1.4", "s4", "s4"),
    ]
    assert [s.text for s in out.spans] == [f"[en] {t}" for t in TEXTS]
    assert out.errors == [] and out.engine_error is False
    assert all(c.chunk_id == "c1" for c in out.candidates)
    # 2 translators on the whole chunk, then 2 translators + 1 reviewer per half
    assert _tasks(fake_backend).count("translate") == 6
    assert _tasks(fake_backend).count("reconcile") == 2
    assert seen.count("translate") == 2 and seen.count("reconcile") == 2


async def test_truncated_review_splits_the_chunk(make_runner, fake_backend):
    fake_backend.script("reconcile", OutputTruncatedError("finish_reason=length"))
    out = await _run(make_runner, _ci())
    assert [s.text for s in out.spans] == [f"[en] {t}" for t in TEXTS]
    assert out.errors == [] and out.engine_error is False


async def test_single_sentence_that_never_fits_is_an_engine_error(make_runner, fake_backend):
    fake_backend.script("translate", *[ContextOverflowError("too long")] * 2)
    out = await _run(make_runner, _ci(("Une phrase interminable.",)))
    assert len(out.spans) == 1
    span = out.spans[0]
    assert span.text == "" and span.chosen_from == "none"
    assert span.score.reliability is Reliability.UNSCORED
    assert [f.code for f in span.flags] == [FlagCode.ENGINE_ERROR]
    assert out.engine_error is True
    assert len(out.errors) == 1
    assert out.errors[0].stage == "translate" and out.errors[0].chunk_id == "c1"
    assert "too large" in out.errors[0].message


async def test_repeated_overflow_splits_down_to_single_segments(make_runner, fake_backend):
    # whole chunk, then both halves overflow once each: 4 single-segment chunks result
    fake_backend.script("translate", *[ContextOverflowError("too long")] * 3)
    out = await _run(make_runner, _ci())
    assert [s.start for s in out.spans] == ["s1", "s2", "s3", "s4"]
    assert out.engine_error is False


def test_split_keeps_neighbouring_context():
    ci = _ci()
    first, second = split_chunk_input(ci)
    assert first.chunk.id == second.chunk.id == "c1"
    assert first.chunk.segment_ids == ["s1", "s2"]
    assert second.chunk.segment_ids == ["s3", "s4"]
    assert first.context_before == ci.context_before
    assert first.context_after == "Demain, le soleil.\nFin."
    assert second.context_before == "Il pleut.\nLe chat dort."
    assert second.context_after == ci.context_after


def _stub_span(span_id: str, start: str) -> Span:
    return Span(
        id=span_id,
        chunk_id="c1",
        start=start,
        end=start,
        text="x",
        agreement=Agreement.UNKNOWN,
        chosen_from="none",
        score=unscored(),
    )


def test_merge_renumbers_spans():
    a = ChunkOutcome(
        chunk_id="c1",
        spans=[_stub_span("c1.1", "s1"), _stub_span("c1.2", "s2")],
        proposed_terms=[[], []],
    )
    b = ChunkOutcome(
        chunk_id="c1",
        spans=[_stub_span("c1.1", "s3")],
        proposed_terms=[[]],
        engine_error=True,
    )
    merged = merge_outcomes("c1", [a, b])
    assert [s.id for s in merged.spans] == ["c1.1", "c1.2", "c1.3"]
    assert [s.start for s in merged.spans] == ["s1", "s2", "s3"]
    assert merged.proposed_terms == [[], [], []]
    assert merged.engine_error is True


def test_merge_of_empty_outcomes():
    a = ChunkOutcome(chunk_id="c1", spans=[], proposed_terms=[])
    merged = merge_outcomes("c1", [a, a])
    assert merged.spans == [] and merged.engine_error is False


def test_engine_error_outcome():
    out = engine_error_outcome(_ci(("A.", "B.")), "boom", stage="sweep")
    assert [s.id for s in out.spans] == ["c1.1", "c1.2"]
    assert [(s.start, s.end, s.text) for s in out.spans] == [("s1", "s1", ""), ("s2", "s2", "")]
    for s in out.spans:
        assert [f.code for f in s.flags] == [FlagCode.ENGINE_ERROR]
        assert s.flags[0].evidence["error"] == "boom"
    assert out.proposed_terms == [[], []]
    assert out.engine_error is True
    assert len(out.errors) == 1
    assert out.errors[0].stage == "sweep" and out.errors[0].message == "boom"
    assert out.errors[0].chunk_id == "c1"
