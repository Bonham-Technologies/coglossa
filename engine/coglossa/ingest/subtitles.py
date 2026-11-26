"""Subtitle files: SubRip (.srt) and WebVTT (.vtt) (CGL-131).

Every cue with text is one block, its lines joined with line breaks, so a cue of two lines is
one passage. The cue's number, its times and cue settings, a WebVTT file's heading, and its
NOTE, STYLE and REGION blocks are never part of a block: the subtitle download
(`coglossa.export.subtitles`) writes each cue's translation in place of its text and leaves
every other character of the file as it was.

Inside a cue, `<b>`, `<i>` and `<u>` are the block's marks, as bold, italic and underlined
words are for Word files (CGL-108). Other tags (a WebVTT voice `<v Name>`, a class `<c.name>`, a
language `<lang en>`, ruby or a karaoke time `<00:00:01.000>`, an SRT `<font>` or a `{\\an8}`
position) are kept as written: tags that open the cue or close it, around all of its text,
stay outside the block; tags inside its text stay in the block's text, protected as text the
translation must keep (CGL-111). In a WebVTT file, `&amp;`, `&lt;` and the other character
references in a cue are read as the characters they stand for.

A cue whose times cannot be read (a timing line such as `00:00:01,000 --> 00:00:04,000` that is
missing or broken) makes the whole file unreadable, with the line named: a guess could move
text into the wrong cue."""

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from coglossa.ingest.base import IngestError, file_sha256, make_blocks, read_utf8
from coglossa.schema import BlockKind, InlineMark, MarkKind, SourceDocument
from coglossa.segment.markup import MarkKey, marks_from_keys

SRT, VTT = "srt", "vtt"
NAMES = {SRT: "SRT subtitle", VTT: "WebVTT subtitle"}
A_FILE = {SRT: "an SRT subtitle file", VTT: "a WebVTT subtitle file"}
EXAMPLES = {SRT: "00:00:01,000 --> 00:00:04,000", VTT: "00:00:01.000 --> 00:00:04.000"}

_TIME = {
    # hours, minutes and seconds, then milliseconds after a comma (a dot is read too)
    SRT: r"\d+:[0-5]\d:[0-5]\d[,.]\d{1,3}",
    # hours are optional
    VTT: r"(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}",
}
_TIMING = {
    fmt: re.compile(rf"^[ \t]*({t})[ \t]*-->[ \t]*({t})(?:[ \t]+.*)?$") for fmt, t in _TIME.items()
}
_EOL = re.compile(r"\r\n|\r|\n")
_TAG = {
    SRT: re.compile(r"<[^<>\r\n]*>|\{\\[^{}\r\n]*\}"),  # also an ASS override, {\an8}
    VTT: re.compile(r"<[^<>\r\n]*>"),
}
_FORMATTING = {"b": MarkKind.BOLD, "i": MarkKind.ITALIC, "u": MarkKind.UNDERLINE}
_VTT_BLOCKS = ("NOTE", "STYLE", "REGION")  # WebVTT blocks that hold no cue text
_SHOWN = 60  # characters of a broken line an error message shows


@dataclass(frozen=True)
class _Line:
    start: int
    end: int  # before its line break
    text: str


@dataclass(frozen=True)
class Cue:
    """A cue with text: where its text is in the file and what it says."""

    number: int  # its place among the file's cues, from 1
    time: str  # its start time as written
    start: int  # the part of the file the translation replaces: from its first character
    end: int  # to the character after its last; tags around it and spaces stay as they are
    text: str
    marks: list[InlineMark] = field(default_factory=list)
    literals: list[str] = field(default_factory=list)  # tags inside the text, kept as written
    newline: str = "\n"  # the line break the cue's lines are written with

    @property
    def locator(self) -> str:
        return f"cue {self.number} at {self.time}"


def _lines(text: str) -> list[_Line]:
    out: list[_Line] = []
    pos = 0
    while pos < len(text):
        m = _EOL.search(text, pos)
        end = m.start() if m else len(text)
        out.append(_Line(pos, end, text[pos:end]))
        pos = m.end() if m else len(text)
    return out


