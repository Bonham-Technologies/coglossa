import asyncio
import contextlib
import json
import uuid

from coglossa.schema import RunState
from coglossa.store.models import Project, Run, utcnow
from coglossa.testing.seed import seed_run
from sqlalchemy import delete, update

from coglossa_api.routes import progress as progress_module


def _events(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


async def _finish_later(db, run_id, delay=0.15):
    await asyncio.sleep(delay)
    async with db.session() as s, s.begin():
        await s.execute(
            update(Run).where(Run.id == run_id).values(state="complete", finished_at=utcnow())
        )


async def test_a_finished_run_sends_one_event(api, db):
    seeded = await seed_run(db)
    response = await api.client.get(f"/api/runs/{seeded.run_id}/events")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.startswith("retry: 3000\n\n")
    assert [(e, d["state"]) for e, d in _events(response.text)] == [("progress", "complete")]


async def test_changes_are_streamed_until_the_run_ends(api, db):
    seeded = await seed_run(db, state=RunState.SWEEPING)
    finisher = asyncio.create_task(_finish_later(db, seeded.run_id))
    response = await api.client.get(f"/api/runs/{seeded.run_id}/events")
    await finisher
    states = [d["state"] for e, d in _events(response.text)]
    assert states == ["sweeping", "complete"]  # no repeats while nothing changed


async def test_quiet_runs_get_heartbeats(api, db, monkeypatch):
    # The run finishes only after the stream has sent a heartbeat, however long the quiet polls
    # take, so the test doesn't race a wall-clock deadline on a slow runner (CGL-93).
    monkeypatch.setattr(progress_module, "HEARTBEAT_S", 0.04)
    real_stream = progress_module._stream
    heartbeat_sent = asyncio.Event()

    async def watched_stream(*args):
        async for chunk in real_stream(*args):
            if chunk == ": keep-alive\n\n":
                heartbeat_sent.set()
            yield chunk

    monkeypatch.setattr(progress_module, "_stream", watched_stream)
    seeded = await seed_run(db, state=RunState.SWEEPING)

    async def finish_after_heartbeat():
        # A stream that never sends one still ends, so the test fails instead of hanging.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(heartbeat_sent.wait(), timeout=30)
        await _finish_later(db, seeded.run_id, delay=0)

    finisher = asyncio.create_task(finish_after_heartbeat())
    response = await api.client.get(f"/api/runs/{seeded.run_id}/events")
    await finisher
    assert ": keep-alive\n\n" in response.text
    assert [d["state"] for _, d in _events(response.text)] == ["sweeping", "complete"]


async def test_a_deleted_run_ends_the_stream(api, db):
    seeded = await seed_run(db, state=RunState.SWEEPING)

    async def delete_later():
        await asyncio.sleep(0.1)
        async with db.session() as s, s.begin():
            await s.execute(delete(Project).where(Project.id == seeded.project_id))

    deleter = asyncio.create_task(delete_later())
    response = await api.client.get(f"/api/runs/{seeded.run_id}/events")
    await deleter
    assert _events(response.text)[-1] == ("deleted", {})


async def test_database_errors_end_the_stream(api, db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    seeded = await seed_run(db, state=RunState.SWEEPING)
    calls = 0
    real = progress_module.service.run_progress

    async def flaky(database, run_id):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OperationalError("SELECT 1", {}, Exception("connection lost"))
        return await real(database, run_id)

    monkeypatch.setattr(progress_module.service, "run_progress", flaky)
    response = await api.client.get(f"/api/runs/{seeded.run_id}/events")
    assert [e for e, _ in _events(response.text)] == ["progress"]


async def test_unknown_runs_are_404(api):
    assert (await api.client.get(f"/api/runs/{uuid.uuid4()}/events")).status_code == 404
