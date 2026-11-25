"""PowerPoint (.pptx) ingestion (CGL-110).

Reading order: slide by slide; on each slide its shapes in the order they are stacked (shapes in
a group in place of the group), then the slide's speaker notes. Every paragraph of a text frame
with text is a block: a slide title is a heading, a table cell's paragraph a table cell (with its
position, so the Word download can rebuild the table), the rest paragraphs. Slide numbers and
dates are left out; charts, SmartArt diagrams and embedded objects cannot be read, and the
document's notices name them. Bold, italic, underlined, superscript and subscript runs and links
to web and mail addresses are the block's marks, as for Word files (CGL-108).

The PowerPoint download (`coglossa.export.pptx`) writes the translation back into a copy of the
original deck. It walks the deck the same way (`walk`), so the n-th paragraph it finds is the
paragraph the n-th block was read from."""

import dataclasses
from collections import Counter
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.oxml.ns import qn

from coglossa.ingest.base import (
    IngestError,
    check_office_file,
    error_reason,
    file_sha256,
    unread_notices,
)
from coglossa.schema import (
    Block,
    BlockKind,
    InlineMark,
    MarkKind,
    Provenance,
    SourceDocument,
    TableCellPosition,
)
from coglossa.segment.markup import MarkKey, link_target, marks_from_keys, strip_marked

APP, EXTENSION = "PowerPoint", ".pptx"
_R, _BR, _FLD, _RPR = qn("a:r"), qn("a:br"), qn("a:fld"), qn("a:rPr")
_HLINK = qn("a:hlinkClick")
_RID = qn("r:id")
_TRUE = ("1", "true")
_TITLES = {  # placeholder -> heading level: 0 a title slide's title, 1 a slide's title
    PP_PLACEHOLDER.CENTER_TITLE: 0,
    PP_PLACEHOLDER.TITLE: 1,
    PP_PLACEHOLDER.VERTICAL_TITLE: 1,
}
_LEFT_OUT = (PP_PLACEHOLDER.SLIDE_NUMBER, PP_PLACEHOLDER.DATE)
_DIAGRAM_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_OLE = (MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT, MSO_SHAPE_TYPE.LINKED_OLE_OBJECT)
# What cannot be read, in the order a notice names it: (key, singular, plural)
_UNREADABLE = (
    ("chart", "chart", "charts"),
    ("diagram", "diagram", "diagrams"),
    ("object", "embedded object", "embedded objects"),
)


@dataclasses.dataclass(frozen=True)
class PptxParagraph:
    """A paragraph with text, where it is and what it says."""

    p: Any  # the a:p element
    part: Any  # the slide's or notes slide's part, which holds its links
    kind: BlockKind
    locator: str
    text: str
    marks: list[InlineMark]
    level: int | None = None
    cell: TableCellPosition | None = None


def run_keys(r: Any, part: Any) -> frozenset[MarkKey]:
    """A run's own formatting and the web or mail address it links to."""
    rpr = r.find(_RPR)
    if rpr is None:
        return frozenset()
    keys: set[MarkKey] = set()
    if rpr.get("b") in _TRUE:
        keys.add((MarkKind.BOLD, None))
    if rpr.get("i") in _TRUE:
        keys.add((MarkKind.ITALIC, None))
    if rpr.get("u") not in (None, "none"):
        keys.add((MarkKind.UNDERLINE, None))
    baseline = rpr.get("baseline")
    if baseline is not None and baseline.lstrip("-").isdigit() and int(baseline):
        keys.add((MarkKind.SUPERSCRIPT if int(baseline) > 0 else MarkKind.SUBSCRIPT, None))
    link = rpr.find(_HLINK)
    rel = part.rels.get(link.get(_RID, "")) if link is not None else None
    href = link_target(rel.target_ref) if rel is not None and rel.is_external else None
    if href is not None:
        keys.add((MarkKind.LINK, href))
    return frozenset(keys)


def _paragraph_text(p: Any, part: Any) -> tuple[str, list[InlineMark]]:
    """The paragraph's text (a line break is "\\n") and its marks, without surrounding space."""
    pieces: list[tuple[str, frozenset[MarkKey]]] = []
    for child in p:
        if child.tag == _BR:
            pieces.append(("\n", frozenset()))
        elif child.tag in (_R, _FLD):
            pieces.append((child.text, run_keys(child, part) if child.tag == _R else frozenset()))
    text = "".join(t for t, _ in pieces)
    keys = [k for t, k in pieces for _ in t]
    return strip_marked(text, marks_from_keys(text, keys) if any(keys) else [])