def _groups(lines: list[_Line]) -> list[list[tuple[int, _Line]]]:
    """The file's blocks: runs of lines that are not blank, with their numbers from 1."""
    groups: list[list[tuple[int, _Line]]] = []
    current: list[tuple[int, _Line]] = []
    for n, line in enumerate(lines, 1):
        if line.text.strip():
            current.append((n, line))
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _shown(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _SHOWN else text[: _SHOWN - 1] + "…"


class _Reader:
    def __init__(self, text: str, name: str, fmt: str) -> None:
        self.text, self.name, self.fmt = text, name, fmt
        self.timing = _TIMING[fmt]
        self.cues: list[Cue] = []
        self.count = 0  # every cue, with text or not

    def fail(self, n: int, problem: str) -> IngestError:
        return IngestError(
            f"{self.name} is not a readable {NAMES[self.fmt]} file: line {n} {problem}"
        )

    def bad_timing(self, n: int, line: _Line) -> IngestError:
        return self.fail(
            n,
            f"should give the cue's start and end times, as in {EXAMPLES[self.fmt]}, but reads "
            f"“{_shown(line.text)}”",
        )

    def read(self) -> list[Cue]:
        groups = _groups(_lines(self.text))
        if self.fmt == VTT:
            groups = self._vtt_heading(groups)
        for group in groups:
            if self.fmt == VTT and group[0][1].text.split(maxsplit=1)[0] in _VTT_BLOCKS:
                continue  # a NOTE, STYLE or REGION block: nothing to translate
            self._group(group)
        return self.cues

    def _vtt_heading(self, groups: list[list[tuple[int, _Line]]]) -> list[list[tuple[int, _Line]]]:
        first = self.text.split("\n", 1)[0].split("\r", 1)[0]
        if not re.match(r"WEBVTT(?:[ \t]|$)", first):
            raise IngestError(
                f"{self.name} is not a readable {NAMES[VTT]} file: its first line should be "
                f"WEBVTT, but reads “{_shown(first)}”"
            )
        heading, rest = groups[0], groups[1:]
        for n, line in heading:
            if "-->" in line.text:
                raise self.fail(n, "is a cue's times; leave a blank line after the WEBVTT heading")
        return rest

    def _starts_cue(self, k: int, lines: list[tuple[int, _Line]]) -> int:
        """Where the timing line of the cue that starts at lines[k] is."""
        n, line = lines[k]
        if self.timing.match(line.text):
            return k
        following = lines[k + 1] if k + 1 < len(lines) else None
        # a cue's number (SRT) or its identifier (WebVTT, any line without -->)
        named = line.text.strip().isdigit() if self.fmt == SRT else "-->" not in line.text
        if named and following is not None:
            if self.timing.match(following[1].text):
                return k + 1
            raise self.bad_timing(*following)
        if named:
            raise self.fail(
                n,
                f"should be followed by the cue's start and end times, as in {EXAMPLES[self.fmt]}",
            )
        raise self.bad_timing(n, line)

    def _group(self, lines: list[tuple[int, _Line]]) -> None:
        k = 0
        while k < len(lines):
            t = self._starts_cue(k, lines)
            end = t + 1
            # a cue's text runs to a blank line; a timing line before one starts the next cue
            while end < len(lines):
                line = lines[end][1].text
                if self.fmt == VTT and "-->" in line:
                    break
                if self.fmt == SRT and self.timing.match(line):
                    if lines[end - 1][1].text.strip().isdigit() and end - 1 > t:
                        end -= 1  # the number of the next cue
                    break
                end += 1
            self._cue(lines[t][1], [line for _, line in lines[t + 1 : end]])
            k = end

    def _cue(self, timing: _Line, lines: list[_Line]) -> None:
        self.count += 1
        if not lines:
            return
        start, end = lines[0].start, lines[-1].end
        raw = self.text[start:end]
        m = _EOL.search(raw)
        newline = m.group() if m else "\n"
        found = cue_text(raw, self.fmt)
        if found is None:
            return
        a, b, text, marks, literals = found
        time = self.timing.match(timing.text).group(1)  # type: ignore[union-attr]
        self.cues.append(
            Cue(self.count, time, start + a, start + b, text, marks, literals, newline)
        )


def _tokens(raw: str, fmt: str) -> list[tuple[str, int, int, MarkKind | None]]:
    """The cue's text as pieces (kind, start, end, formatting): "text", "open" and "close" of
    bold, italic or underlined words, and "tag" for every other tag. A closing </i> with no <i>
    open is another tag."""
    out: list[tuple[str, int, int, MarkKind | None]] = []
    opened: dict[MarkKind, int] = {}
    pos = 0
    for m in _TAG[fmt].finditer(raw):
        if m.start() > pos:
            out.append(("text", pos, m.start(), None))
        name = m.group()[1:-1].strip().lower()
        closing = name.startswith("/")
        kind = _FORMATTING.get(name.lstrip("/").strip()) if m.group()[0] == "<" else None
        if kind is not None and not closing:
            opened[kind] = opened.get(kind, 0) + 1
            out.append(("open", m.start(), m.end(), kind))
        elif kind is not None and opened.get(kind):
            opened[kind] -= 1
            out.append(("close", m.start(), m.end(), kind))
        else:
            out.append(("tag", m.start(), m.end(), None))
        pos = m.end()
    if pos < len(raw):
        out.append(("text", pos, len(raw), None))
    return out


def cue_text(raw: str, fmt: str) -> tuple[int, int, str, list[InlineMark], list[str]] | None:
    """A cue's text as written (its lines and their line breaks): the part of it a
    translation replaces (`raw[a:b]`), and that part's plain text, marks and protected tags;
    None when it has no text."""
    tokens = _tokens(raw, fmt)

    def outer(kind: str, a: int, b: int) -> bool:
        return kind == "tag" or not raw[a:b].strip()

    first, last = 0, len(tokens)
    while first < last and outer(*tokens[first][:3]):
        first += 1
    while last > first and outer(*tokens[last - 1][:3]):
        last -= 1
    if any(kind == "tag" for kind, *_ in tokens[first:last]):
        first, last = 0, len(tokens)  # tags inside the text: every tag stays in it
    if first == last:
        return None
    a, b = tokens[first][1], tokens[last - 1][2]
    while a < b and raw[a].isspace():
        a += 1
    while b > a and raw[b - 1].isspace():
        b -= 1
    chars: list[str] = []
    keys: list[frozenset[MarkKey]] = []
    literals: list[str] = []
    active: dict[MarkKind, int] = {}
    for kind, start, end, mark in tokens:
        start, end = max(start, a), min(end, b)
        if start >= end:
            continue
        key = frozenset((k, None) for k, n in active.items() if n)
        piece = raw[start:end]
        if kind == "open":
            active[mark] = active.get(mark, 0) + 1  # type: ignore[index]
            continue
        if kind == "close":
            active[mark] -= 1  # type: ignore[index]
            continue
        if kind == "tag":
            literals.append(piece)
        else:
            piece = _EOL.sub("\n", piece)
            if fmt == VTT:
                piece = html.unescape(piece)
        chars.append(piece)
        keys += [key] * len(piece)
    text = "".join(chars)
    if not text.strip():
        return None
    return a, b, text, marks_from_keys(text, keys), literals


def read_cues(text: str, name: str, fmt: str) -> list[Cue]:
    """Every cue with text, in the order of the file. `text` is the decoded file."""
    return _Reader(text, name, fmt).read()


class _SubtitleIngestor:
    format: str
    extensions: tuple[str, ...]

    def ingest(self, path: Path) -> SourceDocument:
        cues = read_cues(read_utf8(path), path.name, self.format)
        items = [(BlockKind.PARAGRAPH, c.text, c.locator) for c in cues]
        protect = [(None, c.literals) for c in cues]
        return SourceDocument(
            source_path=str(path),
            format=self.format,
            sha256=file_sha256(path),
            blocks=make_blocks(str(path), items, [c.marks for c in cues], protect),
        )


class SrtIngestor(_SubtitleIngestor):
    format = SRT
    extensions = (".srt",)


class VttIngestor(_SubtitleIngestor):
    format = VTT
    extensions = (".vtt",)
