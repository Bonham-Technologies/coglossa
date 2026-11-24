"""Worker job bodies (spec §7.1–§7.3). Each public coroutine is one queue job. Jobs are
idempotent, keyed by (run, stage, chunk) in stage_result: a re-delivered or re-queued job skips
work that already completed. The library stages do the work; this module loads inputs, saves
outputs and moves the run through its lifecycle. No transaction is held across a model call."""

import asyncio
import functools
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from coglossa.config import CoglossaConfig, ConfigError, config_hash
from coglossa.ingest.base import IngestError
from coglossa.ingest.registry import ingest_file
from coglossa.llm.agent import AgentRunner, CallLog
from coglossa.llm.types import Backend
from coglossa.problems import GUIDE_CHANGED_AGAIN
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import (
    TERMINAL_STATES,
    DocumentProfile,
    Entity,
    GlossaryEntry,
    Guide,
    LLMCallRecord,
    PipelineProfile,
    Reliability,
    RunError,
    RunState,
    ScorerHealth,
    SourceDocument,
)
from coglossa.scoring.aggregate import model_diversity
from coglossa.scoring.client import Scorer
from coglossa.scoring.tiers import LanguageTiers
from coglossa.segment.chunking import chunk_segments, compute_budget
from coglossa.segment.segmenter import segment_document
from coglossa.service import (
    TASK_FINISH_RUN,
    TASK_PROCESS_CHUNK,
    TASK_START_RUN,
    TASK_TRANSLATE_RUN,
    JobQueue,
)
from coglossa.stages.chunk import ChunkOutcome, engine_error_outcome, process_chunk
from coglossa.stages.context import StageContext, StageError
from coglossa.stages.guide import carry_over, guide_was_built, run_guide
from coglossa.stages.profile import run_profile
from coglossa.stages.sweep import SpanChange, run_sweep
from coglossa.stages.translate import ChunkInput, build_chunk_inputs
from coglossa.storage import Storage, StorageError
from coglossa.store import outputs, runs
from coglossa.store.db import Database
from coglossa.store.models import Run, StageResult, utcnow
from coglossa.text import entry_key

log = logging.getLogger("coglossa.worker")

DB_ERRORS: tuple[type[Exception], ...] = (OperationalError, InterfaceError)
RUN_FAILURES: tuple[type[Exception], ...] = (StageError, IngestError, ConfigError, StorageError)
_BEFORE_TRANSLATION = (
    RunState.CREATED,
    RunState.INGESTING,
    RunState.PROFILING,
    RunState.BUILDING_GUIDE,
)
_NEXT_STAGE = {"translate": "reconcile", "reconcile": "score"}
_DONE = (runs.COMPLETE, runs.FAILED)  # a chunk is done when its score row is one of these


@dataclass
class WorkerServices:
    cfg: CoglossaConfig
    db: Database
    storage: Storage
    queue: JobQueue
    backends: dict[str, Backend]
    prompts: PromptLibrary
    tiers: LanguageTiers
    scorer: Scorer | None = None
    scorer_health: ScorerHealth | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # The hash of the configuration this worker runs with. It is taken when the worker loads its
    # tiers file (build_services), not when a run starts: the worker scores with the table it
    # loaded, so an edit to the file it has not applied yet must read as a change.
    cfg_hash: str = ""

    def __post_init__(self) -> None:
        if not self.cfg_hash:
            self.cfg_hash = config_hash(self.cfg)

    def call_log(self) -> CallLog:
        return CallLog(store_io=self.cfg.audit.store_llm_io)

    def context(self, calls: CallLog, target_lang: str) -> StageContext:
        runner = AgentRunner(self.cfg, self.backends, self.prompts, calls, sleep=self.sleep)
        return StageContext(cfg=self.cfg, runner=runner, target_lang=target_lang)

    async def aclose(self) -> None:
        for backend in self.backends.values():
            await backend.aclose()
        if self.scorer is not None:
            await self.scorer.aclose()
        await self.db.dispose()


class _Stop(Exception):
    """The run cannot continue in this job (failed or cancelled); already recorded."""


def _internal(e: BaseException) -> str:
    return f"internal error: {type(e).__name__}: {e}"


