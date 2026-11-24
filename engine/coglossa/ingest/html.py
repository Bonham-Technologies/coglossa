"""HTML ingestion (spec §4.1), also used for the XHTML chapters of EPUB files. Block-level
elements become blocks in document order: headings, paragraphs, list items, table cells,
footnotes and breaks. Inline markup is flattened to its text, and bold, italic, underlined,
superscript and subscript text and links to web addresses are recorded as the block's marks
(CGL-108); the head, scripts, styles, comments and note-reference markers are skipped."""

import re
from pathlib import Path
from typing import Any

import lxml.html
from lxml import etree

from coglossa.ingest.base import file_sha256, is_scene_break, make_blocks, strip_nul
from coglossa.schema import BlockKind, InlineMark, MarkKind, SourceDocument
from coglossa.segment.markup import MarkKey, link_target, marks_from_keys

Item = tuple[BlockKind, str, str | None]
_MARK_OF_TAG = {
    "b": MarkKind.BOLD,
    "strong": MarkKind.BOLD,
    "i": MarkKind.ITALIC,
    "em": MarkKind.ITALIC,
    "u": MarkKind.UNDERLINE,
    "sup": MarkKind.SUPERSCRIPT,
    "sub": MarkKind.SUBSCRIPT,
}

_SKIP = frozenset(
    {
        "head",
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "math",
        "iframe",
        "object",
        "select",
        "textarea",
        "button",
    }
)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_KIND_OF_TAG = {
    **{h: BlockKind.HEADING for h in _HEADINGS},
    "li": BlockKind.LIST_ITEM,
    "td": BlockKind.TABLE_CELL,
    "th": BlockKind.TABLE_CELL,
}
_BLOCK_TAGS = _HEADINGS | {
    "html",
    "body",
    "p",
    "div",
    "section",
    "article",
    "aside",
    "header",
    "footer",
    "main",
    "nav",
    "blockquote",
    "pre",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "li",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "td",
    "th",
    "caption",
    "figure",
    "figcaption",
    "address",
    "details",
    "summary",
    "hgroup",
    "center",
    "form",
    "fieldset",
    "legend",
}
_FOOTNOTE_TYPES = frozenset({"footnote", "endnote", "rearnote", "note"})
_FOOTNOTE_ROLES = frozenset({"doc-footnote", "doc-endnote"})
_NOTEREF_TYPES = frozenset({"noteref"})
_LINE_BREAK = "\ue000"  # private-use char standing for <br> until whitespace is normalized
_WHITESPACE = re.compile(r"\s+")
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")


def _tag(el: Any) -> str | None:
    if not isinstance(el.tag, str):
        return None  # a comment or processing instruction
    return el.tag.rsplit("}", 1)[-1].lower()


def _epub_types(el: Any) -> set[str]:
    return set((el.get("epub:type") or "").split())


def _is_footnote(el: Any) -> bool:
    return bool(_epub_types(el) & _FOOTNOTE_TYPES) or el.get("role") in _FOOTNOTE_ROLES


def _is_noteref(el: Any) -> bool:
    return bool(_epub_types(el) & _NOTEREF_TYPES) or el.get("role") == "doc-noteref"


def _clean(text: str) -> str:
    return strip_nul(text).replace(_LINE_BREAK, "")


def _normalize(text: str) -> str:
    lines = (_WHITESPACE.sub(" ", line).strip() for line in text.split(_LINE_BREAK))
    return "\n".join(line for line in lines if line)


Piece = tuple[str, frozenset[MarkKey]]  # text and its formatting


def _normalize_marked(pieces: list[Piece]) -> tuple[str, list[frozenset[MarkKey]]]:
    """`_normalize` of the pieces' text, with the formatting of each character it keeps (none
    when nothing is formatted). A collapsed run of whitespace takes its first character's."""
    text = "".join(t for t, _ in pieces)
    if not any(k for _, k in pieces):
        return _normalize(text), []
    keys = [k for t, k in pieces for _ in range(len(t))]
    out: list[str] = []
    out_keys: list[frozenset[MarkKey]] = []
    start = 0
    for line in text.split(_LINE_BREAK):
        chars: list[str] = []
        line_keys: list[frozenset[MarkKey]] = []
        space: frozenset[MarkKey] | None = None
        for i in range(start, start + len(line)):
            if text[i].isspace():  # what \s matches
                if chars and space is None:
                    space = keys[i]
                continue
            if space is not None:
                chars.append(" ")
                line_keys.append(space)
                space = None
            chars.append(text[i])
            line_keys.append(keys[i])
        if chars:
            if out:
                out.append("\n")
                out_keys.append(frozenset())
            out += chars
            out_keys += line_keys
        start += len(line) + 1
    return "".join(out), out_keys


