"""DOCX ingestion in reading order (CGL-87).

Reading order: the page headers first, then the body in document order, then the footnotes, the
endnotes and the page footers. In the body a text box follows the paragraph it is anchored in, a
nested table sits in place inside its cell, and text in content controls and tracked insertions
is read where it stands (tracked deletions are not). Notes come after the body, not after their
reference, so a sentence is never split by its note: footnotes are numbered 1, 2, … and endnotes
i, ii, … in the order they are first cited, a block that cites one records its mark
(`note_refs`) and each note's blocks carry it (`note`). A header or footer that another section
repeats exactly is read once. Table rows and cells in content controls are read. Charts,
diagrams, embedded objects and documents, equations and comments cannot be read: the
document's notices name them (a document notice, not a run error)."""

import dataclasses
import re
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

import docx
from docx.enum.style import WD_STYLE_TYPE
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from lxml import etree

from coglossa.ingest.base import UNREAD_PREFIX, IngestError, file_sha256, is_scene_break
from coglossa.schema import (
    Block,
    BlockKind,
    Provenance,
    Region,
    SourceDocument,
    TableCellPosition,
)

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}
_P, _TBL, _SDT, _SDT_CONTENT = qn("w:p"), qn("w:tbl"), qn("w:sdt"), qn("w:sdtContent")
_CUSTOM_XML, _ALT_CHUNK = qn("w:customXml"), qn("w:altChunk")
_TR, _TC = qn("w:tr"), qn("w:tc")
_HEADING_RE = re.compile(r"heading\s*(\d+)")

# What cannot be read, in the order a notice names it: (singular, plural, XPath under a paragraph)
_UNREADABLE = (
    ("chart", "charts", "c:chart"),
    ("diagram", "diagrams", "dgm:relIds"),
    ("embedded object", "embedded objects", "w:object"),
    ("embedded document", "embedded documents", None),  # w:altChunk, a block of its own
    ("equation", "equations", "m:oMath[not(ancestor::m:oMath)]"),
    ("comment", "comments", None),  # in the comments part
)
_ROMAN = (
    (1000, "m"),
    (900, "cm"),
    (500, "d"),
    (400, "cd"),
    (100, "c"),
    (90, "xc"),
    (50, "l"),
    (40, "xl"),
    (10, "x"),
    (9, "ix"),
    (5, "v"),
    (4, "iv"),
    (1, "i"),
)


@cache
def _compiled(path: str) -> etree.XPath:
    return etree.XPath(path, namespaces=_NS)


def _xpath(element: Any, path: str) -> Any:
    return _compiled(path)(element)


def _roman(n: int) -> str:
    out = ""
    for value, letters in _ROMAN:
        while n >= value:
            out, n = out + letters, n - value
    return out


def _paragraph_kind(style_name: str, text: str) -> BlockKind:
    style = style_name.lower()
    if style.startswith(("heading", "title")):
        return BlockKind.HEADING
    if is_scene_break(text):
        return BlockKind.BREAK
    if style.startswith("list"):
        return BlockKind.LIST_ITEM
    return BlockKind.PARAGRAPH


def _heading_level(style_name: str) -> int | None:
    style = style_name.lower()
    if style.startswith("title"):
        return 0
    match = _HEADING_RE.match(style)
    return int(match.group(1)) if match else None


def _join(*parts: str | None) -> str | None:
    return ", ".join(p for p in parts if p) or None


