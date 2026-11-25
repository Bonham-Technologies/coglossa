"""Postgres schema (spec §5). JSONB columns hold Pydantic models dumped with mode="json"; their
shapes are the exported schemas in schema/. Rows cascade from their project, so deleting a
project deletes everything that belongs to it.

The schema differs from the spec's sketch in five places, each a ruling:
- chunk belongs to the run, not the document: chunk sizes depend on the run's context windows.
- guide_version.source_run_id has no foreign key: runs reference guide versions, so a foreign
  key the other way would be circular.
- stage_result.error_count is the number of errors in the stage's output (1 for a failed
  stage), so progress and the final status are cheap SQL queries.
- span.proposed_terms stores the reviewer's proposals per span, so reports can re-resolve them.
- flag.status has a fourth value, resolved, set by the sweep when its revision fixes the
  problem; it stays distinct from a human's accepted (spec §5 lists open|accepted|dismissed).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Dialect, ForeignKey, Identity, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

Json = dict[str, Any]
JsonList = list[Any]


def utcnow() -> datetime:
    return datetime.now(UTC)


def strip_nul(value: Any) -> Any:
    """Postgres rejects NUL characters in text and jsonb values, and model output can contain
    them; every string written through these types loses its NUL characters instead of failing
    the transaction. Dict keys and nested lists are cleaned too."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {strip_nul(k): strip_nul(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [strip_nul(v) for v in value]
    return value


