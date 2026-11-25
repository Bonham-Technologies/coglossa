"""Sweep stage (spec §4.1): after every chunk is done, enforce the locked guide entries
(glossary terms and names a human locked) across the whole document, and resolve the terms the
reviewers proposed. Every text change is a SpanChange with a reason; nothing changes silently.
Scores keep describing the reviewed draft they were computed on."""

import asyncio
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from coglossa.llm.agent import AGENT_FAILURES
from coglossa.schema import (
    DocumentProfile,
    Flag,
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    Guide,
    Revision,
    RevisionSource,
    RunError,
    Segment,
    Span,
    kept_formatting,
)
from coglossa.schema.guide import prompt_entry
from coglossa.scoring.rules import GuideViolation, guide_check
from coglossa.segment.markup import formatting_flag
from coglossa.stages.context import StageContext
from coglossa.stages.guide import guide_slice
from coglossa.stages.translate import CUT_OFF, looks_cut_off

# The evidence of a flag the sweep raised, so that undoing the sweep removes it (CGL-129)
SWEPT = {"raised_by": "sweep"}


class SweepFix(BaseModel):
    text: str = Field(description="The full revised translation of the passage")


@dataclass(frozen=True)
class SpanChange:
    span_id: str
    before: str
    after: str
    reason: str
    resolved: tuple[FlagCode, ...]  # flag codes the change fixed
    raised: tuple[Flag, ...] = ()  # flags the change raised (SWEPT in their evidence)


@dataclass
class SweepResult:
    spans: list[Span]  # same order as the input; text and flag status updated
    changes: list[SpanChange]
    errors: list[RunError]
    # CGL-109: spans whose open terminology flag the guide no longer upholds, as when a form was
    # accepted since: the flag is resolved without changing the text
    cleared: list[str] = field(default_factory=list)

    @property
    def revisions(self) -> list[Revision]:
        return [
            Revision(
                span_id=c.span_id,
                source=RevisionSource.SWEEP,
                before=c.before,
                after=c.after,
                reason=c.reason,
            )
            for c in self.changes
        ]


def span_sources(spans: list[Span], segments: list[Segment]) -> list[str]:
    positions = {s.id: i for i, s in enumerate(segments)}
    return [
        " ".join(seg.text for seg in segments[positions[sp.start] : positions[sp.end] + 1])
        for sp in spans
    ]


def _locked_violations(source: str, text: str, guide: Guide) -> list[GuideViolation]:
    _, violations = guide_check(source, text, guide)
    return [v for v in violations if v.locked]


async def run_sweep(
    ctx: StageContext,
    profile: DocumentProfile,
    guide: Guide,
    spans: list[Span],
    segments: list[Segment],
) -> SweepResult:
    sources = span_sources(spans, segments)
    todo: list[tuple[int, list[GuideViolation]]] = []
    holding: list[int] = []
    for i, span in enumerate(spans):
        if not span.text.strip() or span.kept is not None:  # text left as written (CGL-111)
            continue
        if violations := _locked_violations(sources[i], span.text, guide):
            todo.append((i, violations))
        elif _open_terminology(span):
            holding.append(i)
    results = await asyncio.gather(
        *(_fix(ctx, profile, guide, spans[i], sources[i], v) for i, v in todo),
        return_exceptions=True,
    )
    new_spans = list(spans)
    for i in holding:
        new_spans[i] = spans[i].model_copy(update={"flags": _resolve_terminology(spans[i])})
    changes: list[SpanChange] = []
    errors: list[RunError] = []
    for (i, violations), result in zip(todo, results, strict=True):
        span = spans[i]
        if isinstance(result, AGENT_FAILURES):
            errors.append(
                RunError(
                    stage="sweep",
                    chunk_id=span.chunk_id,
                    role="sweep",
                    message=f"{span.id}: {result}",
                )
            )
            continue
        if isinstance(result, BaseException):
            raise result
        # validation guarantees the text changed: it now contains a rendering it lacked
        rules = "; ".join(f"'{v.term}' → '{v.expected}'" for v in violations)
        flags = _resolve_terminology(span)
        # the rewrite leaves the passage plain in the downloads, so say so (CGL-108)
        raised: tuple[Flag, ...] = ()
        if kept_formatting(span) is not None and not any(
            f.code is FlagCode.FORMATTING_LOST for f in flags
        ):
            raised = (formatting_flag().model_copy(update={"evidence": dict(SWEPT)}),)
        new_spans[i] = span.model_copy(update={"text": result, "flags": [*flags, *raised]})
        changes.append(
            SpanChange(
                span_id=span.id,
                before=span.text,
                after=result,
                reason=f"Applied locked guide entries: {rules}.",
                resolved=(FlagCode.TERMINOLOGY_VIOLATION,),
                raised=raised,
            )
        )
    cleared = [spans[i].id for i in holding]
    return SweepResult(spans=new_spans, changes=changes, errors=errors, cleared=cleared)


