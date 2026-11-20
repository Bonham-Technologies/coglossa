"""Control plane (spec §7.1): submit runs, approve guides, cancel, retry, report progress and
build reports. The CLI uses it now and the REST API will in M4. It never calls a model: work
goes to the worker through a JobQueue."""

import asyncio
import contextlib
import dataclasses
import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import coglossa
from coglossa.config import CoglossaConfig, ProfilesConfig, config_hash
from coglossa.ingest.base import IngestError
from coglossa.ingest.registry import check_extension, check_supported
from coglossa.llm.agent import CallLog, usage_of
from coglossa.prompts.check import run_prompt_ids
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import (
    TERMINAL_STATES,
    ConfigSnapshot,
    DocumentInfo,
    DocumentProfile,
    Guide,
    PipelineProfile,
    Reliability,
    Report,
    RunError,
    RunProgress,
    RunState,
    RunStatus,
    ScorerHealth,
)
from coglossa.scoring.aggregate import any_scored, document_notices
from coglossa.stages.sweep import resolve_proposals
from coglossa.storage import Storage, StorageError, display_name, safe_filename
from coglossa.store import outputs, runs
from coglossa.store.db import Database
from coglossa.store.models import Document, GuideVersion, Project, Run, StageResult, utcnow
from coglossa.text import clean_guide

log = logging.getLogger("coglossa.service")

TASK_START_RUN = "coglossa.start_run"
TASK_TRANSLATE_RUN = "coglossa.translate_run"
TASK_PROCESS_CHUNK = "coglossa.process_chunk"
TASK_FINISH_RUN = "coglossa.finish_run"
_LOCK_SUFFIX = {TASK_START_RUN: "start", TASK_TRANSLATE_RUN: "translate", TASK_FINISH_RUN: "finish"}


class JobQueue(Protocol):
    async def defer(self, task: str, *, lock: str | None = None, **kwargs: str) -> None: ...


class ConflictError(Exception):
    """The request conflicts with the current data; the message says why (HTTP 409). `code`
    names the conflicts a client handles specially; the API sends it beside the message."""

    code: str | None = None


class InvalidRunStateError(ConflictError):
    """The run is not in a state that allows the action; the message says why."""


class GuideChangedError(ConflictError):
    """The project guide changed after the edit or the draft was based on it: reload, redo."""

    code = "guide_changed"


class LanguageExistsError(ConflictError):
    """The set is already translated into that language (CGL-24)."""

    code = "language_exists"


class PassageChangedError(ConflictError):
    """A review action started from a passage text that someone else has changed since
    (CGL-36): `current_text` is what the passage says now."""

    code = "passage_changed"

    def __init__(self, current_text: str) -> None:
        super().__init__(
            "someone else changed this passage after you opened it; "
            "compare their text with yours and save the one to keep"
        )
        self.current_text = current_text


class InvalidRequestError(ValueError):
    """The request itself is invalid; the message says why (HTTP 422)."""


def _sweep_setting(run: Run) -> bool | None:
    """Whether the run's snapshot (profiles.sweep) runs the sweep for its pipeline profile;
    False before profiling; None when the snapshot or the stored profile cannot be read
    (a run from an older version whose profiles section changed; CGL-54)."""
    if not run.profile:
        return False
    try:
        profiles = ProfilesConfig.model_validate((run.config_snapshot or {}).get("profiles") or {})
        profile = DocumentProfile.model_validate(run.profile).pipeline_profile
    except ValidationError:
        return None
    return profiles.sweep.get(profile, True)


def sweep_enabled(run: Run) -> bool:
    """Spec §8.3: whether the config the run was started with (its snapshot's profiles.sweep)
    runs the sweep for the run's pipeline profile. False before profiling, and for a snapshot
    this version cannot read."""
    return _sweep_setting(run) is True


@dataclass(frozen=True)
class RunOptions:
    target_lang: str
    source_lang: str | None = None
    pipeline_profile: PipelineProfile | None = None
    checkpoint: bool | None = None
    use_project_guide: bool = False


