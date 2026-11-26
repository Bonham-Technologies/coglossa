"""The subtitle download (CGL-131): an SRT or WebVTT file with each cue's translation in place of
its text. Every other character of the file stays as it was: the cue numbers, times and cue
settings, a WebVTT file's heading and its NOTE, STYLE and REGION blocks, blank lines, line
breaks and a byte order mark.

A cue's text is the translation of its passages. A passage over sentences of several cues (a
sentence that runs on from one subtitle to the next) is shared out between them by the length
of their original text, at spaces where it has them (a translation with too few spaces
for every cue leaves the last of them empty; no text is lost or repeated). Line breaks: the
passages of a cue are joined with a line break where its original lines broke between them;
where a cue of several lines comes back as one line, it is broken again at the spaces closest
to where its original lines broke. Bold, italic and underlined words come back as `<b>`, `<i>`
and `<u>` where the passage kept them (CGL-108), and tags kept as written stay as they are. A
cue none of whose text was translated keeps its original text."""

import codecs
import hashlib
import itertools
import re
import unicodedata
from collections.abc import Sequence

from coglossa.export.office import ExportError, blocks_in_order, mismatch
from coglossa.ingest.base import IngestError
from coglossa.ingest.subtitles import A_FILE, VTT, Cue, read_cues
from coglossa.schema import Block, InlineMark, MarkKind, Report, Segment, kept_formatting
from coglossa.segment.markup import (
    MarkKey,
    char_keys,
    clip_marks,
    marks_from_keys,
    shift_marks,
    strip_marked,
)
from coglossa.text import clean_text

_TAGS = ((MarkKind.BOLD, "b"), (MarkKind.ITALIC, "i"), (MarkKind.UNDERLINE, "u"))
_VTT_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}

Marked = tuple[str, list[InlineMark]]


def _spaceless(c: str) -> bool:
    """A character of a script written without spaces between words: Thai, Lao, Myanmar,
    Khmer, Chinese, Japanese."""
    return (
        "\u0e00" <= c <= "\u0eff"
        or "\u1000" <= c <= "\u109f"
        or "\u1780" <= c <= "\u17ff"
        or ("\u2e80" <= c <= "\u9fff")
    )


_LEADING_VOWELS = set("เแโใไເແໂໃໄ")  # Thai, Lao


def _may_cut(text: str, at: int) -> bool:
    """Whether `text` can be cut before its character `at` without parting a letter from its
    vowel or tone marks (Thai, Lao, Myanmar, Khmer) or a vowel written before its consonant."""
    c = text[at]
    return not (
        unicodedata.category(c).startswith("M") or c in "‌‍" or text[at - 1] in _LEADING_VOWELS
    )


def split_marked(text: str, marks: Sequence[InlineMark], weights: Sequence[int]) -> list[Marked]:
    """`text` cut into len(weights) parts about as long, in proportion, as `weights`: at the
    space closest to each cut, or at any character in a text with no spaces written in a
    script without them (Chinese, Japanese, Thai). The parts are trimmed, with their marks; a
    part is empty when the text has nowhere left to cut."""
    total = sum(weights) or 1
    spaces = [i for i, c in enumerate(text) if c.isspace()]
    anywhere = not spaces and any(_spaceless(c) for c in text)
    cuts: list[tuple[int, int]] = []  # each part's (start, end)
    start, done = 0, 0
    for weight in weights[:-1]:
        done += weight
        ideal = round(len(text) * done / total)
        if anywhere:
            at = max(start, min(ideal, len(text)))
            while 0 < at < len(text) and not _may_cut(text, at):
                at += 1
            cuts.append((start, at))
            start = at
            continue
        later = [i for i in spaces if i >= start]
        at = min(later, key=lambda i: abs(i - ideal)) if later else len(text)
        cuts.append((start, at))
        start = min(at + 1, len(text))
    cuts.append((start, len(text)))
    return [strip_marked(text[a:b], shift_marks(clip_marks(marks, a, b), -a)) for a, b in cuts]


def cue_texts(report: Report) -> dict[str, Marked]:
    """The translation of each block with a translated passage over any of its text."""
    by_block = {b.id: b for b in report.blocks}
    position = {s.id: k for k, s in enumerate(report.segments)}
    # each block's parts: (its first and last segment the part translates, text, marks)
    parts: dict[str, list[tuple[Segment, Segment, str, list[InlineMark]]]] = {}
    for span in report.spans:
        if not span.text.strip():
            continue
        formatting = kept_formatting(span)
        marks = formatting.marks if formatting is not None else []
        covered = report.segments[position[span.start] : position[span.end] + 1]
        groups = [list(g) for _, g in itertools.groupby(covered, key=lambda s: s.block_id)]
        if len(groups) == 1:
            shares = [strip_marked(span.text, marks)]
        else:
            shares = split_marked(span.text, marks, [sum(len(s.text) for s in g) for g in groups])
        for group, (text, own) in zip(groups, shares, strict=True):
            parts.setdefault(group[0].block_id, []).append((group[0], group[-1], text, own))
    out: dict[str, Marked] = {}
    for block_id, own_parts in parts.items():
        source = by_block[block_id].text
        text, marks = "", []
        after = 0  # where the source text the last part translates ends
        for first, last, part, part_marks in own_parts:
            if part:
                if text:
                    text += "\n" if "\n" in source[after : first.start] else " "
                marks += shift_marks(part_marks, len(text))
                text += part
            after = last.end
        out[block_id] = (text, marks)
    return out