def _open_terminology(span: Span) -> bool:
    return any(
        f.code is FlagCode.TERMINOLOGY_VIOLATION and f.status is FlagStatus.OPEN for f in span.flags
    )


def _resolve_terminology(span: Span) -> list[Flag]:
    return [
        f.model_copy(update={"status": FlagStatus.RESOLVED})
        if f.code is FlagCode.TERMINOLOGY_VIOLATION and f.status is FlagStatus.OPEN
        else f
        for f in span.flags
    ]


def _requirement(v: GuideViolation) -> dict[str, object]:
    found: dict[str, object] = {"term": v.term, "rendering": v.expected, "kind": v.kind}
    if v.forms:
        found["forms"] = list(v.forms)
    return found


def _must_contain(v: GuideViolation) -> str:
    wanted = f"the revised text must contain '{v.expected}' exactly as written"
    if v.forms:
        wanted += " or one of its accepted forms: " + ", ".join(f"'{f}'" for f in v.forms)
    return wanted


async def _fix(
    ctx: StageContext,
    profile: DocumentProfile,
    guide: Guide,
    span: Span,
    source: str,
    violations: list[GuideViolation],
) -> str:

    def check(out: SweepFix) -> list[str]:
        # every locked rendering must hold, including ones that were already right
        problems = [_must_contain(v) for v in _locked_violations(source, out.text, guide)]
        if not out.text.strip():
            problems.append("text must not be empty")
        elif looks_cut_off(out.text):
            problems.append(CUT_OFF)
        return problems

    out = await ctx.runner.run(
        role="sweep",
        model_key=ctx.cfg.roles.reviewer.model,
        max_output_tokens=ctx.cfg.output_limit(ctx.cfg.roles.reviewer),
        prompt_id="sweep",
        prompt_vars={"source_lang": profile.source_lang, "target_lang": profile.target_lang},
        payload={
            "task": "sweep",
            "source_lang": profile.source_lang,
            "target_lang": profile.target_lang,
            "source": source,
            "translation": span.text,
            "requirements": [_requirement(v) for v in violations],
            "entities": [prompt_entry(e) for e in guide_slice(guide, source).entities],
        },
        output_type=SweepFix,
        validate=check,
    )
    return out.text


def resolve_proposals(guide: Guide, proposals: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """One unlocked entry per new term: the most frequent rendering (ties: first seen), with
    conflicting alternatives noted. Terms already in the guide are skipped."""
    known = {g.term.strip().casefold() for g in guide.glossary}
    groups: dict[str, list[GlossaryEntry]] = {}
    for p in proposals:
        key = p.term.strip().casefold()
        if key and key not in known:
            groups.setdefault(key, []).append(p)
    resolved: list[GlossaryEntry] = []
    for entries in groups.values():
        counts: dict[str, int] = {}
        first: dict[str, GlossaryEntry] = {}
        for e in entries:
            k = e.rendering.strip().casefold()
            counts[k] = counts.get(k, 0) + 1
            first.setdefault(k, e)
        best = max(counts, key=lambda k: counts[k])  # max keeps the first of equal counts
        chosen = first[best]
        notes = chosen.notes
        if len(counts) > 1:
            alternatives = ", ".join(
                f"'{first[k].rendering.strip()}' ({n})" for k, n in counts.items()
            )
            notes = f"{notes} " if notes else ""
            notes += f"Reviewers proposed different renderings: {alternatives}."
        resolved.append(
            chosen.model_copy(
                update={
                    "term": chosen.term.strip(),
                    "rendering": chosen.rendering.strip(),
                    "notes": notes,
                    "locked": False,
                    "forms": [],  # people list forms (CGL-109)
                    "source_forms": [],
                }
            )
        )
    return resolved
