"""The canonical run report."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from coglossa.schema.document import Block, Chunk, Segment
from coglossa.schema.guide import GlossaryEntry, Guide
from coglossa.schema.profile import DocumentProfile
from coglossa.schema.scorer_api import ScorerHealth
from coglossa.schema.translation import Candidate, Flag, Reliability, Revision, Span


class LLMCallRecord(BaseModel):
    role: str
    backend: str
    model: str
    prompt_id: str
    prompt_version: int
    attempt: int
    ok: bool
    latency_ms: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None
    request: dict[str, Any] | None = None  # only when audit.store_llm_io
    response: str | None = None  # only when audit.store_llm_io


class Usage(BaseModel):
    calls: int
    tokens_in: int
    tokens_out: int
    by_role: dict[str, dict[str, int]]


class RunStatus(StrEnum):
    COMPLETE = "complete"
    COMPLETE_WITH_ERRORS = "complete_with_errors"


class RunError(BaseModel):
    stage: str
    message: str
    chunk_id: str | None = None
    role: str | None = None


class DocumentInfo(BaseModel):
    source_path: str
    format: str
    sha256: str


class ConfigSnapshot(BaseModel):
    hash: str
    roles: dict[str, str]  # role label -> model key
    prompt_versions: dict[str, int]
    scorer: ScorerHealth | None = None  # models the scorer reported at startup; None: no scorer


class Report(BaseModel):
    schema_version: Literal["1"] = "1"
    coglossa_version: str
    run_id: str | None = None  # set when the worker ran it
    created_at: datetime
    status: RunStatus
    document: DocumentInfo
    profile: DocumentProfile
    guide: Guide
    guide_proposals: list[GlossaryEntry] = Field(default_factory=list)
    blocks: list[Block]
    segments: list[Segment]
    chunks: list[Chunk]
    candidates: list[Candidate]
    spans: list[Span]
    revisions: list[Revision] = Field(
        default_factory=list
    )  # sweep and user edits; reconciled text is the baseline
    language_tier: Reliability = Reliability.UNSCORED
    notices: list[Flag] = Field(
        default_factory=list
    )  # document-level, e.g. low_reliability_language
    errors: list[RunError] = Field(default_factory=list)
    usage: Usage
    config: ConfigSnapshot
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
