"""Agent outputs, candidates, final spans, scores and flags."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from coglossa.schema.guide import GlossaryEntry


class SpanText(BaseModel):
    start: str = Field(description="First segment id covered, e.g. s12")
    end: str = Field(description="Last segment id covered (same as start for one segment)")
    text: str
    notes: str = ""


class TranslationOutput(BaseModel):
    spans: list[SpanText]


class InjectOutput(BaseModel):
    text: str = Field(description="The sentence rewritten with exactly one deliberate error")


class Agreement(StrEnum):
    AGREE = "agree"
    MINOR = "minor"
    MAJOR = "major"
    UNKNOWN = "unknown"  # set by the engine when review did not happen


class ReconciledSpan(BaseModel):
    start: str
    end: str
    text: str
    agreement: Agreement
    chosen_from: str = Field(description="Translator name, 'merged', or 'rewritten'")
    rationale: str = ""
    issues: list[str] = Field(default_factory=list)
    cultural_or_wordplay: bool = False
    proposed_terms: list[GlossaryEntry] = Field(default_factory=list)


class ReconcileOutput(BaseModel):
    spans: list[ReconciledSpan]


class Candidate(BaseModel):
    chunk_id: str
    translator: str
    model: str
    start: str
    end: str
    text: str
    notes: str = ""


class FlagCode(StrEnum):
    LOW_MEANING = "low_meaning"
    STYLE_DRIFT = "style_drift"
    AGENT_DISAGREEMENT = "agent_disagreement"
    TERMINOLOGY_VIOLATION = "terminology_violation"
    POSSIBLE_OMISSION = "possible_omission"
    CULTURAL_OR_WORDPLAY = "cultural_or_wordplay"
    ENGINE_ERROR = "engine_error"
    LOW_RELIABILITY_LANGUAGE = "low_reliability_language"
    UNSCORED = "unscored"
    NUMBER_MISMATCH = "number_mismatch"
    UNREAD_CONTENT = "unread_content"  # a document notice: parts of the file left out (CGL-87)


class FlagStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"  # a reviewer confirmed the passage is fine as it is
    DISMISSED = "dismissed"  # a reviewer rejected the flag; dismiss_reason says why
    RESOLVED = "resolved"  # the sweep fixed the problem; the fix is a Revision


class Flag(BaseModel):
    code: FlagCode
    explanation: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: FlagStatus = FlagStatus.OPEN
    dismiss_reason: str | None = None


class Reliability(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    UNSCORED = "unscored"


class ErrorSeverity(StrEnum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class ErrorCategory(StrEnum):
    MISTRANSLATION = "accuracy/mistranslation"
    OMISSION = "accuracy/omission"
    ADDITION = "accuracy/addition"
    TERMINOLOGY = "terminology"
    FLUENCY = "fluency"
    STYLE = "style/register"
    LOCALE = "locale"


ACCURACY_CATEGORIES = frozenset(
    {
        ErrorCategory.MISTRANSLATION,
        ErrorCategory.OMISSION,
        ErrorCategory.ADDITION,
        ErrorCategory.TERMINOLOGY,
    }
)


class JudgeError(BaseModel):
    category: ErrorCategory
    severity: ErrorSeverity
    excerpt: str = Field(
        "",
        description="Exact text from the translation that contains the error; empty for omissions",
    )
    explanation: str


class SpanJudgement(BaseModel):
    start: str
    end: str
    errors: list[JudgeError] = Field(default_factory=list)


class JudgeOutput(BaseModel):
    spans: list[SpanJudgement]


class SpanScore(BaseModel):
    signals: dict[str, float | None]
    meaning: float | None = None
    style: float | None = None
    reliability: Reliability = Reliability.UNSCORED


class Span(BaseModel):
    id: str  # "p1", "p2", … in reading order
    chunk_id: str
    start: str
    end: str
    text: str
    agreement: Agreement
    chosen_from: str
    rationale: str = ""
    issues: list[str] = Field(default_factory=list)
    score: SpanScore
    flags: list[Flag] = Field(default_factory=list)
    judge_errors: list[JudgeError] | None = None  # None: not judged; []: judged, no errors
    back_translation: str | None = None  # None: not back-translated


class RevisionSource(StrEnum):
    ENGINE = "engine"  # the reviewer's reconciled text
    SWEEP = "sweep"  # consistency fixes after all chunks (spec §4.1)
    USER = "user"  # edits in the review view (M4)


class Revision(BaseModel):
    """One change to a span's text. Span.text is always the latest revision."""

    span_id: str
    source: RevisionSource
    before: str | None = None
    after: str
    reason: str
