"""The review summary (CGL-112): post-editing effort per run and per language of a set, from what
the review view already stores: each passage's revisions, the feedback log (review_event) and
its flags. It only reads; nothing here changes a threshold or teaches the engine anything.

Definitions (also in the response schema, coglossa.schema.review_summary):
- checked: a person approved the passage's text, by the rule of CGL-89's approved texts;
- edited: a person changed the passage's text at least once (a user revision);
- the engine's text: the passage's last revision before the first user revision;
- edit distance: Levenshtein distance in Unicode code points between the engine's text and the
  text the person approved (an edited passage reopened since: its current text).
"""

import asyncio
import csv
import io
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coglossa.schema import FlagStatus, RevisionSource
from coglossa.schema.review_summary import (
    DismissReason,
    FlagReviewFigures,
    LanguageReviewSummary,
    ReviewFigures,
    RunReviewSummary,
    SetReviewSummary,
)
from coglossa.store import review as review_store
from coglossa.store import runs
from coglossa.store.db import Database
from coglossa.store.models import Document, FlagRow, ReviewEvent, RevisionRow, Run, SpanRow

# Levenshtein is O(n*m): about 0.15 s per million cells in pure Python. After the shared
# beginning and end are trimmed, a passage whose differing middles need more cells than
# MAX_CELLS, or that would take the request past BUDGET_CELLS, gets an upper bound instead (the
# longer middle's length) and is counted in `estimated_distances`.
MAX_CELLS = 1_000_000
BUDGET_CELLS = 20_000_000
MAX_REASONS = 20


def _trim(a: str, b: str) -> tuple[str, str]:
    """`a` and `b` without the beginning and end they share, which cost nothing to keep."""
    shortest = min(len(a), len(b))
    start = 0
    while start < shortest and a[start] == b[start]:
        start += 1
    end = 0
    while end < shortest - start and a[-1 - end] == b[-1 - end]:
        end += 1
    return a[start : len(a) - end], b[start : len(b) - end]


def edit_distance(a: str, b: str, *, max_cells: int = MAX_CELLS) -> tuple[int, bool]:
    """The Levenshtein distance between `a` and `b` over Unicode code points (not grapheme
    clusters), and whether it is exact. One row of the table at a time, over the shorter
    string: O(len(a) * len(b)) time, O(min) memory. When the differing middles need more than
    `max_cells` cells, the result is the longer middle's length, an upper bound, and not exact."""
    a, b = _trim(a, b)
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a), True
    if len(a) * len(b) > max_cells:
        return len(a), False
    previous = list(range(len(b) + 1))
    for i, char in enumerate(a, start=1):
        current = [i]
        left = i
        for j, other in enumerate(b):
            left = min(previous[j] + (char != other), previous[j + 1] + 1, left + 1)
            current.append(left)
        previous = current
    return previous[-1], True


@dataclass
class _Flag:
    code: str
    status: str
    reason: str | None
    created_at: datetime


@dataclass
class _Passage:
    run_id: uuid.UUID
    number: int
    flags: list[_Flag] = field(default_factory=list)
    checked: bool = False
    edits: list[datetime] = field(default_factory=list)  # when a person changed the text
    engine_text: str = ""
    reviewed_text: str = ""
    distance: int | None = None  # None: not edited
    ratio: float = 0.0
    exact: bool = True
    reviewers: list[str] = field(default_factory=list)

    @property
    def edited(self) -> bool:
        return bool(self.edits)


async def _passages(s: AsyncSession, run_ids: Sequence[uuid.UUID]) -> list[_Passage]:
    """Every passage of the runs, in reading order run by run, from a fixed number of queries
    per call (and CGL-89's approved texts, a query or two per run): never one per passage."""
    if not run_ids:
        return []
    by_span: dict[uuid.UUID, _Passage] = {}
    numbers: Counter[uuid.UUID] = Counter()
    order = {run_id: i for i, run_id in enumerate(run_ids)}
    rows = (
        await s.execute(
            select(SpanRow.id, SpanRow.run_id)
            .where(SpanRow.run_id.in_(run_ids))
            .order_by(SpanRow.chunk_ordinal, SpanRow.ordinal)
        )
    ).all()
    for span_id, run_id in sorted(rows, key=lambda row: order[row.run_id]):
        numbers[run_id] += 1
        by_span[span_id] = _Passage(run_id=run_id, number=numbers[run_id])
    in_runs = SpanRow.run_id.in_(run_ids)
    for flag in await s.execute(
        select(
            FlagRow.span_id,
            FlagRow.code,
            FlagRow.status,
            FlagRow.dismiss_reason,
            FlagRow.created_at,
        )
        .join(SpanRow, SpanRow.id == FlagRow.span_id)
        .where(in_runs)
        .order_by(FlagRow.id)
    ):
        by_span[flag.span_id].flags.append(
            _Flag(flag.code, flag.status, flag.dismiss_reason, flag.created_at)
        )
    current: dict[uuid.UUID, str] = {}
    for revision in await s.execute(
        select(RevisionRow.span_id, RevisionRow.source, RevisionRow.after, RevisionRow.created_at)
        .join(SpanRow, SpanRow.id == RevisionRow.span_id)
        .where(in_runs)
        .order_by(RevisionRow.id)
    ):
        passage = by_span[revision.span_id]
        if revision.source == RevisionSource.USER.value:
            passage.edits.append(revision.created_at)
        elif not passage.edited:
            passage.engine_text = revision.after
        current[revision.span_id] = revision.after
    for user, span_id in await s.execute(
        select(ReviewEvent.payload["user"].astext, ReviewEvent.span_id)
        .where(ReviewEvent.run_id.in_(run_ids), ReviewEvent.span_id.is_not(None))
        .order_by(ReviewEvent.id)
    ):
        reviewers = by_span[span_id].reviewers  # the event's passage: rows cascade with it
        if user and user not in reviewers:
            reviewers.append(user)
    for run_id in run_ids:
        for span_id, text in (await review_store.approved_texts(s, run_id)).items():
            by_span[span_id].checked = True
            by_span[span_id].reviewed_text = text
    for span_id, passage in by_span.items():
        if passage.edited and not passage.checked:
            passage.reviewed_text = current.get(span_id, passage.engine_text)
    return list(by_span.values())


