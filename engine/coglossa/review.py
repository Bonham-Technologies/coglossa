"""The review view (spec §8.4): pages of spans with filters, one span's full detail, and (Task
11 of the M4a plan) the review actions. Spans are addressed by their row id; `number` is the
reading-order position the report calls p<number>."""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from coglossa import service
from coglossa.catalog import guide_view
from coglossa.schema import (
    Agreement,
    Candidate,
    Entity,
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    Guide,
    JudgeError,
    Reliability,
    RevisionSource,
    RunState,
    SpanScore,
)
from coglossa.schema.guide import tidy_forms
from coglossa.schema.views import (
    GuideView,
    ReviewAction,
    ReviewFlag,
    ReviewSpan,
    RevisionEntry,
    SpanDetail,
    SpanPage,
)
from coglossa.scoring.aggregate import terminology_explanation
from coglossa.scoring.rules import guide_check, term_in
from coglossa.store import review as review_store
from coglossa.store import runs
from coglossa.store.db import Database
from coglossa.store.models import CandidateRow, FlagRow, GuideVersion, Run, SpanRow
from coglossa.store.review import SpanFilter, SpanRecord, segment_number, segment_range
from coglossa.text import clean_text, entry_key

MAX_PAGE = 500


def _flag(row: FlagRow) -> ReviewFlag:
    return ReviewFlag(
        id=row.id,
        code=FlagCode(row.code),
        explanation=row.explanation,
        evidence=dict(row.evidence),
        status=FlagStatus(row.status),
        dismiss_reason=row.dismiss_reason,
    )


async def _review_spans(
    s: AsyncSession, document_id: uuid.UUID, records: Sequence[SpanRecord]
) -> list[ReviewSpan]:
    ids = [r.span.id for r in records]
    flags = await review_store.flags_of(s, ids)
    texts = await review_store.latest_texts(s, ids)
    edited = await review_store.edited_spans(s, ids)
    wanted = {seg for r in records for seg in segment_range(r.span.seg_start, r.span.seg_end)}
    sources = await review_store.segment_texts(s, document_id, wanted)
    spans = []
    for record in records:
        span, score = record.span, record.score
        segment_ids = segment_range(span.seg_start, span.seg_end)
        spans.append(
            ReviewSpan(
                id=str(span.id),
                number=record.number,
                chunk_id=span.chunk_id,
                start=span.seg_start,
                end=span.seg_end,
                source=" ".join(sources[i] for i in segment_ids if i in sources),
                text=texts[span.id],
                agreement=Agreement(span.agreement),
                score=SpanScore(
                    signals=dict(score.signals),
                    meaning=score.meaning,
                    style=score.style,
                    reliability=Reliability(score.reliability),
                ),
                flags=[_flag(f) for f in flags.get(span.id, [])],
                edited=span.id in edited,
            )
        )
    return spans


async def span_page(
    db: Database,
    run_id: uuid.UUID,
    filt: SpanFilter | None = None,
    *,
    offset: int = 0,
    limit: int = 100,
) -> SpanPage:
    if offset < 0 or not 1 <= limit <= MAX_PAGE:
        raise service.InvalidRequestError(f"offset must be ≥ 0 and limit between 1 and {MAX_PAGE}")
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        total, records = await review_store.span_records(
            s, run_id, filt or SpanFilter(), offset=offset, limit=limit
        )
        spans = await _review_spans(s, run.document_id, records)
    return SpanPage(total=total, offset=offset, limit=limit, spans=spans)


def _overlaps(candidate: CandidateRow, span: SpanRow) -> bool:
    return segment_number(candidate.seg_start) <= segment_number(span.seg_end) and segment_number(
        candidate.seg_end
    ) >= segment_number(span.seg_start)


