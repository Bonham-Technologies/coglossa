"""Subtitle files (CGL-131): one block per cue with text, its times and everything else left
out of the blocks."""

import pytest

from coglossa.ingest import registry
from coglossa.ingest.base import IngestError
from coglossa.schema import BlockKind, InlineMark, MarkKind

SRT = (
    "﻿1\r\n"
    "00:00:01,000 --> 00:00:04,000\r\n"
    "<i>Bonjour,</i>\r\n"
    "mon ami.\r\n"
    "\r\n"
    "2\r\n"
    "00:00:05,000 --> 00:00:06,500 X1:10 X2:20 Y1:30 Y2:40\r\n"
    "{\\an8}- Salut.\r\n"
    "- Ça va ?\r\n"
    "\r\n"
    "3\r\n"
    "00:00:07,000 --> 00:00:08,000\r\n"
    "\r\n"
    "4\r\n"
    "00:00:09,000 --> 00:00:10,000\r\n"
    '<b>Le chat</b> dort <font color="#ff0000">ici</font>.\r\n'
)

VTT = (
    "WEBVTT - Le chat\n"
    "Kind: captions\n"
    "Language: fr\n"
    "\n"
    "STYLE\n"
    "::cue(.loud) { color: red }\n"
    "\n"
    "NOTE Une note\n"
    "qui ne se traduit pas\n"
    "\n"
    "intro\n"
    "00:01.000 --> 00:04.000 align:start position:10%\n"
    "<v Roger Bingham>Tom &amp; Jerry\n"
    "<b>dorment</b> bien.</v>\n"
    "\n"
    "00:00:05.000 --> 00:00:06.000\n"
    "<v Anne>Oui.</v> <v Paul>Non.</v>\n"
    "\n"
    "00:07.000 --> 00:08.000\n"
    "<c.loud>Attention</c> &lt;ici&gt; <00:07.500>maintenant\n"
)


def _ingest(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return registry.ingest_file(path)


def test_an_srt_file_is_one_block_per_cue_with_text(tmp_path):
    doc = _ingest(tmp_path, "Film.SRT", SRT)
    assert doc.format == "srt"
    assert [b.kind for b in doc.blocks] == [BlockKind.PARAGRAPH] * 3
    # a cue of two lines is one passage; the times, numbers and the {\an8} position are left out
    assert [b.text for b in doc.blocks] == [
        "Bonjour,\nmon ami.",
        "- Salut.\n- Ça va ?",
        'Le chat dort <font color="#ff0000">ici</font>.',
    ]
    # the empty cue 3 is no block, but keeps its place in the numbering
    assert [b.provenance.locator for b in doc.blocks] == [
        "cue 1 at 00:00:01,000",
        "cue 2 at 00:00:05,000",
        "cue 4 at 00:00:09,000",
    ]
    assert doc.blocks[0].provenance.marks == [InlineMark(start=0, end=8, kind=MarkKind.ITALIC)]
    assert doc.blocks[2].provenance.marks == [InlineMark(start=0, end=7, kind=MarkKind.BOLD)]
    # tags inside the text are kept as written (CGL-111)
    assert doc.blocks[2].provenance.literals == ['<font color="#ff0000">', "</font>"]


def test_a_webvtt_file_leaves_out_its_heading_notes_and_styles(tmp_path):
    doc = _ingest(tmp_path, "film.vtt", VTT)
    assert doc.format == "vtt"
    assert [b.text for b in doc.blocks] == [
        "Tom & Jerry\ndorment bien.",  # the voice around the whole cue stays outside
        "<v Anne>Oui.</v> <v Paul>Non.</v>",
        "<c.loud>Attention</c> <ici> <00:07.500>maintenant",
    ]
    assert [b.provenance.locator for b in doc.blocks] == [
        "cue 1 at 00:01.000",
        "cue 2 at 00:00:05.000",
        "cue 3 at 00:07.000",
    ]
    assert doc.blocks[0].provenance.marks == [InlineMark(start=12, end=19, kind=MarkKind.BOLD)]
    assert doc.blocks[1].provenance.literals == ["<v Anne>", "</v>", "<v Paul>", "</v>"]
    assert doc.blocks[2].provenance.literals == ["<c.loud>", "</c>", "<00:07.500>"]


def test_a_cue_after_one_with_no_blank_line_is_its_own(tmp_path):
    text = "1\n00:00:01,000 --> 00:00:02,000\nUn.\n2\n00:00:03,000 --> 00:00:04,000\nDeux.\n"
    doc = _ingest(tmp_path, "a.srt", text)
    assert [(b.text, b.provenance.locator) for b in doc.blocks] == [
        ("Un.", "cue 1 at 00:00:01,000"),
        ("Deux.", "cue 2 at 00:00:03,000"),
    ]


def test_odd_spacing_and_cues_with_only_tags(tmp_path):
    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\n  Un.  \n\n\n\n"  # spaces and blank lines
        "2\n00:00:03,000 --> 00:00:04,000\n{\\an8}\n\n"  # only a position: nothing to read
        "00:00:05,000 --> 00:00:06,000\nTrois.\n"
        "00:00:07,000 --> 00:00:08,000\nQuatre.\n\n\n"  # no number, no blank line before
    )
    doc = _ingest(tmp_path, "a.srt", srt)
    assert [(b.text, b.provenance.locator) for b in doc.blocks] == [
        ("Un.", "cue 1 at 00:00:01,000"),
        ("Trois.", "cue 3 at 00:00:05,000"),
        ("Quatre.", "cue 4 at 00:00:07,000"),
    ]
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\nUn.\n00:03.000 --> 00:04.000\nDeux.\n"
    doc = _ingest(tmp_path, "a.vtt", vtt)
    assert [b.text for b in doc.blocks] == ["Un.", "Deux."]


