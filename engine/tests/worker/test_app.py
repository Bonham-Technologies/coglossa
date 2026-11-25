import asyncio
import dataclasses
import logging
import os
import signal
from types import SimpleNamespace

import procrastinate
import pytest
from sqlalchemy import text

from coglossa import service
from coglossa.config import ConfigError
from coglossa.schema import PipelineProfile, RunState
from coglossa.store import migrate, runs
from coglossa.store.db import Database
from coglossa.store.migrate import MigrationState
from coglossa.worker import app as worker_app
from coglossa.worker.app import (
    QUEUE,
    ProcrastinateQueue,
    needs_worker,
    open_app,
    queue_status,
    requeue_stalled,
    run_worker,
)
from tests.worker.conftest import make_services


@pytest.fixture
def worker_env(db, fake_backend, tmp_path, monkeypatch, pg_url):
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    svc = make_services(db, fake_backend, tmp_path)

    async def factory(cfg, queue, *, burst=False):
        # its own Database: run_worker closes the services when it stops
        return dataclasses.replace(svc, db=Database(pg_url), queue=queue)

    return svc, factory


async def _submit(svc, tmp_path, pg_url, **opts):
    path = tmp_path / "story.txt"
    path.write_text("Le chat dort. Il pleut.", encoding="utf-8")
    async with open_app(pg_url) as opened:
        return await service.submit_run(
            svc.db,
            svc.storage,
            ProcrastinateQueue(opened),
            svc.cfg,
            svc.prompts,
            path,
            service.SubmitOptions(project="p", target_lang="en", source_lang="fr", **opts),
        )


async def _state(db, run_id):
    async with db.session() as s:
        return RunState((await runs.get_run(s, run_id)).state)


async def _jobs(db, task=service.TASK_FINISH_RUN):
    async with db.engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT status, lock, queueing_lock FROM procrastinate_jobs WHERE task_name = :t"),
            {"t": task},
        )
        return [tuple(r) for r in rows]


def _record_jobs(monkeypatch):
    seen = []

    async def fake_run_job(svc, task, kwargs):
        seen.append((task, kwargs))

    monkeypatch.setattr(worker_app, "run_job", fake_run_job)
    return seen


async def _burst(svc, factory, **kw):
    await run_worker(
        svc.cfg, burst=True, install_signal_handlers=False, services_factory=factory, **kw
    )


async def test_the_worker_completes_a_queued_run(worker_env, tmp_path, pg_url):
    svc, factory = worker_env
    run_id = await _submit(svc, tmp_path, pg_url)
    # one burst call: jobs queued by running jobs (chunks, then finish) run too
    await _burst(svc, factory)
    assert await _state(svc.db, run_id) is RunState.COMPLETE


async def test_checkpoint_then_approval_through_the_queue(worker_env, tmp_path, pg_url):
    svc, factory = worker_env
    run_id = await _submit(svc, tmp_path, pg_url, pipeline_profile=PipelineProfile.LONG_FORM)
    await _burst(svc, factory)
    assert await _state(svc.db, run_id) is RunState.AWAITING_GUIDE_APPROVAL
    async with open_app(pg_url) as opened:
        await service.approve_guide(svc.db, ProcrastinateQueue(opened), run_id)
    await _burst(svc, factory)
    assert await _state(svc.db, run_id) is RunState.COMPLETE


async def test_queueing_lock_deduplicates_and_serialises(db, pg_url):
    async with open_app(pg_url) as opened:
        queue = ProcrastinateQueue(opened)
        await queue.defer(service.TASK_FINISH_RUN, lock="r:finish", run_id="r")
        await queue.defer(service.TASK_FINISH_RUN, lock="r:finish", run_id="r")
    # one waiting job; its lock also stops two copies from running at the same time
    assert await _jobs(db) == [("todo", "r:finish", "r:finish")]


async def test_burst_leaves_retries_scheduled_for_later(worker_env, pg_url, monkeypatch):
    svc, factory = worker_env
    seen = _record_jobs(monkeypatch)
    async with open_app(pg_url) as opened:
        await opened.configure_task(
            service.TASK_FINISH_RUN, queue=worker_app.QUEUE, schedule_in={"hours": 1}
        ).defer_async(run_id="later")
    await asyncio.wait_for(_burst(svc, factory), timeout=30)
    assert ("todo", None, None) in await _jobs(svc.db)
    assert all(kwargs != {"run_id": "later"} for _, kwargs in seen)


