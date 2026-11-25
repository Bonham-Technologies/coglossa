"""Inline formatting through the prompts (CGL-108): markers out, markers back, fallback."""

import pytest

from coglossa.schema import Block, BlockKind, FlagCode, InlineMark, MarkKind, Provenance, Segment
from coglossa.segment.markup import (
    FORMATTING_LOST,
    ChunkMarkup,
    chunk_markup,
    clip_marks,
    formatting_flag,
    join_marked,
    link_target,
    normalize_marks,
    strip_marked,
    unmark,
)

B, IT, LINK, SUP = MarkKind.BOLD, MarkKind.ITALIC, MarkKind.LINK, MarkKind.SUPERSCRIPT


def _mark(text: str, words: str, kind: MarkKind, href: str | None = None) -> InlineMark:
    start = text.index(words)
    return InlineMark(start=start, end=start + len(words), kind=kind, href=href)


def _block(text: str, marks: list[InlineMark], block_id: str = "b1") -> Block:
    provenance = Provenance(source_path="x.docx", index=0, marks=marks)
    return Block(id=block_id, kind=BlockKind.PARAGRAPH, text=text, provenance=provenance)


TEXT = "Le chat dort. Voir ici et la note 2."
BLOCK = _block(
    TEXT,
    [
        _mark(TEXT, "chat", B),
        _mark(TEXT, "Voir ici", IT),
        _mark(TEXT, "ici", LINK, "https://example.org/"),
        _mark(TEXT, "2", SUP),
    ],
)
SEGMENTS = [
    Segment(id="s1", block_id="b1", start=0, end=13, text="Le chat dort."),
    Segment(id="s2", block_id="b1", start=14, end=len(TEXT), text="Voir ici et la note 2."),
]


def _markup() -> ChunkMarkup:
    return chunk_markup(SEGMENTS, {"b1": BLOCK})


def test_each_run_of_formatting_gets_a_numbered_marker_pair_through_the_chunk():
    markup = _markup()
    assert markup.marked == {
        "s1": "Le <1>chat</1> dort.",
        "s2": "<2>Voir</2> <3>ici</3> et la note <4>2</4>.",
    }
    assert markup.numbers == {"s1": (1,), "s2": (2, 3, 4)}
    assert markup.formats[1] == {(B, None)}
    assert markup.formats[3] == {(IT, None), (LINK, "https://example.org/")}
    assert markup.text(SEGMENTS[0]) == "Le <1>chat</1> dort."


def test_segments_without_formatting_and_unknown_blocks_stay_plain():
    plain = Segment(id="s3", block_id="b9", start=0, end=4, text="Fin.")
    markup = chunk_markup([*SEGMENTS, plain], {"b1": BLOCK})
    assert "s3" not in markup.marked and markup.text(plain) == "Fin."
    assert chunk_markup(SEGMENTS, {}) == ChunkMarkup()


def _literal(text: str, marks: list[InlineMark] | None = None) -> tuple[Segment, Block]:
    """A segment whose source text holds what looks like a marker (CGL-129)."""
    segment = Segment(id="s3", block_id="b2", start=0, end=len(text), text=text)
    return segment, _block(text, marks or [], "b2")


def test_text_that_looks_like_a_marker_turns_markers_off_for_its_segment_only():
    """CGL-129: the rest of the chunk keeps its markers, numbered past the literal ones."""
    literal, block = _literal("Press <1> or ＜3＞ now")
    markup = chunk_markup([*SEGMENTS, literal], {"b1": BLOCK, "b2": block})
    assert markup.marked == {
        "s1": "Le <2>chat</2> dort.",
        "s2": "<4>Voir</4> <5>ici</5> et la note <6>2</6>.",
    }
    assert markup.text(literal) == "Press <1> or ＜3＞ now"
    assert markup.unsent == frozenset()  # it had no formatting to lose
    # and nothing is taken out of a text when no markers were sent
    assert unmark(ChunkMarkup(), ["s3"], "Appuyez <1> maintenant").text == (
        "Appuyez <1> maintenant"
    )


