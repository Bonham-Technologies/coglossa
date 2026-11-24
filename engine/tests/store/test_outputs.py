import uuid

from coglossa.schema import (
    Agreement,
    Candidate,
    ErrorCategory,
    ErrorSeverity,
    Flag,
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    InlineMark,
    JudgeError,
    Reliability,
    RevisionSource,
    RunError,
    Span,
    SpanFormatting,
    SpanScore,
)
from coglossa.stages.chunk import ChunkOutcome
from coglossa.stages.sweep import SpanChange
from coglossa.store import outputs, runs


async def _run_id(db):
    async with db.session() as s, s.begin():
        project = await runs.get_or_create_project(s, "p")
        doc = await runs.add_document(
            s,
            project.id,
            document_id=uuid.uuid4(),
            filename="a.txt",
            format="txt",
            sha256="x",
            storage_key="k",
        )
        run = await runs.create_run(
            s, doc.id, target_lang="en", config_hash="h", config_snapshot={}, prompt_versions={}
        )
    return run.id


def _span(chunk, k, text, *, flags=(), judged=True):
    return Span(
        id=f"{chunk}.{k}",
        chunk_id=chunk,
        start=f"s{k}",
        end=f"s{k}",
        text=text,
        agreement=Agreement.MINOR,
        chosen_from="contextual",
        rationale="why",
        issues=["i"],
        score=SpanScore(
            signals={"qe": 0.9, "xsim": None}, meaning=81.5, style=None, reliability=Reliability.B
        ),
        flags=list(flags),
        judge_errors=(
            [
                JudgeError(
                    category=ErrorCategory.FLUENCY,
                    severity=ErrorSeverity.MINOR,
                    excerpt="x",
                    explanation="e",
                )
            ]
            if judged
            else None
        ),
        back_translation="bt" if judged else None,
    )


def _outcome(chunk, texts, **kw):
    spans = [_span(chunk, k + 1, t, **kw) for k, t in enumerate(texts)]
    return ChunkOutcome(
        chunk_id=chunk,
        candidates=[
            Candidate(
                chunk_id=chunk, translator="literal", model="m", start="s1", end="s1", text="c"
            )
        ],
        spans=spans,
        proposed_terms=[[GlossaryEntry(term=f"{chunk}-t", rendering="r")]]
        + [[]] * (len(spans) - 1),
        errors=[RunError(stage="judge", chunk_id=chunk, message="m")],
    )


async def test_a_passages_formatting_is_stored_with_it(db):
    """CGL-108: the marks a passage kept come back with it; a passage without them, None."""
    run_id = await _run_id(db)
    outcome = _outcome("c1", ["The cat.", "Plain."])
    formatting = SpanFormatting(
        text="The cat.",
        marks=[InlineMark(start=4, end=7, kind="link", href="https://example.org/")],
    )
    outcome.spans[0].formatting = formatting
    async with db.session() as s, s.begin():
        await outputs.save_chunk_outcome(s, run_id, 0, outcome)
    async with db.session() as s:
        stored = await outputs.load_spans(s, run_id)
    assert [sp.formatting for sp in stored.spans] == [formatting, None]


async def test_outcome_round_trip_in_reading_order(db):
    run_id = await _run_id(db)
    flag = Flag(code=FlagCode.TERMINOLOGY_VIOLATION, explanation="x", evidence={"a": [1]})
    async with db.session() as s, s.begin():
        # chunk 2 finishes before chunk 1
        await outputs.save_chunk_outcome(s, run_id, 1, _outcome("c2", ["third"]))
        await outputs.save_chunk_outcome(
            s, run_id, 0, _outcome("c1", ["first", "second"], flags=[flag])
        )
    async with db.session() as s:
        stored = await outputs.load_spans(s, run_id)
        candidates = await outputs.load_candidates(s, run_id)
    assert [(sp.id, sp.chunk_id, sp.text) for sp in stored.spans] == [
        ("p1", "c1", "first"),
        ("p2", "c1", "second"),
        ("p3", "c2", "third"),
    ]
    first = stored.spans[0]
    expected = _span("c1", 1, "first", flags=[flag]).model_copy(update={"id": "p1"})
    assert first == expected
    assert set(stored.row_ids) == {"p1", "p2", "p3"}
    assert [t.term for t in stored.proposed_terms] == ["c1-t", "c2-t"]
    assert [c.chunk_id for c in candidates] == ["c1", "c2"]


