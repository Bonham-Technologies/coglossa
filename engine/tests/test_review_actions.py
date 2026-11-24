import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from coglossa import review, service
from coglossa.schema import Entity, FlagStatus, GlossaryEntry, Guide, RevisionSource, RunState
from coglossa.store import runs
from coglossa.store.models import CandidateRow, GuideVersion, ReviewEvent, RevisionRow
from coglossa.store.runs import NotFoundError
from coglossa.testing.seed import seed_run


async def _events(db, run_id):
    async with db.session() as s:
        rows = await s.scalars(
            select(ReviewEvent).where(ReviewEvent.run_id == run_id).order_by(ReviewEvent.id)
        )
        return [(e.action, e.span_id, e.payload) for e in rows]


async def test_edit_appends_a_user_revision_and_an_event(db):
    seeded = await seed_run(db)
    p2 = seeded.span_ids[1]
    detail = await review.edit_span(
        db, seeded.run_id, p2, "The cat is sleeping. It is raining.", reason="tense", user="ana"
    )
    assert detail.text == "The cat is sleeping. It is raining." and detail.edited
    assert [(r.source, r.reason) for r in detail.revisions] == [
        (RevisionSource.ENGINE, "reconciled translation"),
        (RevisionSource.USER, "tense"),
    ]
    assert detail.revisions[1].before == "The cat sleeps. It rains."
    assert await _events(db, seeded.run_id) == [
        (
            "edit",
            p2,
            {
                "user": "ana",
                "before": "The cat sleeps. It rains.",
                "after": "The cat is sleeping. It is raining.",
                "reason": "tense",
            },
        )
    ]


async def test_two_edits_both_stay_in_the_history(db):
    seeded = await seed_run(db)
    p1 = seeded.span_ids[0]
    await review.edit_span(db, seeded.run_id, p1, "Chapter 1")
    detail = await review.edit_span(db, seeded.run_id, p1, "Chapter I")
    assert detail.text == "Chapter I"
    assert [r.after for r in detail.revisions] == ["Chapter One", "Chapter 1", "Chapter I"]
    assert detail.revisions[1].reason == "edited by a reviewer"
    assert [e[2]["user"] for e in await _events(db, seeded.run_id)] == [None, None]


async def test_a_stale_edit_is_refused_and_the_first_edit_stays(db):
    # CGL-36: two reviewers start from the same text; the second save must not silently win
    seeded = await seed_run(db)
    p1 = seeded.span_ids[0]
    await review.edit_span(db, seeded.run_id, p1, "Chapter 1", base="Chapter One", user="ana")
    with pytest.raises(service.PassageChangedError) as e:
        await review.edit_span(db, seeded.run_id, p1, "Chapter I", base="Chapter One", user="li")
    assert e.value.code == "passage_changed" and e.value.current_text == "Chapter 1"
    assert "someone else changed this passage" in str(e.value)
    detail = await review.span_detail(db, seeded.run_id, p1)
    assert detail.text == "Chapter 1"
    assert [r.after for r in detail.revisions] == ["Chapter One", "Chapter 1"]
    assert [p["user"] for _, _, p in await _events(db, seeded.run_id)] == ["ana"]
    # starting again from the current text goes through; no base keeps the old behaviour
    kept = await review.edit_span(db, seeded.run_id, p1, "Chapter I", base="Chapter 1")
    assert kept.text == "Chapter I"
    assert (await review.edit_span(db, seeded.run_id, p1, "Chapter One")).text == "Chapter One"


async def test_a_base_is_compared_exactly_and_a_change_to_the_same_text_is_no_clash(db):
    seeded = await seed_run(db)
    p1 = seeded.span_ids[0]
    # engine text is stored as the model wrote it: a base echoed back exactly always matches
    async with db.session() as s, s.begin():
        row = await s.scalar(select(RevisionRow).where(RevisionRow.span_id == p1))
        row.after = "Chapter\x0cOne"
    detail = await review.edit_span(db, seeded.run_id, p1, "Chapter 1", base="Chapter\x0cOne")
    assert detail.text == "Chapter 1"
    # someone else already saved exactly this text: nothing to refuse, nothing written
    same = await review.edit_span(db, seeded.run_id, p1, "Chapter 1", base="Chapter\x0cOne")
    assert same.text == "Chapter 1" and len(same.revisions) == 2


