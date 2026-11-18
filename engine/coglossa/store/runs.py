"""Data access for projects, documents, guide versions, runs, chunks, stage results and model
calls. Functions take an AsyncSession and never commit: the caller owns the transaction
(`async with db.session() as s, s.begin(): ...`)."""

import uuid
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from sqlalchemy import Table, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from coglossa.schema import (
    Block,
    BlockKind,
    Chunk,
    Guide,
    LLMCallRecord,
    Provenance,
    RunState,
    Segment,
)
from coglossa.store.models import (
    BlockRow,
    ChunkRow,
    Document,
    GuideVersion,
    LLMCall,
    Project,
    Run,
    SegmentRow,
    StageResult,
    utcnow,
)

RUNNING, COMPLETE, FAILED = "running", "complete", "failed"
DRAFT, APPROVED = "draft", "approved"
ENGINE, USER = "engine", "user"
_BATCH = 1000


class NotFoundError(LookupError):
    """A requested row does not exist; the message is safe to show to a user."""


async def get_or_create_project(s: AsyncSession, name: str) -> Project:
    await s.execute(
        pg_insert(Project)
        .values(id=uuid.uuid4(), name=name, created_at=utcnow())
        .on_conflict_do_nothing(index_elements=[Project.name])
    )
    return (await s.execute(select(Project).where(Project.name == name))).scalar_one()


async def find_project(s: AsyncSession, name: str) -> Project:
    project = await s.scalar(select(Project).where(Project.name == name))
    if project is None:
        raise NotFoundError(f"project '{name}' not found")
    return project