async def _detail(s: AsyncSession, run_id: uuid.UUID, span_id: uuid.UUID) -> SpanDetail:
    run = await runs.get_run(s, run_id)
    record = await review_store.span_record(s, run_id, span_id)
    (base,) = await _review_spans(s, run.document_id, [record])
    span = record.span
    candidates = [
        Candidate(
            chunk_id=c.chunk_id,
            translator=c.translator,
            model=c.model,
            start=c.seg_start,
            end=c.seg_end,
            text=c.text,
            notes=c.notes,
        )
        for c in await review_store.chunk_candidates(s, run_id, span.chunk_id)
        if _overlaps(c, span)
    ]
    revisions = [
        RevisionEntry(
            source=RevisionSource(r.source),
            before=r.before,
            after=r.after,
            reason=r.reason,
            created_at=r.created_at,
        )
        for r in await review_store.revisions_of(s, span_id)
    ]
    return SpanDetail(
        **base.model_dump(),
        chosen_from=span.chosen_from,
        rationale=span.rationale,
        issues=list(span.issues),
        judge_errors=[JudgeError.model_validate(e) for e in span.judge_errors]
        if span.judge_errors is not None
        else None,
        back_translation=span.back_translation,
        candidates=candidates,
        revisions=revisions,
    )


async def span_detail(db: Database, run_id: uuid.UUID, span_id: uuid.UUID) -> SpanDetail:
    async with db.session() as s:
        return await _detail(s, run_id, span_id)


_REVIEWABLE = (RunState.COMPLETE, RunState.COMPLETE_WITH_ERRORS)


async def _writable(s: AsyncSession, run_id: uuid.UUID) -> Run:
    """The run, locked: review actions change only a complete run, whose report and exports
    show them, and never while its sweep (a re-sweep) runs."""
    run = await runs.get_run(s, run_id, lock=True)
    state = RunState(run.state)
    if state is RunState.SWEEPING:
        raise service.InvalidRunStateError(
            f"run {run_id} is running its consistency sweep; try again when it finishes"
        )
    if state not in _REVIEWABLE:
        raise service.InvalidRunStateError(
            f"run {run_id} is {state}; review actions are available once the run is complete"
        )
    return run


def _payload(user: str | None, **data: Any) -> dict[str, Any]:
    return {"user": user, **data}


_EMPTY = "the translation must not be empty"


async def _current_text(s: AsyncSession, span_id: uuid.UUID) -> str:
    return (await review_store.latest_texts(s, [span_id]))[span_id]


def _check_base(base: str | None, current: str, text: str) -> None:
    """`base`, when given, is the text the reviewer started from, exactly as the API sent it. A
    different current text means someone else changed the passage meanwhile: the change is
    refused rather than silently replacing theirs (CGL-36), unless it would change nothing."""
    if base is not None and base != current and text != current:
        raise service.PassageChangedError(current)


async def edit_span(
    db: Database,
    run_id: uuid.UUID,
    span_id: uuid.UUID,
    text: str,
    *,
    reason: str | None = None,
    base: str | None = None,
    user: str | None = None,
) -> SpanDetail:
    text = clean_text(text)
    if not text.strip():
        raise service.InvalidRequestError(_EMPTY)
    async with db.session() as s, s.begin():
        await _writable(s, run_id)
        await review_store.span_record(s, run_id, span_id)
        current = await _current_text(s, span_id)
        _check_base(base, current, text)
        if text != current:
            note = clean_text(reason or "").strip() or "edited by a reviewer"
            await review_store.add_revision(s, span_id, before=current, after=text, reason=note)
            await review_store.add_event(
                s,
                run_id,
                span_id,
                ReviewAction.EDIT,
                _payload(user, before=current, after=text, reason=note),
            )
        return await _detail(s, run_id, span_id)


def _tiles(pieces: list[CandidateRow], span: SpanRow) -> bool:
    if pieces[0].seg_start != span.seg_start or pieces[-1].seg_end != span.seg_end:
        return False
    return all(
        segment_number(nxt.seg_start) == segment_number(prev.seg_end) + 1
        for prev, nxt in zip(pieces, pieces[1:], strict=False)
    )