def _mark(el: Any, tag: str) -> MarkKey | None:
    kind = _MARK_OF_TAG.get(tag)
    if kind is not None:
        return kind, None
    href = link_target(el.get("href")) if tag == "a" else None
    return (MarkKind.LINK, href) if href is not None else None


class _Collector:
    def __init__(self, locator: str | None) -> None:
        self.locator = locator
        self.items: list[Item] = []
        self.marks: list[list[InlineMark]] = []  # each item's
        self._buffer: list[Piece] = []
        self._kinds: list[BlockKind] = [BlockKind.PARAGRAPH]
        self._keys: frozenset[MarkKey] = frozenset()  # the formatting of the text being read

    def _text(self, text: str) -> None:
        self._buffer.append((text, self._keys))

    def flush(self) -> None:
        text, keys = _normalize_marked(self._buffer)
        self._buffer.clear()
        if not text:
            return
        kind = self._kinds[-1]
        if kind is BlockKind.PARAGRAPH and is_scene_break(text):
            kind = BlockKind.BREAK
        self.items.append((kind, text, self.locator))
        plain = not keys or kind is BlockKind.BREAK
        self.marks.append([] if plain else marks_from_keys(text, keys))

    def walk(self, el: Any) -> None:
        tag = _tag(el)
        if tag is None or tag in _SKIP or _is_noteref(el):
            return  # the caller keeps the element's tail text
        if tag == "br":
            self._text(_LINE_BREAK)
            return
        if tag == "hr":
            self.flush()
            self.items.append((BlockKind.BREAK, "* * *", self.locator))
            self.marks.append([])
            return
        footnote = _is_footnote(el)
        block = footnote or tag in _BLOCK_TAGS
        if block:
            self.flush()
            outer = self._kinds[-1]
            if outer is not BlockKind.PARAGRAPH:
                kind = outer  # blocks nested in a heading, item, cell or note keep its kind
            else:
                kind = BlockKind.FOOTNOTE if footnote else _KIND_OF_TAG.get(tag, outer)
            self._kinds.append(kind)
        outer_keys = self._keys
        mark = _mark(el, tag)
        if mark is not None:
            self._keys = outer_keys | {mark}
        if tag == "pre":
            self._text(_clean(el.text_content()).replace("\n", _LINE_BREAK))
        else:
            if el.text:
                self._text(_clean(el.text))
            for child in el:
                self.walk(child)
                if child.tail:
                    self._text(_clean(child.tail))
        self._keys = outer_keys
        if block:
            self.flush()
            self._kinds.pop()


def parse_html(data: bytes) -> Any | None:
    """The document's root element, or None for an empty document. UTF-8 is assumed; bytes
    that are not valid UTF-8 go to lxml, which honours the document's declared charset."""
    source: str | bytes
    try:
        source = _XML_DECLARATION.sub("", data.decode("utf-8-sig"), count=1)
    except UnicodeDecodeError:
        source = data
    if not source.strip():
        return None
    try:
        return lxml.html.document_fromstring(source)
    except etree.ParserError:  # "Document is empty": only comments or whitespace
        return None


def collect_marked(root: Any, locator: str | None) -> tuple[list[Item], list[list[InlineMark]]]:
    """The blocks under `root`, and each one's marks."""
    collector = _Collector(locator)
    collector.walk(root)
    collector.flush()
    return collector.items, collector.marks


def collect_blocks(root: Any, locator: str | None) -> list[Item]:
    return collect_marked(root, locator)[0]


class HtmlIngestor:
    format = "html"
    extensions = (".html", ".htm", ".xhtml")

    def ingest(self, path: Path) -> SourceDocument:
        root = parse_html(path.read_bytes())
        items, marks = collect_marked(root, None) if root is not None else ([], [])
        return SourceDocument(
            source_path=str(path),
            format=self.format,
            sha256=file_sha256(path),
            blocks=make_blocks(str(path), items, marks),
        )