async def test_burst_waits_for_jobs_running_elsewhere(worker_env, pg_url, monkeypatch):
    svc, factory = worker_env
    _record_jobs(monkeypatch)
    monkeypatch.setattr(worker_app, "_BURST_POLL_S", 0.05)
    async with open_app(pg_url) as opened:
        await ProcrastinateQueue(opened).defer(service.TASK_FINISH_RUN, lock="o:f", run_id="o")
    async with svc.db.engine.begin() as conn:  # another, live worker is running it
        await conn.execute(
            text(
                "UPDATE procrastinate_jobs SET status = 'doing', "
                "worker_id = (SELECT worker_id FROM procrastinate_register_worker_v1())"
            )
        )
    task = asyncio.create_task(_burst(svc, factory))
    await asyncio.sleep(0.5)
    assert not task.done()
    async with svc.db.engine.begin() as conn:
        await conn.execute(text("UPDATE procrastinate_jobs SET status = 'succeeded'"))
    await asyncio.wait_for(task, timeout=30)


async def test_burst_requeues_the_jobs_of_a_crashed_worker(worker_env, pg_url, monkeypatch):
    svc, factory = worker_env
    seen = _record_jobs(monkeypatch)
    monkeypatch.setattr(worker_app, "_BURST_POLL_S", 0.05)
    # a short pass may end before the periodic re-queue job is deferred: burst mode can't rely on it
    monkeypatch.setattr(worker_app.app.periodic_registry, "periodic_tasks", {})
    async with open_app(pg_url) as opened:
        await ProcrastinateQueue(opened).defer(service.TASK_FINISH_RUN, lock="k:f", run_id="k")
    async with svc.db.engine.begin() as conn:  # its worker died and was pruned
        await conn.execute(text("UPDATE procrastinate_jobs SET status = 'doing'"))
    await asyncio.wait_for(_burst(svc, factory), timeout=30)
    assert (service.TASK_FINISH_RUN, {"run_id": "k"}) in seen


async def _stalled_copy_with_a_waiting_copy(db, pg_url, lock, run_id):
    """A copy of a job left 'doing' by a crashed worker, and a second copy waiting behind it
    (queueing locks only dedupe waiting jobs)."""
    async with open_app(pg_url) as opened:
        queue = ProcrastinateQueue(opened)
        await queue.defer(service.TASK_FINISH_RUN, lock=lock, run_id=run_id)
        async with db.engine.begin() as conn:
            await conn.execute(
                text("UPDATE procrastinate_jobs SET status = 'doing' WHERE lock = :l"),
                {"l": lock},
            )
        await queue.defer(service.TASK_FINISH_RUN, lock=lock, run_id=run_id)


async def test_burst_does_not_spin_on_a_job_whose_lock_is_held(worker_env, pg_url, monkeypatch):
    svc, factory = worker_env
    seen = _record_jobs(monkeypatch)
    monkeypatch.setattr(worker_app, "_BURST_POLL_S", 0.05)
    monkeypatch.setattr(worker_app.app.periodic_registry, "periodic_tasks", {})
    await _stalled_copy_with_a_waiting_copy(svc.db, pg_url, "h:finish", "h")
    await asyncio.wait_for(_burst(svc, factory), timeout=30)
    assert seen == [(service.TASK_FINISH_RUN, {"run_id": "h"})]  # the waiting copy ran
    assert await _jobs(svc.db) == [("failed", "h:finish", "h:finish")]  # the stalled copy


async def test_requeue_stalled_closes_a_copy_that_has_a_waiting_twin(db, pg_url, caplog):
    await _stalled_copy_with_a_waiting_copy(db, pg_url, "t:finish", "t")
    async with open_app(pg_url) as opened:
        queue = ProcrastinateQueue(opened)
        await queue.defer(service.TASK_START_RUN, lock="u:start", run_id="u")
        async with db.engine.begin() as conn:
            await conn.execute(
                text("UPDATE procrastinate_jobs SET status = 'doing' WHERE lock = 'u:start'")
            )
        with caplog.at_level(logging.WARNING, logger="coglossa.worker"):
            assert await requeue_stalled(opened.job_manager, 60) == 1
    assert "(t:finish) from a worker that stopped responding: a copy is waiting" in caplog.text
    assert "re-queued 1 job(s)" in caplog.text
    assert sorted(await _jobs(db)) == [
        ("failed", "t:finish", "t:finish"),
        ("todo", "t:finish", "t:finish"),
    ]
    assert await _jobs(db, service.TASK_START_RUN) == [("todo", "u:start", "u:start")]