def _cancel_if_requested(run: Run) -> bool:
    """True when the run must stop: a cancel was requested (the state becomes cancelled)."""
    if not run.cancel_requested:
        return False
    if RunState(run.state) not in TERMINAL_STATES:
        run.state = RunState.CANCELLED
        run.finished_at = utcnow()
    return True


def _job[**P](fn: Callable[P, Awaitable[None]]) -> Callable[P, Awaitable[None]]:
    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            await fn(*args, **kwargs)
        except runs.NotFoundError as e:
            log.info("job skipped: %s", e)

    return wrapper


# ---------------------------------------------------------------- run-level stages

Saver = Callable[[AsyncSession], Awaitable[None]]
StageFn = Callable[["WorkerServices", uuid.UUID, CallLog], Awaitable[tuple[dict[str, Any], Saver]]]


async def _stage(
    svc: WorkerServices, run_id: uuid.UUID, name: str, state: RunState, fn: StageFn
) -> dict[str, Any]:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        cancelled = _cancel_if_requested(run)
        done = None if cancelled else await runs.get_stage(s, run_id, name)
        row_id: int | None = None
        if not cancelled and (done is None or done.status != runs.COMPLETE):
            run.state = state
            row_id = (await runs.begin_stage(s, run_id, name)).id
    if cancelled:
        raise _Stop
    if row_id is None:
        assert done is not None
        return done.output or {}
    calls = svc.call_log()
    try:
        output, save = await fn(svc, run_id, calls)
    except DB_ERRORS:
        raise
    except RUN_FAILURES as e:
        await _fail_run(svc, run_id, row_id, str(e), calls, name)
        raise _Stop from e
    except Exception as e:
        log.exception("stage %s failed", name, extra={"run_id": str(run_id), "stage": name})
        await _fail_run(svc, run_id, row_id, _internal(e), calls, name)
        raise _Stop from e
    try:
        async with svc.db.session() as s, s.begin():
            await save(s)
            row = await s.get(StageResult, row_id)
            assert row is not None
            runs.finish_stage(row, output)
            await runs.save_llm_calls(s, run_id, calls.records, stage=name)
    except DB_ERRORS:
        raise
    except Exception as e:
        # the save rolled back; the failure is recorded in a fresh transaction (spec §7.3)
        log.exception("saving stage %s failed", name, extra={"run_id": str(run_id), "stage": name})
        await _fail_run(svc, run_id, row_id, _internal(e), calls, name)
        raise _Stop from e
    log.info("stage complete", extra={"run_id": str(run_id), "stage": name})
    return output


async def _record_failure(run_id: uuid.UUID, record: Callable[[bool], Awaitable[None]]) -> None:
    """Records a failure in a fresh transaction: record(True) saves it with everything the
    failed work produced (the call log, engine_error passages). If that cannot be saved for a
    reason other than the database, record(False) saves the failure alone, so the run never
    stays stranded in its working state."""
    try:
        await record(True)
    except DB_ERRORS:
        raise
    except Exception:
        log.exception("recording a failure in full failed", extra={"run_id": str(run_id)})
        await record(False)


async def _fail_run(
    svc: WorkerServices, run_id: uuid.UUID, row_id: int, message: str, calls: CallLog, stage: str
) -> None:
    async def record(full: bool) -> None:
        async with svc.db.session() as s, s.begin():
            run = await runs.get_run(s, run_id, lock=True)
            row = await s.get(StageResult, row_id)
            assert row is not None
            runs.fail_stage(row, message)
            await runs.save_llm_calls(s, run_id, calls.records if full else [], stage=stage)
            if RunState(run.state) not in TERMINAL_STATES:
                run.state, run.error, run.finished_at = RunState.FAILED, message, utcnow()

    await _record_failure(run_id, record)