def test_marker_like_source_text_is_never_read_as_a_marker():
    literal, block = _literal("Press <1> or ＜3＞ now")
    markup = chunk_markup([*SEGMENTS, literal], {"b1": BLOCK, "b2": block})
    alone = unmark(markup, ["s3"], "Appuyez <1> ou ＜3＞ maintenant")
    assert (alone.text, alone.formatting, alone.lost) == (
        "Appuyez <1> ou ＜3＞ maintenant",
        None,
        False,
    )
    # a passage over it and a formatted segment: the literal text stays, the markers work
    merged = unmark(markup, ["s1", "s3"], "The <2>cat</2> sleeps. Press <1> or ＜3＞ now")
    assert merged.text == "The cat sleeps. Press <1> or ＜3＞ now" and not merged.lost
    assert merged.formatting is not None
    assert [merged.text[m.start : m.end] for m in merged.formatting.marks] == ["cat"]
    # a passage that does not cover it reads that number as a marker it was never sent
    stray = unmark(markup, ["s1"], "The <2>cat</2> sleeps <1>.")
    assert stray.lost and stray.text == "The cat sleeps ."


def test_a_formatted_segment_with_marker_like_text_is_plain_and_flagged():
    text = "Press <1> now"
    literal, block = _literal(text, [_mark(text, "now", B)])
    markup = chunk_markup([*SEGMENTS, literal], {"b1": BLOCK, "b2": block})
    assert "s3" not in markup.marked and markup.unsent == {"s3"}
    alone = unmark(markup, ["s3"], "Appuyez <1> maintenant")
    assert (alone.text, alone.formatting, alone.lost) == ("Appuyez <1> maintenant", None, True)
    # over several segments: plain, flagged, the real markers gone and the literal text kept
    merged = unmark(markup, ["s1", "s3"], "The <2>cat</2> sleeps. Press <1> now")
    assert (merged.text, merged.formatting, merged.lost) == (
        "The cat sleeps. Press <1> now",
        None,
        True,
    )
    assert not unmark(markup, ["s3"], "").lost  # nothing written, nothing flagged
    # when it is the only formatted segment, no markers go out and the text stays as written
    only = chunk_markup([literal], {"b2": block})
    assert only.marked == {} and only.unsent == {"s3"}
    found = unmark(only, ["s3"], "Appuyez <1> maintenant")
    assert (found.text, found.formatting, found.lost) == ("Appuyez <1> maintenant", None, True)


def test_a_marker_like_number_too_long_for_int_is_never_a_marker():
    """CGL-129: Python's `int` refuses more than 4300 digits; such text is read, not raised."""
    long = "<" + "9" * 5000 + ">"
    literal, block = _literal(f"x {long} y")
    markup = chunk_markup([*SEGMENTS, literal], {"b1": BLOCK, "b2": block})
    assert markup.marked == _markup().marked and markup.text(literal) == f"x {long} y"
    kept = unmark(markup, ["s3"], f"x {long} y")
    assert (kept.text, kept.formatting, kept.lost) == (f"x {long} y", None, False)
    # in a model's answer over a formatted segment it is a marker that was never sent
    stray = unmark(_markup(), ["s1"], f"The <1>cat</1> sleeps {long}.")
    assert stray.lost and stray.text == "The cat sleeps ."


def test_markers_given_back_become_marks_over_the_plain_translation():
    found = unmark(
        _markup(), ["s1", "s2"], "The <1>cat</1> sleeps. <3>Here</3> <2>see</2> note <4>2</4>."
    )
    assert found.text == "The cat sleeps. Here see note 2."
    assert not found.lost
    assert found.formatting is not None and found.formatting.text == found.text
    got = {(m.kind, m.href, found.text[m.start : m.end]) for m in found.formatting.marks}
    assert got == {
        (B, None, "cat"),
        (IT, None, "Here"),  # in another order now, and the space between is not italic
        (IT, None, "see"),
        (LINK, "https://example.org/", "Here"),
        (SUP, None, "2"),
    }


def test_a_passage_over_unformatted_segments_is_left_plain_without_a_flag():
    markup = _markup()
    found = unmark(markup, ["s9"], "Nothing <7>here</7>.")
    assert (found.text, found.formatting, found.lost) == ("Nothing here.", None, False)
    blank = unmark(markup, ["s1"], "")
    assert (blank.text, blank.formatting, blank.lost) == ("", None, False)


