"""Data access for the review view (spec §8.4): statistics, span pages, flags, revisions and
the review_event feedback log (spec §6.5). Functions take an AsyncSession and never commit:
the caller owns the transaction."""

import re
import uuid
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, exists, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from coglossa.schema import FlagCode, FlagStatus, RevisionSource
from coglossa.schema.views import ReviewAction, ReviewStats
from coglossa.store.models import (
    CandidateRow,
    FlagRow,
    ReviewEvent,
    RevisionRow,
    SegmentRow,
    SpanRow,
    SpanScoreRow,
)
from coglossa.store.runs import NotFoundError


def run_span_ids(run_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    return select(SpanRow.id).where(SpanRow.run_id == run_id)


async def review_stats(s: AsyncSession, run_id: uuid.UUID) -> ReviewStats:
    spans = await s.scalar(select(func.count()).where(SpanRow.run_id == run_id))
    open_by_code = dict(
        (
            await s.execute(
                select(FlagRow.code, func.count())
                .where(
                    FlagRow.span_id.in_(run_span_ids(run_id)),
                    FlagRow.status == FlagStatus.OPEN.value,
                )
                .group_by(FlagRow.code)
            )
        ).all()
    )
    edited = await s.scalar(
        select(func.count(func.distinct(RevisionRow.span_id))).where(
            RevisionRow.span_id.in_(run_span_ids(run_id)),
            RevisionRow.source == RevisionSource.USER.value,
        )
    )
    return ReviewStats(
        spans=spans or 0,
        open_flags=sum(open_by_code.values()),
        flags_by_code=dict(sorted(open_by_code.items())),
        edited_spans=edited or 0,
    )


async def has_scores(s: AsyncSession, run_id: uuid.UUID) -> bool:
    """Whether any span has a meaning or style score (spec §6.2; the unscored notice)."""
    scored = exists().where(
        SpanScoreRow.span_id.in_(run_span_ids(run_id)),
        SpanScoreRow.meaning.is_not(None) | SpanScoreRow.style.is_not(None),
    )
    return bool(await s.scalar(select(scored)))


_SEGMENT_ID = re.compile(r"^s(\d+)$")


def segment_number(segment_id: str) -> int:
    """Segment ids are s1, s2, … in reading order (M1's segmenter)."""
    match = _SEGMENT_ID.match(segment_id)
    if match is None:
        raise ValueError(f"{segment_id!r} is not a segment id")
    return int(match.group(1))


def segment_range(start: str, end: str) -> list[str]:
    return [f"s{n}" for n in range(segment_number(start), segment_number(end) + 1)]


@dataclass(frozen=True)
class SpanFilter:
    flagged: bool | None = None  # True: has an open flag; False: has none
    code: FlagCode | None = None  # has an open flag with this code
    min_meaning: float | None = None  # bounds are inclusive; null scores never match
    max_meaning: float | None = None
    min_style: float | None = None
    max_style: float | None = None


def _has_open_flag(code: FlagCode | None = None) -> ColumnElement[bool]:
    condition = exists().where(
        FlagRow.span_id == SpanRow.id, FlagRow.status == FlagStatus.OPEN.value
    )
    if code is not None:
        condition = condition.where(FlagRow.code == code.value)
    return condition


def _conditions(f: SpanFilter) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if f.flagged is not None:
        conditions.append(_has_open_flag() if f.flagged else ~_has_open_flag())
    if f.code is not None:
        conditions.append(_has_open_flag(f.code))
    if f.min_meaning is not None:
        conditions.append(SpanScoreRow.meaning >= f.min_meaning)
    if f.max_meaning is not None:
        conditions.append(SpanScoreRow.meaning <= f.max_meaning)
    if f.min_style is not None:
        conditions.append(SpanScoreRow.style >= f.min_style)
    if f.max_style is not None:
        conditions.append(SpanScoreRow.style <= f.max_style)
    return conditions


# The passage list's "To check": passages with an open flag (the web app asks for flagged=true).
TO_CHECK = SpanFilter(flagged=True)


def _matching(*columns: Any, filt: SpanFilter) -> Select[Any]:
    """`columns` over the spans `filt` matches. A span without a score row never matches, as in
    the passage list."""
    return (
        select(*columns)
        .select_from(SpanRow)
        .join(SpanScoreRow, SpanScoreRow.span_id == SpanRow.id)
        .where(*_conditions(filt))
    )


async def to_check_counts(s: AsyncSession, run_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many passages each run still has to check, in one query (CGL-73); a run with none is
    left out."""
    if not run_ids:
        return {}
    rows = await s.execute(
        _matching(SpanRow.run_id, func.count(), filt=TO_CHECK)
        .where(SpanRow.run_id.in_(run_ids))
        .group_by(SpanRow.run_id)
    )
    return {run_id: count for run_id, count in rows.all()}


@dataclass(frozen=True)
class SpanRecord:
    number: int  # 1-based position among all of the run's spans
    span: SpanRow
    score: SpanScoreRow


async def span_records(
    s: AsyncSession, run_id: uuid.UUID, filt: SpanFilter, *, offset: int, limit: int
) -> tuple[int, list[SpanRecord]]:
    conditions = _conditions(filt)
    numbered = (
        select(
            SpanRow.id.label("span_id"),
            func.row_number()
            .over(order_by=(SpanRow.chunk_ordinal, SpanRow.ordinal))
            .label("number"),
        )
        .where(SpanRow.run_id == run_id)
        .subquery()
    )
    total = await s.scalar(_matching(func.count(), filt=filt).where(SpanRow.run_id == run_id))
    rows = await s.execute(
        select(numbered.c.number, SpanRow, SpanScoreRow)
        .join(numbered, numbered.c.span_id == SpanRow.id)
        .join(SpanScoreRow, SpanScoreRow.span_id == SpanRow.id)
        .where(*conditions)
        .order_by(numbered.c.number)
        .offset(offset)
        .limit(limit)
    )
    return total or 0, [SpanRecord(number, span, score) for number, span, score in rows.all()]


async def span_record(s: AsyncSession, run_id: uuid.UUID, span_id: uuid.UUID) -> SpanRecord:
    row = (
        await s.execute(
            select(SpanRow, SpanScoreRow)
            .join(SpanScoreRow, SpanScoreRow.span_id == SpanRow.id)
            .where(SpanRow.id == span_id, SpanRow.run_id == run_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"span {span_id} not found in run {run_id}")
    span, score = row
    number = await s.scalar(
        select(func.count()).where(
            SpanRow.run_id == run_id,
            tuple_(SpanRow.chunk_ordinal, SpanRow.ordinal) <= (span.chunk_ordinal, span.ordinal),
        )
    )
    return SpanRecord(number or 0, span, score)


async def flags_of(
    s: AsyncSession, span_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[FlagRow]]:
    flags: dict[uuid.UUID, list[FlagRow]] = defaultdict(list)
    for flag in await s.scalars(
        select(FlagRow).where(FlagRow.span_id.in_(span_ids)).order_by(FlagRow.id)
    ):
        flags[flag.span_id].append(flag)
    return flags


async def latest_texts(s: AsyncSession, span_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
    newest = (
        select(func.max(RevisionRow.id))
        .where(RevisionRow.span_id.in_(span_ids))
        .group_by(RevisionRow.span_id)
    )
    rows = await s.execute(
        select(RevisionRow.span_id, RevisionRow.after).where(RevisionRow.id.in_(newest))
    )
    return dict(rows.all())


async def edited_spans(s: AsyncSession, span_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    rows = await s.scalars(
        select(RevisionRow.span_id)
        .where(RevisionRow.span_id.in_(span_ids), RevisionRow.source == RevisionSource.USER.value)
        .distinct()
    )
    return set(rows)


async def segment_texts(
    s: AsyncSession, document_id: uuid.UUID, segment_ids: Collection[str]
) -> dict[str, str]:
    rows = await s.execute(
        select(SegmentRow.segment_id, SegmentRow.text).where(
            SegmentRow.document_id == document_id, SegmentRow.segment_id.in_(list(segment_ids))
        )
    )
    return dict(rows.all())


async def chunk_candidates(s: AsyncSession, run_id: uuid.UUID, chunk_id: str) -> list[CandidateRow]:
    rows = await s.scalars(
        select(CandidateRow)
        .where(CandidateRow.run_id == run_id, CandidateRow.chunk_id == chunk_id)
        .order_by(CandidateRow.id)
    )
    return list(rows)


async def revisions_of(s: AsyncSession, span_id: uuid.UUID) -> list[RevisionRow]:
    rows = await s.scalars(
        select(RevisionRow).where(RevisionRow.span_id == span_id).order_by(RevisionRow.id)
    )
    return list(rows)


async def add_revision(
    s: AsyncSession, span_id: uuid.UUID, *, before: str, after: str, reason: str
) -> None:
    s.add(
        RevisionRow(
            span_id=span_id,
            source=RevisionSource.USER.value,
            before=before,
            after=after,
            reason=reason,
        )
    )
    await s.flush()


async def add_event(
    s: AsyncSession,
    run_id: uuid.UUID,
    span_id: uuid.UUID | None,
    action: ReviewAction,
    payload: dict[str, Any],
) -> None:
    s.add(ReviewEvent(run_id=run_id, span_id=span_id, action=action.value, payload=payload))
    await s.flush()


# CGL-89: the actions by which a person approves a passage's text. Accepting or dismissing one
# flag is a decision about that flag, not about the text, so it does not count; reopening one
# after the latest approval puts the passage back among those to check, so it withdraws it.
APPROVALS = (ReviewAction.ACCEPT, ReviewAction.EDIT, ReviewAction.USE_CANDIDATE)
_WITHDRAWS = ReviewAction.REOPEN_FLAG


async def approved_texts(s: AsyncSession, run_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """The text a person approved, by span row id, for every span a person accepted, edited or
    chose a candidate for, and has not reopened a flag on since. The latest such action counts:
    an edit or a choice approves the text it wrote (its event's `after`); an accept approves the
    text the passage had then (its event's `text`, or, for an accept recorded before accepts
    kept it, the latest revision written no later than the accept). A sweep re-run afterwards
    may have changed the passage since; that change was never approved, so it is not returned."""
    events = await s.scalars(
        select(ReviewEvent)
        .where(
            ReviewEvent.run_id == run_id,
            ReviewEvent.span_id.is_not(None),
            ReviewEvent.action.in_([a.value for a in (*APPROVALS, _WITHDRAWS)]),
        )
        .order_by(ReviewEvent.id)
    )
    latest: dict[uuid.UUID, ReviewEvent] = {}
    for event in events:  # in the order they happened, per passage
        if event.action == _WITHDRAWS.value:
            latest.pop(event.span_id, None)
        else:
            latest[event.span_id] = event
    texts: dict[uuid.UUID, str] = {}
    undated: list[tuple[uuid.UUID, ReviewEvent]] = []
    for span_id, event in latest.items():
        if event.action != ReviewAction.ACCEPT.value:
            texts[span_id] = event.payload["after"]
        elif "text" in event.payload:
            texts[span_id] = event.payload["text"]
        else:
            undated.append((span_id, event))
    if undated:
        history: dict[uuid.UUID, list[RevisionRow]] = defaultdict(list)
        for revision in await s.scalars(
            select(RevisionRow)
            .where(RevisionRow.span_id.in_(run_span_ids(run_id)))
            .order_by(RevisionRow.id)
        ):
            history[revision.span_id].append(revision)
        for span_id, event in undated:
            seen = [r.after for r in history[span_id] if r.created_at <= event.at]
            if seen:
                texts[span_id] = seen[-1]
    return texts


async def reading_order(s: AsyncSession, run_id: uuid.UUID) -> list[uuid.UUID]:
    """The run's span row ids in reading order, the order the report numbers p1, p2, …"""
    rows = await s.scalars(
        select(SpanRow.id)
        .where(SpanRow.run_id == run_id)
        .order_by(SpanRow.chunk_ordinal, SpanRow.ordinal)
    )
    return list(rows)


async def set_open_flags(s: AsyncSession, span_id: uuid.UUID, status: FlagStatus) -> list[int]:
    ids = await s.scalars(
        update(FlagRow)
        .where(FlagRow.span_id == span_id, FlagRow.status == FlagStatus.OPEN.value)
        .values(status=status.value)
        .returning(FlagRow.id)
    )
    return sorted(ids)


async def get_flag(s: AsyncSession, run_id: uuid.UUID, flag_id: int) -> FlagRow:
    flag = await s.scalar(
        select(FlagRow)
        .join(SpanRow, SpanRow.id == FlagRow.span_id)
        .where(FlagRow.id == flag_id, SpanRow.run_id == run_id)
    )
    if flag is None:
        raise NotFoundError(f"flag {flag_id} not found in run {run_id}")
    return flag