@dataclass(frozen=True)
class SubmitOptions(RunOptions):
    project: str = "default"  # documents in a project share one guide per language


async def _queue_run_job(queue: JobQueue, task: str, run_id: uuid.UUID) -> None:
    await queue.defer(task, lock=f"{run_id}:{_LOCK_SUFFIX[task]}", run_id=str(run_id))


_MAX_FILENAME = 255


async def _new_run(
    s: AsyncSession,
    document_id: uuid.UUID,
    cfg: CoglossaConfig,
    prompts: PromptLibrary,
    opts: RunOptions,
) -> uuid.UUID:
    run = await runs.create_run(
        s,
        document_id,
        target_lang=opts.target_lang,
        source_lang=opts.source_lang,
        pipeline_profile=opts.pipeline_profile.value if opts.pipeline_profile else None,
        checkpoint=opts.checkpoint,
        use_project_guide=opts.use_project_guide,
        config_hash=config_hash(cfg),
        config_snapshot=cfg.model_dump(mode="json"),
        prompt_versions=prompts.versions(run_prompt_ids(cfg)),
    )
    return run.id


async def _in_set_spelling(s: AsyncSession, project: Project, opts: RunOptions) -> RunOptions:
    """`opts` with its language spelled as the set already spells it (CGL-24)."""
    lang = await runs.set_spelling(s, project, opts.target_lang)
    return dataclasses.replace(opts, target_lang=lang)


async def submit_bytes(
    db: Database,
    storage: Storage,
    queue: JobQueue,
    cfg: CoglossaConfig,
    prompts: PromptLibrary,
    filename: str,
    data: bytes,
    opts: RunOptions,
    *,
    project: str | uuid.UUID,
    all_languages: bool = False,
) -> uuid.UUID:
    """Store an uploaded document and queue a run for it. `project` is a name (created if
    missing, as the CLI does) or the id of an existing project. `all_languages`: also a run in
    every other language of the set (CGL-24), decided under the project lock so a language
    being added meanwhile is not missed; the run returned is the one in `opts.target_lang`."""
    name = display_name(filename)
    if not name:
        raise InvalidRequestError("the upload has no file name")
    if len(name) > _MAX_FILENAME:
        raise InvalidRequestError(f"the file name is longer than {_MAX_FILENAME} characters")
    check_extension(name)
    if not data:
        raise IngestError(f"{name} is empty")
    document_id = uuid.uuid4()
    stored_dir: str | None = None
    try:
        async with db.session() as s, s.begin():
            if isinstance(project, uuid.UUID):
                row = await runs.get_project(s, project)
            else:
                row = await runs.get_or_create_project(s, project)
            stored_dir = f"projects/{row.id}/documents/{document_id}/"
            key = stored_dir + safe_filename(name)
            # storage is blocking I/O (a bucket upload): off the event loop
            await asyncio.to_thread(storage.put, key, data)
            # after the file is stored: the lock orders this upload with a language being added
            row = await runs.lock_project(s, row.id)
            opts = await _in_set_spelling(s, row, opts)
            wanted = runs.language_key(opts.target_lang)
            also = (
                [
                    lang
                    for lang in await runs.set_languages(s, row)
                    if runs.language_key(lang) != wanted
                ]
                if all_languages
                else []
            )
            doc = await runs.add_document(
                s,
                row.id,
                document_id=document_id,
                filename=name,
                format=Path(name).suffix.lower().lstrip("."),
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
            )
            run_id = await _new_run(s, doc.id, cfg, prompts, opts)
            others = [
                await _new_run(s, doc.id, cfg, prompts, dataclasses.replace(opts, target_lang=lang))
                for lang in also
            ]
    except BaseException:
        if stored_dir is not None:  # the transaction rolled back: nothing points at the file
            with contextlib.suppress(StorageError):
                await asyncio.to_thread(storage.delete_prefix, stored_dir)
        raise
    for queued in (run_id, *others):
        await _queue_run_job(queue, TASK_START_RUN, queued)
    return run_id


