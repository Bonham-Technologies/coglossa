"""Run progress over server-sent events (spec §7.1). Each stream reads the run's progress every
api.progress_poll_s and sends an event when it changes, until the run reaches a terminal
state. GET /runs/{run_id}/progress is the polling fallback."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

from coglossa import service
from coglossa.schema import TERMINAL_STATES, RunProgress
from coglossa.store.db import error_reason
from coglossa.store.runs import NotFoundError
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import InterfaceError, OperationalError

from coglossa_api.context import ApiContext, Ctx

log = logging.getLogger("coglossa.api")
router = APIRouter(tags=["runs"])
HEARTBEAT_S = 15.0  # comment lines keep proxies from closing a quiet stream


def _event(progress: RunProgress) -> str:
    return f"event: progress\ndata: {progress.model_dump_json()}\n\n"


async def _stream(ctx: ApiContext, run_id: uuid.UUID, first: RunProgress) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    yield _event(first)
    last, quiet = first, 0.0
    poll = ctx.cfg.api.progress_poll_s
    while last.state not in TERMINAL_STATES:
        await asyncio.sleep(poll)
        try:
            current = await service.run_progress(ctx.db, run_id)
        except NotFoundError:
            yield "event: deleted\ndata: {}\n\n"
            return
        except (OperationalError, InterfaceError) as e:
            log.warning("progress stream ended: %s", error_reason(e), extra={"run_id": str(run_id)})
            return  # the browser reconnects after `retry`
        if current != last:
            yield _event(current)
            last, quiet = current, 0.0
        else:
            quiet += poll
            if quiet >= HEARTBEAT_S:
                yield ": keep-alive\n\n"
                quiet = 0.0


@router.get(
    "/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def run_events(run_id: uuid.UUID, ctx: Ctx) -> StreamingResponse:
    """Server-sent events: `progress` (a RunProgress) whenever it changes; `deleted` if the
    run is deleted. The stream ends after a terminal state."""
    first = await service.run_progress(ctx.db, run_id)  # an unknown run is a plain 404
    return StreamingResponse(
        _stream(ctx, run_id, first),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