def _measure(passages: list[_Passage]) -> None:
    """Each edited passage's distance, within the request's budget (CPU-bound: run in a
    thread)."""
    budget = BUDGET_CELLS
    for p in passages:
        if not p.edited:
            continue
        a, b = _trim(p.engine_text, p.reviewed_text)
        p.distance, p.exact = edit_distance(a, b, max_cells=max(0, min(MAX_CELLS, budget)))
        if p.exact:
            budget -= len(a) * len(b)
        longest = max(len(p.engine_text), len(p.reviewed_text))
        p.ratio = p.distance / longest if longest else 0.0


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _flag_figures(passages: list[_Passage]) -> list[FlagReviewFigures]:
    raised: Counter[str] = Counter()
    statuses: dict[str, Counter[str]] = defaultdict(Counter)
    flagged: dict[str, set[int]] = defaultdict(set)
    edited_after: dict[str, set[int]] = defaultdict(set)
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    for i, p in enumerate(passages):
        for f in p.flags:
            raised[f.code] += 1
            statuses[f.code][f.status] += 1
            flagged[f.code].add(i)
            if any(at >= f.created_at for at in p.edits):
                edited_after[f.code].add(i)
            if f.status == FlagStatus.DISMISSED.value and f.reason:
                reasons[f.code][f.reason] += 1
    return [
        FlagReviewFigures(
            code=code,
            raised=count,
            passages=len(flagged[code]),
            open=statuses[code][FlagStatus.OPEN.value],
            accepted=statuses[code][FlagStatus.ACCEPTED.value],
            dismissed=statuses[code][FlagStatus.DISMISSED.value],
            resolved=statuses[code][FlagStatus.RESOLVED.value],
            edited_after=len(edited_after[code]),
            edited_after_share=_share(len(edited_after[code]), len(flagged[code])),
            dismiss_reasons=[
                DismissReason(reason=reason, count=n)
                for reason, n in sorted(reasons[code].items(), key=lambda r: (-r[1], r[0]))[
                    :MAX_REASONS
                ]
            ],
        )
        for code, count in sorted(raised.items(), key=lambda item: (-item[1], item[0]))
    ]


def _figures(passages: list[_Passage]) -> ReviewFigures:
    total = len(passages)
    checked = sum(p.checked for p in passages)
    edited = [p for p in passages if p.edited]
    distance = sum(p.distance or 0 for p in edited)
    return ReviewFigures(
        passages=total,
        checked=checked,
        checked_share=_share(checked, total),
        edited=len(edited),
        edited_share=_share(len(edited), total),
        edit_distance_total=distance,
        mean_edit_distance=_share(distance, len(edited)),
        mean_edit_ratio=_share(sum(p.ratio for p in edited), len(edited)),
        estimated_distances=sum(not p.exact for p in edited),
        flags=_flag_figures(passages),
    )


async def _measured(s: AsyncSession, run_ids: Sequence[uuid.UUID]) -> list[_Passage]:
    passages = await _passages(s, run_ids)
    await asyncio.to_thread(_measure, passages)
    return passages


async def run_summary(db: Database, run_id: uuid.UUID) -> RunReviewSummary:
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        doc = await runs.get_document(s, run.document_id)
        passages = await _measured(s, [run_id])
    return RunReviewSummary(
        run_id=str(run_id),
        filename=doc.filename,
        target_lang=run.target_lang,
        **dict(_figures(passages)),
    )


@dataclass(frozen=True)
class _Counted:
    run_id: uuid.UUID
    filename: str
    target_lang: str


