"""Inline formatting through translation (CGL-108).

Ingest records a block's inline formatting as marks over its plain text. Translators and the
reviewer see a chunk's segments with numbered paired markers around the formatted words,
"Un <1>beau</1> jour.", and give the same markers back around the words that carry that
meaning. Each marker stands for one set of formatting; the numbers run through the chunk, so a
passage that merges segments keeps them apart. A passage whose markers come back whole gets its
formatting as marks over its plain text; one whose markers are missing or broken keeps its plain
text and is flagged. Markers stay in the prompts: everything stored, scored or shown is plain.

A segment whose own text looks like a marker ("Press <1>") gets no markers (CGL-129): the rest
of its chunk is numbered past the numbers it shows, and in a passage over it those numbers are
its text, never markers. A passage over such a segment that had formatting is flagged."""

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from coglossa.schema import Block, Flag, FlagCode, InlineMark, MarkKind, Segment, SpanFormatting

MarkKey = tuple[MarkKind, str | None]  # a kind of formatting and, for a link, its target
_MARKER = re.compile(r"<(/?)(\d+)>")
# A marker, or what a model meant as one, also with full-width angle brackets (CJK output)
_MARKER_LIKE = re.compile(r"[<\uff1c]\s*/?\s*\d+\s*/?\s*[>\uff1e]")
# Markers in a row and the spaces either side of them
_AROUND_MARKERS = re.compile(rf"([ \t]*)(?:{_MARKER_LIKE.pattern})+([ \t]*)")
_LINK_SCHEMES = frozenset({"http", "https", "mailto", "ftp"})  # targets a Word file can open
FORMATTING_LOST = (
    "Formatting could not be kept here, so the Word download shows this passage as plain text."
)


def link_target(href: str | None) -> str | None:
    """`href` trimmed, when a link in a Word file may point to it: a web, mail or FTP address
    with no control characters (XML refuses most of them). Anything else, a script or a local
    file for instance, or an address that cannot be parsed, is no link."""
    href = (href or "").strip()
    if not href or any(c < " " or c == "\x7f" or c in "\ufffe\uffff" for c in href):
        return None
    if any("\ud800" <= c <= "\udfff" for c in href):
        return None
    try:
        scheme = urlsplit(href).scheme.lower()
    except ValueError:  # a malformed address, such as "http://["
        return None
    return href if scheme in _LINK_SCHEMES else None


def strip_markers(text: str) -> str:
    """`text` without marker-like text, for what a model wrote besides the translation (notes,
    rationale, issues, proposed terms): "drops <1>the cat</1> here" becomes "drops the cat
    here". Where a marker stood between two spaces, one is left."""
    if not _MARKER_LIKE.search(text):
        return text
    return _AROUND_MARKERS.sub(_one_space, text).strip()


def _one_space(m: re.Match[str]) -> str:
    return " " if m.group(1) and m.group(2) else m.group(1) or m.group(2)


def formatting_flag() -> Flag:
    return Flag(code=FlagCode.FORMATTING_LOST, explanation=FORMATTING_LOST)


def clip_marks(marks: Iterable[InlineMark], start: int, end: int) -> list[InlineMark]:
    """The marks over text[start:end], counted from `start`."""
    out = []
    for m in marks:
        a, b = max(m.start, start), min(m.end, end)
        if a < b:
            out.append(m.model_copy(update={"start": a - start, "end": b - start}))
    return out


def shift_marks(marks: Iterable[InlineMark], offset: int) -> list[InlineMark]:
    return [m.model_copy(update={"start": m.start + offset, "end": m.end + offset}) for m in marks]


def join_marked(
    parts: Sequence[tuple[str, Sequence[InlineMark]]], sep: str
) -> tuple[str, list[InlineMark]]:
    """Texts joined with `sep`, and their marks."""
    marks: list[InlineMark] = []
    pos = 0
    for k, (text, own) in enumerate(parts):
        pos += len(sep) if k else 0
        marks += shift_marks(own, pos)
        pos += len(text)
    return sep.join(t for t, _ in parts), marks