def _tidy(text: str, marks: list[InlineMark]) -> Marked:
    """One cue's text as a subtitle file can hold it: no blank lines, which would end the cue,
    no "-->", which would start one ("->" in its place), and no spaces at the ends of its
    lines. A carriage return is a line break: a file reader takes it for one."""
    keys = char_keys(len(text), marks)
    chars: list[tuple[str, frozenset[MarkKey]]] = []
    for i, (c, k) in enumerate(zip(text, keys, strict=True)):
        if c == "\r":
            if text.startswith("\n", i + 1):
                continue
            c = "\n"
        if c == ">":
            while len(chars) >= 2 and chars[-1][0] == "-" and chars[-2][0] == "-":
                chars.pop()  # "-->", "--->" and longer arrows become "->"
        chars.append((c, k))
    lines: list[list[tuple[str, frozenset[MarkKey]]]] = [[]]
    for c, k in chars:
        if c == "\n":
            lines.append([])
        else:
            lines[-1].append((c, k))
    kept: list[tuple[str, frozenset[MarkKey]]] = []
    for line in lines:
        while line and line[0][0].isspace():
            line.pop(0)
        while line and line[-1][0].isspace():
            line.pop()
        if line:
            if kept:
                kept.append(("\n", frozenset()))
            kept += line
    out = "".join(c for c, _ in kept)
    return out, marks_from_keys(out, [k for _, k in kept])


def _rebreak(text: str, source: str, literals: Sequence[str]) -> str:
    """`text`, written on one line, broken at the spaces closest to where the lines of its
    `source` break, never inside a tag kept as written; unchanged when it already has line
    breaks or has too few spaces."""
    if "\n" in text or "\n" not in source:
        return text
    breaks = [i for i, c in enumerate(source) if c == "\n"]
    tag = _protected(text, literals)
    spaces = [i for i, c in enumerate(text) if c == " " and not tag[i]]
    if len(spaces) < len(breaks):
        return text
    chars = list(text)
    for b in breaks:
        ideal = round(len(text) * b / len(source))
        free = [i for i in spaces if chars[i] == " "]
        chars[min(free, key=lambda i: abs(i - ideal))] = "\n"
    return "".join(chars)


def _protected(text: str, literals: Sequence[str]) -> list[bool]:
    """Which characters of `text` are tags kept as written."""
    out = [False] * len(text)
    for literal in sorted(set(literals), key=len, reverse=True):
        for m in re.finditer(re.escape(literal), text):
            out[m.start() : m.end()] = [True] * (m.end() - m.start())
    return out


def render(text: str, marks: list[InlineMark], cue: Cue, fmt: str) -> str:
    """A cue's translated text as the file writes it: its formatting as tags, its line breaks
    the cue's own, and, in a WebVTT file, &, < and > written as character references outside
    the tags kept as written."""
    keys = char_keys(len(text), marks)
    kept = _protected(text, cue.literals) if fmt == VTT else [True] * len(text)
    out: list[str] = []
    i = 0
    while i < len(text):
        j = i + 1
        while j < len(text) and keys[j] == keys[i]:
            j += 1
        kinds = {kind for kind, _ in keys[i]}
        tags = [tag for kind, tag in _TAGS if kind in kinds]
        body = "".join(c if kept[k] else _VTT_ESCAPES.get(c, c) for k, c in enumerate(text[i:j], i))
        out += [f"<{t}>" for t in tags] + [body] + [f"</{t}>" for t in reversed(tags)]
        i = j
    return "".join(out).replace("\n", cue.newline)


def translated_subtitles(report: Report, original: bytes, fmt: str) -> bytes:
    """The SRT (`fmt` "srt") or WebVTT ("vtt") file the run translated, with the translation
    of each cue in place of its text."""
    name = report.document.source_path.replace("\\", "/").rsplit("/", 1)[-1]
    if report.document.format != fmt:
        raise ExportError(f"only a translation of {A_FILE[fmt]} can be downloaded as one")
    if hashlib.sha256(original).hexdigest() != report.document.sha256:
        raise ExportError(f"the stored copy of {name} is not the file that was translated")
    bom = original.startswith(codecs.BOM_UTF8)
    try:
        decoded = original.decode("utf-8-sig")
        cues = read_cues(decoded, name, fmt)
    except (UnicodeDecodeError, IngestError) as e:  # pragma: no cover - the file the run read
        raise ExportError(f"{name} could not be read again ({e})") from e
    blocks: list[Block] = blocks_in_order(report)
    if [c.locator for c in cues] != [b.provenance.locator for b in blocks]:
        raise mismatch(report, "subtitle")
    texts = cue_texts(report)
    out: list[str] = []
    pos = 0
    for cue, block in zip(cues, blocks, strict=True):
        got = texts.get(block.id)
        if got is None:
            continue  # nothing of it was translated: it keeps its text
        text, marks = _tidy(clean_text(got[0]), got[1])
        # tidied again: a break at one of two spaces in a row would leave a space at a line's
        # end, and breaks at both a blank line, which would end the cue
        text, marks = _tidy(_rebreak(text, block.text, cue.literals), marks)
        out += [decoded[pos : cue.start], render(text, marks, cue, fmt)]
        pos = cue.end
    out.append(decoded[pos:])
    return (codecs.BOM_UTF8 if bom else b"") + "".join(out).encode("utf-8")
