import uuid

import pytest
from sqlalchemy import update

from coglossa import review, service
from coglossa.schema import FlagCode, FlagStatus, KeepReason, Reliability, RevisionSource
from coglossa.store.models import CandidateRow, FlagRow, RevisionRow, SpanRow
from coglossa.store.review import SpanFilter, segment_range
from coglossa.store.runs import NotFoundError
from coglossa.testing.seed import seed_run


async def _numbers(db, run_id, **filters):
    page = await review.span_page(db, run_id, SpanFilter(**filters))
    return [s.number for s in page.spans]


async def test_page_of_all_spans(db):
    seeded = await seed_run(db)
    page = await review.span_page(db, seeded.run_id)
    assert (page.total, page.offset, page.limit) == (3, 0, 100)
    assert [(s.number, s.source, s.text) for s in page.spans] == [
        (1, "Chapitre un", "Chapter One"),
        (2, "Le chat dort. Il pleut.", "The cat sleeps. It rains."),
        (3, "Fin.", "End."),
    ]
    p2 = page.spans[1]
    assert p2.id == str(seeded.span_ids[1])
    assert (p2.start, p2.end, p2.chunk_id, p2.agreement.value) == ("s2", "s3", "c1", "major")
    assert [(f.id, f.code, f.status) for f in p2.flags] == [
        (seeded.flag_ids["p2:low_meaning"], FlagCode.LOW_MEANING, FlagStatus.OPEN),
        (seeded.flag_ids["p2:agent_disagreement"], FlagCode.AGENT_DISAGREEMENT, FlagStatus.OPEN),
    ]
    assert p2.score.meaning == 55.0 and p2.score.reliability is Reliability.B
    assert page.spans[2].flags[0].evidence == {"term": "Fin", "expected": "The End"}
    assert not any(s.edited for s in page.spans)


@pytest.mark.parametrize(
    ("filters", "numbers"),
    [
        ({"flagged": True}, [2, 3]),
        ({"flagged": False}, [1]),
        ({"code": FlagCode.LOW_MEANING}, [2]),
        ({"code": FlagCode.TERMINOLOGY_VIOLATION}, [3]),
        ({"code": FlagCode.STYLE_DRIFT}, []),
        ({"min_meaning": 60}, [1]),
        ({"max_meaning": 55}, [2]),
        ({"min_meaning": 55, "max_meaning": 92}, [1, 2]),
        ({"min_style": 80}, [1]),
        ({"max_style": 75, "flagged": True}, [2]),
    ],
)
async def test_filters(db, filters, numbers):
    seeded = await seed_run(db)
    assert await _numbers(db, seeded.run_id, **filters) == numbers


async def test_only_open_flags_count(db):
    seeded = await seed_run(db)
    async with db.session() as s, s.begin():
        await s.execute(
            update(FlagRow)
            .where(FlagRow.id == seeded.flag_ids["p3:terminology_violation"])
            .values(status=FlagStatus.DISMISSED.value, dismiss_reason="fine")
        )
    assert await _numbers(db, seeded.run_id, flagged=True) == [2]
    assert await _numbers(db, seeded.run_id, code=FlagCode.TERMINOLOGY_VIOLATION) == []
    page = await review.span_page(db, seeded.run_id, SpanFilter(flagged=False))
    assert page.spans[1].flags[0].status is FlagStatus.DISMISSED  # still shown on the span


async def test_paging_keeps_global_numbers(db):
    seeded = await seed_run(db)
    page = await review.span_page(db, seeded.run_id, offset=1, limit=1)
    assert (page.total, [s.number for s in page.spans]) == (3, [2])
    flagged = await review.span_page(db, seeded.run_id, SpanFilter(flagged=True), offset=1, limit=5)
    assert (flagged.total, [s.number for s in flagged.spans]) == (2, [3])
    beyond = await review.span_page(db, seeded.run_id, offset=10)
    assert (beyond.total, beyond.spans) == (3, [])


@pytest.mark.parametrize(("offset", "limit"), [(-1, 10), (0, 0), (0, 501)])
async def test_bad_paging(db, offset, limit):
    seeded = await seed_run(db)
    with pytest.raises(service.InvalidRequestError, match="limit between 1 and 500"):
        await review.span_page(db, seeded.run_id, offset=offset, limit=limit)