def strip_marked(text: str, marks: Sequence[InlineMark]) -> tuple[str, list[InlineMark]]:
    """`text.strip()`, and the marks over what is left."""
    stripped = text.strip()
    if not marks:
        return stripped, []
    start = len(text) - len(text.lstrip())
    return stripped, clip_marks(marks, start, start + len(stripped))


def char_keys(length: int, marks: Iterable[InlineMark]) -> list[frozenset[MarkKey]]:
    """The formatting of each character of a text `length` long."""
    keys: list[set[MarkKey]] = [set() for _ in range(length)]
    for m in marks:
        for i in range(max(m.start, 0), min(m.end, length)):
            keys[i].add((m.kind, m.href))
    return [frozenset(k) for k in keys]


def marks_from_keys(text: str, keys: Sequence[frozenset[MarkKey]]) -> list[InlineMark]:
    """One mark per maximal run of each kind of formatting, without whitespace at its ends."""
    marks: list[InlineMark] = []
    opened: dict[MarkKey, int] = {}
    for i in range(len(text) + 1):
        here = keys[i] if i < len(text) else frozenset()
        for key in [k for k in opened if k not in here]:
            a, b = _trim(text, opened.pop(key), i)
            if a < b:
                marks.append(InlineMark(start=a, end=b, kind=key[0], href=key[1]))
        for key in here:
            opened.setdefault(key, i)
    return sorted(marks, key=lambda m: (m.start, m.end, m.kind.value, m.href or ""))


def normalize_marks(text: str, marks: Iterable[InlineMark]) -> list[InlineMark]:
    """`marks` merged where they overlap or touch, and trimmed of whitespace."""
    return marks_from_keys(text, char_keys(len(text), marks))


def _trim(text: str, a: int, b: int) -> tuple[int, int]:
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    return a, b


def _runs(text: str, keys: Sequence[frozenset[MarkKey]]) -> Iterator[tuple[int, int, frozenset]]:
    """Maximal runs of one non-empty set of formatting, without whitespace at their ends."""
    i = 0
    while i < len(text):
        j = i + 1
        while j < len(text) and keys[j] == keys[i]:
            j += 1
        a, b = _trim(text, i, j)
        if keys[i] and a < b:
            yield a, b, keys[i]
        i = j


@dataclass(frozen=True)
class ChunkMarkup:
    """A chunk's segments with markers; empty when none of them has formatting."""

    marked: dict[str, str] = field(default_factory=dict)  # segment id -> text with markers
    numbers: dict[str, tuple[int, ...]] = field(default_factory=dict)  # segment id -> markers
    formats: dict[int, frozenset[MarkKey]] = field(default_factory=dict)  # marker -> formatting
    # segment id -> the numbers its own marker-like text shows; it is sent without markers
    literal: dict[str, frozenset[int]] = field(default_factory=dict)
    unsent: frozenset[str] = frozenset()  # of those, the segments with formatting

    def text(self, segment: Segment) -> str:
        """What a prompt shows of `segment`."""
        return self.marked.get(segment.id, segment.text)


def _number(marker: str) -> int:
    """The number in marker-like text; -1, never a marker's, when it is too long to be one
    (`int` refuses more than 4300 digits)."""
    digits = re.search(r"\d+", marker).group()
    return int(digits) if len(digits) <= 18 else -1


def chunk_markup(segments: Sequence[Segment], blocks: Mapping[str, Block]) -> ChunkMarkup:
    literal = {
        s.id: frozenset(_number(m.group()) for m in _MARKER_LIKE.finditer(s.text))
        for s in segments
        if _MARKER_LIKE.search(s.text)
    }
    taken = frozenset().union(*literal.values())  # never a marker's number in this chunk
    marked: dict[str, str] = {}
    numbers: dict[str, tuple[int, ...]] = {}
    formats: dict[int, frozenset[MarkKey]] = {}
    unsent: set[str] = set()
    n = 0
    for segment in segments:
        block = blocks.get(segment.block_id)
        if block is None or not block.provenance.marks:
            continue
        marks = clip_marks(block.provenance.marks, segment.start, segment.end)
        runs = list(_runs(segment.text, char_keys(len(segment.text), marks)))
        if segment.id in literal:
            if runs:
                unsent.add(segment.id)  # its text would be read as markers: it goes plain
            continue
        pieces: list[str] = []
        own: list[int] = []
        pos = 0
        for a, b, key in runs:
            n += 1
            while n in taken:
                n += 1
            formats[n] = key
            own.append(n)
            pieces += [segment.text[pos:a], f"<{n}>", segment.text[a:b], f"</{n}>"]
            pos = b
        if own:
            marked[segment.id] = "".join(pieces) + segment.text[pos:]
            numbers[segment.id] = tuple(own)
    return ChunkMarkup(
        marked=marked, numbers=numbers, formats=formats, literal=literal, unsent=frozenset(unsent)
    )