async def test_a_stale_choice_of_candidate_is_refused(db):
    seeded = await seed_run(db)
    p2 = seeded.span_ids[1]
    await review.edit_span(db, seeded.run_id, p2, "The cat naps.")
    with pytest.raises(service.PassageChangedError) as e:
        await review.use_candidate(
            db, seeded.run_id, p2, "contextual", base="The cat sleeps. It rains."
        )
    assert e.value.current_text == "The cat naps."
    assert (await review.span_detail(db, seeded.run_id, p2)).text == "The cat naps."
    used = await review.use_candidate(db, seeded.run_id, p2, "contextual", base="The cat naps.")
    assert used.text == "The cat is asleep. It rains."


async def test_edits_lose_control_characters(db):
    seeded = await seed_run(db)
    p1 = seeded.span_ids[0]
    detail = await review.edit_span(
        db, seeded.run_id, p1, "Chapter\x00 1\x0b", reason="nul\x00 \x01reason"
    )
    assert detail.text == "Chapter 1\n"
    assert detail.revisions[-1].reason == "nul reason"
    for text in ("\x00", " \x01\x02 "):
        with pytest.raises(service.InvalidRequestError, match="must not be empty"):
            await review.edit_span(db, seeded.run_id, p1, text)
    [(_, _, payload)] = await _events(db, seeded.run_id)
    assert payload["after"] == "Chapter 1\n"


async def test_an_unchanged_edit_writes_nothing_and_blank_text_is_refused(db):
    seeded = await seed_run(db)
    p1 = seeded.span_ids[0]
    detail = await review.edit_span(db, seeded.run_id, p1, "Chapter One")
    assert len(detail.revisions) == 1
    with pytest.raises(service.InvalidRequestError, match="must not be empty"):
        await review.edit_span(db, seeded.run_id, p1, "  \n")
    assert await _events(db, seeded.run_id) == []


async def test_actions_are_refused_while_the_sweep_runs(db):
    seeded = await seed_run(db, state=RunState.SWEEPING)
    p2 = seeded.span_ids[1]
    flag = seeded.flag_ids["p2:low_meaning"]
    attempts = [
        review.edit_span(db, seeded.run_id, p2, "x"),
        review.use_candidate(db, seeded.run_id, p2, "literal"),
        review.accept_span(db, seeded.run_id, p2),
        review.set_flag_status(db, seeded.run_id, flag, FlagStatus.ACCEPTED),
        review.add_guide_entry(db, seeded.run_id, p2, GlossaryEntry(term="a", rendering="b")),
    ]
    for attempt in attempts:
        with pytest.raises(service.InvalidRunStateError, match="try again when it finishes"):
            await attempt
    assert await _events(db, seeded.run_id) == []


@pytest.mark.parametrize(
    "state", [RunState.TRANSLATING, RunState.CANCELLED, RunState.FAILED, RunState.CREATED]
)
async def test_actions_need_a_complete_run(db, state):
    seeded = await seed_run(db, state=state)
    p2 = seeded.span_ids[1]
    flag = seeded.flag_ids["p2:low_meaning"]
    attempts = [
        lambda: review.edit_span(db, seeded.run_id, p2, "x"),
        lambda: review.use_candidate(db, seeded.run_id, p2, "literal"),
        lambda: review.accept_span(db, seeded.run_id, p2),
        lambda: review.set_flag_status(db, seeded.run_id, flag, FlagStatus.ACCEPTED),
        lambda: review.add_guide_entry(
            db, seeded.run_id, p2, GlossaryEntry(term="a", rendering="b")
        ),
    ]
    message = (
        f"run {seeded.run_id} is {state}; review actions are available once the run is complete"
    )
    for attempt in attempts:
        with pytest.raises(service.InvalidRunStateError) as e:
            await attempt()
        assert str(e.value) == message
    assert await _events(db, seeded.run_id) == []


