"""Data access for what chunks produce (spec §5: candidate, span, span_score, flag, revision)
and for the sweep's changes. A span's current text is its latest revision."""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from coglossa.schema import (
    Agreement,
    Candidate,
    Flag,
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    JudgeError,
    Reliability,
    Revision,
    RevisionSource,
    Span,
    SpanFormatting,
    SpanScore,
)
from coglossa.stages.chunk import ChunkOutcome
from coglossa.stages.sweep import SWEPT, SpanChange
from coglossa.store.models import CandidateRow, FlagRow, RevisionRow, SpanRow, SpanScoreRow


async def save_chunk_outcome(
    s: AsyncSession, run_id: uuid.UUID, chunk_ordinal: int, outcome: ChunkOutcome
) -> None:
    for c in outcome.candidates:
        s.add(
            CandidateRow(
                run_id=run_id,
                chunk_id=c.chunk_id,
                chunk_ordinal=chunk_ordinal,
                translator=c.translator,
                model=c.model,
                seg_start=c.start,
                seg_end=c.end,
                text=c.text,
                notes=c.notes,
            )
        )
    rows: list[SpanRow] = []
    for k, (span, terms) in enumerate(zip(outcome.spans, outcome.proposed_terms, strict=True)):
        row = SpanRow(
            id=uuid.uuid4(),
            run_id=run_id,
            chunk_id=outcome.chunk_id,
            chunk_ordinal=chunk_ordinal,
            ordinal=k,
            seg_start=span.start,
            seg_end=span.end,
            agreement=span.agreement.value,
            chosen_from=span.chosen_from,
            rationale=span.rationale,
            issues=list(span.issues),
            judge_errors=(
                [e.model_dump(mode="json") for e in span.judge_errors]
                if span.judge_errors is not None
                else None
            ),
            back_translation=span.back_translation,
            proposed_terms=[t.model_dump(mode="json") for t in terms],
            formatting=(
                span.formatting.model_dump(mode="json") if span.formatting is not None else None
            ),
        )
        rows.append(row)
        s.add(row)
    # The span rows must exist before the rows that reference them (no relationship() orders it).
    await s.flush()
    for row, span in zip(rows, outcome.spans, strict=True):
        s.add(
            SpanScoreRow(
                span_id=row.id,
                signals=dict(span.score.signals),
                meaning=span.score.meaning,
                style=span.score.style,
                reliability=span.score.reliability.value,
            )
        )
        for f in span.flags:
            s.add(
                FlagRow(
                    span_id=row.id,
                    code=f.code.value,
                    explanation=f.explanation,
                    evidence=f.model_dump(mode="json")["evidence"],
                    status=f.status.value,
                    dismiss_reason=f.dismiss_reason,
                )
            )
        s.add(
            RevisionRow(
                span_id=row.id,
                source=RevisionSource.ENGINE.value,
                before=None,
                after=span.text,
                reason="reconciled translation",
            )
        )
    await s.flush()


async def delete_chunk_outputs(s: AsyncSession, run_id: uuid.UUID, chunk_ids: list[str]) -> None:
    await s.execute(
        delete(CandidateRow).where(
            CandidateRow.run_id == run_id, CandidateRow.chunk_id.in_(chunk_ids)
        )
    )
    await s.execute(
        delete(SpanRow).where(SpanRow.run_id == run_id, SpanRow.chunk_id.in_(chunk_ids))
    )


@dataclass
class StoredSpans:
    spans: list[Span]  # reading order, ids p1..pN, text = latest revision
    row_ids: dict[str, uuid.UUID]  # report span id -> span row id
    proposed_terms: list[GlossaryEntry]  # reading order