async def use_candidate(
    db: Database,
    run_id: uuid.UUID,
    span_id: uuid.UUID,
    translator: str,
    *,
    base: str | None = None,
    user: str | None = None,
) -> SpanDetail:
    async with db.session() as s, s.begin():
        await _writable(s, run_id)
        span = (await review_store.span_record(s, run_id, span_id)).span
        pieces = sorted(
            (
                c
                for c in await review_store.chunk_candidates(s, run_id, span.chunk_id)
                if c.translator == translator and _overlaps(c, span)
            ),
            key=lambda c: segment_number(c.seg_start),
        )
        if not pieces:
            raise service.InvalidRequestError(f"no '{translator}' candidate covers this passage")
        if not _tiles(pieces, span):
            raise service.ConflictError(
                f"the '{translator}' candidate's passage boundaries differ from this passage "
                f"({span.seg_start}–{span.seg_end}); copy its text and edit instead"
            )
        text = " ".join(clean_text(p.text).strip() for p in pieces)
        if not text.strip():
            raise service.InvalidRequestError(_EMPTY)
        current = await _current_text(s, span_id)
        _check_base(base, current, text)
        if text != current:
            await review_store.add_revision(
                s, span_id, before=current, after=text, reason=f"used the {translator} candidate"
            )
        await review_store.add_event(
            s,
            run_id,
            span_id,
            ReviewAction.USE_CANDIDATE,
            _payload(user, translator=translator, before=current, after=text),
        )
        return await _detail(s, run_id, span_id)


async def accept_span(
    db: Database, run_id: uuid.UUID, span_id: uuid.UUID, *, user: str | None = None
) -> SpanDetail:
    async with db.session() as s, s.begin():
        await _writable(s, run_id)
        await review_store.span_record(s, run_id, span_id)
        accepted = await review_store.set_open_flags(s, span_id, FlagStatus.ACCEPTED)
        # the text accepted, so a checked-only memory (CGL-89) need not date it by the clock
        text = await _current_text(s, span_id)
        await review_store.add_event(
            s, run_id, span_id, ReviewAction.ACCEPT, _payload(user, flags=accepted, text=text)
        )
        return await _detail(s, run_id, span_id)


_FLAG_ACTIONS = {
    FlagStatus.ACCEPTED: ReviewAction.ACCEPT_FLAG,
    FlagStatus.DISMISSED: ReviewAction.DISMISS_FLAG,
    FlagStatus.OPEN: ReviewAction.REOPEN_FLAG,
}


async def set_flag_status(
    db: Database,
    run_id: uuid.UUID,
    flag_id: int,
    status: FlagStatus,
    *,
    reason: str | None = None,
    user: str | None = None,
) -> SpanDetail:
    if status not in _FLAG_ACTIONS:
        raise service.InvalidRequestError(
            "only the sweep resolves flags; use accepted, dismissed or open"
        )
    reason = clean_text(reason or "").strip() or None
    if status is FlagStatus.DISMISSED and reason is None:
        raise service.InvalidRequestError("dismissing a flag needs a reason")
    async with db.session() as s, s.begin():
        await _writable(s, run_id)
        flag = await review_store.get_flag(s, run_id, flag_id)
        before = flag.status
        flag.status = status.value
        flag.dismiss_reason = reason if status is FlagStatus.DISMISSED else None
        await review_store.add_event(
            s,
            run_id,
            flag.span_id,
            _FLAG_ACTIONS[status],
            _payload(user, flag=flag_id, code=flag.code, before=before, reason=reason),
        )
        return await _detail(s, run_id, flag.span_id)