class SafeText(TypeDecorator[str]):
    """TEXT that drops NUL characters on write (see strip_nul)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return strip_nul(value)


class SafeJSONB(TypeDecorator[Any]):
    """JSONB that drops NUL characters from every string inside the value on write."""

    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        return strip_nul(value)


class Base(DeclarativeBase):
    type_annotation_map = {
        Json: SafeJSONB,
        JsonList: SafeJSONB,
        datetime: TIMESTAMP(timezone=True),
        uuid.UUID: UUID(as_uuid=True),
        str: SafeText,
    }


def _cascade(target: str, **kwargs: Any) -> Any:
    return mapped_column(ForeignKey(target, ondelete="CASCADE"), **kwargs)


class Project(Base):
    __tablename__ = "project"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(unique=True)
    # Defaults for new runs (spec §8.1); projects created by the CLI leave them empty.
    target_lang: Mapped[str | None]
    source_lang: Mapped[str | None]
    pipeline_profile: Mapped[str | None]
    # CGL-26: the team whose members see the set; None: personal to `created_by` (admins only
    # when that is None too, as for sets made before teams)
    team: Mapped[str | None]
    created_by: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class GuideVersion(Base):
    """Append-only (spec §5). source_run_id has no foreign key: runs point at versions."""

    __tablename__ = "guide_version"
    __table_args__ = (UniqueConstraint("project_id", "target_lang", "version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = _cascade("project.id", index=True)
    target_lang: Mapped[str]  # the language whose guide this is (CGL-24)
    version: Mapped[int]  # counts per project and language
    status: Mapped[str]  # draft | approved
    author: Mapped[str]  # engine | user
    source_run_id: Mapped[uuid.UUID | None]
    # a guide-stage draft: the project-guide version it was built from (NULL: built without one,
    # or a version that is not a guide-stage draft); a checkpoint approval is measured against it
    built_from_version: Mapped[int | None]
    content: Mapped[Json]  # Guide
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Document(Base):
    __tablename__ = "document"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = _cascade("project.id", index=True)
    filename: Mapped[str]
    format: Mapped[str]
    sha256: Mapped[str]
    storage_key: Mapped[str]
    ingested_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class BlockRow(Base):
    __tablename__ = "block"
    document_id: Mapped[uuid.UUID] = _cascade("document.id", primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    block_id: Mapped[str]
    kind: Mapped[str]
    text: Mapped[str]
    provenance: Mapped[Json]


class SegmentRow(Base):
    __tablename__ = "segment"
    document_id: Mapped[uuid.UUID] = _cascade("document.id", primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[str]
    block_id: Mapped[str]
    start: Mapped[int] = mapped_column("char_start")
    end: Mapped[int] = mapped_column("char_end")
    text: Mapped[str]


class Run(Base):
    __tablename__ = "run"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = _cascade("document.id", index=True)
    state: Mapped[str] = mapped_column(default="created", index=True)
    target_lang: Mapped[str]
    source_lang: Mapped[str | None]  # user override
    pipeline_profile: Mapped[str | None]  # user override
    checkpoint: Mapped[bool | None]  # user override
    use_project_guide: Mapped[bool] = mapped_column(default=False)
    config_hash: Mapped[str]
    config_snapshot: Mapped[Json]
    prompt_versions: Mapped[Json]
    profile: Mapped[Json | None]  # DocumentProfile, after profiling
    language_tier: Mapped[str | None]
    guide_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("guide_version.id", ondelete="SET NULL")
    )
    scorer_health: Mapped[Json | None]
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class ChunkRow(Base):
    """Chunks belong to the run: their size depends on the run's configured context windows."""

    __tablename__ = "chunk"
    __table_args__ = (UniqueConstraint("run_id", "chunk_id"),)
    run_id: Mapped[uuid.UUID] = _cascade("run.id", primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[str]
    segment_ids: Mapped[JsonList]


class StageResult(Base):
    """Idempotency key (run, stage, chunk) — spec §7.1. chunk_id is "" for run-level stages."""

    __tablename__ = "stage_result"
    __table_args__ = (UniqueConstraint("run_id", "stage", "chunk_id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = _cascade("run.id", index=True)
    stage: Mapped[str]
    chunk_id: Mapped[str] = mapped_column(default="")
    status: Mapped[str]  # running | complete | failed
    attempt: Mapped[int] = mapped_column(default=1)
    output: Mapped[Json | None]
    error: Mapped[str | None]
    error_count: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    duration_ms: Mapped[int | None]


class LLMCall(Base):
    __tablename__ = "llm_call"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = _cascade("run.id", index=True)
    stage: Mapped[str]
    chunk_id: Mapped[str | None]
    role: Mapped[str]
    backend: Mapped[str]
    model: Mapped[str]
    prompt_id: Mapped[str]
    prompt_version: Mapped[int]
    attempt: Mapped[int]
    ok: Mapped[bool]
    latency_ms: Mapped[int]
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    error: Mapped[str | None]
    request: Mapped[Json | None]  # only when audit.store_llm_io
    response: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CandidateRow(Base):
    __tablename__ = "candidate"
    __table_args__ = (Index("ix_candidate_run_chunk", "run_id", "chunk_id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = _cascade("run.id")
    chunk_id: Mapped[str]
    chunk_ordinal: Mapped[int]
    translator: Mapped[str]
    model: Mapped[str]
    seg_start: Mapped[str]
    seg_end: Mapped[str]
    text: Mapped[str]
    notes: Mapped[str]


class SpanRow(Base):
    __tablename__ = "span"
    __table_args__ = (UniqueConstraint("run_id", "chunk_ordinal", "ordinal"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = _cascade("run.id", index=True)
    chunk_id: Mapped[str]
    chunk_ordinal: Mapped[int]
    ordinal: Mapped[int]  # position within the chunk
    seg_start: Mapped[str]
    seg_end: Mapped[str]
    agreement: Mapped[str]
    chosen_from: Mapped[str]
    rationale: Mapped[str]
    issues: Mapped[JsonList]
    judge_errors: Mapped[JsonList | None]
    back_translation: Mapped[str | None]
    proposed_terms: Mapped[JsonList]
    formatting: Mapped[Json | None]  # SpanFormatting (CGL-108); None: none kept, or older runs
    kept: Mapped[str | None]  # KeepReason: left as written (CGL-111); None: translated, or older


class SpanScoreRow(Base):
    __tablename__ = "span_score"
    span_id: Mapped[uuid.UUID] = _cascade("span.id", primary_key=True)
    signals: Mapped[Json]
    meaning: Mapped[float | None]
    style: Mapped[float | None]
    reliability: Mapped[str]


class FlagRow(Base):
    __tablename__ = "flag"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    span_id: Mapped[uuid.UUID] = _cascade("span.id", index=True)
    code: Mapped[str]
    explanation: Mapped[str]
    evidence: Mapped[Json]
    status: Mapped[str] = mapped_column(default="open")  # open | accepted | dismissed | resolved
    dismiss_reason: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class RevisionRow(Base):
    """Append-only; a span's current text is its latest revision (spec §5)."""

    __tablename__ = "revision"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    span_id: Mapped[uuid.UUID] = _cascade("span.id", index=True)
    source: Mapped[str]  # engine | sweep | user
    before: Mapped[str | None]
    after: Mapped[str]
    reason: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ReviewEvent(Base):
    """Feedback log (spec §6.5); written by the review UI in M4."""

    __tablename__ = "review_event"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = _cascade("run.id", index=True)
    span_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("span.id", ondelete="CASCADE"))
    action: Mapped[str]
    payload: Mapped[Json]
    at: Mapped[datetime] = mapped_column(default=utcnow)