async def submit_run(
    db: Database,
    storage: Storage,
    queue: JobQueue,
    cfg: CoglossaConfig,
    prompts: PromptLibrary,
    path: Path,
    opts: SubmitOptions,
) -> uuid.UUID:
    check_supported(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise IngestError(f"{path.name} cannot be read: {e.strerror or e}") from e
    return await submit_bytes(
        db, storage, queue, cfg, prompts, path.name, data, opts, project=opts.project
    )


async def submit_existing(
    db: Database,
    queue: JobQueue,
    cfg: CoglossaConfig,
    prompts: PromptLibrary,
    document_id: uuid.UUID,
    opts: RunOptions,
) -> uuid.UUID:
    """A new run of a document already uploaded (spec §5: re-running creates a new run)."""
    async with db.session() as s, s.begin():
        doc = await runs.get_document(s, document_id)
        project = await runs.lock_project(s, doc.project_id)
        opts = await _in_set_spelling(s, project, opts)
        run_id = await _new_run(s, doc.id, cfg, prompts, opts)
    await _queue_run_job(queue, TASK_START_RUN, run_id)
    return run_id


async def add_language(
    db: Database,
    queue: JobQueue,
    cfg: CoglossaConfig,
    prompts: PromptLibrary,
    project_id: uuid.UUID,
    opts: RunOptions,
) -> list[uuid.UUID]:
    """Every file of a set in another language, with nothing uploaded again (CGL-24). One
    transaction under the project lock: every file gets a run or none does. The runs build the
    new language's own guide and never pause for it."""
    opts = dataclasses.replace(opts, checkpoint=False, use_project_guide=False)
    async with db.session() as s, s.begin():
        project = await runs.lock_project(s, project_id)
        wanted = runs.language_key(opts.target_lang)
        if any(runs.language_key(lang) == wanted for lang in await runs.set_languages(s, project)):
            raise LanguageExistsError(
                f"project '{project.name}' is already translated into {opts.target_lang}"
            )
        documents = list(
            await s.scalars(
                select(Document.id)
                .where(Document.project_id == project_id)
                .order_by(Document.created_at, Document.id)
            )
        )
        if not documents:
            raise InvalidRequestError(f"project '{project.name}' has no files to translate")
        run_ids = [await _new_run(s, doc_id, cfg, prompts, opts) for doc_id in documents]
    for run_id in run_ids:
        await _queue_run_job(queue, TASK_START_RUN, run_id)
    return run_ids


async def run_guide_row(s: AsyncSession, run_id: uuid.UUID) -> GuideVersion:
    """The guide version a run uses; a run that has not produced one yet is refused."""
    run = await runs.get_run(s, run_id)
    if run.guide_version_id is None:
        raise InvalidRunStateError(f"the guide is not ready yet; run {run_id} is {run.state}")
    return await runs.get_guide_version(s, run.guide_version_id)


async def run_guide(db: Database, run_id: uuid.UUID) -> Guide:
    async with db.session() as s:
        return Guide.model_validate((await run_guide_row(s, run_id)).content)


async def approve_guide(
    db: Database,
    queue: JobQueue,
    run_id: uuid.UUID,
    guide: Guide | None = None,
    *,
    base_version: int | None = None,
) -> int:
    """Approve the guide of a run waiting at the checkpoint: `guide` (an edit) or the draft.
    `base_version` is the version the edit started from: the run's draft (the default), or the
    project guide the editor loaded. A version approved or saved by a person after the draft's
    base refuses the approval, as a stale save is refused. Another run's unapproved draft does
    not: while nothing is approved it is the project guide, and approving this run's guide
    supersedes it. Any other `base_version` is refused. The draft as generated is always based
    on itself, so `base_version` needs a `guide`."""
    if guide is None and base_version is not None:
        raise InvalidRequestError(
            "base_version is only used with an edited guide; send the guide you approve"
        )
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        if run.state != RunState.AWAITING_GUIDE_APPROVAL or run.guide_version_id is None:
            raise InvalidRunStateError(
                f"run {run_id} is {run.state}; only a run awaiting guide approval can be approved"
            )
        draft = await runs.get_guide_version(s, run.guide_version_id)
        generated = Guide.model_validate(draft.content)
        content = clean_guide(guide) if guide is not None else generated
        doc = await runs.get_document(s, run.document_id)
        await runs.lock_project(s, doc.project_id)  # lock order: run, then project
        project = await runs.prior_guide(s, doc.project_id, run.target_lang)
        current = project.version if project is not None else None
        if base_version is None or base_version == draft.version:
            base = draft.built_from_version  # the project guide the draft was built from
        elif current is not None and base_version <= current:
            base = base_version  # the project guide the editor loaded: current, or stale
        else:
            raise InvalidRequestError(
                f"base_version {base_version} is neither this run's draft (version "
                f"{draft.version}) nor the project guide (version {current or 'none'})"
            )
        approved = await runs.latest_guide(s, doc.project_id, run.target_lang, approved_only=True)
        if approved is not None and approved.version > (base or 0):
            raise GuideChangedError(
                f"the project guide changed since this draft (now version {approved.version}); "
                "reload it and approve the current guide"
            )
        version = await runs.add_guide_version(
            s,
            doc.project_id,
            content,
            target_lang=run.target_lang,
            status=runs.APPROVED,
            author=runs.ENGINE if content == generated else runs.USER,
            source_run_id=run.id,
        )
        run.guide_version_id = version.id
        run.state = RunState.TRANSLATING
        number = version.version
    await _queue_run_job(queue, TASK_TRANSLATE_RUN, run_id)
    return number


async def cancel_run(db: Database, run_id: uuid.UUID) -> RunState:
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        state = RunState(run.state)
        if state in TERMINAL_STATES:
            raise InvalidRunStateError(f"run {run_id} is already {state}")
        if run.finished_at is not None:  # a re-sweep or chunk retry: cancelling would lose it
            raise InvalidRunStateError(
                f"run {run_id} is reworking a finished translation and cannot be cancelled; it "
                "finishes on its own"
            )
        run.cancel_requested = True
        if state in (RunState.CREATED, RunState.AWAITING_GUIDE_APPROVAL):
            run.state = RunState.CANCELLED  # nothing is running for it
            run.finished_at = utcnow()
        return RunState(run.state)


async def retry_run(db: Database, queue: JobQueue, run_id: uuid.UUID) -> str:
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        state = RunState(run.state)
        if state is RunState.COMPLETE:
            raise InvalidRunStateError(f"run {run_id} completed without errors; nothing to retry")
        if state is RunState.CANCELLED:
            raise InvalidRunStateError(f"run {run_id} was cancelled; submit a new run")
        if state is RunState.AWAITING_GUIDE_APPROVAL:
            raise InvalidRunStateError(
                f"run {run_id} is waiting for guide approval; approve the guide to continue"
            )
        if state is RunState.FAILED:
            await runs.delete_stages(s, run_id, status=runs.FAILED)
            run.state, run.error, run.finished_at = RunState.CREATED, None, None
            task, message = TASK_START_RUN, "restarting from the failed stage"
        elif state is RunState.COMPLETE_WITH_ERRORS:
            failed, sweep_error = await _retry_scope(s, run_id)
            if not failed and not sweep_error:
                stages = sorted(
                    {r.stage for r in await runs.stage_rows(s, run_id) if r.error_count}
                )
                raise InvalidRunStateError(
                    f"run {run_id} has no failed chunks or sweep errors; its errors "
                    f"({', '.join(stages)}) come from earlier stages, which a retry does not "
                    "re-run: submit the document again for a new run"
                )
            if failed:
                await outputs.delete_chunk_outputs(s, run_id, failed)
                await runs.delete_stages(s, run_id, chunk_ids=failed)
                await outputs.undo_sweep(s, run_id)
                await runs.delete_stages(s, run_id, stages=["sweep"])
                run.state = RunState.TRANSLATING  # finished_at marks the rework (see cancel_run)
                task, message = TASK_TRANSLATE_RUN, f"retrying {len(failed)} chunk(s)"
            else:  # a re-sweep: earlier revisions stay, finished_at marks it (see cancel_run)
                await runs.delete_stages(s, run_id, stages=["sweep"])
                run.state = RunState.SWEEPING
                task, message = TASK_FINISH_RUN, "re-running the sweep"
        elif state is RunState.TRANSLATING:
            task, message = TASK_TRANSLATE_RUN, "resumed"
        elif state is RunState.SWEEPING:
            task, message = TASK_FINISH_RUN, "resumed"
        else:  # created, ingesting, profiling, building_guide
            task, message = TASK_START_RUN, "resumed"
        run.cancel_requested = False  # a retry is a later, explicit intent
    await _queue_run_job(queue, task, run_id)
    return message


async def _retry_scope(s: AsyncSession, run_id: uuid.UUID) -> tuple[list[str], bool]:
    """What a retry of a complete_with_errors run can redo: the chunks with errors, and the
    sweep when it had errors. Errors of earlier run-level stages are out of its reach."""
    failed = await runs.chunks_with_errors(s, run_id)
    sweep = await runs.get_stage(s, run_id, "sweep")
    return failed, sweep is not None and sweep.error_count > 0


async def run_progress(db: Database, run_id: uuid.UUID) -> RunProgress:
    async with db.session() as s:
        # Only the columns progress shows: streams poll this every second (CGL-101).
        row = (await s.execute(select(Run.state, Run.error).where(Run.id == run_id))).one_or_none()
        if row is None:
            raise runs.NotFoundError(f"run {run_id} not found")
        state = RunState(row.state)
        total = await runs.chunk_count(s, run_id)
        done = await runs.chunk_progress(s, run_id)
        done["score"] = await runs.finished_chunk_count(s, run_id)  # failed chunks are done too
        failed, sweep_error = await _retry_scope(s, run_id)
    retryable = state is RunState.FAILED or (
        state is RunState.COMPLETE_WITH_ERRORS and (bool(failed) or sweep_error)
    )
    return RunProgress(
        run_id=str(run_id),
        state=state,
        chunks_total=total,
        chunks_done={stage: done.get(stage, 0) for stage in ("translate", "reconcile", "score")},
        chunks_with_errors=len(failed),
        error=row.error,
        retryable=retryable,
    )


def stage_errors(rows: list[StageResult]) -> list[RunError]:
    """Every error a run's stages recorded: a failed stage's own, and the notes of the rest."""
    errors: list[RunError] = []
    for row in rows:
        if row.status == runs.FAILED:
            errors.append(
                RunError(stage=row.stage, chunk_id=row.chunk_id or None, message=row.error or "")
            )
        elif row.output:
            errors.extend(RunError.model_validate(e) for e in row.output.get("errors") or [])
    return errors


def ingest_notices(rows: list[StageResult]) -> list[str]:
    """What the run's ingest found in the file and could not read (CGL-87)."""
    for row in rows:
        if row.stage == "ingest" and row.status == runs.COMPLETE and row.output:
            return [str(n) for n in row.output.get("notices") or []]
    return []


async def build_report(
    db: Database, run_id: uuid.UUID, *, include_llm_calls: bool = True
) -> Report:
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        state = RunState(run.state)
        if state not in (RunState.COMPLETE, RunState.COMPLETE_WITH_ERRORS):
            raise InvalidRunStateError(
                f"run {run_id} is {state}; a report is available once it is complete"
            )
        doc = await runs.get_document(s, run.document_id)
        blocks, segments = await runs.load_source(s, doc.id)
        chunks = await runs.load_chunks(s, run_id)
        guide = await runs.load_guide(s, run.guide_version_id)
        stored = await outputs.load_spans(s, run_id)
        candidates = await outputs.load_candidates(s, run_id)
        revisions = await outputs.load_revisions(
            s, run_id, {row: sid for sid, row in stored.row_ids.items()}
        )
        if include_llm_calls:
            llm_calls = await runs.load_llm_calls(s, run_id)
            usage = CallLog(records=llm_calls).usage()
        else:
            llm_calls = []
            usage = usage_of(await runs.load_llm_usage(s, run_id))
        rows = await runs.stage_rows(s, run_id)
        errors = stage_errors(rows)
    profile = DocumentProfile.model_validate(run.profile)
    tier = Reliability(run.language_tier)
    snapshot = CoglossaConfig.model_validate(run.config_snapshot)
    return Report(
        run_id=str(run.id),
        coglossa_version=coglossa.__version__,
        created_at=run.finished_at or run.created_at,
        status=RunStatus(state.value),
        document=DocumentInfo(source_path=doc.filename, format=doc.format, sha256=doc.sha256),
        profile=profile,
        guide=guide,
        guide_proposals=resolve_proposals(guide, stored.proposed_terms),
        blocks=blocks,
        segments=segments,
        chunks=chunks,
        candidates=candidates,
        spans=stored.spans,
        revisions=revisions,
        language_tier=tier,
        notices=document_notices(
            tier,
            profile.source_lang,
            profile.target_lang,
            scored=any_scored(stored.spans),
            unread=ingest_notices(rows),
        ),
        errors=errors,
        usage=usage,
        config=ConfigSnapshot(
            hash=run.config_hash,
            roles=dict(snapshot.roles.all_roles()),
            prompt_versions=dict(run.prompt_versions),
            scorer=ScorerHealth.model_validate(run.scorer_health) if run.scorer_health else None,
        ),
        llm_calls=llm_calls,
    )


async def _delete(
    db: Database, storage: Storage, find: Callable[[AsyncSession], Awaitable[Project]]
) -> None:
    """Spec §5: deleting a project removes every row, stored file and model-call payload."""
    async with db.session() as s, s.begin():
        project = await find(s)
        await runs.delete_project(s, project.id)
    # The set is gone once the rows are, so a failed file clean-up does not fail the delete
    # (CGL-84): it is logged, and `coglossa doctor` lists the folder under "leftover files".
    prefix = f"projects/{project.id}/"
    try:
        await asyncio.to_thread(storage.delete_prefix, prefix)
    except StorageError as e:
        log.error("set %s was deleted but its files under %s were not: %s", project.id, prefix, e)


async def delete_project(db: Database, storage: Storage, name: str) -> None:
    await _delete(db, storage, lambda s: runs.find_project(s, name))


async def delete_project_id(db: Database, storage: Storage, project_id: uuid.UUID) -> None:
    await _delete(db, storage, lambda s: runs.get_project(s, project_id))


async def rerun_sweep(db: Database, queue: JobQueue, run_id: uuid.UUID) -> str:
    """Spec §8.3: after a locked guide entry changes, re-run the consistency sweep of a
    finished run. The run takes the latest approved version as its own guide, and the sweep
    enforces the project guide's locked entries (runs.prior_guide, which may be a newer draft of
    a file translating alongside this one; CGL-23, CGL-28), so the message names that version.
    Earlier revisions stay: the new sweep works on the current text, reviewers' edits
    included."""
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        state = RunState(run.state)
        if state not in (RunState.COMPLETE, RunState.COMPLETE_WITH_ERRORS):
            raise InvalidRunStateError(
                f"run {run_id} is {state}; the sweep can be re-run once the run is complete"
            )
        setting = _sweep_setting(run)
        if setting is None:
            raise InvalidRunStateError(
                "the settings this file was translated with can't be read, so it can't be rechecked"
            )
        if not setting:
            profile = DocumentProfile.model_validate(run.profile).pipeline_profile
            raise InvalidRunStateError(
                f"the sweep is off for {profile} documents (profiles.sweep in the config)"
            )
        doc = await runs.get_document(s, run.document_id)
        approved = await runs.latest_guide(s, doc.project_id, run.target_lang, approved_only=True)
        if approved is not None:
            run.guide_version_id = approved.id
        current = await runs.prior_guide(s, doc.project_id, run.target_lang)
        version = (current or await runs.get_guide_version(s, run.guide_version_id)).version
        await runs.delete_stages(s, run_id, stages=["sweep"])
        run.state, run.cancel_requested = RunState.SWEEPING, False  # finished_at marks a re-sweep
    await _queue_run_job(queue, TASK_FINISH_RUN, run_id)
    return f"re-running the sweep with the project guide, version {version}"
