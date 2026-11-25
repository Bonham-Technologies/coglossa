"""Markdown ingestion: headings, list items, paragraphs and breaks. A fenced code block
(```, or ~~~ with a language), also indented under a list item or inside a > blockquote, is one
block left as written (CGL-111): it is never sent to a model and comes back unchanged. Limits:
a fence not closed inside its blockquote, a fence on a list marker's line ("1. ```") and a code
block indented without a fence are read as text and translated. A `code span` inside a
sentence stays part of the sentence, so the sentence still reads whole, and its text is checked
to come back unchanged; a paragraph that is only a code span is left as written."""

import re
from collections.abc import Iterator
from pathlib import Path

from coglossa.ingest.base import (
    NO_PROTECTION,
    Protection,
    file_sha256,
    is_scene_break,
    make_blocks,
    read_utf8,
    split_paragraphs,
    strip_nul,
    trim_blank_lines,
)
from coglossa.schema import BlockKind, KeepReason, SourceDocument

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")
_QUOTES_RE = re.compile(r"(?:[ \t]*>)*")
_QUOTE_RE = re.compile(r"[ \t]*> ?")
_CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)

Item = tuple[BlockKind, str, str | None]


def _paragraph_items(para: str) -> list[Item]:
    if is_scene_break(para):
        return [(BlockKind.BREAK, para, None)]
    lines = para.split("\n")
    heading = _HEADING_RE.match(lines[0])
    if heading:
        rest = "\n".join(lines[1:]).strip()
        items: list[Item] = [(BlockKind.HEADING, heading.group(1), None)]
        return items + (_paragraph_items(rest) if rest else [])
    list_matches = [_LIST_RE.match(line) for line in lines]
    if all(list_matches):
        return [(BlockKind.LIST_ITEM, m.group(1), None) for m in list_matches if m]
    return [(BlockKind.PARAGRAPH, para, None)]


def _protection(text: str) -> Protection:
    """A block that is one code span is left as written; otherwise its code spans' text is
    checked in the translation."""
    spans = list(_CODE_SPAN_RE.finditer(text))
    if len(spans) == 1 and spans[0].group() == text.strip():
        return KeepReason.CODE, []
    literals = [m.group(2).strip() for m in spans]
    return None, [t for t in literals if t]


def _depth(line: str) -> int:
    """How many blockquote markers (>) the line starts with."""
    m = _QUOTES_RE.match(line)
    return m.group().count(">") if m else 0


def _unquote(line: str, depth: int) -> str:
    """`line` without its first `depth` blockquote markers, each with the space after it."""
    for _ in range(depth):
        m = _QUOTE_RE.match(line)
        if m is None:
            break
        line = line[m.end() :]
    return line


def _dedent(line: str, indent: int) -> str:
    """`line` without up to `indent` characters of its leading whitespace."""
    k = 0
    while k < indent and k < len(line) and line[k] in " \t":
        k += 1
    return line[k:]


def _opens_fence(line: str) -> tuple[int, re.Match[str]] | None:
    """A fence that opens a code block, with its blockquote depth. A bare ~~~ is a scene break,
    as in plain text files, so a tilde fence needs an info string (~~~python); a backtick
    fence's may not hold one."""
    depth = _depth(line)
    m = _FENCE_RE.match(_unquote(line, depth))
    if m is None:
        return None
    fence, info = m.group(2), m.group(3).strip()
    if fence[0] == "`" and "`" in info:
        return None
    if fence[0] == "~" and not info:
        return None
    return depth, m


def _sections(text: str) -> Iterator[tuple[bool, str]]:
    """The text cut at fenced code blocks: (True, code) for each block, (False, text) between.
    A fence may be indented (under a list item) and inside a blockquote; its body and its
    closing line lose the same blockquote markers and up to the same indent. A fence that is
    not closed before the file or its blockquote ends opens no block: its text is translated
    as before."""
    lines = text.split("\n")
    prose: list[str] = []
    # per fence character and blockquote depth, the shortest fence known to have no closing
    # line before `stop` (the end of its blockquote, or of the file): a longer one opened before
    # `stop` has none either, so it is not looked for again (linear time)
    unclosed: dict[tuple[str, int], tuple[int, int]] = {}
    i = 0
    while i < len(lines):
        opening = _opens_fence(lines[i])
        end = -1
        if opening is not None:
            depth, m = opening
            fence = m.group(2)
            key = (fence[0], depth)
            known = unclosed.get(key)
            if known is None or i >= known[1] or len(fence) < known[0]:
                closing = re.compile(rf"^[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
                j = i + 1
                while j < len(lines) and _depth(lines[j]) >= depth:
                    if closing.match(_unquote(lines[j], depth)):
                        end = j
                        break
                    j += 1
                if end < 0:
                    unclosed[key] = (len(fence), j)
        if opening is None or end < 0:
            prose.append(lines[i])
            i += 1
            continue
        yield False, "\n".join(prose)
        prose = []
        depth, m = opening
        indent = len(m.group(1))
        code = [_dedent(_unquote(line, depth), indent) for line in lines[i + 1 : end]]
        yield True, trim_blank_lines(code)
        i = end + 1
    yield False, "\n".join(prose)


class MarkdownIngestor:
    format = "md"
    extensions = (".md", ".markdown")

    def ingest(self, path: Path) -> SourceDocument:
        text = strip_nul(read_utf8(path)).replace("\r\n", "\n").replace("\r", "\n")
        items: list[Item] = []
        protect: list[Protection] = []
        for is_code, part in _sections(text):
            if is_code:
                if part:
                    items.append((BlockKind.PARAGRAPH, part, None))
                    protect.append((KeepReason.CODE, []))
                continue
            for item in (i for p in split_paragraphs(part) for i in _paragraph_items(p)):
                items.append(item)
                protect.append(
                    NO_PROTECTION if item[0] is BlockKind.BREAK else _protection(item[1])
                )
        return SourceDocument(
            source_path=str(path),
            format=self.format,
            sha256=file_sha256(path),
            blocks=make_blocks(str(path), items, protect=protect),
        )
