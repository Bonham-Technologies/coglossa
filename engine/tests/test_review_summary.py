"""CGL-112: the review summary (post-editing effort), read from what the review view stores."""

import codecs
import csv
import io
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from coglossa import catalog, review, review_summary
from coglossa.review_summary import edit_distance
from coglossa.schema import FlagStatus, RevisionSource
from coglossa.store import runs
from coglossa.store.models import FlagRow, RevisionRow
from coglossa.testing.seed import seed_run


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("", "", 0),
        ("abc", "", 3),
        ("", "abc", 3),
        ("kitten", "sitting", 3),
        ("sitting", "kitten", 3),
        ("Chapter One", "Chapter 1", 3),
        ("flaw", "lawn", 2),
        ("😀 ok", "😃 ok", 1),  # one code point, not two UTF-16 units
        ("é", "é", 2),  # code points, not grapheme clusters
        ("same text", "same text", 0),
    ],
)
def test_edit_distance_counts_code_points(a, b, expected):
    assert edit_distance(a, b) == (expected, True)


def test_a_long_passage_is_estimated_not_compared():
    a, b = "x" * 3000, "y" * 3000
    distance, exact = edit_distance(a, b, max_cells=1_000_000)
    assert (distance, exact) == (3000, False)  # an upper bound: every character replaced
    # a shared beginning and end cost nothing, so a small change in a long passage stays exact
    long = "word " * 2000
    assert edit_distance(long + "cat" + long, long + "cart" + long, max_cells=100) == (1, True)


async def test_a_run_nobody_reviewed_has_zeros_not_gaps(db):
    seeded = await seed_run(db)
    summary = await review_summary.run_summary(db, seeded.run_id)
    assert (summary.filename, summary.target_lang) == ("story.txt", "en")
    assert (summary.passages, summary.checked, summary.edited) == (3, 0, 0)
    assert (summary.checked_share, summary.edited_share) == (0.0, 0.0)
    assert (summary.edit_distance_total, summary.mean_edit_distance) == (0, 0.0)
    assert (summary.mean_edit_ratio, summary.estimated_distances) == (0.0, 0)
    by_code = {f.code: f for f in summary.flags}
    assert sorted(by_code) == ["agent_disagreement", "low_meaning", "terminology_violation"]
    low = by_code["low_meaning"]
    assert (low.raised, low.passages, low.open, low.dismissed, low.edited_after) == (1, 1, 1, 0, 0)
    assert (low.edited_after_share, low.dismiss_reasons) == (0.0, [])


async def _review(db, seeded):
    p1, p2, p3 = seeded.span_ids
    await review.edit_span(db, seeded.run_id, p1, "Chapter 1", user="Ana Lima")
    await review.accept_span(db, seeded.run_id, p2, user="Tevita")
    await review.set_flag_status(
        db,
        seeded.run_id,
        seeded.flag_ids["p3:terminology_violation"],
        FlagStatus.DISMISSED,
        reason="=HYPERLINK(x) the client prefers End",
        user="Tevita",
    )
    await review.edit_span(db, seeded.run_id, p2, "The cat sleeps. It is raining.", user="Ana Lima")


async def test_counts_and_edit_distance_from_the_stored_review(db):
    seeded = await seed_run(db)
    await _review(db, seeded)
    summary = await review_summary.run_summary(db, seeded.run_id)
    assert (summary.passages, summary.checked, summary.edited) == (3, 2, 2)
    assert summary.checked_share == pytest.approx(2 / 3)
    # p1 "Chapter One" -> "Chapter 1": 3; p2 "... It rains." -> "... It is raining.": 6
    assert summary.edit_distance_total == 9
    assert summary.mean_edit_distance == pytest.approx(4.5)
    assert summary.mean_edit_ratio == pytest.approx((3 / 11 + 6 / 30) / 2)
    by_code = {f.code: f for f in summary.flags}
    low = by_code["low_meaning"]
    assert (low.raised, low.edited_after, low.edited_after_share) == (1, 1, 1.0)
    term = by_code["terminology_violation"]
    assert (term.dismissed, term.open, term.edited_after) == (1, 0, 0)
    assert [(r.reason, r.count) for r in term.dismiss_reasons] == [
        ("=HYPERLINK(x) the client prefers End", 1)
    ]


