"""The review view (spec §8.4): span pages with filters, span detail, and the review actions.
Every action records the signed-in user in the feedback log (spec §6.5)."""

import uuid
from typing import Annotated

from coglossa import review
from coglossa.schema import FlagCode, FlagStatus
from coglossa.schema.views import GuideView, SpanDetail, SpanPage
from coglossa.store.review import SpanFilter
from fastapi import APIRouter, Query

from coglossa_api.auth import User
from coglossa_api.context import Ctx
from coglossa_api.models import (
    AcceptForm,
    EditSpan,
    ErrorBody,
    FlagUpdate,
    GuideAddition,
    UseCandidate,
)

router = APIRouter(tags=["review"])
Score = Annotated[float | None, Query(ge=0, le=100)]


@router.get("/runs/{run_id}/spans")
async def span_page(
    run_id: uuid.UUID,
    ctx: Ctx,
    flagged: bool | None = None,
    code: FlagCode | None = None,
    min_meaning: Score = None,
    max_meaning: Score = None,
    min_style: Score = None,
    max_style: Score = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=review.MAX_PAGE)] = 100,
) -> SpanPage:
    """Spans in reading order. `flagged` and `code` look at open flags only; a span without a
    score never matches a score bound."""
    filt = SpanFilter(
        flagged=flagged,
        code=code,
        min_meaning=min_meaning,
        max_meaning=max_meaning,
        min_style=min_style,
        max_style=max_style,
    )
    return await review.span_page(ctx.db, run_id, filt, offset=offset, limit=limit)


@router.get("/runs/{run_id}/spans/{span_id}")
async def span_detail(run_id: uuid.UUID, span_id: uuid.UUID, ctx: Ctx) -> SpanDetail:
    return await review.span_detail(ctx.db, run_id, span_id)


# CGL-36: a `base` that is no longer the passage's text is refused, with the current text.
_STALE = {
    409: {
        "model": ErrorBody,
        "description": "Someone else changed the passage since `base` (code `passage_changed`, "
        "with `current_text`), or the run is not open for review.",
    }
}


@router.post("/runs/{run_id}/spans/{span_id}/edit", responses=_STALE)
async def edit(
    run_id: uuid.UUID, span_id: uuid.UUID, body: EditSpan, ctx: Ctx, user: User
) -> SpanDetail:
    return await review.edit_span(
        ctx.db, run_id, span_id, body.text, reason=body.reason, base=body.base, user=user
    )


@router.post("/runs/{run_id}/spans/{span_id}/use-candidate", responses=_STALE)
async def use_candidate(
    run_id: uuid.UUID, span_id: uuid.UUID, body: UseCandidate, ctx: Ctx, user: User
) -> SpanDetail:
    return await review.use_candidate(
        ctx.db, run_id, span_id, body.translator, base=body.base, user=user
    )


@router.post("/runs/{run_id}/spans/{span_id}/accept")
async def accept(run_id: uuid.UUID, span_id: uuid.UUID, ctx: Ctx, user: User) -> SpanDetail:
    """Accept the passage as it is: its open flags become accepted."""
    return await review.accept_span(ctx.db, run_id, span_id, user=user)


@router.post("/runs/{run_id}/flags/{flag_id}")
async def set_flag(
    run_id: uuid.UUID, flag_id: int, body: FlagUpdate, ctx: Ctx, user: User
) -> SpanDetail:
    return await review.set_flag_status(
        ctx.db, run_id, flag_id, FlagStatus(body.status), reason=body.reason, user=user
    )


@router.post("/runs/{run_id}/flags/{flag_id}/accept-form")
async def accept_form(
    run_id: uuid.UUID, flag_id: int, body: AcceptForm, ctx: Ctx, user: User
) -> SpanDetail:
    """CGL-109 "This form is fine": dismiss an open terminology flag because the passage uses an
    accepted form of the translation. The form joins the guide entry in a new approved version of
    the set guide; rechecking the set clears the same flag where the form is used elsewhere."""
    return await review.accept_form(ctx.db, run_id, flag_id, body.term, body.form, user=user)


@router.post("/runs/{run_id}/spans/{span_id}/guide")
async def add_to_guide(
    run_id: uuid.UUID, span_id: uuid.UUID, body: GuideAddition, ctx: Ctx, user: User
) -> GuideView:
    entry = body.glossary if body.glossary is not None else body.entity
    assert entry is not None  # GuideAddition validates exactly one
    return await review.add_guide_entry(ctx.db, run_id, span_id, entry, user=user)