async def test_requeue_stalled_raises_other_constraint_errors():
    from procrastinate.exceptions import UniqueViolation

    class Manager:
        async def get_stalled_jobs(self, *, seconds_since_heartbeat):
            return ["job-1"]

        async def retry_job(self, job):
            raise UniqueViolation(constraint_name="other", queueing_lock=None)

    with pytest.raises(UniqueViolation):
        await requeue_stalled(Manager(), 60)


async def test_workers_use_the_configured_stalled_timeout(worker_env, monkeypatch):
    svc, factory = worker_env
    calls = []

    async def spy(self, **options):
        calls.append(options)

    monkeypatch.setattr(procrastinate.App, "run_worker_async", spy)
    worker = svc.cfg.worker.model_copy(update={"stalled_after_s": 45})
    cfg = svc.cfg.model_copy(update={"worker": worker})
    await run_worker(cfg, burst=True, install_signal_handlers=False, services_factory=factory)
    await run_worker(cfg, install_signal_handlers=False, services_factory=factory)
    assert [c["stalled_worker_timeout"] for c in calls] == [45, 45]


async def test_a_stop_signal_ends_burst_mode(worker_env, pg_url, monkeypatch):
    svc, factory = worker_env

    async def stop_then_queue_more(services, task, kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.1)  # the signal handler runs
        await services.queue.defer(service.TASK_FINISH_RUN, lock="s:finish", run_id="s")

    monkeypatch.setattr(worker_app, "run_job", stop_then_queue_more)
    async with open_app(pg_url) as opened:
        await ProcrastinateQueue(opened).defer(service.TASK_START_RUN, lock="s:start", run_id="s")
    await asyncio.wait_for(
        run_worker(svc.cfg, burst=True, install_signal_handlers=True, services_factory=factory),
        timeout=30,
    )
    assert await _jobs(svc.db) == [("todo", "s:finish", "s:finish")]  # not run: stopped


async def test_the_long_running_worker_runs_jobs_until_cancelled(worker_env, tmp_path, pg_url):
    svc, factory = worker_env
    closed = []

    async def tracking_factory(cfg, queue, *, burst=False):
        services = await factory(cfg, queue, burst=burst)
        original = services.aclose

        async def aclose():
            closed.append(True)
            await original()

        services.aclose = aclose
        return services

    run_id = await _submit(svc, tmp_path, pg_url)
    task = asyncio.create_task(
        run_worker(svc.cfg, install_signal_handlers=False, services_factory=tracking_factory)
    )
    for _ in range(300):
        if await _state(svc.db, run_id) is RunState.COMPLETE:
            break
        await asyncio.sleep(0.05)
    assert not task.done()  # still waiting for more jobs
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _state(svc.db, run_id) is RunState.COMPLETE
    assert closed == [True]