@dataclass(frozen=True)
class Unmarked:
    text: str  # without markers
    formatting: SpanFormatting | None  # the formatting its markers carried
    lost: bool  # its source has formatting that did not come back whole


def unmark(markup: ChunkMarkup, segment_ids: Sequence[str], text: str) -> Unmarked:
    """A passage over `segment_ids` as a model wrote it: its text without markers and the
    formatting the markers carried. Every marker of its segments must come back once as an
    opening and once as a closing marker around some text; nothing else counts. Marker-like
    text with a number its segments' source shows is that text, not a marker; a passage over a
    segment whose formatting was not sent is plain and flagged."""
    unsent = any(sid in markup.unsent for sid in segment_ids)
    if not markup.marked:  # no markers were sent: the text is left as it is
        return Unmarked(text, None, unsent and bool(text.strip()))
    own = {n for sid in segment_ids for n in markup.literal.get(sid, ())}
    pieces: list[str] = []
    tags: list[tuple[int, bool, int]] = []  # (number, closing, where in the plain text)
    broken = False
    length = pos = 0
    for m in _MARKER_LIKE.finditer(text):
        if _number(m.group()) in own:
            continue  # the source's own text: it stays in the next piece
        piece = _after_marker(pieces, text[pos : m.start()])
        pieces.append(piece)
        length += len(piece)
        pos = m.end()
        strict = _MARKER.fullmatch(m.group())
        if strict is None:
            broken = True
        else:
            tags.append((_number(m.group()), strict.group(1) == "/", length))
    pieces.append(_after_marker(pieces, text[pos:]))
    plain = "".join(pieces)
    if text == text.strip():  # "<1>Un</1> jour </2>": no space left where a marker was at an end
        lead = len(plain) - len(plain.lstrip())
        plain = plain.strip()
        tags = [(n, closes, min(max(at - lead, 0), len(plain))) for n, closes, at in tags]
    expected = {n for sid in segment_ids for n in markup.numbers.get(sid, ())}
    if not plain.strip():
        return Unmarked(plain, None, False)
    if unsent or not expected:
        return Unmarked(plain, None, unsent)
    ranges = None if broken else _ranges(tags, expected)
    if ranges is None or any(not plain[a:b].strip() for a, b in ranges.values()):
        return Unmarked(plain, None, True)
    marks = [
        InlineMark(start=a, end=b, kind=kind, href=href)
        for n, (a, b) in ranges.items()
        for kind, href in markup.formats[n]
    ]
    return Unmarked(plain, SpanFormatting(text=plain, marks=normalize_marks(plain, marks)), False)


def _after_marker(pieces: list[str], piece: str) -> str:
    """`piece` without its first space when a marker stood between it and a space: "Un <1>
    beau </1> jour" reads "Un beau jour", not "Un beau  jour"."""
    last = next((p[-1] for p in reversed(pieces) if p), "")
    return piece[1:] if last in (" ", "\t") and piece[:1] in (" ", "\t") else piece


def _ranges(tags: list[tuple[int, bool, int]], expected: set[int]) -> dict[int, tuple[int, int]]:
    opening: dict[int, int] = {}
    closing: dict[int, int] = {}
    for n, closes, at in tags:
        side = closing if closes else opening
        if n not in expected or n in side:
            return None  # a marker that is not this passage's, or one given twice
        side[n] = at
    if opening.keys() != expected or closing.keys() != expected:
        return None
    if any(opening[n] >= closing[n] for n in expected):
        return None
    return {n: (opening[n], closing[n]) for n in sorted(expected)}