def _trimmed[E: GlossaryEntry | Entity](entry: E, key: str) -> E:
    """The entry with control characters cleaned from every text field, and its key attribute
    (`term` or `name`) and its rendering trimmed."""
    cleaned: dict[str, Any] = {
        name: clean_text(value) for name, value in entry if isinstance(value, str)
    }
    for name in ("forms", "source_forms"):  # CGL-109
        cleaned[name] = tidy_forms([clean_text(f) for f in getattr(entry, name)])
    cleaned[key], cleaned["rendering"] = cleaned[key].strip(), cleaned["rendering"].strip()
    return entry.model_copy(update=cleaned)


def _with_entry[E: GlossaryEntry | Entity](entries: list[E], entry: E, key: str) -> list[E]:
    """`entries` with `entry` replacing the one of the same key (case-insensitive), or appended."""
    wanted = entry_key(getattr(entry, key))
    if any(entry_key(getattr(e, key)) == wanted for e in entries):
        return [entry if entry_key(getattr(e, key)) == wanted else e for e in entries]
    return [*entries, entry]


async def add_guide_entry(
    db: Database,
    run_id: uuid.UUID,
    span_id: uuid.UUID,
    entry: GlossaryEntry | Entity,
    *,
    user: str | None = None,
) -> GuideView:
    """Spec §8.4 "add to guide": the entry joins the project's guide as a new approved
    version. The run keeps the version it was translated with; re-running the sweep applies
    new locked entries to it."""
    glossary = isinstance(entry, GlossaryEntry)
    key = "term" if glossary else "name"
    entry = _trimmed(entry, key)
    if not getattr(entry, key) or not entry.rendering:
        raise service.InvalidRequestError("a guide entry needs a term or name and a rendering")
    async with db.session() as s, s.begin():
        run = await _writable(s, run_id)
        await review_store.span_record(s, run_id, span_id)
        project_id, current = await _set_guide(s, run)
        if isinstance(entry, GlossaryEntry):
            guide = current.model_copy(
                update={"glossary": _with_entry(current.glossary, entry, "term")}
            )
        else:
            guide = current.model_copy(
                update={"entities": _with_entry(current.entities, entry, "name")}
            )
        row = await _add_version(s, run, project_id, guide)
        kind = "glossary" if glossary else "entity"
        await review_store.add_event(
            s,
            run_id,
            span_id,
            ReviewAction.ADD_TO_GUIDE,
            _payload(user, kind=kind, entry=entry.model_dump(mode="json"), version=row.version),
        )
        return guide_view(row)


async def _set_guide(s: AsyncSession, run: Run) -> tuple[uuid.UUID, Guide]:
    """The run's set guide as it is now, with the project locked for a new version of it."""
    doc = await runs.get_document(s, run.document_id)
    await runs.lock_project(s, doc.project_id)
    base = await runs.prior_guide(s, doc.project_id, run.target_lang)
    if base is None:
        raise service.InvalidRunStateError(
            "the project has no guide yet; wait for the run to build one"
        )
    return doc.project_id, Guide.model_validate(base.content)


async def _add_version(
    s: AsyncSession, run: Run, project_id: uuid.UUID, guide: Guide
) -> GuideVersion:
    return await runs.add_guide_version(
        s,
        project_id,
        guide,
        target_lang=run.target_lang,
        status=runs.APPROVED,
        author=runs.USER,
        source_run_id=run.id,
    )


def _accept_form[E: GlossaryEntry | Entity](
    entries: list[E], key: str, word: str, form: str
) -> list[E] | None:
    """`entries` with `form` among the forms of the entry for `word`; None without one."""
    wanted = entry_key(word)
    if not any(entry_key(getattr(e, key)) == wanted for e in entries):
        return None
    return [
        e.model_copy(update={"forms": tidy_forms([*e.forms, form])})
        if entry_key(getattr(e, key)) == wanted
        else e
        for e in entries
    ]


