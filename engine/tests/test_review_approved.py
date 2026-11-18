"""CGL-89: the passages a person accepted or edited, with the text they approved."""

from datetime import timedelta

from sqlalchemy import func, select

from coglossa import review
from coglossa.schema import FlagStatus, RevisionSource
from coglossa.store.models import ReviewEvent, RevisionRow
from coglossa.testing.seed import seed_run


async def test_nothing_reviewed_means_nothing_approved(db):
    seeded = await seed_run(db)
    await review.set_flag_status(
        db,
        seeded.run_id,
        seeded.flag_ids["p3:terminology_violation"],
        FlagStatus.DISMISSED,
        reason="fine",
    )
    await review.set_flag_status(
        db, seeded.run_id, seeded.flag_ids["p2:low_meaning"], FlagStatus.ACCEPTED
    )
    # a decision on one flag is not a decision on the passage's text
    assert await review.approved_texts(db, seeded.run_id) == {}


async def test_accepted_edited_and_chosen_passages(db):
    seeded = await seed_run(db)
    p1, p2, p3 = seeded.span_ids
    await review.edit_span(db, seeded.run_id, p1, "Chapter 1")
    await review.use_candidate(db, seeded.run_id, p2, "contextual")
    await review.accept_span(db, seeded.run_id, p3)
    assert await review.approved_texts(db, seeded.run_id) == {
        "p1": "Chapter 1",
        "p2": "The cat is asleep. It rains.",
        "p3": "End.",
    }


async def test_choosing_the_text_already_there_approves_it(db):
    seeded = await seed_run(db)
    await review.use_candidate(db, seeded.run_id, seeded.span_ids[0], "contextual")
    assert await review.approved_texts(db, seeded.run_id) == {"p1": "Chapter One"}


async def test_the_latest_action_wins(db):
    seeded = await seed_run(db)
    p1 = seeded.span_ids[0]
    await review.accept_span(db, seeded.run_id, p1)
    await review.edit_span(db, seeded.run_id, p1, "Chapter 1")
    await review.edit_span(db, seeded.run_id, p1, "Chapter I")
    assert await review.approved_texts(db, seeded.run_id) == {"p1": "Chapter I"}


async def test_a_later_change_nobody_approved_is_left_out(db):
    """A re-run sweep can change a passage after a person accepted or edited it: the memory
    keeps the text the person approved, not the sweep's."""
    seeded = await seed_run(db)
    p1, p2 = seeded.span_ids[:2]
    await review.edit_span(db, seeded.run_id, p1, "Chapter 1")
    await review.accept_span(db, seeded.run_id, p2)
    async with db.session() as s, s.begin():
        events = list(await s.scalars(select(ReviewEvent).order_by(ReviewEvent.id)))
        after = max(e.at for e in events) + timedelta(seconds=1)
        for span_id, text in ((p1, "Chapter One"), (p2, "The cat sleeps. It is raining.")):
            s.add(
                RevisionRow(
                    span_id=span_id,
                    source=RevisionSource.SWEEP.value,
                    before=None,
                    after=text,
                    reason="locked term",
                    created_at=after,
                )
            )
    assert await review.approved_texts(db, seeded.run_id) == {
        "p1": "Chapter 1",
        "p2": "The cat sleeps. It rains.",
    }


async def test_an_accept_older_than_every_revision_approves_nothing(db):
    """Clocks only move forward, but if one did not, no text is known to be the accepted one."""
    seeded = await seed_run(db)
    async with db.session() as s, s.begin():
        first = await s.scalar(select(RevisionRow.created_at).order_by(RevisionRow.id).limit(1))
        s.add(
            ReviewEvent(
                run_id=seeded.run_id,
                span_id=seeded.span_ids[0],
                action="accept",
                payload={"user": None, "flags": []},
                at=first - timedelta(days=1),
            )
        )
    assert await review.approved_texts(db, seeded.run_id) == {}


async def test_an_accept_keeps_the_text_it_accepted(db):
    """The accept records its text, so a sweep revision stamped earlier by a clock that runs
    ahead (another machine's, say) cannot pass for the text the person accepted."""
    seeded = await seed_run(db)
    p1 = seeded.span_ids[0]
    await review.accept_span(db, seeded.run_id, p1)
    async with db.session() as s, s.begin():
        event = await s.scalar(select(ReviewEvent).where(ReviewEvent.action == "accept"))
        assert event.payload["text"] == "Chapter One"
        s.add(
            RevisionRow(
                span_id=p1,
                source=RevisionSource.SWEEP.value,
                before="Chapter One",
                after="Chapter 1",
                reason="locked term",
                created_at=event.at - timedelta(seconds=5),
            )
        )
    assert await review.approved_texts(db, seeded.run_id) == {"p1": "Chapter One"}


async def test_an_accept_recorded_without_its_text_takes_the_text_it_saw(db):
    """Accepts recorded before they kept their text: the latest revision no later than it."""
    seeded = await seed_run(db)
    p1 = seeded.span_ids[0]
    await review.edit_span(db, seeded.run_id, p1, "Chapter 1")
    async with db.session() as s, s.begin():
        last = await s.scalar(select(func.max(RevisionRow.created_at)))
        s.add(
            ReviewEvent(
                run_id=seeded.run_id,
                span_id=p1,
                action="accept",
                payload={"user": None, "flags": []},
                at=last + timedelta(seconds=1),
            )
        )
        s.add(
            RevisionRow(
                span_id=p1,
                source=RevisionSource.SWEEP.value,
                before="Chapter 1",
                after="Chapter I",
                reason="locked term",
                created_at=last + timedelta(seconds=2),
            )
        )
    assert await review.approved_texts(db, seeded.run_id) == {"p1": "Chapter 1"}


async def test_reopening_a_flag_withdraws_the_approval(db):
    """A flag reopened after the passage was checked puts it back among those to check, so it
    leaves the checked-only memory until a person approves it again."""
    seeded = await seed_run(db)
    p2, p3 = seeded.span_ids[1:]
    await review.accept_span(db, seeded.run_id, p2)
    await review.edit_span(db, seeded.run_id, p3, "The end.")
    for flag in ("p2:low_meaning", "p3:terminology_violation"):
        await review.set_flag_status(db, seeded.run_id, seeded.flag_ids[flag], FlagStatus.OPEN)
    assert await review.approved_texts(db, seeded.run_id) == {}
    await review.accept_span(db, seeded.run_id, p2)
    assert await review.approved_texts(db, seeded.run_id) == {"p2": "The cat sleeps. It rains."}