async def _newest_runs(s: AsyncSession, project_id: uuid.UUID) -> dict[str, list[_Counted]]:
    """Each language of the set (in the set's order, its own spelling) with the newest run of
    each file in it, files in upload order: what the set page shows."""
    project = await runs.get_project(s, project_id)
    languages = await runs.set_languages(s, project)
    spelling = {runs.language_key(lang): lang for lang in languages}
    rows = await s.execute(
        select(Run.id, Run.target_lang, Run.document_id, Document.filename)
        .join(Document, Run.document_id == Document.id)
        .where(Document.project_id == project_id)
        .order_by(Document.created_at, Document.id, Run.created_at.desc(), Run.id.desc())
    )
    seen: set[tuple[uuid.UUID, str]] = set()
    counted: dict[str, list[_Counted]] = {lang: [] for lang in languages}
    for run_id, target_lang, document_id, filename in rows:
        key = runs.language_key(target_lang)
        if (document_id, key) in seen:
            continue
        seen.add((document_id, key))
        lang = spelling.get(key, target_lang)
        counted.setdefault(lang, []).append(_Counted(run_id, filename, lang))
    return counted


async def set_summary(db: Database, project_id: uuid.UUID) -> SetReviewSummary:
    async with db.session() as s:
        counted = await _newest_runs(s, project_id)
        passages = await _measured(s, [c.run_id for listed in counted.values() for c in listed])
    by_run: dict[uuid.UUID, list[_Passage]] = defaultdict(list)
    for p in passages:
        by_run[p.run_id].append(p)
    return SetReviewSummary(
        project_id=str(project_id),
        languages=[
            LanguageReviewSummary(
                target_lang=lang,
                run_ids=[str(c.run_id) for c in listed],
                **dict(_figures([p for c in listed for p in by_run[c.run_id]])),
            )
            for lang, listed in counted.items()
        ],
    )


# A cell starting with one of these is a formula to Excel, LibreOffice and Google Sheets.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
CSV_HEADER = [
    "file",
    "language",
    "passage",
    "flags",
    "dismiss_reasons",
    "checked",
    "edited",
    "edit_distance",
    "edit_ratio",
    "distance_estimated",
    "reviewers",
]


def csv_cell(text: str) -> str:
    """`text` as a spreadsheet shows it rather than runs it: a leading apostrophe defuses a
    cell that would start a formula (CSV injection)."""
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _csv(rows: list[tuple[_Counted, _Passage]]) -> bytes:
    """One row per passage, UTF-8 with a byte order mark so Excel reads the accents (as the
    bench's A/B sheet)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_HEADER)
    for counted, p in rows:
        flags = "; ".join(f"{f.code} ({f.status})" for f in p.flags)
        reasons = "; ".join(f"{f.code}: {f.reason}" for f in p.flags if f.reason)
        cells = [
            counted.filename,
            counted.target_lang,
            f"p{p.number}",
            flags,
            reasons,
            _yes(p.checked),
            _yes(p.edited),
            "" if p.distance is None else str(p.distance),
            "" if p.distance is None else f"{p.ratio:.3f}",
            "" if p.exact else "yes",
            "; ".join(p.reviewers),
        ]
        writer.writerow([csv_cell(c) for c in cells])
    return out.getvalue().encode("utf-8-sig")


def _stem(name: str) -> str:
    return PurePath(name).stem or "translation"


async def run_csv(db: Database, run_id: uuid.UUID) -> tuple[str, bytes]:
    """The file name and the CSV of one run's passages."""
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        doc = await runs.get_document(s, run.document_id)
        passages = await _measured(s, [run_id])
    counted = _Counted(run_id, doc.filename, run.target_lang)
    filename = f"{_stem(doc.filename)}.{run.target_lang}.review-summary.csv"
    return filename, _csv([(counted, p) for p in passages])


async def set_csv(
    db: Database, project_id: uuid.UUID, *, target_lang: str | None = None
) -> tuple[str, bytes]:
    """The file name and the CSV of the passages the set summary counts, language by language;
    `target_lang` keeps one language."""
    async with db.session() as s:
        project = await runs.get_project(s, project_id)
        counted = await _newest_runs(s, project_id)
        if target_lang is not None:
            wanted = runs.language_key(target_lang)
            counted = {k: v for k, v in counted.items() if runs.language_key(k) == wanted}
        listed = [c for cs in counted.values() for c in cs]
        passages = await _measured(s, [c.run_id for c in listed])
    by_run: dict[uuid.UUID, list[_Passage]] = defaultdict(list)
    for p in passages:
        by_run[p.run_id].append(p)
    rows = [(c, p) for c in listed for p in by_run[c.run_id]]
    name = project.name or "translation"
    if target_lang is not None:
        name = f"{name}.{next(iter(counted), target_lang)}"
    return f"{name}.review-summary.csv", _csv(rows)