async def _ingest(
    svc: WorkerServices, run_id: uuid.UUID, calls: CallLog
) -> tuple[dict[str, Any], Saver]:
    async with svc.db.session() as s:
        run = await runs.get_run(s, run_id)
        doc = await runs.get_document(s, run.document_id)
        blocks, segments = await runs.load_source(s, doc.id)
        notices = await runs.first_ingest_notices(s, doc.id) if blocks else []
    fresh = not blocks
    if fresh:
        with svc.storage.local_file(doc.storage_key) as path:
            try:
                source = ingest_file(path)
            except IngestError as e:  # name the uploaded file, not its safe storage key
                raise IngestError(str(e).replace(path.name, doc.filename)) from e
        blocks = [
            b.model_copy(
                update={"provenance": b.provenance.model_copy(update={"source_path": doc.filename})}
            )
            for b in source.blocks
        ]
        source = source.model_copy(update={"source_path": doc.filename, "blocks": blocks})
        segments = segment_document(source)
        notices = source.notices
    else:
        source = SourceDocument(
            source_path=doc.filename, format=doc.format, sha256=doc.sha256, blocks=blocks
        )
    chunks = chunk_segments(source, segments, compute_budget(svc.cfg))

    async def save(s: AsyncSession) -> None:
        if fresh:
            await runs.save_source(s, doc.id, source.blocks, segments)
        await runs.save_chunks(s, run_id, chunks)

    # notices: parts of the file left out (CGL-87), shown as document notices, not errors
    output = {"errors": [], "notices": notices, "segments": len(segments), "chunks": len(chunks)}
    return output, save


async def _profile(
    svc: WorkerServices, run_id: uuid.UUID, calls: CallLog
) -> tuple[dict[str, Any], Saver]:
    async with svc.db.session() as s:
        run = await runs.get_run(s, run_id)
        _, segments = await runs.load_source(s, run.document_id)
    profile = await run_profile(
        svc.context(calls, run.target_lang),
        segments,
        source_lang=run.source_lang,
        pipeline_profile=PipelineProfile(run.pipeline_profile) if run.pipeline_profile else None,
    )
    tier = svc.tiers.tier(profile.source_lang, profile.target_lang)

    async def save(s: AsyncSession) -> None:
        locked = await runs.get_run(s, run_id, lock=True)
        locked.profile = profile.model_dump(mode="json")
        locked.language_tier = tier.value

    return {"errors": []}, save


async def _guide(
    svc: WorkerServices, run_id: uuid.UUID, calls: CallLog
) -> tuple[dict[str, Any], Saver]:
    async with svc.db.session() as s:
        run = await runs.get_run(s, run_id)
        doc = await runs.get_document(s, run.document_id)
        _, segments = await runs.load_source(s, doc.id)
        chunks = await runs.load_chunks(s, run_id)
        prior_row = await runs.prior_guide(s, doc.project_id, run.target_lang)
    profile = DocumentProfile.model_validate(run.profile)
    if run.use_project_guide and prior_row is not None:
        # reused as-is: the set guide, which may be a draft of a file translating alongside this
        # one and newer than the latest approved version, so the files share names (CGL-28)
        reused_id, reused_version = prior_row.id, prior_row.version

        async def reuse(s: AsyncSession) -> None:
            (await runs.get_run(s, run_id, lock=True)).guide_version_id = reused_id

        return {"errors": [], "checkpoint": False, "reused_version": reused_version}, reuse
    prior = Guide.model_validate(prior_row.content) if prior_row is not None else None
    built_from = prior_row.version if prior_row is not None else None
    guide, errors = await run_guide(
        svc.context(calls, run.target_lang), profile, segments, chunks, prior=prior
    )
    checkpoint = guide_was_built(errors) and (
        run.checkpoint
        if run.checkpoint is not None
        else svc.cfg.profiles.checkpoint.get(profile.pipeline_profile, False)
    )

    async def save(s: AsyncSession) -> None:
        locked = await runs.get_run(s, run_id, lock=True)  # lock order: run, then project
        await runs.lock_project(s, doc.project_id)
        # The files of one set agree on names: the draft is merged onto the project guide as it
        # is now, which may have grown while the model worked (another file's draft, a person's
        # save). Its entries win; the draft adds what is new (CGL-23).
        current = await runs.prior_guide(s, doc.project_id, run.target_lang)
        merged = guide
        if current is not None:
            now = Guide.model_validate(current.content)
            merged = carry_over(now, guide)
            if current.author == runs.USER and current.version != built_from:
                # a person saved the guide while the model worked: their style guide wins
                merged = merged.model_copy(update={"style_guide": now.style_guide})
        version = await runs.add_guide_version(
            s,
            doc.project_id,
            merged,
            target_lang=run.target_lang,
            status=runs.DRAFT,
            author=runs.ENGINE,
            source_run_id=run_id,
            # what a checkpoint approval is measured against
            built_from_version=current.version if current is not None else None,
        )
        locked.guide_version_id = version.id
        if checkpoint:  # in the same transaction: an unapproved draft is never the set guide
            locked.state = RunState.AWAITING_GUIDE_APPROVAL

    return {"errors": [e.model_dump() for e in errors], "checkpoint": checkpoint}, save


