"""procrastinate wiring (spec §2.1, §7.3). Queue jobs call coglossa.worker.execute; a periodic
job re-queues jobs whose worker stopped sending heartbeats (a crashed or killed worker), and
idempotent jobs make the re-run safe."""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import procrastinate
from procrastinate import JobContext, RetryStrategy, signals
from procrastinate.exceptions import AlreadyEnqueued, UniqueViolation
from procrastinate.jobs import Status
from procrastinate.manager import QUEUEING_LOCK_CONSTRAINT
from sqlalchemy.exc import InterfaceError, OperationalError

from coglossa.config import CoglossaConfig, config_hash
from coglossa.llm.registry import build_backends
from coglossa.prompts.check import validate_prompts
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import ScorerHealth
from coglossa.scoring.client import Scorer, build_scorer, probe_scorer, scorer_location
from coglossa.scoring.tiers import LanguageTiers
from coglossa.service import (
    TASK_FINISH_RUN,
    TASK_PROCESS_CHUNK,
    TASK_START_RUN,
    TASK_TRANSLATE_RUN,
    JobQueue,
)
from coglossa.storage import build_storage
from coglossa.store.db import CONNECT_ARGS, Database, database_url, to_libpq_url
from coglossa.store.migrate import ensure_migrated
from coglossa.worker.execute import WorkerServices, run_job

log = logging.getLogger("coglossa.worker")

QUEUE = "coglossa"
SERVICES_KEY = "coglossa_services"
TASK_REQUEUE_STALLED = "coglossa.requeue_stalled"
_DB_RETRY = RetryStrategy(
    max_attempts=5,
    wait=5,
    exponential_wait=2,
    retry_exceptions={OperationalError, InterfaceError},
)
_BURST_POLL_S = 1.0  # burst mode: pause before looking again while other workers run jobs
SCORER_POLL_S = 5.0  # startup: pause between health checks while the scorer loads its models

# The connector is replaced by open_app(); this one is never opened.
app = procrastinate.App(connector=procrastinate.PsycopgConnector())


def _services(context: JobContext) -> WorkerServices:
    return context.additional_context[SERVICES_KEY]


@app.task(name=TASK_START_RUN, queue=QUEUE, pass_context=True, retry=_DB_RETRY)
async def start_run_task(context: JobContext, run_id: str) -> None:
    await run_job(_services(context), TASK_START_RUN, {"run_id": run_id})


@app.task(name=TASK_TRANSLATE_RUN, queue=QUEUE, pass_context=True, retry=_DB_RETRY)
async def translate_run_task(context: JobContext, run_id: str) -> None:
    await run_job(_services(context), TASK_TRANSLATE_RUN, {"run_id": run_id})


@app.task(name=TASK_PROCESS_CHUNK, queue=QUEUE, pass_context=True, retry=_DB_RETRY)
async def process_chunk_task(context: JobContext, run_id: str, chunk_id: str) -> None:
    await run_job(_services(context), TASK_PROCESS_CHUNK, {"run_id": run_id, "chunk_id": chunk_id})


@app.task(name=TASK_FINISH_RUN, queue=QUEUE, pass_context=True, retry=_DB_RETRY)
async def finish_run_task(context: JobContext, run_id: str) -> None:
    await run_job(_services(context), TASK_FINISH_RUN, {"run_id": run_id})


@dataclass(frozen=True)
class QueueStatus:
    """What an on-demand worker would find (hosted preview): jobs it could run now, jobs left
    'doing' by a worker that stopped sending heartbeats, and whether a worker is alive."""

    due: bool
    stalled: bool
    live_worker: bool

    @property
    def has_work(self) -> bool:
        return self.due or self.stalled

    @property
    def needs_worker(self) -> bool:
        # a live worker fetches due jobs itself; stalled jobs need a pass that re-queues them
        return self.stalled or (self.due and not self.live_worker)


# procrastinate's own heartbeat table; every worker registers there while it runs
_LIVE_WORKERS_SQL = """
SELECT count(*) AS live
  FROM procrastinate_workers
 WHERE last_heartbeat >= now() - make_interval(secs => %(seconds)s)
"""


async def queue_status(url: str, stalled_after_s: float) -> QueueStatus:
    async with open_app(url) as opened:
        pending = await opened.connector.execute_query_one_async(_PENDING_SQL, queue=QUEUE)
        live = await opened.connector.execute_query_one_async(
            _LIVE_WORKERS_SQL, seconds=stalled_after_s
        )
        stalled = await opened.job_manager.get_stalled_jobs(
            queue=QUEUE, seconds_since_heartbeat=stalled_after_s
        )
    return QueueStatus(
        due=bool(pending["due"]), stalled=bool(stalled), live_worker=bool(live["live"])
    )