async def test_reviewing_a_run_complete_with_errors(db):
    seeded = await seed_run(db, state=RunState.COMPLETE_WITH_ERRORS)
    detail = await review.edit_span(db, seeded.run_id, seeded.span_ids[0], "Chapter 1")
    assert detail.text == "Chapter 1"


@pytest.mark.parametrize(
    ("span", "translator", "text"),
    [
        (1, "contextual", "The cat is asleep. It rains."),
        (1, "literal", "The cat sleeps. It is raining."),
        (2, "contextual", "The End."),
    ],
)
async def test_use_candidate(db, span, translator, text):
    seeded = await seed_run(db)
    span_id = seeded.span_ids[span]
    detail = await review.use_candidate(db, seeded.run_id, span_id, translator, user="li")
    assert detail.text == text
    assert detail.revisions[-1].reason == f"used the {translator} candidate"
    [(action, _, payload)] = await _events(db, seeded.run_id)
    assert action == "use_candidate"
    assert payload == {
        "user": "li",
        "translator": translator,
        "before": detail.revisions[-1].before,
        "after": text,
    }


async def test_choosing_the_current_text_records_only_the_choice(db):
    seeded = await seed_run(db)
    detail = await review.use_candidate(db, seeded.run_id, seeded.span_ids[0], "contextual")
    assert len(detail.revisions) == 1
    assert [e[0] for e in await _events(db, seeded.run_id)] == ["use_candidate"]


async def test_use_candidate_refusals(db):
    seeded = await seed_run(db)
    p2 = seeded.span_ids[1]
    with pytest.raises(service.InvalidRequestError, match="no 'nobody' candidate covers"):
        await review.use_candidate(db, seeded.run_id, p2, "nobody")
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
    with pytest.raises(
        service.ConflictError, match=r"boundaries differ from this passage \(s2–s3\)"
    ):
        await review.use_candidate(db, seeded.run_id, p2, "extra")
    assert await _events(db, seeded.run_id) == []


async def test_a_blank_candidate_is_refused(db):
    seeded = await seed_run(db)
    p3 = seeded.span_ids[2]
    async with db.session() as s, s.begin():
        s.add(
            CandidateRow(
                run_id=seeded.run_id,
                chunk_id="c1",
                chunk_ordinal=0,
                translator="blank",
                model="m",
                seg_start="s4",
                seg_end="s4",
                text=" \x01 ",
                notes="",
            )
        )
    with pytest.raises(service.InvalidRequestError, match="the translation must not be empty"):
        await review.use_candidate(db, seeded.run_id, p3, "blank")
    assert await _events(db, seeded.run_id) == []


_CHOICES = json.loads((Path(__file__).parent / "fixtures" / "translator_choices.json").read_text())


@pytest.mark.parametrize("case", _CHOICES["cases"], ids=lambda case: case["name"])
async def test_translator_choices_match_the_shared_cases(db, case):
    # CGL-44: the passage panel joins a translator's pieces itself (web/src/review/byTranslator.ts)
    # and is checked against these same cases, so it offers exactly the text this action writes
    seeded = await seed_run(db)
    p2 = seeded.span_ids[1]
    async with db.session() as s, s.begin():
        for c in case["candidates"]:
            s.add(
                CandidateRow(
                    run_id=seeded.run_id,
                    chunk_id="c1",
                    chunk_ordinal=0,
                    translator=c["translator"],
                    model="m",
                    seg_start=c["start"],
                    seg_end=c["end"],
                    text=c["text"],
                    notes="",
                )
            )
            await s.flush()  # ids, and so the order the API sends, follow the case
    detail = await review.span_detail(db, seeded.run_id, p2)
    assert (detail.start, detail.end) == ("s2", "s3")
    added = {c["translator"] for c in case["candidates"]}
    sent = [
        (c.translator, c.start, c.end, c.text) for c in detail.candidates if c.translator in added
    ]
    assert sent == [(c["translator"], c["start"], c["end"], c["text"]) for c in case["candidates"]]
    assert {c["translator"] for c in case["choices"]} == added
    for choice in case["choices"]:
        if choice["usable"]:
            used = await review.use_candidate(db, seeded.run_id, p2, choice["translator"])
            assert used.text == choice["text"]
        else:
            with pytest.raises((service.ConflictError, service.InvalidRequestError)):
                await review.use_candidate(db, seeded.run_id, p2, choice["translator"])