@_job
async def start_run(svc: WorkerServices, run_id: uuid.UUID) -> None:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        cancelled = _cancel_if_requested(run)
        stop = cancelled or RunState(run.state) not in _BEFORE_TRANSLATION
        # translating was committed but the chunk jobs may not all be queued (a crash between)
        resume = not cancelled and run.state in (RunState.TRANSLATING, RunState.SWEEPING)
        if not stop:
            run.started_at = run.started_at or utcnow()
            if svc.scorer_health is not None:
                run.scorer_health = svc.scorer_health.model_dump(mode="json")
            current = svc.cfg_hash
            if run.config_hash != current and await runs.get_stage(s, run_id, "config") is None:
                message = (
                    f"the worker's configuration ({current}) differs from the one this run "
                    f"was submitted with ({run.config_hash}); results reflect the worker's "
                    "configuration"
                )
                row = await runs.begin_stage(s, run_id, "config")
                runs.finish_stage(
                    row, {"errors": [RunError(stage="config", message=message).model_dump()]}
                )
    if resume:
        await translate_run(svc, run_id)
    if stop:
        return
    try:
        await _stage(svc, run_id, "ingest", RunState.INGESTING, _ingest)
        await _stage(svc, run_id, "profile", RunState.PROFILING, _profile)
        guide = await _stage(svc, run_id, "guide", RunState.BUILDING_GUIDE, _guide)
    except _Stop:
        return
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        proceed = not _cancel_if_requested(run) and RunState(run.state) in _BEFORE_TRANSLATION
        if proceed:
            if guide.get("checkpoint"):
                run.state = RunState.AWAITING_GUIDE_APPROVAL
                proceed = False
            else:
                run.state = RunState.TRANSLATING
    if proceed:
        await translate_run(svc, run_id)


# ---------------------------------------------------------------- chunks


@_job
async def translate_run(svc: WorkerServices, run_id: uuid.UUID) -> None:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        if _cancel_if_requested(run):
            return
        state = RunState(run.state)
        if state is RunState.TRANSLATING:
            chunks = await runs.load_chunks(s, run_id)
            finished = {
                r.chunk_id
                for r in await runs.stage_rows(s, run_id)
                if r.stage == "score" and r.status in _DONE
            }
    if state is RunState.SWEEPING:
        await _queue_finish(svc, run_id)
    if state is not RunState.TRANSLATING:
        return
    pending = [c.id for c in chunks if c.id not in finished]
    for chunk_id in pending:
        await svc.queue.defer(
            TASK_PROCESS_CHUNK,
            lock=f"{run_id}:{chunk_id}",
            run_id=str(run_id),
            chunk_id=chunk_id,
        )
    if not pending:
        await _maybe_finish(svc, run_id)