async def needs_worker(url: str, stalled_after_s: float) -> bool:
    """Whether the on-demand cron should start a worker: jobs are due and no worker is alive
    to fetch them, or a dead worker left jobs stalled."""
    return (await queue_status(url, stalled_after_s)).needs_worker


async def requeue_stalled(manager: Any, seconds: float) -> int:
    """Re-queue the jobs of workers silent for `seconds`; returns how many were re-queued.
    A stalled job whose copy is already waiting (same queueing lock) can't be re-queued: it
    is closed as failed instead, and the waiting copy does the work (jobs are idempotent)."""
    requeued = 0
    for job in await manager.get_stalled_jobs(seconds_since_heartbeat=seconds):
        try:
            await manager.retry_job(job)
        except UniqueViolation as e:
            if e.constraint_name != QUEUEING_LOCK_CONSTRAINT:
                raise
            await manager.finish_job_by_id_async(job.id, Status.FAILED, delete_job=False)
            log.warning(
                "closed job %s (%s) from a worker that stopped responding: a copy is waiting",
                job.id,
                job.queueing_lock,
            )
            continue
        requeued += 1
    if requeued:
        log.warning("re-queued %d job(s) from a worker that stopped responding", requeued)
    return requeued


@app.periodic(cron="* * * * *", periodic_id="requeue-stalled")
@app.task(name=TASK_REQUEUE_STALLED, queue=QUEUE, pass_context=True)
async def requeue_stalled_task(context: JobContext, timestamp: int) -> None:
    await requeue_stalled(context.app.job_manager, _services(context).cfg.worker.stalled_after_s)


@contextlib.asynccontextmanager
async def open_app(url: str) -> AsyncIterator[procrastinate.App]:
    connector = procrastinate.PsycopgConnector(
        conninfo=to_libpq_url(url), kwargs=dict(CONNECT_ARGS)
    )
    with app.replace_connector(connector) as opened:
        async with opened.open_async():
            yield opened


class ProcrastinateQueue:
    def __init__(self, opened: procrastinate.App) -> None:
        self._app = opened

    async def defer(self, task: str, *, lock: str | None = None, **kwargs: str) -> None:
        # queueing_lock: one waiting copy of a job; lock: copies never run at the same time
        deferrer = self._app.configure_task(task, queue=QUEUE, queueing_lock=lock, lock=lock)
        with contextlib.suppress(AlreadyEnqueued):  # already waiting: idempotent by design
            await deferrer.defer_async(**kwargs)