async def accept_form(
    db: Database,
    run_id: uuid.UUID,
    flag_id: int,
    term: str,
    form: str,
    *,
    user: str | None = None,
) -> SpanDetail:
    """CGL-109 "This form is fine": a reviewer dismisses a terminology flag because the passage
    uses an accepted form of the rendering. The form joins the entry for `term` (a term or a
    name the flag names) in a new approved version of the set guide, as "add to guide" does; the
    same flag on other passages clears when the set is rechecked (the sweep). A flag that also
    names another locked entry the passage still does not follow stays open, naming only the
    entries still not followed."""
    form = clean_text(form).strip()
    if not form:
        raise service.InvalidRequestError("name the form the passage uses")
    async with db.session() as s, s.begin():
        run = await _writable(s, run_id)
        flag = await review_store.get_flag(s, run_id, flag_id)
        open_flag = flag.status == FlagStatus.OPEN.value
        if flag.code != FlagCode.TERMINOLOGY_VIOLATION.value or not open_flag:
            raise service.InvalidRequestError("only an open terminology flag takes a form")
        named = [
            v
            for v in (flag.evidence or {}).get("violations", [])
            if isinstance(v, dict) and entry_key(str(v.get("term", ""))) == entry_key(term)
        ]
        if not named:
            raise service.InvalidRequestError(f"this flag is not about “{term}”")
        text = await _current_text(s, flag.span_id)
        if not term_in(form, text):
            raise service.InvalidRequestError(f"the passage does not use “{form}”")
        project_id, current = await _set_guide(s, run)
        if named[0].get("kind") == "entity":
            entities = _accept_form(current.entities, "name", term, form)
            guide = None if entities is None else current.model_copy(update={"entities": entities})
        else:
            glossary = _accept_form(current.glossary, "term", term, form)
            guide = None if glossary is None else current.model_copy(update={"glossary": glossary})
        if guide is None:
            raise service.InvalidRequestError(f"the translation guide no longer has “{term}”")
        version = None
        if guide != current:
            version = (await _add_version(s, run, project_id, guide)).version
        accepted = {"term": term, "form": form, "version": version}
        _, violations = guide_check(await _span_source(s, run, flag.span_id), text, guide)
        if still := [v for v in violations if v.locked]:
            # another locked entry is still not followed: the flag stays open, naming it alone
            flag.evidence = {
                **(flag.evidence or {}),
                "violations": [v.as_evidence() for v in still],
            }
            flag.explanation = terminology_explanation(still)
            await review_store.add_event(
                s,
                run_id,
                flag.span_id,
                ReviewAction.ADD_TO_GUIDE,
                _payload(user, kind="form", flag=flag_id, form=accepted),
            )
            return await _detail(s, run_id, flag.span_id)
        reason = f"This form is fine: “{form}”"
        flag.status, flag.dismiss_reason = FlagStatus.DISMISSED.value, reason
        await review_store.add_event(
            s,
            run_id,
            flag.span_id,
            ReviewAction.DISMISS_FLAG,
            _payload(
                user,
                flag=flag_id,
                code=flag.code,
                before=FlagStatus.OPEN.value,
                reason=reason,
                form=accepted,
            ),
        )
        return await _detail(s, run_id, flag.span_id)


async def _span_source(s: AsyncSession, run: Run, span_id: uuid.UUID) -> str:
    """The source text a span translates, its segments joined as the scorer and sweep join them."""
    span = await s.get(SpanRow, span_id)
    assert span is not None  # the flag's span
    segment_ids = segment_range(span.seg_start, span.seg_end)
    sources = await review_store.segment_texts(s, run.document_id, segment_ids)
    return " ".join(sources[i] for i in segment_ids if i in sources)


async def approved_texts(db: Database, run_id: uuid.UUID) -> dict[str, str]:
    """CGL-89: the passages a person accepted or edited, by report span id (p1, p2, …), with
    the text the person approved (`review_store.approved_texts`)."""
    async with db.session() as s:
        order = await review_store.reading_order(s, run_id)
        approved = await review_store.approved_texts(s, run_id)
    return {f"p{n}": approved[row] for n, row in enumerate(order, start=1) if row in approved}