@_job
async def process_chunk_job(svc: WorkerServices, run_id: uuid.UUID, chunk_id: str) -> None:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        if _cancel_if_requested(run):
            return
        state = RunState(run.state)
        done = await runs.get_stage(s, run_id, "score", chunk_id)
        finished = done is not None and done.status in _DONE
        if state is RunState.TRANSLATING and not finished:
            chunks = await runs.load_chunks(s, run_id)
            blocks, segments = await runs.load_source(s, run.document_id)
            guide = await runs.load_guide(s, run.guide_version_id)
            profile = DocumentProfile.model_validate(run.profile)
            tier = Reliability(run.language_tier)
            target_lang = run.target_lang
            await runs.begin_stage(s, run_id, "translate", chunk_id)
    if state is RunState.SWEEPING:
        await _queue_finish(svc, run_id)
    if state is not RunState.TRANSLATING:
        return
    if finished:
        await _maybe_finish(svc, run_id)
        return

    calls = svc.call_log()

    async def on_stage(stage: str) -> None:
        # With a split chunk this fires once per half; the rows settle when the chunk ends.
        # A late copy of the job (another delivery finished the chunk, or the run moved on)
        # leaves the rows alone, and a stage row that already finished is never reopened.
        following = _NEXT_STAGE[stage]
        async with svc.db.session() as s, s.begin():
            run = await runs.get_run(s, run_id, lock=True)
            score = await runs.get_stage(s, run_id, "score", chunk_id)
            if (
                run.cancel_requested
                or run.state != RunState.TRANSLATING
                or (score is not None and score.status in _DONE)
            ):
                return
            row = await runs.get_stage(s, run_id, stage, chunk_id)
            if row is not None and row.status == runs.RUNNING:
                runs.finish_stage(row, None)
            if await runs.get_stage(s, run_id, following, chunk_id) is None:
                await runs.begin_stage(s, run_id, following, chunk_id)

    ci: ChunkInput | None = None
    ordinal = -1
    try:
        ordinal = [c.id for c in chunks].index(chunk_id)
        ci = build_chunk_inputs(chunks, segments, blocks)[ordinal]
        outcome = await process_chunk(
            svc.context(calls, target_lang),
            svc.scorer,
            profile,
            guide,
            ci,
            tier=tier,
            diverse=model_diversity(svc.cfg),
            on_stage=on_stage,
        )
    except DB_ERRORS:
        raise
    except Exception as e:
        log.exception("chunk failed", extra={"run_id": str(run_id), "chunk_id": chunk_id})
        await _fail_chunk(svc, run_id, chunk_id, ci, ordinal, calls, _internal(e))
    else:
        try:
            await _save_chunk(svc, run_id, chunk_id, (ordinal, outcome), calls)
        except DB_ERRORS:
            raise
        except Exception as e:
            # the save rolled back; the chunk fails in a fresh transaction (spec §7.3)
            log.exception(
                "saving chunk failed", extra={"run_id": str(run_id), "chunk_id": chunk_id}
            )
            await _fail_chunk(svc, run_id, chunk_id, ci, ordinal, calls, _internal(e))
    await _maybe_finish(svc, run_id)


async def _fail_chunk(
    svc: WorkerServices,
    run_id: uuid.UUID,
    chunk_id: str,
    ci: ChunkInput | None,
    ordinal: int,
    calls: CallLog,
    message: str,
) -> None:
    """Fails the chunk's score row. Spec §7.3: the chunk's passages stay in the report, as
    engine_error passages. If even that cannot be saved, the failure is recorded alone."""
    failed = (ordinal, engine_error_outcome(ci, message)) if ci is not None else None

    async def record(full: bool) -> None:
        saved, log_ = (failed, calls) if full else (None, svc.call_log())
        await _save_chunk(svc, run_id, chunk_id, saved, log_, error=message)

    await _record_failure(run_id, record)


async def _save_chunk(
    svc: WorkerServices,
    run_id: uuid.UUID,
    chunk_id: str,
    saved: tuple[int, ChunkOutcome] | None,
    calls: CallLog,
    *,
    error: str | None = None,
) -> None:
    """Replaces the chunk's outputs with `saved` (ordinal, outcome) and settles its stage rows
    in one transaction: `error` fails the score row, otherwise it completes with the outcome.
    A late copy of the job (another delivery already finished the chunk, or the run has moved
    on) only logs its model calls: a finished chunk's results are never overwritten."""
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        await runs.save_llm_calls(s, run_id, calls.records, stage="chunk", chunk_id=chunk_id)
        score = await runs.get_stage(s, run_id, "score", chunk_id)
        finished = score is not None and score.status in _DONE
        if finished or run.state != RunState.TRANSLATING:
            log.info(
                "late chunk result discarded", extra={"run_id": str(run_id), "chunk_id": chunk_id}
            )
            return
        if score is None:
            score = await runs.begin_stage(s, run_id, "score", chunk_id)
        if saved is not None:
            ordinal, outcome = saved
            await outputs.delete_chunk_outputs(s, run_id, [chunk_id])
            await outputs.save_chunk_outcome(s, run_id, ordinal, outcome)
        if error is not None:
            runs.fail_stage(score, error)
            return
        assert saved is not None
        for stage in ("translate", "reconcile"):
            row = await runs.get_stage(s, run_id, stage, chunk_id)
            if row is not None and row.status == runs.RUNNING:
                runs.finish_stage(row, None)
        runs.finish_stage(score, saved[1].model_dump(mode="json"))