def _run_spans(run_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    return select(SpanRow.id).where(SpanRow.run_id == run_id)


async def load_spans(s: AsyncSession, run_id: uuid.UUID) -> StoredSpans:
    rows = list(
        await s.scalars(
            select(SpanRow)
            .where(SpanRow.run_id == run_id)
            .order_by(SpanRow.chunk_ordinal, SpanRow.ordinal)
        )
    )
    scores = {
        r.span_id: r
        for r in await s.scalars(
            select(SpanScoreRow).where(SpanScoreRow.span_id.in_(_run_spans(run_id)))
        )
    }
    flags: dict[uuid.UUID, list[FlagRow]] = defaultdict(list)
    for f in await s.scalars(
        select(FlagRow).where(FlagRow.span_id.in_(_run_spans(run_id))).order_by(FlagRow.id)
    ):
        flags[f.span_id].append(f)
    newest = (
        select(func.max(RevisionRow.id).label("id"))
        .where(RevisionRow.span_id.in_(_run_spans(run_id)))
        .group_by(RevisionRow.span_id)
        .subquery()
    )
    latest = {
        span_id: after
        for span_id, after in (
            await s.execute(
                select(RevisionRow.span_id, RevisionRow.after).join(
                    newest, RevisionRow.id == newest.c.id
                )
            )
        ).all()
    }
    spans: list[Span] = []
    row_ids: dict[str, uuid.UUID] = {}
    proposed: list[GlossaryEntry] = []
    for n, row in enumerate(rows, start=1):
        score = scores[row.id]
        sid = f"p{n}"
        row_ids[sid] = row.id
        spans.append(
            Span(
                id=sid,
                chunk_id=row.chunk_id,
                start=row.seg_start,
                end=row.seg_end,
                text=latest[row.id],
                agreement=Agreement(row.agreement),
                chosen_from=row.chosen_from,
                rationale=row.rationale,
                issues=list(row.issues),
                score=SpanScore(
                    signals=dict(score.signals),
                    meaning=score.meaning,
                    style=score.style,
                    reliability=Reliability(score.reliability),
                ),
                flags=[
                    Flag(
                        code=FlagCode(f.code),
                        explanation=f.explanation,
                        evidence=dict(f.evidence),
                        status=FlagStatus(f.status),
                        dismiss_reason=f.dismiss_reason,
                    )
                    for f in flags[row.id]
                ],
                judge_errors=(
                    [JudgeError.model_validate(e) for e in row.judge_errors]
                    if row.judge_errors is not None
                    else None
                ),
                back_translation=row.back_translation,
                formatting=(
                    SpanFormatting.model_validate(row.formatting)
                    if row.formatting is not None
                    else None
                ),
            )
        )
        proposed.extend(GlossaryEntry.model_validate(t) for t in row.proposed_terms)
    return StoredSpans(spans=spans, row_ids=row_ids, proposed_terms=proposed)


async def apply_sweep(
    s: AsyncSession, row_ids: dict[str, uuid.UUID], changes: list[SpanChange]
) -> None:
    for c in changes:
        span_row = row_ids[c.span_id]
        s.add(
            RevisionRow(
                span_id=span_row,
                source=RevisionSource.SWEEP.value,
                before=c.before,
                after=c.after,
                reason=c.reason,
            )
        )
        await s.execute(
            update(FlagRow)
            .where(
                FlagRow.span_id == span_row,
                FlagRow.code.in_([code.value for code in c.resolved]),
                FlagRow.status == FlagStatus.OPEN.value,
            )
            .values(status=FlagStatus.RESOLVED.value)
        )
        for f in c.raised:
            s.add(
                FlagRow(
                    span_id=span_row,
                    code=f.code.value,
                    explanation=f.explanation,
                    evidence=f.model_dump(mode="json")["evidence"],
                    status=f.status.value,
                    dismiss_reason=f.dismiss_reason,
                )
            )
    await s.flush()


async def undo_sweep(s: AsyncSession, run_id: uuid.UUID) -> None:
    await s.execute(
        delete(RevisionRow).where(
            RevisionRow.span_id.in_(_run_spans(run_id)),
            RevisionRow.source == RevisionSource.SWEEP.value,
        )
    )
    await s.execute(
        delete(FlagRow).where(
            FlagRow.span_id.in_(_run_spans(run_id)),
            FlagRow.evidence.contains(SWEPT),
        )
    )
    await s.execute(
        update(FlagRow)
        .where(
            FlagRow.span_id.in_(_run_spans(run_id)),
            FlagRow.status == FlagStatus.RESOLVED.value,
        )
        .values(status=FlagStatus.OPEN.value)
    )


async def load_candidates(s: AsyncSession, run_id: uuid.UUID) -> list[Candidate]:
    rows = await s.scalars(
        select(CandidateRow)
        .where(CandidateRow.run_id == run_id)
        .order_by(CandidateRow.chunk_ordinal, CandidateRow.id)
    )
    return [
        Candidate(
            chunk_id=r.chunk_id,
            translator=r.translator,
            model=r.model,
            start=r.seg_start,
            end=r.seg_end,
            text=r.text,
            notes=r.notes,
        )
        for r in rows
    ]


async def load_revisions(
    s: AsyncSession, run_id: uuid.UUID, ids_by_row: dict[uuid.UUID, str]
) -> list[Revision]:
    rows = await s.scalars(
        select(RevisionRow)
        .where(
            RevisionRow.span_id.in_(_run_spans(run_id)),
            RevisionRow.source != RevisionSource.ENGINE.value,
        )
        .order_by(RevisionRow.id)
    )
    return [
        Revision(
            span_id=ids_by_row[r.span_id],
            source=RevisionSource(r.source),
            before=r.before,
            after=r.after,
            reason=r.reason,
        )
        for r in rows
    ]