async def test_accept_span(db):
    seeded = await seed_run(db)
    detail = await review.accept_span(db, seeded.run_id, seeded.span_ids[1], user="ana")
    assert {f.status for f in detail.flags} == {FlagStatus.ACCEPTED}
    await review.accept_span(db, seeded.run_id, seeded.span_ids[0])
    ids = sorted([seeded.flag_ids["p2:low_meaning"], seeded.flag_ids["p2:agent_disagreement"]])
    assert await _events(db, seeded.run_id) == [
        (
            "accept",
            seeded.span_ids[1],
            {"user": "ana", "flags": ids, "text": "The cat sleeps. It rains."},
        ),
        ("accept", seeded.span_ids[0], {"user": None, "flags": [], "text": "Chapter One"}),
    ]


async def test_flag_status_changes(db):
    seeded = await seed_run(db)
    flag = seeded.flag_ids["p3:terminology_violation"]
    with pytest.raises(service.InvalidRequestError, match="needs a reason"):
        await review.set_flag_status(db, seeded.run_id, flag, FlagStatus.DISMISSED, reason=" ")
    with pytest.raises(service.InvalidRequestError, match="only the sweep resolves flags"):
        await review.set_flag_status(db, seeded.run_id, flag, FlagStatus.RESOLVED)
    detail = await review.set_flag_status(
        db, seeded.run_id, flag, FlagStatus.DISMISSED, reason="house style", user="ana"
    )
    assert [(f.status, f.dismiss_reason) for f in detail.flags] == [
        (FlagStatus.DISMISSED, "house style")
    ]
    detail = await review.set_flag_status(db, seeded.run_id, flag, FlagStatus.OPEN)
    assert [(f.status, f.dismiss_reason) for f in detail.flags] == [(FlagStatus.OPEN, None)]
    await review.set_flag_status(db, seeded.run_id, flag, FlagStatus.ACCEPTED)
    events = await _events(db, seeded.run_id)
    assert [(a, p["before"], p["reason"]) for a, _, p in events] == [
        ("dismiss_flag", "open", "house style"),
        ("reopen_flag", "dismissed", None),
        ("accept_flag", "open", None),
    ]
    assert events[0][2] == {
        "user": "ana",
        "flag": flag,
        "code": "terminology_violation",
        "before": "open",
        "reason": "house style",
    }
    other = await seed_run(db, project="other")
    with pytest.raises(NotFoundError, match="not found in run"):
        await review.set_flag_status(
            db, seeded.run_id, other.flag_ids["p2:low_meaning"], FlagStatus.ACCEPTED
        )


async def test_add_glossary_entry_and_entity(db):
    seeded = await seed_run(db)
    p3 = seeded.span_ids[2]
    view = await review.add_guide_entry(
        db,
        seeded.run_id,
        p3,
        GlossaryEntry(term="Fin", rendering="The End", locked=True),
        user="ana",
    )
    assert (view.info.version, view.info.status, view.info.author) == (2, "approved", "user")
    assert view.info.source_run_id == str(seeded.run_id)
    assert [(g.term, g.rendering, g.locked) for g in view.guide.glossary] == [
        ("chat", "cat", False),
        ("Fin", "The End", True),
    ]
    view = await review.add_guide_entry(
        db, seeded.run_id, p3, GlossaryEntry(term=" CHAT ", rendering="kitty")
    )
    assert [(g.term, g.rendering) for g in view.guide.glossary] == [
        ("CHAT", "kitty"),
        ("Fin", "The End"),
    ]
    view = await review.add_guide_entry(
        db, seeded.run_id, p3, Entity(name="Minou", rendering="Kitty", kind="person")
    )
    assert [e.name for e in view.guide.entities] == ["Minou"] and view.info.version == 4
    events = await _events(db, seeded.run_id)
    assert [(p["kind"], p["version"]) for _, _, p in events] == [
        ("glossary", 2),
        ("glossary", 3),
        ("entity", 4),
    ]
    assert events[0][2]["entry"] == {
        "term": "Fin",
        "rendering": "The End",
        "notes": "",
        "locked": True,
        "forms": [],
        "source_forms": [],
    }