async def wait_for_scorer(
    scorer: Scorer,
    cfg: CoglossaConfig,
    *,
    burst: bool = False,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ScorerHealth | None:
    """Wait up to worker.scorer_wait_s for the scorer to answer its health check. Compose's
    depends_on holds the worker back on `up`, but not when Docker restarts both after a reboot;
    chunks scored while the scorer still loads would get no qe or xsim. After the wait the
    worker starts anyway (spec §7.3: an unavailable scorer leaves those signals null).
    A burst drain waits up to worker.burst_scorer_wait_s instead; at 0 it checks once and
    starts at once."""
    limit = cfg.worker.burst_scorer_wait_s if burst else cfg.worker.scorer_wait_s
    deadline = clock() + limit
    health, warnings = await probe_scorer(scorer, cfg)
    where = scorer_location(cfg)
    if health is None and burst and limit == 0:
        log.warning("the scorer at %s is not reachable; the drain starts without waiting", where)
    elif health is None and limit > 0:
        log.info("waiting up to %.0f s for the scorer at %s to become healthy", limit, where)
        while health is None and (left := deadline - clock()) > 0:
            await sleep(min(SCORER_POLL_S, left))
            health, warnings = await probe_scorer(scorer, cfg)
        if health is None:
            log.warning("the scorer was not healthy after %.0f s; starting without it", limit)
        else:
            log.info("the scorer at %s is healthy", where)
    for warning in warnings:
        log.warning(warning)
    return health


async def build_services(
    cfg: CoglossaConfig, queue: JobQueue, *, burst: bool = False
) -> WorkerServices:
    prompts = PromptLibrary(cfg.prompts_dir)
    validate_prompts(cfg, prompts)
    tiers = LanguageTiers.load(cfg.scoring.language_tiers_file)
    cfg_hash = config_hash(cfg)  # with the tiers file as it was just loaded
    storage = build_storage(cfg.storage)
    storage.check_writable()
    backends = build_backends(cfg)
    scorer = build_scorer(cfg)
    health = await wait_for_scorer(scorer, cfg, burst=burst) if scorer is not None else None
    return WorkerServices(
        cfg=cfg,
        db=Database(database_url(cfg)),
        storage=storage,
        queue=queue,
        backends=backends,
        prompts=prompts,
        tiers=tiers,
        scorer=scorer,
        scorer_health=health,
        cfg_hash=cfg_hash,
    )


class ServicesFactory(Protocol):
    def __call__(
        self, cfg: CoglossaConfig, queue: JobQueue, *, burst: bool = False
    ) -> Awaitable[WorkerServices]: ...


# Jobs a burst worker still has to wait for: due jobs it can fetch, and jobs another worker is
# running (they may queue follow-ups). A waiting job whose lock is held by a running job can't
# be fetched (procrastinate_fetch_job_v2), so it is not due: the running job is waited for.
# A retry scheduled for later is left to a long-running worker, so a failing job never keeps
# burst mode alive. Every job is deferred with queueing_lock = lock, so two waiting jobs never
# share a lock and a due job is never held back by an earlier waiting one.
_PENDING_SQL = """
SELECT count(*) FILTER (
           WHERE status = 'todo'
             AND (scheduled_at IS NULL OR scheduled_at <= now())
             AND NOT EXISTS (
                 SELECT 1 FROM procrastinate_jobs AS holder
                  WHERE holder.lock = jobs.lock AND holder.status = 'doing'
             )
       ) AS due,
       count(*) FILTER (WHERE status = 'doing') AS running
  FROM procrastinate_jobs AS jobs
 WHERE queue_name = %(queue)s
"""


async def _queue_settled(opened: procrastinate.App, stalled_after_s: float) -> bool:
    row = await opened.connector.execute_query_one_async(_PENDING_SQL, queue=QUEUE)
    if row["due"]:
        return False
    if row["running"]:
        # A crashed worker's jobs stay 'doing' until re-queued. The periodic job may not get
        # to run in a short pass, so burst mode re-queues them itself.
        await requeue_stalled(opened.job_manager, stalled_after_s)
        await asyncio.sleep(_BURST_POLL_S)
        return False
    return True


async def _run_burst(
    opened: procrastinate.App,
    cfg: CoglossaConfig,
    install_signal_handlers: bool,
    options: dict[str, Any],
) -> None:
    """Drain the queue, including jobs that running jobs queue. procrastinate's own burst
    mode (wait=False) stops at the first empty fetch, so it runs in passes until the queue
    has settled. A stop signal ends the current pass gracefully and the loop."""
    stopped = False
    current: asyncio.Task[None] | None = None

    def stop() -> None:
        nonlocal stopped
        stopped = True
        if current is not None:
            current.cancel()  # procrastinate stops fetching and waits for running jobs

    handlers = signals.on_stop(stop) if install_signal_handlers else contextlib.nullcontext()
    with handlers:
        while not stopped:  # a signal may also arrive between passes
            current = asyncio.create_task(
                opened.run_worker_async(wait=False, install_signal_handlers=False, **options)
            )
            try:
                await current
            except asyncio.CancelledError:
                if not stopped:
                    raise
            if stopped or await _queue_settled(opened, cfg.worker.stalled_after_s):
                return


async def run_worker(
    cfg: CoglossaConfig,
    *,
    burst: bool = False,
    install_signal_handlers: bool = True,
    services_factory: ServicesFactory = build_services,
) -> None:
    url = database_url(cfg)
    await asyncio.to_thread(ensure_migrated, url)
    async with open_app(url) as opened:
        services = await services_factory(cfg, ProcrastinateQueue(opened), burst=burst)
        options: dict[str, Any] = {
            "queues": [QUEUE],
            "concurrency": cfg.worker.concurrency,
            "name": "coglossa-worker",
            "additional_context": {SERVICES_KEY: services},
            "delete_jobs": "successful",
            # procrastinate's own default (30 s) would detach a slow live worker's jobs, and
            # the stalled-job query re-queues detached jobs at once
            "stalled_worker_timeout": cfg.worker.stalled_after_s,
        }
        try:
            if burst:
                await _run_burst(opened, cfg, install_signal_handlers, options)
            else:
                await opened.run_worker_async(
                    wait=True, install_signal_handlers=install_signal_handlers, **options
                )
        finally:
            await services.aclose()
