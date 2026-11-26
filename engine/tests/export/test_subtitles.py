"""The subtitle download (CGL-131): the same file, byte for byte outside each cue's text, with
the translation in place of the text."""

import re
from datetime import UTC, datetime

import pytest

from coglossa.export.office import ExportError
from coglossa.export.subtitles import cue_texts, split_marked, translated_subtitles
from coglossa.ingest import registry
from coglossa.ingest.base import make_blocks
from coglossa.ingest.subtitles import read_cues
from coglossa.pipeline import RunOptions, run_document
from coglossa.schema import BlockKind, InlineMark, MarkKind, Report, SpanFormatting
from tests.export.conftest import make_span, translated_report
from tests.ingest.test_subtitles import SRT, VTT

_SRT_TIMING = re.compile(r"^\d+:\d\d:\d\d,\d{3} --> .*$")
_VTT_TIMING = re.compile(r"^(?:\d\d:)?\d\d:\d\d\.\d{3} --> .*$")


async def _no_sleep(_):
    return None


async def _translate(path, cfg, fake_backend) -> Report:
    report = await run_document(
        path,
        cfg,
        {"fake": fake_backend},
        RunOptions(target_lang="en", source_lang="fr"),
        sleep=_no_sleep,
        now=lambda: datetime(2026, 11, 26, tzinfo=UTC),
    )
    assert isinstance(report, Report)
    return report


def _frame(text: str, timing: re.Pattern[str]) -> list[str]:
    """Every line of a subtitle file but its cue text: numbers, times, headings, notes."""
    out, in_cue = [], False
    for line in text.splitlines():
        if not line.strip():
            in_cue = False
            out.append(line)
        elif timing.match(line):
            in_cue = True
            out.append(line)
        elif not in_cue:
            out.append(line)
    return out


def _outside(original: str, got: str, report: Report) -> None:
    """Both files are the same outside the text of each cue (acceptance 1)."""
    fmt = report.document.format
    name = "f." + fmt
    a, b = read_cues(original, name, fmt), read_cues(got, name, fmt)
    assert [c.locator for c in a] == [c.locator for c in b]
    pieces_a, pieces_b, pa, pb = [], [], 0, 0
    for ca, cb in zip(a, b, strict=True):
        pieces_a.append(original[pa : ca.start])
        pieces_b.append(got[pb : cb.start])
        pa, pb = ca.end, cb.end
    assert pieces_a + [original[pa:]] == pieces_b + [got[pb:]]


async def test_an_srt_file_comes_back_with_the_same_cues_translated(
    tmp_path, make_config, fake_backend
):
    path = tmp_path / "film.srt"
    original = SRT.encode("utf-8")
    path.write_bytes(original)
    report = await _translate(path, make_config(), fake_backend)
    data = translated_subtitles(report, original, "srt")
    assert data.startswith(b"\xef\xbb\xbf")  # the byte order mark stays
    got = data.decode("utf-8-sig")
    source = SRT.removeprefix("\ufeff")
    assert _frame(got, _SRT_TIMING) == _frame(source, _SRT_TIMING)
    _outside(source, got, report)
    assert got == (
        "1\r\n"
        "00:00:01,000 --> 00:00:04,000\r\n"
        "[en] <i>Bonjour,</i>\r\n"  # the line break inside a passage, and the italics
        "mon ami.\r\n"
        "\r\n"
        "2\r\n"
        "00:00:05,000 --> 00:00:06,500 X1:10 X2:20 Y1:30 Y2:40\r\n"
        "{\\an8}[en] - Salut.\r\n"  # two passages of one cue, on their own lines
        "[en] - Ça va ?\r\n"
        "\r\n"
        "3\r\n"
        "00:00:07,000 --> 00:00:08,000\r\n"
        "\r\n"
        "4\r\n"
        "00:00:09,000 --> 00:00:10,000\r\n"
        '[en] <b>Le chat</b> dort <font color="#ff0000">ici</font>.\r\n'
    )


async def test_a_webvtt_file_comes_back_with_its_heading_notes_and_voices(
    tmp_path, make_config, fake_backend
):
    path = tmp_path / "film.vtt"
    original = VTT.encode("utf-8")
    path.write_bytes(original)
    report = await _translate(path, make_config(), fake_backend)
    got = translated_subtitles(report, original, "vtt").decode("utf-8")
    assert _frame(got, _VTT_TIMING) == _frame(VTT, _VTT_TIMING)
    _outside(VTT, got, report)
    assert got.split("\n\n")[:3] == VTT.split("\n\n")[:3]  # heading, STYLE and NOTE
    assert got.split("\n\n")[3:] == [
        "intro\n"
        "00:01.000 --> 00:04.000 align:start position:10%\n"
        "<v Roger Bingham>[en] Tom &amp; Jerry\n"
        "<b>dorment</b> bien.</v>",
        "00:00:05.000 --> 00:00:06.000\n[en] <v Anne>Oui.</v> <v Paul>Non.</v>",
        "00:07.000 --> 00:08.000\n[en] <c.loud>Attention</c> &lt;ici&gt; <00:07.500>maintenant\n",
    ]