async def get_project(s: AsyncSession, project_id: uuid.UUID) -> Project:
    project = await s.get(Project, project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    return project


async def lock_project(s: AsyncSession, project_id: uuid.UUID) -> Project:
    """The project row, locked until the transaction ends (guide saves are serialized)."""
    project = await s.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    return project


async def add_document(
    s: AsyncSession,
    project_id: uuid.UUID,
    *,
    document_id: uuid.UUID,
    filename: str,
    format: str,
    sha256: str,
    storage_key: str,
) -> Document:
    doc = Document(
        id=document_id,
        project_id=project_id,
        filename=filename,
        format=format,
        sha256=sha256,
        storage_key=storage_key,
    )
    s.add(doc)
    await s.flush()
    return doc


async def get_document(s: AsyncSession, document_id: uuid.UUID) -> Document:
    doc = await s.get(Document, document_id)
    if doc is None:
        raise NotFoundError(f"document {document_id} not found")
    return doc


async def _insert_many(s: AsyncSession, table: Table, rows: Sequence[dict[str, Any]]) -> None:
    for i in range(0, len(rows), _BATCH):
        await s.execute(
            pg_insert(table).values(list(rows[i : i + _BATCH])).on_conflict_do_nothing()
        )


async def save_source(
    s: AsyncSession, document_id: uuid.UUID, blocks: list[Block], segments: list[Segment]
) -> None:
    """Blocks and segments are immutable after ingest (spec §5): a second save is a no-op."""
    await _insert_many(
        s,
        BlockRow.__table__,
        [
            {
                "document_id": document_id,
                "ordinal": k,
                "block_id": b.id,
                "kind": b.kind.value,
                "text": b.text,
                "provenance": b.provenance.model_dump(mode="json"),
            }
            for k, b in enumerate(blocks)
        ],
    )
    await _insert_many(
        s,
        SegmentRow.__table__,
        [
            {
                "document_id": document_id,
                "ordinal": k,
                "segment_id": seg.id,
                "block_id": seg.block_id,
                "char_start": seg.start,
                "char_end": seg.end,
                "text": seg.text,
            }
            for k, seg in enumerate(segments)
        ],
    )
    await s.execute(
        update(Document)
        .where(Document.id == document_id, Document.ingested_at.is_(None))
        .values(ingested_at=utcnow())
    )


async def load_source(s: AsyncSession, document_id: uuid.UUID) -> tuple[list[Block], list[Segment]]:
    block_rows = await s.scalars(
        select(BlockRow).where(BlockRow.document_id == document_id).order_by(BlockRow.ordinal)
    )
    segment_rows = await s.scalars(
        select(SegmentRow).where(SegmentRow.document_id == document_id).order_by(SegmentRow.ordinal)
    )
    blocks = [
        Block(
            id=r.block_id,
            kind=BlockKind(r.kind),
            text=r.text,
            provenance=Provenance.model_validate(r.provenance),
        )
        for r in block_rows
    ]
    segments = [
        Segment(id=r.segment_id, block_id=r.block_id, start=r.start, end=r.end, text=r.text)
        for r in segment_rows
    ]
    return blocks, segments


async def first_ingest_notices(s: AsyncSession, document_id: uuid.UUID) -> list[str]:
    """The notices the document's first finished ingest recorded (CGL-87): what its file holds
    that could not be read. Later runs reuse the stored blocks without reading the file, so they
    repeat these."""
    output = await s.scalar(
        select(StageResult.output)
        .join(Run, Run.id == StageResult.run_id)
        .where(
            Run.document_id == document_id,
            StageResult.stage == "ingest",
            StageResult.status == COMPLETE,
        )
        .order_by(StageResult.id)
        .limit(1)
    )
    return [str(n) for n in (output or {}).get("notices") or []]


def language_key(code: str) -> str:
    """Language codes compare case-insensitively, with - and _ alike (BCP 47)."""
    return code.casefold().replace("_", "-")


def ordered_languages(default: str | None, used: Iterable[str]) -> list[str]:
    """A set's languages (CGL-24): its default first, then `used` in order, one spelling per
    language."""
    out: list[str] = []
    seen: set[str] = set()
    for code in [*([default] if default else []), *used]:
        if language_key(code) not in seen:
            seen.add(language_key(code))
            out.append(code)
    return out


async def set_languages(s: AsyncSession, project: Project) -> list[str]:
    """The languages a project is translated into: its default, then every other language of
    its runs in the order each language's first run was created."""
    first = func.min(Run.created_at)
    rows = await s.execute(
        select(Run.target_lang, first)
        .join(Document, Run.document_id == Document.id)
        .where(Document.project_id == project.id)
        .group_by(Run.target_lang)
        .order_by(first, Run.target_lang)
    )
    return ordered_languages(project.target_lang, (lang for lang, _ in rows))


async def set_spelling(s: AsyncSession, project: Project, code: str) -> str:
    """The set's own spelling of `code`'s language, or `code` for a language new to the set:
    one spelling per language, so all its runs share one guide (CGL-24)."""
    key = language_key(code)
    languages = await set_languages(s, project)
    return next((lang for lang in languages if language_key(lang) == key), code)


async def latest_guide(
    s: AsyncSession, project_id: uuid.UUID, target_lang: str, *, approved_only: bool
) -> GuideVersion | None:
    query = select(GuideVersion).where(
        GuideVersion.project_id == project_id, GuideVersion.target_lang == target_lang
    )
    if approved_only:
        query = query.where(GuideVersion.status == APPROVED)
    return await s.scalar(query.order_by(GuideVersion.version.desc()).limit(1))


FINISHED_STATES = (RunState.COMPLETE.value, RunState.COMPLETE_WITH_ERRORS.value)


# A draft is not the project guide while its run waits for a person's approval, nor once the
# run failed or was cancelled.
UNUSABLE_DRAFT_STATES = (
    RunState.FAILED.value,
    RunState.CANCELLED.value,
    RunState.AWAITING_GUIDE_APPROVAL.value,
)


def usable_guide_version() -> ColumnElement[bool]:
    """Which versions can be the project guide: approved ones, and engine drafts of runs that
    use them. The query must outer-join Run on GuideVersion.source_run_id."""
    return or_(GuideVersion.status == APPROVED, Run.state.not_in(UNUSABLE_DRAFT_STATES))


async def prior_guide(
    s: AsyncSession, project_id: uuid.UUID, target_lang: str
) -> GuideVersion | None:
    """The project guide: what the guide editor shows, what saves and checkpoint approvals are
    measured against, what a new run of the project builds on (or reuses as-is, with
    use_project_guide; CGL-28) and what a run's draft is merged onto when it is saved
    (documents in one project share a guide, spec §5; CGL-23). It is the newest version that is
    approved, or an engine draft of a run that has built it and has not failed, been cancelled
    or stopped for approval. A run still translating counts, so files translated together share
    their names. Every saved draft keeps all of the project guide's entries
    (stages.guide.carry_over), so the newest is also the most complete. Nothing here locks an
    entry: locks come only from people. Each target language has its own guide (CGL-24)."""
    return await s.scalar(
        select(GuideVersion)
        .outerjoin(Run, Run.id == GuideVersion.source_run_id)
        .where(
            GuideVersion.project_id == project_id,
            GuideVersion.target_lang == target_lang,
            usable_guide_version(),
        )
        .order_by(GuideVersion.version.desc())
        .limit(1)
    )


async def get_guide_version(s: AsyncSession, version_id: uuid.UUID) -> GuideVersion:
    row = await s.get(GuideVersion, version_id)
    if row is None:
        raise NotFoundError(f"guide version {version_id} not found")
    return row


async def load_guide(s: AsyncSession, version_id: uuid.UUID) -> Guide:
    return Guide.model_validate((await get_guide_version(s, version_id)).content)


async def add_guide_version(
    s: AsyncSession,
    project_id: uuid.UUID,
    guide: Guide,
    *,
    target_lang: str,
    status: Literal["draft", "approved"],
    author: Literal["engine", "user"],
    source_run_id: uuid.UUID | None = None,
    built_from_version: int | None = None,
) -> GuideVersion:
    await s.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    current = await s.scalar(
        select(func.max(GuideVersion.version)).where(
            GuideVersion.project_id == project_id, GuideVersion.target_lang == target_lang
        )
    )
    row = GuideVersion(
        project_id=project_id,
        target_lang=target_lang,
        version=(current or 0) + 1,
        status=status,
        author=author,
        source_run_id=source_run_id,
        built_from_version=built_from_version,
        content=guide.model_dump(mode="json"),
    )
    s.add(row)
    await s.flush()
    return row


async def create_run(
    s: AsyncSession,
    document_id: uuid.UUID,
    *,
    target_lang: str,
    config_hash: str,
    config_snapshot: dict[str, Any],
    prompt_versions: dict[str, int],
    source_lang: str | None = None,
    pipeline_profile: str | None = None,
    checkpoint: bool | None = None,
    use_project_guide: bool = False,
) -> Run:
    run = Run(
        document_id=document_id,
        target_lang=target_lang,
        source_lang=source_lang,
        pipeline_profile=pipeline_profile,
        checkpoint=checkpoint,
        use_project_guide=use_project_guide,
        config_hash=config_hash,
        config_snapshot=config_snapshot,
        prompt_versions=prompt_versions,
    )
    s.add(run)
    await s.flush()
    return run


async def get_run(s: AsyncSession, run_id: uuid.UUID, *, lock: bool = False) -> Run:
    query = select(Run).where(Run.id == run_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    run = await s.scalar(query)
    if run is None:
        raise NotFoundError(f"run {run_id} not found")
    return run


async def save_chunks(s: AsyncSession, run_id: uuid.UUID, chunks: list[Chunk]) -> None:
    await s.execute(delete(ChunkRow).where(ChunkRow.run_id == run_id))
    await _insert_many(
        s,
        ChunkRow.__table__,
        [
            {"run_id": run_id, "ordinal": k, "chunk_id": c.id, "segment_ids": c.segment_ids}
            for k, c in enumerate(chunks)
        ],
    )


async def load_chunks(s: AsyncSession, run_id: uuid.UUID) -> list[Chunk]:
    rows = await s.scalars(
        select(ChunkRow).where(ChunkRow.run_id == run_id).order_by(ChunkRow.ordinal)
    )
    return [Chunk(id=r.chunk_id, segment_ids=list(r.segment_ids)) for r in rows]


async def chunk_count(s: AsyncSession, run_id: uuid.UUID) -> int:
    count = await s.scalar(select(func.count()).where(ChunkRow.run_id == run_id))
    return count or 0


async def get_stage(
    s: AsyncSession, run_id: uuid.UUID, stage: str, chunk_id: str = ""
) -> StageResult | None:
    return await s.scalar(
        select(StageResult)
        .where(
            StageResult.run_id == run_id,
            StageResult.stage == stage,
            StageResult.chunk_id == chunk_id,
        )
        .execution_options(populate_existing=True)
    )


async def begin_stage(
    s: AsyncSession, run_id: uuid.UUID, stage: str, chunk_id: str = ""
) -> StageResult:
    now = utcnow()
    statement = (
        pg_insert(StageResult)
        .values(
            run_id=run_id,
            stage=stage,
            chunk_id=chunk_id,
            status=RUNNING,
            attempt=1,
            error_count=0,
            started_at=now,
        )
        .on_conflict_do_update(
            index_elements=["run_id", "stage", "chunk_id"],
            set_={
                "status": RUNNING,
                "attempt": StageResult.attempt + 1,
                "output": None,
                "error": None,
                "error_count": 0,
                "started_at": now,
                "finished_at": None,
                "duration_ms": None,
            },
        )
        .returning(StageResult)
    )
    result = await s.execute(statement, execution_options={"populate_existing": True})
    return result.scalar_one()


def _finish(row: StageResult) -> None:
    now = utcnow()
    row.finished_at = now
    row.duration_ms = int((now - row.started_at).total_seconds() * 1000)


def count_errors(output: dict[str, Any] | None) -> int:
    if not output:
        return 0
    return len(output.get("errors") or []) + (1 if output.get("engine_error") else 0)


def finish_stage(row: StageResult, output: dict[str, Any] | None) -> None:
    row.status = COMPLETE
    row.output = output
    row.error = None
    row.error_count = count_errors(output)
    _finish(row)


def fail_stage(row: StageResult, error: str) -> None:
    row.status = FAILED
    row.error = error
    row.error_count = 1
    _finish(row)


async def delete_stages(
    s: AsyncSession,
    run_id: uuid.UUID,
    *,
    stages: Iterable[str] | None = None,
    chunk_ids: Iterable[str] | None = None,
    status: str | None = None,
) -> None:
    statement = delete(StageResult).where(StageResult.run_id == run_id)
    if stages is not None:
        statement = statement.where(StageResult.stage.in_(list(stages)))
    if chunk_ids is not None:
        statement = statement.where(StageResult.chunk_id.in_(list(chunk_ids)))
    if status is not None:
        statement = statement.where(StageResult.status == status)
    await s.execute(statement)


async def stage_rows(s: AsyncSession, run_id: uuid.UUID) -> list[StageResult]:
    rows = await s.scalars(
        select(StageResult).where(StageResult.run_id == run_id).order_by(StageResult.id)
    )
    return list(rows)


async def chunk_progress(s: AsyncSession, run_id: uuid.UUID) -> dict[str, int]:
    rows = await s.execute(
        select(StageResult.stage, func.count())
        .where(
            StageResult.run_id == run_id,
            StageResult.chunk_id != "",
            StageResult.status == COMPLETE,
        )
        .group_by(StageResult.stage)
    )
    return {stage: count for stage, count in rows.all()}


async def finished_chunk_count(s: AsyncSession, run_id: uuid.UUID) -> int:
    count = await s.scalar(
        select(func.count()).where(
            StageResult.run_id == run_id,
            StageResult.stage == "score",
            StageResult.status.in_([COMPLETE, FAILED]),
        )
    )
    return count or 0


async def chunks_with_errors(s: AsyncSession, run_id: uuid.UUID) -> list[str]:
    rows = await s.scalars(
        select(StageResult.chunk_id).where(
            StageResult.run_id == run_id,
            StageResult.stage == "score",
            (StageResult.status == FAILED) | (StageResult.error_count > 0),
        )
    )
    return list(rows)


async def total_error_count(s: AsyncSession, run_id: uuid.UUID) -> int:
    total = await s.scalar(
        select(func.coalesce(func.sum(StageResult.error_count), 0)).where(
            StageResult.run_id == run_id
        )
    )
    return int(total or 0)


async def save_llm_calls(
    s: AsyncSession,
    run_id: uuid.UUID,
    records: list[LLMCallRecord],
    *,
    stage: str,
    chunk_id: str | None = None,
) -> None:
    for r in records:
        s.add(LLMCall(run_id=run_id, stage=stage, chunk_id=chunk_id, **r.model_dump()))
    await s.flush()


async def load_llm_calls(s: AsyncSession, run_id: uuid.UUID) -> list[LLMCallRecord]:
    rows = await s.scalars(select(LLMCall).where(LLMCall.run_id == run_id).order_by(LLMCall.id))
    return [
        LLMCallRecord(
            role=r.role,
            backend=r.backend,
            model=r.model,
            prompt_id=r.prompt_id,
            prompt_version=r.prompt_version,
            attempt=r.attempt,
            ok=r.ok,
            latency_ms=r.latency_ms,
            tokens_in=r.tokens_in,
            tokens_out=r.tokens_out,
            error=r.error,
            request=r.request,
            response=r.response,
        )
        for r in rows
    ]


async def load_llm_usage(
    s: AsyncSession, run_id: uuid.UUID
) -> list[tuple[str, int | None, int | None]]:
    """(role, tokens_in, tokens_out) per model call, in call order; never reads the stored
    request and response bodies (CGL-63)."""
    rows = await s.execute(
        select(LLMCall.role, LLMCall.tokens_in, LLMCall.tokens_out)
        .where(LLMCall.run_id == run_id)
        .order_by(LLMCall.id)
    )
    return [(r.role, r.tokens_in, r.tokens_out) for r in rows]


async def delete_project(s: AsyncSession, project_id: uuid.UUID) -> None:
    """Deletes the project and, by cascade, everything in it. Lock order: its runs, then the
    project, as guide saves take them (CGL-28); the cascade alone would lock the project first
    and could deadlock with a save waiting for the project while holding its run."""
    await s.execute(
        select(Run.id)
        .join(Document, Document.id == Run.document_id)
        .where(Document.project_id == project_id)
        .order_by(Run.id)
        .with_for_update(of=Run)
    )
    await s.execute(delete(Project).where(Project.id == project_id))