def test_an_srt_file_without_cue_numbers_is_read(tmp_path):
    doc = _ingest(tmp_path, "a.srt", "00:00:01,000 --> 00:00:02,000\nUn.\n")
    assert [b.text for b in doc.blocks] == ["Un."]


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        (
            "film.srt",
            "1\n00:00:01,000 -> 00:00:04,000\nBonjour.\n",
            "film.srt is not a readable SRT subtitle file: line 2 should give the cue's start "
            "and end times, as in 00:00:01,000 --> 00:00:04,000, but reads "
            "“00:00:01,000 -> 00:00:04,000”",
        ),
        (
            "film.srt",
            "1\n00:00:01,000 --> 00:00:04,000\nBonjour.\n\n2\n00:00:61,000 --> 00:00:62,000\nOui.",
            "line 6 should give the cue's start and end times",
        ),
        (
            "film.srt",
            "Bonjour.\n",
            "line 1 should give the cue's start and end times",
        ),
        (
            "film.srt",
            "1\n00:00:01,000 --> 00:00:04,000\nBonjour.\n\n2\n",
            "line 5 should be followed by the cue's start and end times",
        ),
        (
            "film.vtt",
            "WEBVTT\n\n00:00:01,000 --> 00:00:04,000\nBonjour.\n",
            "film.vtt is not a readable WebVTT subtitle file: line 3 should give the cue's start "
            "and end times, as in 00:00:01.000 --> 00:00:04.000, but reads "
            "“00:00:01,000 --> 00:00:04,000”",
        ),
        (
            "film.vtt",
            "WEBVTT\n\nintro\nBonjour, ceci est une très longue ligne qui ne finit jamais vraiment",
            "but reads “Bonjour, ceci est une très longue ligne qui ne finit jamais…”",
        ),
        (
            "film.vtt",
            "1\n00:00:01.000 --> 00:00:04.000\nBonjour.\n",
            "film.vtt is not a readable WebVTT subtitle file: its first line should be WEBVTT",
        ),
        (
            "film.vtt",
            "WEBVTT\n00:01.000 --> 00:04.000\nBonjour.\n",
            "line 2 is a cue's times; leave a blank line after the WEBVTT heading",
        ),
    ],
)
def test_a_malformed_file_fails_with_a_plain_message(tmp_path, name, text, message):
    with pytest.raises(IngestError) as e:
        _ingest(tmp_path, name, text)
    assert message in str(e.value)


def test_a_file_with_no_cue_text_has_nothing_to_translate(tmp_path):
    with pytest.raises(IngestError, match="contains no translatable text"):
        _ingest(tmp_path, "empty.vtt", "WEBVTT\n\n00:01.000 --> 00:02.000\n<i></i>\n")


def test_a_file_that_is_not_utf8_is_refused(tmp_path):
    path = tmp_path / "latin.srt"
    path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nÇa va.\n".encode("latin-1"))
    with pytest.raises(IngestError, match="not valid UTF-8"):
        registry.ingest_file(path)