async def test_unjudged_spans_keep_nulls(db):
    run_id = await _run_id(db)
    async with db.session() as s, s.begin():
        await outputs.save_chunk_outcome(s, run_id, 0, _outcome("c1", ["x"], judged=False))
    async with db.session() as s:
        (span,) = (await outputs.load_spans(s, run_id)).spans
    assert span.judge_errors is None and span.back_translation is None


async def test_sweep_apply_and_undo(db):
    run_id = await _run_id(db)
    flag = Flag(code=FlagCode.TERMINOLOGY_VIOLATION, explanation="x")
    other = Flag(code=FlagCode.LOW_MEANING, explanation="y")
    async with db.session() as s, s.begin():
        await outputs.save_chunk_outcome(s, run_id, 0, _outcome("c1", ["old"], flags=[flag, other]))
    async with db.session() as s, s.begin():
        stored = await outputs.load_spans(s, run_id)
        change = SpanChange(
            "p1",
            "old",
            "new",
            "Applied locked guide entries: 'a' → 'b'.",
            (FlagCode.TERMINOLOGY_VIOLATION,),
        )
        await outputs.apply_sweep(s, stored.row_ids, [change])
    async with db.session() as s:
        stored = await outputs.load_spans(s, run_id)
        (span,) = stored.spans
        assert span.text == "new"
        assert [f.status for f in span.flags] == [FlagStatus.RESOLVED, FlagStatus.OPEN]
        ids = {v: k for k, v in stored.row_ids.items()}
        (rev,) = await outputs.load_revisions(s, run_id, ids)
        assert (rev.span_id, rev.source, rev.before, rev.after) == (
            "p1",
            RevisionSource.SWEEP,
            "old",
            "new",
        )
    async with db.session() as s, s.begin():
        await outputs.undo_sweep(s, run_id)
    async with db.session() as s:
        (span,) = (await outputs.load_spans(s, run_id)).spans
        assert span.text == "old"
        assert [f.status for f in span.flags] == [FlagStatus.OPEN, FlagStatus.OPEN]
        assert await outputs.load_revisions(s, run_id, {}) == []


async def test_delete_chunk_outputs(db):
    run_id = await _run_id(db)
    async with db.session() as s, s.begin():
        await outputs.save_chunk_outcome(s, run_id, 0, _outcome("c1", ["a"]))
        await outputs.save_chunk_outcome(s, run_id, 1, _outcome("c2", ["b"]))
        await outputs.delete_chunk_outputs(s, run_id, ["c1"])
    async with db.session() as s:
        assert [sp.chunk_id for sp in (await outputs.load_spans(s, run_id)).spans] == ["c2"]
        assert [c.chunk_id for c in await outputs.load_candidates(s, run_id)] == ["c2"]


async def test_nul_characters_are_stripped_on_write(db):
    """Postgres rejects NUL in text and jsonb; model output can contain it (final review)."""
    from coglossa.schema import LLMCallRecord

    run_id = await _run_id(db)
    flag = Flag(
        code=FlagCode.TERMINOLOGY_VIOLATION, explanation="ex\x00", evidence={"k\x00": ["v\x00"]}
    )
    outcome = _outcome("c1", ["fir\x00st"], flags=[flag])
    outcome.candidates[0].text = "can\x00didate"
    record = LLMCallRecord(
        role="translator",
        backend="b",
        model="m",
        prompt_id="p",
        prompt_version=1,
        attempt=1,
        ok=True,
        latency_ms=1,
        request={"messages": [{"content": "a\x00b"}]},
        response="re\x00ply",
    )
    async with db.session() as s, s.begin():
        await outputs.save_chunk_outcome(s, run_id, 0, outcome)
        await runs.save_llm_calls(s, run_id, [record], stage="chunk", chunk_id="c1")
    async with db.session() as s:
        stored = await outputs.load_spans(s, run_id)
        (candidate,) = await outputs.load_candidates(s, run_id)
        (call,) = await runs.load_llm_calls(s, run_id)
    (span,) = stored.spans
    assert span.text == "first" and candidate.text == "candidate"
    assert span.flags[0].explanation == "ex" and span.flags[0].evidence == {"k": ["v"]}
    assert call.request == {"messages": [{"content": "ab"}]} and call.response == "reply"


def test_strip_nul_cleans_nested_values():
    from coglossa.store.models import strip_nul

    assert strip_nul({"a\x00": ["b\x00", ("c\x00", 1)], "n": None}) == {
        "a": ["b", ["c", 1]],
        "n": None,
    }
    assert strip_nul(None) is None and strip_nul(2.5) == 2.5