async def test_cancelling_burst_mode_propagates(worker_env, pg_url, monkeypatch):
    svc, factory = worker_env
    started = asyncio.Event()

    async def slow_job(services, task, kwargs):
        started.set()
        await asyncio.sleep(0.2)

    monkeypatch.setattr(worker_app, "run_job", slow_job)
    async with open_app(pg_url) as opened:
        await ProcrastinateQueue(opened).defer(service.TASK_START_RUN, lock="c:start", run_id="c")
    task = asyncio.create_task(_burst(svc, factory))
    await asyncio.wait_for(started.wait(), timeout=30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_requeue_stalled():
    class Manager:
        def __init__(self):
            self.retried = []

        async def get_stalled_jobs(self, *, seconds_since_heartbeat):
            assert seconds_since_heartbeat == 60
            return [
                SimpleNamespace(id=1, queueing_lock=None),
                SimpleNamespace(id=2, queueing_lock=None),
            ]

        async def retry_job(self, job):
            self.retried.append(job.id)

    manager = Manager()
    assert await requeue_stalled(manager, 60) == 2
    assert manager.retried == [1, 2]


async def test_requeue_stalled_task_uses_the_configured_threshold(make_config):
    seen = []

    class Manager:
        async def get_stalled_jobs(self, *, seconds_since_heartbeat):
            seen.append(seconds_since_heartbeat)
            return []

    cfg = make_config(worker={"stalled_after_s": 90})
    context = SimpleNamespace(
        app=SimpleNamespace(job_manager=Manager()),
        additional_context={worker_app.SERVICES_KEY: SimpleNamespace(cfg=cfg)},
    )
    await worker_app.requeue_stalled_task(context, timestamp=0)
    assert seen == [90]


async def test_worker_refuses_an_unmigrated_database(worker_env, monkeypatch):
    svc, factory = worker_env
    monkeypatch.setattr(migrate, "migration_state", lambda url: MigrationState(None, "0001"))
    with pytest.raises(ConfigError, match="coglossa db upgrade"):
        await _burst(svc, factory)


async def test_worker_refuses_an_unreachable_database(worker_env, monkeypatch):
    svc, factory = worker_env
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", "postgresql://u:secret@127.0.0.1:9/x")
    with pytest.raises(ConfigError, match="cannot connect") as info:
        await _burst(svc, factory)
    assert "secret" not in str(info.value)


async def test_build_services(tmp_path, monkeypatch, pg_url, make_config, caplog):
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    cfg = make_config(
        storage={"path": str(tmp_path / "files")},
        scorer={"url": "http://127.0.0.1:9", "connect_timeout_s": 0.2, "timeout_s": 0.5},
        worker={"scorer_wait_s": 0},
    )
    svc = await worker_app.build_services(cfg, queue=None)
    try:
        assert svc.scorer is not None and svc.scorer_health is None
        assert set(svc.backends) == {"fake"}
        assert "scorer unavailable" in caplog.text
    finally:
        await svc.aclose()


async def test_the_queue_connection_has_a_connect_timeout(pg_url, monkeypatch):
    real = procrastinate.PsycopgConnector
    seen: dict = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(procrastinate, "PsycopgConnector", spy)
    async with open_app(pg_url):
        pass
    assert seen["kwargs"] == {"connect_timeout": 10}


class _Clock:
    """A fake monotonic clock that the fake sleep advances."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _LoadingScorer:
    """Unreachable for its first `failures` health checks, then healthy."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def health(self):
        from coglossa.schema import ScorerHealth
        from coglossa.scoring.client import ScorerUnavailableError

        self.calls += 1
        if self.calls <= self.failures:
            raise ScorerUnavailableError("scorer unreachable: ConnectError: refused")
        return ScorerHealth(status="ok", qe_model="mx", embed_model="labse", device="cpu")


def _wait_cfg(make_config, **worker):
    return make_config(scorer={"url": "http://127.0.0.1:9"}, worker=worker)


async def _wait(scorer, cfg, clock):
    return await worker_app.wait_for_scorer(scorer, cfg, sleep=clock.sleep, clock=clock)


async def test_the_worker_does_not_wait_for_a_healthy_scorer(make_config, caplog):
    clock, scorer = _Clock(), _LoadingScorer(failures=0)
    health = await _wait(scorer, _wait_cfg(make_config), clock)
    assert health is not None and health.qe_model == "mx"
    assert (scorer.calls, clock.sleeps) == (1, [])
    assert "waiting" not in caplog.text


async def test_the_worker_waits_for_a_loading_scorer(make_config, caplog):
    clock, scorer = _Clock(), _LoadingScorer(failures=3)
    caplog.set_level("INFO", logger="coglossa.worker")
    health = await _wait(scorer, _wait_cfg(make_config), clock)
    assert health is not None
    assert scorer.calls == 4
    assert clock.sleeps == [worker_app.SCORER_POLL_S] * 3
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "waiting up to 900 s for the scorer at http://127.0.0.1:9 to become healthy",
        "the scorer at http://127.0.0.1:9 is healthy",
    ]


async def test_the_worker_starts_without_the_scorer_after_the_wait(make_config, caplog):
    clock, scorer = _Clock(), _LoadingScorer(failures=10_000)
    health = await _wait(scorer, _wait_cfg(make_config, scorer_wait_s=60), clock)
    assert health is None
    assert sum(clock.sleeps) <= 60 and clock.now >= 60 - worker_app.SCORER_POLL_S
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings[0] == "the scorer was not healthy after 60 s; starting without it"
    assert warnings[1].startswith("scorer unavailable at http://127.0.0.1:9: scorer unreachable")
    assert len(warnings) == 2 and not any("\n" in w for w in warnings)