async def test_guide_entries_and_dismiss_reasons_lose_control_characters(db):
    seeded = await seed_run(db)
    p3 = seeded.span_ids[2]
    view = await review.add_guide_entry(
        db,
        seeded.run_id,
        p3,
        GlossaryEntry(term="Fin\x00", rendering="The\x0bEnd", notes="n\x01"),
    )
    assert [(g.term, g.rendering, g.notes) for g in view.guide.glossary][-1] == (
        "Fin",
        "The\nEnd",
        "n",
    )
    view = await review.add_guide_entry(
        db,
        seeded.run_id,
        p3,
        Entity(
            name="Min\x00ou",
            rendering="Kitty",
            kind="per\x01son",
            speech_style="\x0c",
            forms=["Kit\x00ties", "\x01"],
        ),
    )
    assert view.guide.entities == [
        Entity(name="Minou", rendering="Kitty", kind="person", speech_style="\n", forms=["Kitties"])
    ]
    with pytest.raises(service.InvalidRequestError, match="needs a term or name"):
        await review.add_guide_entry(
            db, seeded.run_id, p3, GlossaryEntry(term="\x00", rendering="x")
        )
    flag = seeded.flag_ids["p3:terminology_violation"]
    detail = await review.set_flag_status(
        db, seeded.run_id, flag, FlagStatus.DISMISSED, reason="house\x00 style"
    )
    assert detail.flags[0].dismiss_reason == "house style"
    with pytest.raises(service.InvalidRequestError, match="needs a reason"):
        await review.set_flag_status(
            db, seeded.run_id, flag, FlagStatus.DISMISSED, reason="\x00\x01"
        )


async def test_add_guide_entry_refusals(db):
    seeded = await seed_run(db)
    p3 = seeded.span_ids[2]
    for entry in (
        GlossaryEntry(term=" ", rendering="x"),
        Entity(name="A", rendering="", kind="person"),
    ):
        with pytest.raises(service.InvalidRequestError, match="needs a term or name"):
            await review.add_guide_entry(db, seeded.run_id, p3, entry)
    async with db.session() as s, s.begin():
        await s.execute(delete(GuideVersion).where(GuideVersion.project_id == seeded.project_id))
    with pytest.raises(service.InvalidRunStateError, match="has no guide yet"):
        await review.add_guide_entry(db, seeded.run_id, p3, GlossaryEntry(term="a", rendering="b"))
    with pytest.raises(NotFoundError, match="not found in run"):
        await review.add_guide_entry(
            db, seeded.run_id, uuid.uuid4(), GlossaryEntry(term="a", rendering="b")
        )


async def test_review_never_deletes_revisions(db):
    seeded = await seed_run(db)
    for text in ("a", "b", "c"):
        await review.edit_span(db, seeded.run_id, seeded.span_ids[0], text)
    async with db.session() as s:
        count = len(
            list(
                await s.scalars(
                    select(RevisionRow).where(RevisionRow.span_id == seeded.span_ids[0])
                )
            )
        )
    assert count == 4


async def test_always_translate_writes_only_to_the_runs_language(db):
    """A set in English and Spanish: a rule added from the Spanish run joins the Spanish guide
    and leaves the English one as it was (CGL-30)."""
    english = await seed_run(db, project="two", target_lang="en")
    spanish = await seed_run(db, project="two", target_lang="es", filename="other.txt")
    assert english.project_id == spanish.project_id
    view = await review.add_guide_entry(
        db,
        spanish.run_id,
        spanish.span_ids[2],
        GlossaryEntry(term="Fin", rendering="Fin", locked=True),
    )
    assert (view.info.target_lang, view.info.version) == ("es", 2)
    async with db.session() as s:
        rows = (
            await s.scalars(
                select(GuideVersion)
                .where(GuideVersion.project_id == english.project_id)
                .order_by(GuideVersion.target_lang, GuideVersion.version)
            )
        ).all()
    assert [(r.target_lang, r.version) for r in rows] == [("en", 1), ("es", 1), ("es", 2)]
    assert all(g["term"] != "Fin" for g in rows[0].content["glossary"])
    assert rows[2].content["glossary"][-1]["term"] == "Fin"