async def _maybe_finish(svc: WorkerServices, run_id: uuid.UUID) -> None:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        ready = not _cancel_if_requested(run) and run.state == RunState.TRANSLATING
        if ready:
            total = await runs.chunk_count(s, run_id)
            ready = await runs.finished_chunk_count(s, run_id) >= total
            if ready:
                run.state = RunState.SWEEPING  # the state change guards against a second finish
    if ready:
        await _queue_finish(svc, run_id)


async def _queue_finish(svc: WorkerServices, run_id: uuid.UUID) -> None:
    """Queues finish_run. Re-delivered jobs call it again when the run is already sweeping, in
    case a crash lost the first one; the queueing lock drops a duplicate and finish_run is
    idempotent."""
    await svc.queue.defer(TASK_FINISH_RUN, lock=f"{run_id}:finish", run_id=str(run_id))


# ---------------------------------------------------------------- sweep and final state


MAX_SWEEP_PASSES = 3  # the first sweep and at most two more for a guide that changed meanwhile


@_job
async def finish_run(svc: WorkerServices, run_id: uuid.UUID) -> None:
    # a rule that changed while the file was swept is swept in with another pass, up to a cap
    for passes in range(1, MAX_SWEEP_PASSES + 1):
        if not await _sweep_and_finish(svc, run_id, passes):
            break


async def _sweep_guide(s: AsyncSession, run: Run) -> Guide:
    """The guide the sweep enforces: the run's own merged onto the set guide as it is now, so a
    rule a person added, changed or unlocked while this file translated applies to it too; the
    run keeps its own summary and style guide (CGL-23)."""
    own = await runs.load_guide(s, run.guide_version_id)
    doc = await runs.get_document(s, run.document_id)
    current = await runs.prior_guide(s, doc.project_id, run.target_lang)
    return own if current is None else carry_over(Guide.model_validate(current.content), own)


def _rules(guide: Guide) -> tuple[list[GlossaryEntry], list[Entity]]:
    """What the sweep enforces: the locked entries, sorted by key so their order does not matter.
    A list, not a dict by key, so a change to either of two entries with one key is still seen."""
    terms = [g for g in guide.glossary if g.locked]
    names = [e for e in guide.entities if e.locked]
    return (
        sorted(terms, key=lambda g: (entry_key(g.term), g.model_dump_json())),
        sorted(names, key=lambda e: (entry_key(e.name), e.model_dump_json())),
    )


