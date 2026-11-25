"""The review summary (CGL-112): post-editing effort per run and per language of a set, and the
same passages as a CSV download. Read-only and for administrators: a person who sees the set
but is not an administrator is refused (403), and a set the person cannot see stays a missing
id (404), as the id guard answers on every route."""

import uuid

from coglossa import review_summary
from coglossa.schema.review_summary import RunReviewSummary, SetReviewSummary
from fastapi import APIRouter, Depends, Response

from coglossa_api.auth import require_admin
from coglossa_api.context import Ctx
from coglossa_api.routes.exports import content_disposition

router = APIRouter(tags=["review summary"], dependencies=[Depends(require_admin)])
_CSV = {200: {"content": {"text/csv": {}}}}


def _csv(filename: str, body: bytes) -> Response:
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.get("/runs/{run_id}/review-summary")
async def run_summary(run_id: uuid.UUID, ctx: Ctx) -> RunReviewSummary:
    """How much the run's passages were checked and edited, and what became of its flags."""
    return await review_summary.run_summary(ctx.db, run_id)


@router.get("/runs/{run_id}/review-summary.csv", response_class=Response, responses=_CSV)
async def run_summary_csv(run_id: uuid.UUID, ctx: Ctx) -> Response:
    """One row per passage: its flags, whether it was checked and edited, the edit distance
    and who reviewed it. UTF-8 with a byte order mark; no cell starts a formula."""
    return _csv(*await review_summary.run_csv(ctx.db, run_id))


@router.get("/projects/{project_id}/review-summary")
async def set_summary(project_id: uuid.UUID, ctx: Ctx) -> SetReviewSummary:
    """Each language of the set, over the newest run of each file in it."""
    return await review_summary.set_summary(ctx.db, project_id)


@router.get("/projects/{project_id}/review-summary.csv", response_class=Response, responses=_CSV)
async def set_summary_csv(
    project_id: uuid.UUID, ctx: Ctx, target_lang: str | None = None
) -> Response:
    """The passages the set summary counts, one row each; `target_lang` keeps one language."""
    return _csv(*await review_summary.set_csv(ctx.db, project_id, target_lang=target_lang))