async def test_a_zero_wait_probes_the_scorer_once(make_config, caplog):
    clock, scorer = _Clock(), _LoadingScorer(failures=1)
    health = await _wait(scorer, _wait_cfg(make_config, scorer_wait_s=0), clock)
    assert health is None
    assert (scorer.calls, clock.sleeps) == (1, [])
    assert "scorer unavailable at http://127.0.0.1:9" in caplog.text
    assert "waiting" not in caplog.text


async def _drain_wait(scorer, cfg, clock):
    return await worker_app.wait_for_scorer(scorer, cfg, burst=True, sleep=clock.sleep, clock=clock)


async def test_a_burst_drain_probes_the_scorer_once(make_config, caplog):
    clock, scorer = _Clock(), _LoadingScorer(failures=10_000)
    cfg = _wait_cfg(make_config)  # burst_scorer_wait_s 0 by default; scorer_wait_s is 900
    health = await _drain_wait(scorer, cfg, clock)
    assert health is None
    assert (scorer.calls, clock.sleeps, clock.now) == (1, [], 0.0)
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings[0] == (
        "the scorer at http://127.0.0.1:9 is not reachable; the drain starts without waiting"
    )
    assert warnings[1].startswith("scorer unavailable at http://127.0.0.1:9: scorer unreachable")
    assert len(warnings) == 2
    assert "waiting up to" not in caplog.text


async def test_a_burst_drain_uses_a_healthy_scorer(make_config, caplog):
    clock, scorer = _Clock(), _LoadingScorer(failures=0)
    health = await _drain_wait(scorer, _wait_cfg(make_config), clock)
    assert health is not None and health.qe_model == "mx"
    assert (scorer.calls, clock.sleeps) == (1, [])
    assert "not reachable" not in caplog.text


async def test_a_burst_drain_waits_out_its_own_limit(make_config, caplog):
    clock, scorer = _Clock(), _LoadingScorer(failures=10_000)
    caplog.set_level("INFO", logger="coglossa.worker")
    health = await _drain_wait(scorer, _wait_cfg(make_config, burst_scorer_wait_s=300), clock)
    assert health is None
    assert clock.now == 300 and sum(clock.sleeps) == 300  # not scorer_wait_s (900)
    assert scorer.calls == 1 + 300 / worker_app.SCORER_POLL_S
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings[0] == "the scorer was not healthy after 300 s; starting without it"
    assert "waiting up to 300 s" in caplog.text
    assert "not reachable" not in caplog.text


async def test_a_burst_drain_starts_once_a_cold_scorer_answers(make_config, caplog):
    clock, scorer = _Clock(), _LoadingScorer(failures=4)
    caplog.set_level("INFO", logger="coglossa.worker")
    health = await _drain_wait(scorer, _wait_cfg(make_config, burst_scorer_wait_s=300), clock)
    assert health is not None
    assert scorer.calls == 5
    assert clock.sleeps == [worker_app.SCORER_POLL_S] * 4
    assert "the scorer at http://127.0.0.1:9 is healthy" in caplog.text


async def test_the_long_running_worker_still_waits_out_the_limit(make_config, caplog):
    clock, scorer = _Clock(), _LoadingScorer(failures=10_000)
    caplog.set_level("INFO", logger="coglossa.worker")
    cfg = _wait_cfg(make_config, burst_scorer_wait_s=300)  # not used outside burst mode
    health = await _wait(scorer, cfg, clock)
    assert health is None
    assert clock.now == 900 and sum(clock.sleeps) == 900
    assert scorer.calls == 1 + 900 / worker_app.SCORER_POLL_S
    assert "waiting up to 900 s" in caplog.text
    assert "not reachable" not in caplog.text


@pytest.mark.parametrize("burst", [True, False])
async def test_build_services_tells_the_scorer_wait_whether_it_drains(
    tmp_path, monkeypatch, pg_url, make_config, burst
):
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    cfg = make_config(storage={"path": str(tmp_path / "files")}, scorer={"url": "http://x:9"})
    seen = []

    async def spy(scorer, cfg, *, burst=False):
        seen.append(burst)

    monkeypatch.setattr(worker_app, "wait_for_scorer", spy)
    svc = await worker_app.build_services(cfg, queue=None, burst=burst)
    try:
        assert seen == [burst]
    finally:
        await svc.aclose()