def _plural_list(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def unread_notice(counts: Counter[str]) -> list[str]:
    names = [
        f"{counts[one]} {one if counts[one] == 1 else many}"
        for one, many, _ in _UNREADABLE
        if counts[one]
    ]
    return [f"{UNREAD_PREFIX}{_plural_list(names)}."] if names else []


def _related_xml(part: Any, reltype: str) -> Any | None:
    for rel in part.rels.values():
        if rel.reltype == reltype and not rel.is_external:
            target = rel.target_part
            element = getattr(target, "element", None)
            return element if element is not None else parse_xml(target.blob)
    return None


@dataclasses.dataclass(frozen=True)
class _Context:
    region: Region | None = None
    locator: str | None = None
    cell: TableCellPosition | None = None
    note: str | None = None  # the mark of the note being read


@dataclasses.dataclass
class _Paragraph:
    text: str
    refs: list[str]
    boxes: list[Any]


@dataclasses.dataclass
class _Cell:
    tc: Any
    row: int
    col: int
    col_span: int
    row_span: int = 1


def _shape(provenance: Provenance) -> tuple[Any, ...]:
    """Where a header or footer block sits, without the table numbering, which differs between
    two copies of the same header."""
    cell = provenance.cell
    position: list[tuple[int, ...]] = []
    while cell is not None:
        position.append((cell.rows, cell.cols, cell.row, cell.col, cell.row_span, cell.col_span))
        cell = cell.parent
    return (provenance.level, tuple(position), tuple(provenance.note_refs))


class _Reader:
    def __init__(self, document: Any, source_path: str) -> None:
        self.document = document
        self.source_path = source_path
        self.styles = {s.style_id: s.name for s in document.styles if s.style_id}
        default = document.styles.default(WD_STYLE_TYPE.PARAGRAPH)
        self.default_style = default.name if default is not None else ""
        self.blocks: list[Block] = []
        self.unread: Counter[str] = Counter()
        self.marks: dict[tuple[str, str], str] = {}  # (note kind, note id) -> mark
        self.tables = 0
        self.text_boxes = 0
        self.furniture: set[tuple[Any, ...]] = set()  # header and footer parts already read

    def read(self) -> None:
        self._page_furniture(Region.HEADER)
        self._container(self.document.element.body, _Context())
        self._notes("footnote", RT.FOOTNOTES, Region.FOOTNOTE)
        self._notes("endnote", RT.ENDNOTES, Region.ENDNOTE)
        self._page_furniture(Region.FOOTER)
        comments = _related_xml(self.document.part, RT.COMMENTS)
        if comments is not None:
            self.unread["comment"] += len(_xpath(comments, "./w:comment"))

    def _add(self, kind: BlockKind, text: str, ctx: _Context, **extra: Any) -> None:
        index = len(self.blocks)
        provenance = Provenance(
            source_path=self.source_path,
            index=index,
            locator=ctx.locator,
            region=ctx.region,
            cell=ctx.cell,
            note=ctx.note,
            **extra,
        )
        self.blocks.append(Block(id=f"b{index + 1}", kind=kind, text=text, provenance=provenance))

    def _mark(self, kind: str, note_id: str) -> str:
        key = (kind, note_id)
        if key not in self.marks:
            n = 1 + sum(1 for k, _ in self.marks if k == kind)
            self.marks[key] = str(n) if kind == "footnote" else _roman(n)
        return self.marks[key]

    def _children(self, element: Any, tags: tuple[str, ...] = (_P, _TBL)) -> list[Any]:
        """The children of `element` with one of `tags` (a container's paragraphs and tables, a
        table's rows or a row's cells), unwrapping content controls and custom XML."""
        out: list[Any] = []
        for child in element:
            if child.tag in tags:
                out.append(child)
            elif child.tag == _SDT:
                for content in child.iterchildren(_SDT_CONTENT):
                    out.extend(self._children(content, tags))
            elif child.tag == _CUSTOM_XML:
                out.extend(self._children(child, tags))
            elif child.tag == _ALT_CHUNK:
                self.unread["embedded document"] += 1
        return out

    def _paragraph(self, p: Any) -> _Paragraph:
        """The paragraph's own text, the notes it cites and its text boxes; what cannot be read
        is counted. Content of the paragraphs of its text boxes and of fallback copies (the old
        readers' duplicate of a text box) is left out."""
        depth = int(_xpath(p, "count(ancestor-or-self::w:p)"))
        own = f"[count(ancestor::w:p) = {depth} and not(ancestor::mc:Fallback)]"

        def find(path: str) -> list[Any]:
            return _xpath(p, f".//{path}{own}")

        runs = find("w:r[not(ancestor::w:del) and not(ancestor::w:moveFrom)]")
        refs = [
            self._mark(ref.tag.rsplit("}", 1)[1].removesuffix("Reference"), ref.get(qn("w:id")))
            for ref in find("*[self::w:footnoteReference or self::w:endnoteReference]")
        ]
        for name, _, path in _UNREADABLE:
            if path is not None:
                self.unread[name] += len(find(path))
        return _Paragraph(
            text="".join(r.text for r in runs), refs=refs, boxes=find("w:txbxContent")
        )

    def _container(self, element: Any, ctx: _Context, *, cell: bool = False) -> None:
        """A cell's paragraphs are joined into one block, up to a nested table or a text box."""
        buffer: list[str] = []
        refs: list[str] = []

        def flush() -> None:
            text = "\n".join(buffer).strip()
            if text:
                self._add(BlockKind.TABLE_CELL, text, ctx, note_refs=list(refs))
            buffer.clear()
            refs.clear()

        for child in self._children(element):
            if child.tag == _TBL:
                flush()
                self._table(child, ctx)
                continue
            paragraph = self._paragraph(child)
            if cell:
                buffer.append(paragraph.text)
                refs.extend(paragraph.refs)
                if paragraph.boxes:
                    flush()
            else:
                self._paragraph_block(child, paragraph, ctx)
            for box in paragraph.boxes:
                self.text_boxes += 1
                box_ctx = dataclasses.replace(
                    ctx, locator=_join(ctx.locator, f"text box {self.text_boxes}")
                )
                self._container(box, box_ctx)
        flush()

    def _paragraph_block(self, p: Any, paragraph: _Paragraph, ctx: _Context) -> None:
        text = paragraph.text.strip()
        if not text:
            return
        if ctx.note is not None:
            self._add(BlockKind.FOOTNOTE, text, ctx, note_refs=paragraph.refs)
            return
        style_id = p.style
        style = self.default_style if style_id is None else self.styles.get(style_id, style_id)
        kind = _paragraph_kind(style or "", text)
        level = _heading_level(style or "") if kind is BlockKind.HEADING else None
        self._add(kind, text, ctx, level=level, note_refs=paragraph.refs)

    def _table(self, tbl: Any, ctx: _Context) -> None:
        self.tables += 1
        number = self.tables
        rows = self._children(tbl, (_TR,))
        cells: list[_Cell] = []
        above: dict[int, _Cell] = {}  # grid column -> the cell a vertical merge continues
        width = 0
        for r, tr in enumerate(rows, start=1):
            col = 1 + sum(int(v) for v in _xpath(tr, "./w:trPr/w:gridBefore/@w:val"))
            for tc in self._children(tr, (_TC,)):
                span = tc.grid_span
                origin = above.get(col)
                if tc.vMerge == "continue" and origin is not None:
                    origin.row_span = r - origin.row + 1
                else:
                    above[col] = _Cell(tc=tc, row=r, col=col, col_span=span)
                    cells.append(above[col])
                col += span
            width = max(width, col - 1)
        cols = max(width, len(_xpath(tbl, "./w:tblGrid/w:gridCol")))
        for c in cells:
            position = TableCellPosition(
                table=number,
                rows=len(rows),
                cols=cols,
                row=c.row,
                col=c.col,
                row_span=c.row_span,
                col_span=c.col_span,
                parent=ctx.cell,
            )
            locator = _join(ctx.locator, f"table {number} row {c.row} col {c.col}")
            cell_ctx = dataclasses.replace(ctx, cell=position, locator=locator)
            self._container(c.tc, cell_ctx, cell=True)

    def _page_furniture(self, region: Region) -> None:
        """Every section's headers (or footers) that are in use and not linked to the
        previous section's. A header (or footer) that repeats one already read, block for
        block, is read once; within one, every block is kept, repeats too."""
        name = region.value
        odd_and_even = self.document.settings.odd_and_even_pages_header_footer
        for section in self.document.sections:
            variants = [("", getattr(section, name))]
            if section.different_first_page_header_footer:
                variants.append(("first-page ", getattr(section, f"first_page_{name}")))
            if odd_and_even:
                variants.append(("even-page ", getattr(section, f"even_page_{name}")))
            for prefix, part in variants:
                if not part.is_linked_to_previous:
                    self._furniture_part(part._element, region, prefix + name)

    def _furniture_part(self, element: Any, region: Region, locator: str) -> None:
        start, tables, boxes = len(self.blocks), self.tables, self.text_boxes
        unread = self.unread.copy()
        self._container(element, _Context(region=region, locator=locator))
        added = self.blocks[start:]
        # what this part could not hold makes it differ too, even with no text or the same text
        left_out = tuple(sorted((self.unread - unread).items()))
        key = (region, left_out, *((b.kind, b.text, _shape(b.provenance)) for b in added))
        if key not in self.furniture:
            self.furniture.add(key)
            return
        # a repeat: forget it, its tables and text boxes, and what it could not hold
        del self.blocks[start:]
        self.tables, self.text_boxes, self.unread = tables, boxes, unread

    def _notes(self, kind: str, reltype: str, region: Region) -> None:
        root = _related_xml(self.document.part, reltype)
        if root is None:
            return
        notes = {
            n.get(qn("w:id")): n
            for n in _xpath(root, f"./w:{kind}")
            if n.get(qn("w:type")) in (None, "normal")  # not the separators
        }
        cited = [note_id for k, note_id in self.marks if k == kind and note_id in notes]
        for note_id in cited + [i for i in notes if i not in cited]:
            mark = self._mark(kind, note_id)
            ctx = _Context(region=region, locator=f"{kind} {mark}", note=mark)
            self._container(notes[note_id], ctx)


class DocxIngestor:
    format = "docx"
    extensions = (".docx",)

    def ingest(self, path: Path) -> SourceDocument:
        try:
            document = docx.Document(str(path))
        except Exception as e:  # python-docx raises several unrelated types for bad files
            raise IngestError(f"{path.name} is not a readable DOCX file ({e})") from e
        reader = _Reader(document, str(path))
        try:
            reader.read()
        except Exception as e:  # malformed markup in a file that opens: bad numbers, ids, parts
            raise IngestError(
                f"{path.name} is not a readable DOCX file: part of it is damaged ({e})"
            ) from e
        return SourceDocument(
            source_path=str(path),
            format=self.format,
            sha256=file_sha256(path),
            blocks=reader.blocks,
            notices=unread_notice(reader.unread),
        )