async def test_the_engine_text_is_the_last_before_a_person_edited(db):
    """A recheck (sweep) revision before the first edit is the engine's text; one after it,
    which nobody approved, changes nothing."""
    seeded = await seed_run(db)
    p1 = seeded.span_ids[0]
    async with db.session() as s, s.begin():
        s.add(
            RevisionRow(
                span_id=p1,
                source=RevisionSource.SWEEP.value,
                before="Chapter One",
                after="Chapter Uno",
                reason="locked term",
            )
        )
    await review.edit_span(db, seeded.run_id, p1, "Chapter Unos")
    async with db.session() as s, s.begin():
        last = await s.scalar(select(RevisionRow).order_by(RevisionRow.id.desc()).limit(1))
        s.add(
            RevisionRow(
                span_id=p1,
                source=RevisionSource.SWEEP.value,
                before="Chapter Unos",
                after="Something else entirely",
                reason="locked term",
                created_at=last.created_at + timedelta(seconds=1),
            )
        )
    summary = await review_summary.run_summary(db, seeded.run_id)
    assert (summary.edited, summary.edit_distance_total) == (1, 1)


async def test_only_edited_passages_load_their_texts(db):
    """An unedited passage has no distance, so its revisions' texts are never read: a large set
    does not hold every engine text in memory."""
    seeded = await seed_run(db)
    await review.edit_span(db, seeded.run_id, seeded.span_ids[0], "Chapter 1")
    async with db.session() as s:
        passages = await review_summary._passages(s, [seeded.run_id])
    assert [(p.edited, p.engine_text) for p in passages] == [
        (True, "Chapter One"),
        (False, ""),
        (False, ""),
    ]


async def test_an_edited_passage_reopened_since_is_measured_by_its_current_text(db):
    seeded = await seed_run(db)
    p2 = seeded.span_ids[1]
    await review.edit_span(db, seeded.run_id, p2, "The cat sleeps. It is raining.")
    await review.set_flag_status(
        db, seeded.run_id, seeded.flag_ids["p2:low_meaning"], FlagStatus.OPEN
    )
    summary = await review_summary.run_summary(db, seeded.run_id)
    assert (summary.checked, summary.edited, summary.edit_distance_total) == (0, 1, 6)


async def test_past_the_budget_a_distance_is_an_upper_bound(db, monkeypatch):
    seeded = await seed_run(db)
    await _review(db, seeded)
    monkeypatch.setattr(review_summary, "BUDGET_CELLS", 10)  # p1's 3 x 1 fits, p2's does not
    summary = await review_summary.run_summary(db, seeded.run_id)
    # p1 exact (3); p2's differing middles "rains" / "is raining": 10, the longer one's length
    assert (summary.estimated_distances, summary.edit_distance_total) == (1, 13)
    _, data = await review_summary.run_csv(db, seeded.run_id)
    assert [r["distance_estimated"] for r in _rows(data)] == ["", "yes", ""]


async def test_a_set_without_files_has_zeros(db):
    project = await catalog.create_project(db, "empty", target_lang="fr")
    summary = await review_summary.set_summary(db, uuid.UUID(project.id))
    (fr,) = summary.languages
    assert (fr.target_lang, fr.run_ids, fr.passages, fr.checked_share, fr.flags) == (
        "fr",
        [],
        0,
        0.0,
        [],
    )
    _, data = await review_summary.set_csv(db, uuid.UUID(project.id), target_lang="fr")
    assert _rows(data) == []


async def test_a_flag_raised_after_the_edit_is_not_counted_as_edited_after(db):
    seeded = await seed_run(db)
    p1 = seeded.span_ids[0]
    await review.edit_span(db, seeded.run_id, p1, "Chapter 1")
    async with db.session() as s, s.begin():
        edit = await s.scalar(select(RevisionRow).order_by(RevisionRow.id.desc()).limit(1))
        s.add(
            FlagRow(
                span_id=p1,
                code="number_mismatch",
                explanation="1 is not in the original.",
                evidence={},
                created_at=edit.created_at + timedelta(seconds=1),
            )
        )
    summary = await review_summary.run_summary(db, seeded.run_id)
    numbers = next(f for f in summary.flags if f.code == "number_mismatch")
    assert (numbers.raised, numbers.edited_after) == (1, 0)


