"""Read models the REST API returns (spec §8). The control plane builds them and the API
serves them unchanged, so the OpenAPI document describes exactly what the service returns.
IDs are strings: UUIDs for projects, documents, runs and spans; integers for flags."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from coglossa.schema.document import KeepReason
from coglossa.schema.guide import Guide
from coglossa.schema.profile import DocumentProfile, PipelineProfile
from coglossa.schema.run import RunProgress, RunState
from coglossa.schema.translation import (
    Agreement,
    Candidate,
    Flag,
    FlagCode,
    FlagStatus,
    JudgeError,
    Reliability,
    RevisionSource,
    SpanScore,
)

# BCP-47-like: a 2–3 letter language, then optional subtags (script, region, variant).
LANGUAGE_CODE_PATTERN = r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8})*$"
LanguageCode = Annotated[str, Field(pattern=LANGUAGE_CODE_PATTERN, max_length=35)]


class SetLanguage(BaseModel):
    """One language a set is translated into (CGL-24)."""

    target_lang: str
    guide_version: int | None  # its set guide's version (runs.prior_guide); None: no guide yet


class ProjectInfo(BaseModel):
    id: str
    name: str
    target_lang: str | None  # default for new runs; None for projects made by the CLI
    source_lang: str | None  # None: detected per document
    pipeline_profile: PipelineProfile | None  # None: chosen per document
    team: str | None  # CGL-26: whose members see it; None: personal to created_by
    created_by: str | None  # who made it; None for sets from before teams or auth.mode none
    created_at: datetime
    documents: int
    runs: int
    guide_version: int | None  # the first language's guide version; None: no guide yet
    languages: list[SetLanguage]  # the default language first, then by first run (CGL-24)
    last_activity_at: datetime  # the latest of created_at and its runs' created and finished times
    # Counted per file and language: each file's newest run in each of its languages.
    files_translating: int  # newest run still under way (or waiting for approval)
    files_ready: int  # newest run finished with output
    files_failed: int  # newest run failed or was cancelled
    open_flags: int  # open flags on those newest runs


class RunSummary(BaseModel):
    id: str
    project_id: str
    document_id: str
    filename: str
    state: RunState
    target_lang: str
    source_lang: str | None  # detected once profiled, else the requested override
    pipeline_profile: PipelineProfile | None  # chosen once profiled, else the override
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None


class DocumentRun(RunSummary):
    """A run as the documents list shows it: its summary and how many passages still wait to be
    checked, by the rule of the passage list's "To check" (CGL-73)."""

    to_check: int  # passages with an open flag; 0 until there are passages


class DocumentSummary(BaseModel):
    id: str
    project_id: str
    filename: str
    format: str
    created_at: datetime
    ingested: bool
    runs: list[DocumentRun]  # newest first


class ReviewStats(BaseModel):
    spans: int
    open_flags: int
    flags_by_code: dict[str, int]  # open flags only
    edited_spans: int  # spans with at least one user revision


class RunProblem(BaseModel):
    """A problem with the whole file rather than one passage, such as a translation guide that
    couldn't be built (CGL-80). `text` is a plain sentence for the app; the error itself stays in
    the log and the report."""

    stage: str  # the stage it came from, e.g. "guide" or "config"
    text: str


class RunDetail(BaseModel):
    run: RunSummary
    progress: RunProgress
    profile: DocumentProfile | None  # None until profiled
    language_tier: Reliability | None
    notices: list[Flag]  # document-level notices (spec §6.4), once passages exist
    problems: list[RunProblem] = Field(default_factory=list)  # one per stage, in stage order
    summary: str | None  # the guide's summary, once the guide exists
    guide_version: int | None
    stats: ReviewStats
    report_available: bool  # complete or complete_with_errors
    sweep_enabled: bool  # the run's config runs the sweep for its profile (profiles.sweep)


class GuideVersionInfo(BaseModel):
    version: int
    target_lang: str  # each language of a set has its own guide (CGL-24)
    status: Literal["draft", "approved"]
    author: Literal["engine", "user"]
    source_run_id: str | None
    created_at: datetime


class GuideView(BaseModel):
    info: GuideVersionInfo
    guide: Guide


class GlossaryImportTerm(BaseModel):
    """One term of a glossary file, compared with the guide (CGL-88)."""

    line: int  # where it is in the file
    term: str
    rendering: str
    # new: not in the guide; identical: there with this translation; conflict: there with
    # another; duplicate: an earlier line has the same term, and that line is used
    status: Literal["new", "identical", "conflict", "duplicate"]
    current_rendering: str | None = None  # the guide's translation, or the earlier line's
    current_locked: bool = False  # whether the guide's entry is locked
    first_line: int | None = None  # duplicate: the line that is used


class GlossaryImportPreview(BaseModel):
    """What importing a glossary file would do; nothing is written (CGL-88)."""

    format: Literal["csv", "tsv", "tbx"]
    target_lang: str
    source_lang: str | None  # TBX: the language the terms were read from
    base_version: int | None  # the guide version compared with; None: no guide yet
    new: int
    identical: int
    conflicts: int
    locked_conflicts: int  # conflicts whose guide entry is locked
    duplicates: int
    skipped_entries: int  # TBX entries without a term in one of the two languages
    terms: list[GlossaryImportTerm]


class GlossaryImportResult(BaseModel):
    """An applied glossary import (CGL-88). With nothing to change, no version is added."""

    view: GuideView
    created_version: bool
    added: int
    updated: int  # conflicts whose guide translation the file replaced
    locked: int  # imported entries newly locked
    kept: int  # conflicts left as the guide has them
    duplicates: int


class ReviewFlag(BaseModel):
    id: int
    code: FlagCode
    explanation: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: FlagStatus
    dismiss_reason: str | None = None


class ReviewSpan(BaseModel):
    id: str
    number: int  # 1-based reading-order position; the report calls this span p<number>
    chunk_id: str
    start: str
    end: str
    source: str
    text: str  # the latest revision
    agreement: Agreement
    score: SpanScore  # describes the reviewed draft, not later edits
    flags: list[ReviewFlag]
    edited: bool  # a person changed the text
    kept: KeepReason | None = None  # left as written, never translated (CGL-111)


class SpanPage(BaseModel):
    total: int  # spans matching the filter
    offset: int
    limit: int
    spans: list[ReviewSpan]


class RevisionEntry(BaseModel):
    source: RevisionSource
    before: str | None
    after: str
    reason: str
    created_at: datetime


class SpanDetail(ReviewSpan):
    chosen_from: str
    rationale: str
    issues: list[str]
    judge_errors: list[JudgeError] | None
    back_translation: str | None
    candidates: list[Candidate]  # every candidate overlapping this span's segments
    revisions: list[RevisionEntry]  # oldest first; the first is the reconciled text


class ReviewAction(StrEnum):
    """`review_event.action` values (spec §6.5)."""

    ACCEPT = "accept"
    EDIT = "edit"
    USE_CANDIDATE = "use_candidate"
    ACCEPT_FLAG = "accept_flag"
    DISMISS_FLAG = "dismiss_flag"
    REOPEN_FLAG = "reopen_flag"
    ADD_TO_GUIDE = "add_to_guide"


class TierTable(BaseModel):
    """The language tier table for the System page (spec §6.4)."""

    default: Reliability
    languages: dict[str, Reliability]
    pairs: dict[str, Reliability]  # "src>tgt"
