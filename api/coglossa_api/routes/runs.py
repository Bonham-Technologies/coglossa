"""Runs: listing, detail, progress, a new run of a document, cancel, retry and re-running the
sweep (spec §7.1, §8.2, §8.3)."""

import uuid
from typing import Annotated

from coglossa import catalog, service
from coglossa.schema import RunProgress, RunState
from coglossa.schema.views import RunDetail, RunSummary
from fastapi import APIRouter, Query

from coglossa_api.auth import ViewerDep
from coglossa_api.context import Ctx
from coglossa_api.models import ActionResult, RunRequest
from coglossa_api.routes._common import action_result, run_options

router = APIRouter(tags=["runs"])


@router.get("/runs")
async def list_runs(
    ctx: Ctx,
    project_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    *,
    viewer: ViewerDep,
) -> list[RunSummary]:
    """The runs of the sets the person may see (CGL-26)."""
    return await catalog.list_runs(
        ctx.db, project_id=project_id, document_id=document_id, limit=limit, viewer=viewer
    )


@router.post("/documents/{document_id}/runs", status_code=202)
async def new_run(document_id: uuid.UUID, body: RunRequest, ctx: Ctx) -> RunSummary:
    """A new run of an uploaded document. Fields left out take the project's defaults."""
    project = await catalog.document_project(ctx.db, document_id)
    opts = run_options(
        project,
        target_lang=body.target_lang,
        source_lang=body.source_lang,
        pipeline_profile=body.pipeline_profile,
        checkpoint=body.checkpoint,
        use_project_guide=body.use_project_guide,
    )
    run_id = await service.submit_existing(
        ctx.db, ctx.queue, ctx.cfg, ctx.prompts, document_id, opts
    )
    return (await catalog.run_detail(ctx.db, run_id)).run


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, ctx: Ctx) -> RunDetail:
    return await catalog.run_detail(ctx.db, run_id)


@router.get("/runs/{run_id}/progress")
async def get_progress(run_id: uuid.UUID, ctx: Ctx) -> RunProgress:
    """Polling fallback for the progress stream (GET /runs/{run_id}/events)."""
    return await service.run_progress(ctx.db, run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: uuid.UUID, ctx: Ctx) -> ActionResult:
    state = await service.cancel_run(ctx.db, run_id)
    message = (
        "cancelled"
        if state is RunState.CANCELLED
        else "cancel requested; running work stops after the current chunk or stage"
    )
    return await action_result(ctx, run_id, message)


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry(run_id: uuid.UUID, ctx: Ctx) -> ActionResult:
    return await action_result(ctx, run_id, await service.retry_run(ctx.db, ctx.queue, run_id))


@router.post("/runs/{run_id}/sweep", status_code=202)
async def rerun_sweep(run_id: uuid.UUID, ctx: Ctx) -> ActionResult:
    """Re-run the consistency sweep with the project guide, which may be a newer draft of a
    file translating alongside this one (spec §8.3)."""
    return await action_result(ctx, run_id, await service.rerun_sweep(ctx.db, ctx.queue, run_id))
