"""Per-chunk pipeline (spec §4.1, §7.2): translate → reconcile → score. When a model reports
that the chunk is too large (context overflow or truncated output), the chunk is split in two
and each half is processed on its own. A single sentence that is still too large becomes an
engine_error passage instead of failing the run."""

import asyncio
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from coglossa.llm.types import OVERSIZE_ERRORS
from coglossa.schema import (
    Agreement,
    Candidate,
    Chunk,
    DocumentProfile,
    GlossaryEntry,
    Guide,
    ReconciledSpan,
    Reliability,
    RunError,
    Span,
)
from coglossa.scoring.client import Scorer
from coglossa.stages.context import StageContext
from coglossa.stages.reconcile import ReconcileResult, reconcile_chunk
from coglossa.stages.score import (
    ChunkScores,
    competing_candidates,
    initial_flags,
    score_chunk,
    unscored,
)
from coglossa.stages.translate import CONTEXT_CHARS, ChunkInput, translate_chunk

StageHook = Callable[[str], Awaitable[None]]


class ChunkOutcome(BaseModel):
    """Everything one chunk produced. Span ids are chunk-local ("c3.1", "c3.2", …); the
    report renumbers them p1, p2, … in reading order. proposed_terms has one list per span."""

    chunk_id: str
    candidates: list[Candidate] = Field(default_factory=list)
    spans: list[Span] = Field(default_factory=list)
    proposed_terms: list[list[GlossaryEntry]] = Field(default_factory=list)
    errors: list[RunError] = Field(default_factory=list)
    engine_error: bool = False


async def process_chunk(
    ctx: StageContext,
    scorer: Scorer | None,
    profile: DocumentProfile,
    guide: Guide,
    ci: ChunkInput,
    *,
    tier: Reliability,
    diverse: bool,
    on_stage: StageHook | None = None,
) -> ChunkOutcome:
    try:
        return await _attempt(
            ctx, scorer, profile, guide, ci, tier=tier, diverse=diverse, on_stage=on_stage
        )
    except OVERSIZE_ERRORS as e:
        if len(ci.segments) < 2:
            return _too_large(ci, str(e))
        parts = await asyncio.gather(
            *(
                process_chunk(
                    ctx, scorer, profile, guide, half, tier=tier, diverse=diverse, on_stage=on_stage
                )
                for half in split_chunk_input(ci)
            )
        )
        return merge_outcomes(ci.chunk.id, list(parts))


async def _attempt(
    ctx: StageContext,
    scorer: Scorer | None,
    profile: DocumentProfile,
    guide: Guide,
    ci: ChunkInput,
    *,
    tier: Reliability,
    diverse: bool,
    on_stage: StageHook | None,
) -> ChunkOutcome:
    candidates, translate_errors = await translate_chunk(ctx, profile, guide, ci)
    if on_stage is not None:
        await on_stage("translate")
    reconciled = await reconcile_chunk(ctx, profile, guide, ci, candidates)
    if on_stage is not None:
        await on_stage("reconcile")
    scored = await score_chunk(
        ctx, scorer, profile, guide, ci, candidates, reconciled, tier=tier, diverse=diverse
    )
    return build_outcome(ci, candidates, reconciled, scored, translate_errors)


def build_outcome(
    ci: ChunkInput,
    candidates: list[Candidate],
    reconciled: ReconcileResult,
    scored: ChunkScores,
    translate_errors: list[RunError],
) -> ChunkOutcome:
    positions = {s.id: i for i, s in enumerate(ci.segments)}
    spans: list[Span] = []
    for j, rs in enumerate(reconciled.spans):
        competing = competing_candidates(candidates, rs.start, rs.end, positions)
        spans.append(
            Span(
                id=f"{ci.chunk.id}.{j + 1}",
                chunk_id=ci.chunk.id,
                start=rs.start,
                end=rs.end,
                text=rs.text,
                agreement=rs.agreement,
                chosen_from=rs.chosen_from,
                rationale=rs.rationale,
                issues=rs.issues,
                score=scored.scores[j],
                flags=initial_flags(rs, engine_error=reconciled.engine_error, competing=competing)
                + scored.flags[j],
                judge_errors=scored.judge_errors[j],
                back_translation=scored.back_translations[j],
            )
        )
    return ChunkOutcome(
        chunk_id=ci.chunk.id,
        candidates=candidates,
        spans=spans,
        proposed_terms=[list(rs.proposed_terms) for rs in reconciled.spans],
        errors=translate_errors + reconciled.errors + scored.run_errors,
        engine_error=reconciled.engine_error is not None,
    )


def split_chunk_input(ci: ChunkInput) -> tuple[ChunkInput, ChunkInput]:
    mid = len(ci.segments) // 2
    first, second = ci.segments[:mid], ci.segments[mid:]
    first_text = "\n".join(s.text for s in first)
    second_text = "\n".join(s.text for s in second)

    def half(segments, before: str, after: str) -> ChunkInput:
        chunk = Chunk(id=ci.chunk.id, segment_ids=[s.id for s in segments])
        return ChunkInput(
            chunk=chunk, segments=segments, context_before=before, context_after=after
        )

    return (
        half(first, ci.context_before, second_text[:CONTEXT_CHARS]),
        half(second, first_text[-CONTEXT_CHARS:], ci.context_after),
    )


def merge_outcomes(chunk_id: str, parts: list[ChunkOutcome]) -> ChunkOutcome:
    spans = [s for p in parts for s in p.spans]
    return ChunkOutcome(
        chunk_id=chunk_id,
        candidates=[c for p in parts for c in p.candidates],
        spans=[s.model_copy(update={"id": f"{chunk_id}.{k + 1}"}) for k, s in enumerate(spans)],
        proposed_terms=[t for p in parts for t in p.proposed_terms],
        errors=[e for p in parts for e in p.errors],
        engine_error=any(p.engine_error for p in parts),
    )


def _too_large(ci: ChunkInput, message: str) -> ChunkOutcome:
    return engine_error_outcome(
        ci, f"too large for the configured models even as a single sentence: {message}"
    )


def engine_error_outcome(ci: ChunkInput, error: str, stage: str = "translate") -> ChunkOutcome:
    """Every segment becomes an empty engine_error passage, so a failed chunk is visible in
    the report instead of silently missing (spec §7.3)."""
    spans: list[Span] = []
    for k, segment in enumerate(ci.segments):
        rs = ReconciledSpan(
            start=segment.id,
            end=segment.id,
            text="",
            agreement=Agreement.UNKNOWN,
            chosen_from="none",
        )
        spans.append(
            Span(
                id=f"{ci.chunk.id}.{k + 1}",
                chunk_id=ci.chunk.id,
                start=segment.id,
                end=segment.id,
                text="",
                agreement=Agreement.UNKNOWN,
                chosen_from="none",
                score=unscored(),
                flags=initial_flags(rs, engine_error=error, competing=[]),
            )
        )
    return ChunkOutcome(
        chunk_id=ci.chunk.id,
        spans=spans,
        proposed_terms=[[] for _ in spans],
        errors=[RunError(stage=stage, chunk_id=ci.chunk.id, message=error)],
        engine_error=True,
    )