async def test_a_set_summary_counts_each_files_newest_run_per_language(db):
    first = await seed_run(db, project="desk", filename="one.txt")
    second = await seed_run(db, project="desk", filename="two.txt")
    german = await seed_run(db, project="desk", filename="three.txt", target_lang="de")
    await _review(db, first)
    await review.accept_span(db, second.run_id, second.span_ids[0])
    summary = await review_summary.set_summary(db, first.project_id)
    assert [lang.target_lang for lang in summary.languages] == ["en", "de"]
    en, de = summary.languages
    assert sorted(en.run_ids) == sorted([str(first.run_id), str(second.run_id)])
    assert (en.passages, en.checked, en.edited, en.edit_distance_total) == (6, 3, 2, 9)
    assert (de.run_ids, de.passages, de.checked) == ([str(german.run_id)], 3, 0)
    # a newer run of a file replaces the older one, as on the set page
    async with db.session() as s, s.begin():
        newer = await runs.create_run(
            s,
            first.document_id,
            target_lang="en",
            config_hash="x",
            config_snapshot={},
            prompt_versions={},
        )
    summary = await review_summary.set_summary(db, first.project_id)
    en = summary.languages[0]
    assert sorted(en.run_ids) == sorted([str(newer.id), str(second.run_id)])
    assert (en.passages, en.checked, en.edited) == (3, 1, 0)


def _rows(data: bytes) -> list[dict[str, str]]:
    assert data.startswith(codecs.BOM_UTF8)  # Excel reads UTF-8 only with the mark
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


async def test_the_csv_has_a_row_per_passage_with_reviewers(db):
    seeded = await seed_run(db)
    await _review(db, seeded)
    filename, data = await review_summary.run_csv(db, seeded.run_id)
    assert filename == "story.en.review-summary.csv"
    rows = _rows(data)
    assert [r["passage"] for r in rows] == ["p1", "p2", "p3"]
    p1, p2, p3 = rows
    assert (p1["file"], p1["language"]) == ("story.txt", "en")
    assert (p1["checked"], p1["edited"], p1["edit_distance"]) == ("yes", "yes", "3")
    assert p1["reviewers"] == "Ana Lima"
    assert p2["flags"] == "low_meaning (accepted); agent_disagreement (accepted)"
    assert p2["reviewers"] == "Tevita; Ana Lima"
    assert (p3["checked"], p3["edited"], p3["edit_distance"]) == ("no", "no", "")
    assert p3["flags"] == "terminology_violation (dismissed)"
    assert p3["dismiss_reasons"] == "terminology_violation: =HYPERLINK(x) the client prefers End"
    assert p3["reviewers"] == "Tevita"


async def test_a_csv_cell_a_spreadsheet_would_run_is_defused(db):
    seeded = await seed_run(db, filename="=cmd.txt")
    await review.accept_span(db, seeded.run_id, seeded.span_ids[0], user="@admin")
    _, data = await review_summary.run_csv(db, seeded.run_id)
    first = _rows(data)[0]
    assert (first["file"], first["reviewers"]) == ("'=cmd.txt", "'@admin")


@pytest.mark.parametrize(
    ("text", "safe"),
    [
        ("=1+1", "'=1+1"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\t=1", "'\t=1"),
        ("\r=1", "'\r=1"),
        ("plain", "plain"),
        ("", ""),
    ],
)
def test_csv_cells_cannot_start_a_formula(text, safe):
    assert review_summary.csv_cell(text) == safe


async def test_a_set_csv_lists_every_counted_passage(db):
    first = await seed_run(db, project="desk", filename="one.txt")
    await seed_run(db, project="desk", filename="two.txt", target_lang="de")
    filename, data = await review_summary.set_csv(db, first.project_id)
    assert filename == "desk.review-summary.csv"
    rows = _rows(data)
    assert [(r["file"], r["language"], r["passage"]) for r in rows] == [
        ("one.txt", "en", "p1"),
        ("one.txt", "en", "p2"),
        ("one.txt", "en", "p3"),
        ("two.txt", "de", "p1"),
        ("two.txt", "de", "p2"),
        ("two.txt", "de", "p3"),
    ]
    _, german = await review_summary.set_csv(db, first.project_id, target_lang="DE")
    assert [r["language"] for r in _rows(german)] == ["de", "de", "de"]