@pytest.mark.parametrize(
    "answer",
    [
        "The cat sleeps.",  # dropped
        "The <1>cat sleeps.",  # no closing marker
        "The <1>cat</1> <1>sleeps</1>.",  # given twice
        "The </1>cat<1> sleeps.",  # the wrong way round
        "The <1></1>cat sleeps.",  # around nothing
        "The <1 >cat</1> sleeps.",  # corrupted
        "The <1>cat</1> <5>sleeps</5>.",  # one that was never sent
        "The \uff1c1\uff1ecat\uff1c/1\uff1e sleeps.",  # full-width brackets, as in CJK output
    ],
)
def test_a_dropped_or_broken_marker_gives_the_plain_text_and_says_so(answer):
    found = unmark(_markup(), ["s1"], answer)
    assert found.lost and found.formatting is None
    assert "<" not in found.text and found.text.replace("  ", " ").startswith("The ")
    assert "\uff1c" not in found.text


def test_markers_at_the_ends_leave_no_space_behind():
    found = unmark(_markup(), ["s1"], "<1> The cat</1> sleeps.")
    assert found.text == "The cat sleeps." and not found.lost
    assert found.formatting is not None and found.formatting.marks[0].start == 0
    found = unmark(_markup(), ["s1"], "The <1>cat sleeps. </1>")
    assert found.text == "The cat sleeps."
    assert found.formatting is not None and found.formatting.marks[0].end == len(found.text)


@pytest.mark.parametrize(
    ("href", "target"),
    [
        (" https://example.org/a ", "https://example.org/a"),
        ("mailto:a@example.org", "mailto:a@example.org"),
        ("javascript:alert(1)", None),
        ("file:///C:/secret.docx", None),
        ("chapitre2.xhtml", None),
        ("http://[bad", None),  # urlsplit raises
        ("https://example.org/\x01", None),  # XML refuses it
        ("", None),
        (None, None),
    ],
)
def test_link_target_keeps_only_addresses_a_word_file_can_open(href, target):
    assert link_target(href) == target


def test_nested_markers_are_accepted():
    found = unmark(_markup(), ["s2"], "<2>See <3>here</3></2> and note <4>2</4>.")
    assert not found.lost and found.formatting is not None


def test_the_flag_is_plain():
    flag = formatting_flag()
    assert flag.code is FlagCode.FORMATTING_LOST
    assert flag.explanation == FORMATTING_LOST
    assert FORMATTING_LOST.startswith("Formatting could not be kept here")
    assert "the download shows" in FORMATTING_LOST  # every download, not only Word (CGL-110)


def test_mark_helpers():
    marks = [InlineMark(start=2, end=6, kind=B)]
    assert clip_marks(marks, 4, 10) == [InlineMark(start=0, end=2, kind=B)]
    assert clip_marks(marks, 6, 10) == []
    text, joined = join_marked([("ab", []), ("cdef", marks)], "\n")
    assert text == "ab\ncdef" and joined == [InlineMark(start=5, end=9, kind=B)]
    assert strip_marked("  ab cd ", [InlineMark(start=2, end=8, kind=IT)]) == (
        "ab cd",
        [InlineMark(start=0, end=5, kind=IT)],
    )
    assert strip_marked(" x ", []) == ("x", [])
    overlapping = [InlineMark(start=0, end=3, kind=B), InlineMark(start=2, end=6, kind=B)]
    assert normalize_marks("abc de ", overlapping) == [InlineMark(start=0, end=6, kind=B)]


def test_a_marker_between_two_spaces_leaves_one():
    found = unmark(_markup(), ["s1"], "The <1> cat </1> sleeps.")
    assert found.text == "The cat sleeps." and not found.lost
    assert found.formatting is not None
    assert [(m.start, m.end) for m in found.formatting.marks] == [(4, 7)]
    found = unmark(_markup(), ["s1"], "The <1></1> cat sleeps.")
    assert found.lost and found.text == "The cat sleeps."
