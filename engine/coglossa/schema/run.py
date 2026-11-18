"""Run lifecycle (spec §7.1)."""

from enum import StrEnum

from pydantic import BaseModel, Field


class RunState(StrEnum):
    CREATED = "created"
    INGESTING = "ingesting"
    PROFILING = "profiling"
    BUILDING_GUIDE = "building_guide"
    AWAITING_GUIDE_APPROVAL = "awaiting_guide_approval"
    TRANSLATING = "translating"
    SWEEPING = "sweeping"
    COMPLETE = "complete"
    COMPLETE_WITH_ERRORS = "complete_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {RunState.COMPLETE, RunState.COMPLETE_WITH_ERRORS, RunState.FAILED, RunState.CANCELLED}
)


class RunProgress(BaseModel):
    """Progress of a queued run (spec §7.1); the API streams it over SSE in M4."""

    run_id: str
    state: RunState
    chunks_total: int
    chunks_done: dict[str, int]  # translate / reconcile / score -> chunks finished
    chunks_with_errors: int
    error: str | None = None
    retryable: bool = Field(
        default=False,
        description=(
            "A retry can clear errors: the run failed, or it completed with failed or errored "
            "chunks or a sweep error. Errors from earlier stages (a config change, the guide) "
            "need a new run."
        ),
    )