async def test_run_worker_tells_the_services_whether_it_drains(worker_env, monkeypatch):
    svc, factory = worker_env
    seen = []

    async def spy_factory(cfg, queue, *, burst=False):
        seen.append(burst)
        return await factory(cfg, queue, burst=burst)

    async def fake_run(self, **options):
        pass

    monkeypatch.setattr(procrastinate.App, "run_worker_async", fake_run)
    await run_worker(
        svc.cfg, burst=True, install_signal_handlers=False, services_factory=spy_factory
    )
    await run_worker(svc.cfg, install_signal_handlers=False, services_factory=spy_factory)
    assert seen == [True, False]


async def test_no_scorer_means_no_wait(tmp_path, monkeypatch, pg_url, make_config):
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    cfg = make_config(storage={"path": str(tmp_path / "files")})
    calls = []

    async def spy(*args, **kwargs):  # pragma: no cover - must not be called
        calls.append(args)

    monkeypatch.setattr(worker_app, "wait_for_scorer", spy)
    svc = await worker_app.build_services(cfg, queue=None)
    try:
        assert svc.scorer is None and svc.scorer_health is None
        assert calls == []
    finally:
        await svc.aclose()


async def _register_worker(db, *, heartbeat_age_s=0):
    async with db.engine.begin() as conn:
        worker_id = (
            await conn.execute(text("SELECT worker_id FROM procrastinate_register_worker_v1()"))
        ).scalar_one()
        await conn.execute(
            text(
                "UPDATE procrastinate_workers"
                " SET last_heartbeat = now() - make_interval(secs => :a) WHERE id = :w"
            ),
            {"a": heartbeat_age_s, "w": worker_id},
        )
    return worker_id


async def _defer_finish(pg_url, run_id="1"):
    async with open_app(pg_url) as opened:
        await ProcrastinateQueue(opened).defer(
            service.TASK_FINISH_RUN, lock=f"run:{run_id}", run_id=run_id
        )


async def test_an_empty_queue_needs_no_worker(db, pg_url):
    assert await queue_status(pg_url, 60) == worker_app.QueueStatus(
        due=False, stalled=False, live_worker=False
    )
    assert not await needs_worker(pg_url, 60)


async def test_a_due_job_without_a_live_worker_needs_one(db, pg_url):
    await _defer_finish(pg_url)
    await _register_worker(db, heartbeat_age_s=600)  # a dead worker's row is not a live one
    status = await queue_status(pg_url, 60)
    assert (status.due, status.live_worker, status.has_work) == (True, False, True)
    assert await needs_worker(pg_url, 60)


async def test_a_live_worker_fetches_due_jobs_itself(db, pg_url):
    await _defer_finish(pg_url)
    await _register_worker(db)
    status = await queue_status(pg_url, 60)
    assert (status.due, status.live_worker, status.has_work) == (True, True, True)
    assert not await needs_worker(pg_url, 60)


async def test_a_stalled_job_needs_a_worker(db, pg_url):
    await _defer_finish(pg_url)
    dead = await _register_worker(db, heartbeat_age_s=600)
    await _register_worker(db)  # even with another worker alive: someone must re-queue it
    async with db.engine.begin() as conn:
        await conn.execute(
            text("UPDATE procrastinate_jobs SET status = 'doing', worker_id = :w"), {"w": dead}
        )
    status = await queue_status(pg_url, 60)
    assert (status.due, status.stalled, status.has_work) == (False, True, True)
    assert await needs_worker(pg_url, 60)


async def test_a_job_running_on_a_live_worker_needs_none(db, pg_url):
    await _defer_finish(pg_url)
    live = await _register_worker(db)
    async with db.engine.begin() as conn:
        await conn.execute(
            text("UPDATE procrastinate_jobs SET status = 'doing', worker_id = :w"), {"w": live}
        )
    status = await queue_status(pg_url, 60)
    assert not status.has_work and not status.needs_worker


async def test_a_job_scheduled_ahead_is_not_due(db, pg_url):
    async with open_app(pg_url) as opened:
        later = opened.configure_task(
            service.TASK_FINISH_RUN, queue=QUEUE, schedule_in={"hours": 1}
        )
        await later.defer_async(run_id="1")
    assert not (await queue_status(pg_url, 60)).has_work
    assert not await needs_worker(pg_url, 60)