async def _sweep_and_finish(svc: WorkerServices, run_id: uuid.UUID, passes: int = 1) -> bool:
    """Sweeps the run and completes it. True when a rule changed during the sweep's model calls:
    the sweep stage is cleared for another pass (up to MAX_SWEEP_PASSES in all; after that the run
    finishes with a note), since the recheck a person starts after adding a
    rule refuses a file that is not complete (CGL-28). The check is made under the run lock that
    completes the run, so a rule saved before a refused recheck is always seen."""
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        if _cancel_if_requested(run) or run.state != RunState.SWEEPING:
            return False
        swept = await runs.get_stage(s, run_id, "sweep")
        needed = swept is None or swept.status != runs.COMPLETE
        if needed:
            profile = DocumentProfile.model_validate(run.profile)
            snapshot = CoglossaConfig.model_validate(run.config_snapshot)
            enabled = snapshot.profiles.sweep.get(profile.pipeline_profile, True)
            await runs.begin_stage(s, run_id, "sweep")
            target_lang = run.target_lang
            if enabled:
                stored = await outputs.load_spans(s, run_id)
                _, segments = await runs.load_source(s, run.document_id)
                guide = await _sweep_guide(s, run)
    if needed:
        calls = svc.call_log()
        changes: list[SpanChange] = []
        if enabled:
            errors: list[RunError]
            try:
                result = await run_sweep(
                    svc.context(calls, target_lang), profile, guide, stored.spans, segments
                )
                changes, errors = result.changes, result.errors
            except DB_ERRORS:
                raise
            except Exception as e:
                log.exception("sweep failed", extra={"run_id": str(run_id), "stage": "sweep"})
                errors = [RunError(stage="sweep", message=_internal(e))]
            output: dict[str, Any] = {
                "errors": [e.model_dump() for e in errors],
                "revised": len(changes),
            }
        else:
            output = {"errors": [], "skipped": True}
        try:
            row_ids = stored.row_ids if changes else {}
            await _save_sweep(svc, run_id, calls.records, output, changes, row_ids)
        except DB_ERRORS:
            raise
        except Exception as e:
            # the save rolled back: the run keeps its unswept text and ends with a sweep error
            log.exception("saving the sweep failed", extra={"run_id": str(run_id)})
            message = _internal(e)

            async def record(full: bool) -> None:
                records = calls.records if full else []
                await _save_sweep(svc, run_id, records, None, [], {}, error=message)

            await _record_failure(run_id, record)
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        if _cancel_if_requested(run) or run.state != RunState.SWEEPING:
            return False
        if needed and enabled and _rules(await _sweep_guide(s, run)) != _rules(guide):
            if passes < MAX_SWEEP_PASSES:
                await runs.delete_stages(s, run_id, stages=["sweep"])
                return True
            await _note_sweep_cap(s, run_id)
        problems = await runs.total_error_count(s, run_id)
        run.state = RunState.COMPLETE_WITH_ERRORS if problems else RunState.COMPLETE
        run.finished_at = utcnow()
    log.info(
        "run finished after %d sweep pass%s",
        passes,
        "" if passes == 1 else "es",
        extra={"run_id": str(run_id), "state": str(run.state), "sweep_passes": passes},
    )
    return False


async def _note_sweep_cap(s: AsyncSession, run_id: uuid.UUID) -> None:
    """Records on the sweep stage that the last guide change was not applied. A sweep that could
    not be saved already ends the run with an error a retry re-sweeps with the latest guide."""
    row = await runs.get_stage(s, run_id, "sweep")
    if row is None or row.status != runs.COMPLETE:
        return
    output = dict(row.output or {})
    note = RunError(stage="sweep", role="sweep", message=GUIDE_CHANGED_AGAIN)
    output["errors"] = [*(output.get("errors") or []), note.model_dump()]
    row.output = output
    row.error_count = runs.count_errors(output)


async def _save_sweep(
    svc: WorkerServices,
    run_id: uuid.UUID,
    records: list[LLMCallRecord],
    output: dict[str, Any] | None,
    changes: list[SpanChange],
    row_ids: dict[str, uuid.UUID],
    *,
    error: str | None = None,
) -> None:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        await runs.save_llm_calls(s, run_id, records, stage="sweep")
        row = await runs.get_stage(s, run_id, "sweep")
        # a duplicate finish job that already saved the sweep must not apply it twice
        if row is None or row.status == runs.COMPLETE or run.state != RunState.SWEEPING:
            return
        if error is not None:
            runs.fail_stage(row, error)
            return
        if changes:
            await outputs.apply_sweep(s, row_ids, changes)
        runs.finish_stage(row, output)


async def run_job(svc: WorkerServices, task: str, kwargs: dict[str, str]) -> None:
    run_id = uuid.UUID(kwargs["run_id"])
    if task == TASK_START_RUN:
        await start_run(svc, run_id)
    elif task == TASK_TRANSLATE_RUN:
        await translate_run(svc, run_id)
    elif task == TASK_PROCESS_CHUNK:
        await process_chunk_job(svc, run_id, kwargs["chunk_id"])
    elif task == TASK_FINISH_RUN:
        await finish_run(svc, run_id)
    else:
        raise ValueError(f"unknown job '{task}'")
