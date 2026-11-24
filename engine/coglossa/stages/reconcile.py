"""Reconcile stage (spec §4.3, §4.5): the reviewer turns candidates into final spans."""

from dataclasses import dataclass

from coglossa.llm.agent import AGENT_FAILURES
from coglossa.llm.types import OVERSIZE_ERRORS
from coglossa.schema import (
    Agreement,
    Candidate,
    DocumentProfile,
    Guide,
    ReconciledSpan,
    ReconcileOutput,
    RunError,
)
from coglossa.stages.context import StageContext
from coglossa.stages.guide import guide_slice
from coglossa.stages.translate import (
    ChunkInput,
    check_coverage,
    check_cut_off,
    drop_repeated_spans,
)

NO_OUTPUT = "no translator produced output for this passage"


@dataclass
class ReconcileResult:
    spans: list[ReconciledSpan]
    engine_error: str | None
    errors: list[RunError]


async def reconcile_chunk(
    ctx: StageContext,
    profile: DocumentProfile,
    guide: Guide,
    ci: ChunkInput,
    candidates: list[Candidate],
) -> ReconcileResult:
    names = [t.name for t in ctx.cfg.roles.translators()]
    by_translator = {n: [c for c in candidates if c.translator == n] for n in names}
    available = [n for n in names if by_translator[n]]
    if not available:
        spans = [
            ReconciledSpan(
                start=s.id, end=s.id, text="", agreement=Agreement.UNKNOWN, chosen_from="none"
            )
            for s in ci.segments
        ]
        return ReconcileResult(spans=spans, engine_error=NO_OUTPUT, errors=[])

    segment_ids = [s.id for s in ci.segments]
    sliced = guide_slice(guide, "\n".join(s.text for s in ci.segments))
    try:
        out = await ctx.runner.run(
            role="reviewer",
            model_key=ctx.cfg.roles.reviewer.model,
            max_output_tokens=ctx.cfg.output_limit(ctx.cfg.roles.reviewer),
            prompt_id="reconcile",
            prompt_vars={
                "source_lang": profile.source_lang,
                "target_lang": profile.target_lang,
                "translators": available,
            },
            payload={
                "task": "reconcile",
                "source_lang": profile.source_lang,
                "target_lang": profile.target_lang,
                "guide": sliced.prompt_dump(),
                "segments": [{"id": s.id, "text": ci.markup.text(s)} for s in ci.segments],
                "candidates": [
                    {
                        "translator": n,
                        "spans": [
                            {"start": c.start, "end": c.end, "text": c.text, "notes": c.notes}
                            for c in by_translator[n]
                        ],
                    }
                    for n in available
                ],
            },
            output_type=ReconcileOutput,
            normalize=lambda o: o.model_copy(
                update={"spans": drop_repeated_spans(o.spans, segment_ids)}
            ),
            validate=lambda o: check_coverage(o.spans, segment_ids) + check_cut_off(o.spans),
        )
    except OVERSIZE_ERRORS:
        raise  # the caller splits the chunk (spec §7.2)
    except AGENT_FAILURES as e:
        best = "contextual" if "contextual" in available else available[0]
        spans = [
            ReconciledSpan(
                start=c.start, end=c.end, text=c.text, agreement=Agreement.UNKNOWN, chosen_from=best
            )
            for c in by_translator[best]
        ]
        return ReconcileResult(
            spans=spans,
            engine_error=f"review failed: {e}",
            errors=[
                RunError(stage="reconcile", chunk_id=ci.chunk.id, role="reviewer", message=str(e))
            ],
        )
    return ReconcileResult(spans=out.spans, engine_error=None, errors=[])