async def _term_flag(db, seeded, term="Fin", expected="The End", kind="glossary"):
    """p3's terminology flag, with the evidence the scorer writes."""
    from coglossa.store.models import FlagRow

    flag_id = seeded.flag_ids["p3:terminology_violation"]
    violation = {"term": term, "expected": expected, "kind": kind, "locked": True}
    async with db.session() as s, s.begin():
        (await s.get(FlagRow, flag_id)).evidence = {"violations": [violation]}
    return flag_id


async def test_this_form_is_fine_adds_the_form_and_dismisses_the_flag(db):
    """CGL-109: the passage reads "End."; the reviewer accepts "End" as a form of "The End"."""
    seeded = await seed_run(db)
    p3 = seeded.span_ids[2]
    await review.add_guide_entry(
        db, seeded.run_id, p3, GlossaryEntry(term="Fin", rendering="The End", locked=True)
    )
    flag = await _term_flag(db, seeded)
    detail = await review.accept_form(db, seeded.run_id, flag, " fin ", " End ", user="ana")
    assert [(f.status, f.dismiss_reason) for f in detail.flags] == [
        (FlagStatus.DISMISSED, "This form is fine: “End”")
    ]
    async with db.session() as s:
        latest = await runs.prior_guide(s, seeded.project_id, "en")
    assert latest.version == 3 and latest.status == "approved" and latest.author == "user"
    entry = Guide.model_validate(latest.content).glossary[-1]
    assert (entry.term, entry.rendering, entry.forms, entry.locked) == (
        "Fin",
        "The End",
        ["End"],
        True,
    )
    *_, (action, span, payload) = await _events(db, seeded.run_id)
    assert (action, span) == ("dismiss_flag", p3)
    assert payload["form"] == {"term": " fin ", "form": "End", "version": 3}
    assert payload["user"] == "ana" and payload["reason"] == "This form is fine: “End”"


async def test_this_form_is_fine_on_a_name(db):
    seeded = await seed_run(db)
    p3 = seeded.span_ids[2]
    await review.add_guide_entry(
        db, seeded.run_id, p3, Entity(name="Fin", rendering="Finn", kind="place", locked=True)
    )
    flag = await _term_flag(db, seeded, expected="Finn", kind="entity")
    await review.accept_form(db, seeded.run_id, flag, "Fin", "End")
    async with db.session() as s:
        latest = await runs.prior_guide(s, seeded.project_id, "en")
    assert Guide.model_validate(latest.content).entities[0].forms == ["End"]


async def test_this_form_is_fine_refusals(db):
    seeded = await seed_run(db)
    p3 = seeded.span_ids[2]
    flag = await _term_flag(db, seeded)
    with pytest.raises(service.InvalidRequestError, match="no longer has “Fin”"):
        await review.accept_form(db, seeded.run_id, flag, "Fin", "End")
    await review.add_guide_entry(
        db, seeded.run_id, p3, GlossaryEntry(term="Fin", rendering="The End", locked=True)
    )
    with pytest.raises(service.InvalidRequestError, match="name the form"):
        await review.accept_form(db, seeded.run_id, flag, "Fin", " \x00")
    with pytest.raises(service.InvalidRequestError, match="not about “chat”"):
        await review.accept_form(db, seeded.run_id, flag, "chat", "End")
    with pytest.raises(service.InvalidRequestError, match="does not use “Ending”"):
        await review.accept_form(db, seeded.run_id, flag, "Fin", "Ending")
    low = seeded.flag_ids["p2:low_meaning"]
    with pytest.raises(service.InvalidRequestError, match="only an open terminology flag"):
        await review.accept_form(db, seeded.run_id, low, "Fin", "End")
    await review.set_flag_status(db, seeded.run_id, flag, FlagStatus.ACCEPTED)
    with pytest.raises(service.InvalidRequestError, match="only an open terminology flag"):
        await review.accept_form(db, seeded.run_id, flag, "Fin", "End")
    async with db.session() as s:
        assert (await runs.prior_guide(s, seeded.project_id, "en")).version == 2