def _cues_report(fmt: str, original: str, spans) -> Report:
    """A report of `original` read as the ingestor reads it, one segment per cue."""
    cues = read_cues(original, "f." + fmt, fmt)
    blocks = make_blocks(
        "f." + fmt,
        [(BlockKind.PARAGRAPH, c.text, c.locator) for c in cues],
        [c.marks for c in cues],
        [(None, c.literals) for c in cues],
    )
    return translated_report(blocks, original.encode(), fmt=fmt, name="f").model_copy(
        update={"spans": spans}
    )


TWO_CUES = (
    "1\n00:00:01,000 --> 00:00:02,000\nJe suis allé\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nau marché hier soir.\n"
)


def test_a_sentence_over_two_cues_is_shared_between_them():
    report = _cues_report(
        "srt", TWO_CUES, [make_span("p1", "s1", "s2", "I went to the market last night.")]
    )
    got = translated_subtitles(report, TWO_CUES.encode(), "srt").decode()
    assert got == (
        "1\n00:00:01,000 --> 00:00:02,000\nI went to the\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nmarket last night.\n"
    )


def test_a_short_translation_over_two_cues_leaves_the_second_empty():
    report = _cues_report("srt", TWO_CUES, [make_span("p1", "s1", "s2", "Oui")])
    got = translated_subtitles(report, TWO_CUES.encode(), "srt").decode()
    assert got == ("1\n00:00:01,000 --> 00:00:02,000\nOui\n\n2\n00:00:02,000 --> 00:00:04,000\n\n")


def test_a_two_line_cue_written_on_one_line_is_broken_again():
    text = "1\n00:00:01,000 --> 00:00:04,000\nLe chat dort\nsur la table.\n"
    report = _cues_report(
        "srt", text, [make_span("p1", "s1", "s1", "The cat is sleeping on the table.")]
    )
    got = translated_subtitles(report, text.encode(), "srt").decode()
    assert got.endswith("\nThe cat is sleeping\non the table.\n")
    report = _cues_report("srt", text, [make_span("p1", "s1", "s1", "Sleeping.")])
    got = translated_subtitles(report, text.encode(), "srt").decode()
    assert got.endswith("\nSleeping.\n")  # no space to break it at


def test_formatting_comes_back_and_blank_lines_and_arrows_never_break_the_file():
    text = (
        "1\n00:00:01,000 --> 00:00:04,000\n<i>Le chat</i> dort.\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\nNon.\n"
    )
    italic = make_span("p1", "s1", "s1", "The cat \n\n --> sleeps.").model_copy(
        update={
            "formatting": SpanFormatting(
                text="The cat \n\n --> sleeps.",
                marks=[InlineMark(start=4, end=7, kind=MarkKind.ITALIC)],
            )
        }
    )
    report = _cues_report("srt", text, [italic, make_span("p2", "s2", "s2", "")])
    got = translated_subtitles(report, text.encode(), "srt").decode()
    assert got == (
        "1\n00:00:01,000 --> 00:00:04,000\nThe <i>cat</i>\n-> sleeps.\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\nNon.\n"  # nothing translated: it keeps its text
    )


def test_split_marked_cuts_at_spaces_or_anywhere_without_them():
    marks = [InlineMark(start=0, end=5, kind=MarkKind.BOLD)]
    assert split_marked("Hello there world", marks, [1, 1]) == [
        ("Hello", [InlineMark(start=0, end=5, kind=MarkKind.BOLD)]),
        ("there world", []),
    ]
    assert split_marked("我昨天去了市场", [], [1, 1]) == [("我昨天去", []), ("了市场", [])]
    assert split_marked("Oui", [], [1, 1, 1]) == [("Oui", []), ("", []), ("", [])]


def test_cue_texts_leave_out_blocks_nothing_was_translated_into():
    report = _cues_report("srt", TWO_CUES, [make_span("p1", "s1", "s2", "")])
    assert cue_texts(report) == {}


def test_only_a_subtitle_run_and_its_own_file_can_be_downloaded(report):
    with pytest.raises(ExportError, match="only a translation of an SRT subtitle file"):
        translated_subtitles(report, b"", "srt")
    one = _cues_report("srt", TWO_CUES, [make_span("p1", "s1", "s2", "x")])
    with pytest.raises(ExportError, match="only a translation of a WebVTT subtitle file"):
        translated_subtitles(one, TWO_CUES.encode(), "vtt")
    with pytest.raises(ExportError, match="is not the file that was translated"):
        translated_subtitles(one, TWO_CUES.encode() + b"\n", "srt")


def test_a_file_that_no_longer_matches_its_blocks_is_refused():
    one = _cues_report("srt", TWO_CUES, [make_span("p1", "s1", "s2", "x")])
    blocks = [one.blocks[0]]
    broken = one.model_copy(update={"blocks": blocks})
    with pytest.raises(ExportError, match="could not be written back as a subtitle file"):
        translated_subtitles(broken, TWO_CUES.encode(), "srt")


def test_a_registry_round_trip_reads_the_download_again(tmp_path, make_config):
    """The download is itself a readable subtitle file."""
    report = _cues_report(
        "srt", TWO_CUES, [make_span("p1", "s1", "s2", "I went to the market last night.")]
    )
    out = tmp_path / "out.srt"
    out.write_bytes(translated_subtitles(report, TWO_CUES.encode(), "srt"))
    assert [b.text for b in registry.ingest_file(out).blocks] == [
        "I went to the",
        "market last night.",
    ]