class _Walker:
    def __init__(self) -> None:
        self.paragraphs: list[PptxParagraph] = []
        self.unread: Counter[str] = Counter()
        self.tables = 0

    def deck(self, presentation: Any) -> None:
        for number, slide in enumerate(presentation.slides, 1):
            self._shapes(slide.shapes, slide.part, f"slide {number}")
            if slide.has_notes_slide:
                notes = slide.notes_slide
                frame = notes.notes_text_frame
                if frame is not None:
                    where = f"slide {number} notes"
                    self._frame(frame, notes.part, where, BlockKind.PARAGRAPH)

    def _shapes(self, shapes: Any, part: Any, where: str) -> None:
        for shape in shapes:
            kind = shape.shape_type
            if kind == MSO_SHAPE_TYPE.GROUP:
                self._shapes(shape.shapes, part, where)
                continue
            placeholder = shape.placeholder_format.type if shape.is_placeholder else None
            if placeholder in _LEFT_OUT:
                continue
            if shape.has_table:
                self._table(shape, part, where)
            elif shape.has_chart:
                self.unread["chart"] += 1
            elif kind in _OLE:
                self.unread["object"] += 1
            elif _DIAGRAM_URI in shape.element.xpath("./a:graphic/a:graphicData/@uri"):
                self.unread["diagram"] += 1
            if shape.has_text_frame:
                level = _TITLES.get(placeholder)
                kind = BlockKind.PARAGRAPH if level is None else BlockKind.HEADING
                locator = f"{where}, shape {shape.shape_id}"
                self._frame(shape.text_frame, part, locator, kind, level=level)

    def _table(self, shape: Any, part: Any, where: str) -> None:
        self.tables += 1
        number, table = self.tables, shape.table
        rows, cols = len(table.rows), len(table.columns)
        for r, row in enumerate(table.rows, 1):
            for c, cell in enumerate(row.cells, 1):
                if cell.is_spanned:
                    continue
                merged = cell.is_merge_origin
                position = TableCellPosition(
                    table=number,
                    rows=rows,
                    cols=cols,
                    row=r,
                    col=c,
                    row_span=cell.span_height if merged else 1,
                    col_span=cell.span_width if merged else 1,
                )
                locator = f"{where}, table {number} row {r} col {c}"
                self._frame(cell.text_frame, part, locator, BlockKind.TABLE_CELL, cell=position)

    def _frame(self, frame: Any, part: Any, locator: str, kind: BlockKind, **extra: Any) -> None:
        for n, paragraph in enumerate(frame.paragraphs, 1):
            text, marks = _paragraph_text(paragraph._p, part)
            if text:
                where = f"{locator}, paragraph {n}"
                self.paragraphs.append(
                    PptxParagraph(paragraph._p, part, kind, where, text, marks, **extra)
                )


def open_deck(source: Any, name: str) -> Any:
    try:
        return Presentation(source)
    except Exception as e:  # python-pptx raises several unrelated types for bad files
        raise IngestError(f"{name} is not a readable {APP} file ({error_reason(e)})") from e


def walk(presentation: Any, name: str) -> tuple[list[PptxParagraph], Counter[str]]:
    """Every paragraph with text in reading order, and what could not be read."""
    walker = _Walker()
    try:
        walker.deck(presentation)
    except Exception as e:  # malformed markup in a file that opens
        raise IngestError(
            f"{name} is not a readable {APP} file: part of it is damaged ({error_reason(e)})"
        ) from e
    return walker.paragraphs, walker.unread


class PptxIngestor:
    format = "pptx"
    extensions = (EXTENSION,)

    def ingest(self, path: Path) -> SourceDocument:
        check_office_file(path, APP, EXTENSION)
        paragraphs, unread = walk(open_deck(str(path), path.name), path.name)
        blocks = [
            Block(
                id=f"b{i + 1}",
                kind=p.kind,
                text=p.text,
                provenance=Provenance(
                    source_path=str(path),
                    index=i,
                    locator=p.locator,
                    level=p.level,
                    cell=p.cell,
                    marks=p.marks,
                ),
            )
            for i, p in enumerate(paragraphs)
        ]
        return SourceDocument(
            source_path=str(path),
            format=self.format,
            sha256=file_sha256(path),
            blocks=blocks,
            notices=unread_notices((unread[k], one, many) for k, one, many in _UNREADABLE),
        )