async def test_latest_revision_and_edited(db):
    seeded = await seed_run(db)
    async with db.session() as s, s.begin():
        s.add(
            RevisionRow(
                span_id=seeded.span_ids[0],
                source=RevisionSource.USER.value,
                before="Chapter One",
                after="Chapter 1",
                reason="house style",
            )
        )
    page = await review.span_page(db, seeded.run_id)
    assert (page.spans[0].text, page.spans[0].edited) == ("Chapter 1", True)
    assert page.spans[1].edited is False


async def test_a_passage_left_as_written_says_so(db):
    """CGL-111: the review screen is told which passages were left as written; seeded spans, like
    those of runs from before, were all translated."""
    seeded = await seed_run(db)
    page = await review.span_page(db, seeded.run_id)
    assert [s.kept for s in page.spans] == [None, None, None]
    async with db.session() as s, s.begin():
        await s.execute(
            update(SpanRow).where(SpanRow.id == seeded.span_ids[2]).values(kept="no_translate")
        )
    page = await review.span_page(db, seeded.run_id)
    assert [s.kept for s in page.spans] == [None, None, KeepReason.NO_TRANSLATE]
    detail = await review.span_detail(db, seeded.run_id, seeded.span_ids[2])
    assert detail.kept is KeepReason.NO_TRANSLATE


async def test_span_detail(db):
    seeded = await seed_run(db)
    detail = await review.span_detail(db, seeded.run_id, seeded.span_ids[1])
    assert (detail.number, detail.chosen_from, detail.issues) == (2, "merged", ["tense"])
    assert detail.rationale == "Merged the literal tense with the contextual rhythm."
    assert [(c.translator, c.start, c.end, c.text) for c in detail.candidates] == [
        ("literal", "s2", "s2", "The cat sleeps."),
        ("literal", "s3", "s3", "It is raining."),
        ("contextual", "s2", "s3", "The cat is asleep. It rains."),
    ]
    assert [(r.source, r.before, r.after) for r in detail.revisions] == [
        (RevisionSource.ENGINE, None, "The cat sleeps. It rains.")
    ]
    assert detail.judge_errors[0].excerpt == "sleeps"
    assert detail.back_translation == "Le chat dort. Il pleut."
    p3 = await review.span_detail(db, seeded.run_id, seeded.span_ids[2])
    assert p3.judge_errors is None and p3.back_translation is None
    assert [c.text for c in p3.candidates] == ["End.", "The End."]


async def test_candidates_that_straddle_the_span_are_included(db):
    seeded = await seed_run(db)
    async with db.session() as s, s.begin():
        s.add(
            CandidateRow(
                run_id=seeded.run_id,
                chunk_id="c1",
                chunk_ordinal=0,
                translator="extra",
                model="m",
                seg_start="s1",
                seg_end="s2",
                text="Chapter One. The cat sleeps.",
                notes="",
            )
        )
    detail = await review.span_detail(db, seeded.run_id, seeded.span_ids[1])
    assert [c.translator for c in detail.candidates][-1] == "extra"
    p3 = await review.span_detail(db, seeded.run_id, seeded.span_ids[2])
    assert "extra" not in [c.translator for c in p3.candidates]


async def test_missing_runs_and_spans(db):
    seeded = await seed_run(db)
    other = await seed_run(db, project="other")
    with pytest.raises(NotFoundError, match="run .* not found"):
        await review.span_page(db, uuid.uuid4())
    with pytest.raises(NotFoundError, match="not found in run"):
        await review.span_detail(db, seeded.run_id, other.span_ids[0])
    with pytest.raises(NotFoundError, match="not found in run"):
        await review.span_detail(db, seeded.run_id, uuid.uuid4())


def test_segment_range():
    assert segment_range("s9", "s11") == ["s9", "s10", "s11"]
    assert segment_range("s4", "s4") == ["s4"]
    with pytest.raises(ValueError, match="not a segment id"):
        segment_range("x1", "s2")
