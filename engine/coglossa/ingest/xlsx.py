"""Excel (.xlsx) ingestion (CGL-110).

Only cells that hold text are read, sheet by sheet and row by row: formulas, numbers, dates,
booleans and errors never are, and neither is text with no letters in it (a code or a number
stored as text, "00123"), so none of them can change in the translation. Each text cell is one
block (a table cell without a position: a sheet is no table to rebuild in a Word file), and its
locator names the sheet and the cell, which is how the Excel download (`coglossa.export.xlsx`)
finds it again. Sheet names are not translated, since formulas refer to them. Bold, italic,
underlined, superscript and subscript words inside a cell are the block's marks (CGL-108);
formatting of the whole cell stays on the cell. Charts, comments and text boxes cannot be read,
and the document's notices name them. Neither are the heading and totals rows of a table
("Format as Table"), which Excel requires to match the table's own column names; the notices
name them too."""

import re
import zipfile
from pathlib import Path
from typing import Any

import openpyxl
from lxml import etree
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.utils.cell import range_boundaries
from openpyxl.utils.escape import unescape

from coglossa.ingest.base import (
    IngestError,
    check_office_file,
    error_reason,
    file_sha256,
    unread_notices,
)
from coglossa.schema import Block, BlockKind, InlineMark, MarkKind, Provenance, SourceDocument
from coglossa.segment.markup import MarkKey, marks_from_keys, strip_marked

APP, EXTENSION = "Excel", ".xlsx"
_LOCATOR = re.compile(r"sheet '(.*)', cell ([A-Z]+[0-9]+)")
_CHART_PART = re.compile(r"xl/charts/chart[^/]*\.xml")
_DRAWING_PART = re.compile(r"xl/drawings/[^/]*\.xml")
_SHAPE_WITH_TEXT = etree.XPath(
    "//xdr:sp[.//a:t[normalize-space()]]",
    namespaces={
        "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    },
)


def xml_parser() -> etree.XMLParser:
    """For XML from a file someone uploaded: no entities, no network. One per use: a parser
    must not be shared between threads."""
    return etree.XMLParser(resolve_entities=False, no_network=True)


def cell_locator(sheet: str, coordinate: str) -> str:
    return f"sheet '{sheet}', cell {coordinate}"


def parse_locator(locator: str | None) -> tuple[str, str] | None:
    """The sheet name and cell coordinate of a block's locator; None for any other locator."""
    match = _LOCATOR.fullmatch(locator or "")
    return None if match is None else (match.group(1), match.group(2))


def _font_keys(font: Any) -> frozenset[MarkKey]:
    keys: set[MarkKey] = set()
    if font.b:
        keys.add((MarkKind.BOLD, None))
    if font.i:
        keys.add((MarkKind.ITALIC, None))
    if font.u not in (None, "none"):
        keys.add((MarkKind.UNDERLINE, None))
    if font.vertAlign == "superscript":
        keys.add((MarkKind.SUPERSCRIPT, None))
    elif font.vertAlign == "subscript":
        keys.add((MarkKind.SUBSCRIPT, None))
    return frozenset(keys)


def _plain(text: str) -> str:
    return unescape(text).replace("\r\n", "\n").replace("\r", "\n")


def _cell_text(value: str | CellRichText) -> tuple[str, list[InlineMark]]:
    """The cell's text and marks. A character XML cannot hold, such as the carriage return of a
    Windows line break, is written `_x000D_` in the file; openpyxl leaves that as it is, so it is
    read here, and every line break becomes a line feed."""
    if not isinstance(value, CellRichText):
        return _plain(value).strip(), []
    pieces = [
        (_plain(p.text), _font_keys(p.font))
        if isinstance(p, TextBlock)
        else (_plain(p), frozenset())
        for p in value
    ]
    text = "".join(t for t, _ in pieces)
    keys = [k for t, k in pieces for _ in t]
    return strip_marked(text, marks_from_keys(text, keys) if any(keys) else [])


def _table_labels(sheet: Any) -> set[tuple[int, int]]:
    """(row, column) of the cells in the heading and totals rows of the sheet's tables. Excel
    keeps each column's name in the table too, and repairs (drops) a table whose heading cells
    no longer match it; renaming the column would break the formulas that name it."""
    cells: set[tuple[int, int]] = set()
    for table in sheet.tables.values():
        first_col, first_row, last_col, last_row = range_boundaries(table.ref)
        heading = 1 if table.headerRowCount is None else table.headerRowCount
        totals = table.totalsRowCount or 0
        rows = [*range(first_row, first_row + heading), *range(last_row - totals + 1, last_row + 1)]
        cells.update((r, c) for r in rows for c in range(first_col, last_col + 1))
    return cells


def _unread(path: Path, workbook: Any, headings: int) -> list[str]:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        charts = sum(1 for n in names if _CHART_PART.fullmatch(n))
        boxes = 0
        for name in names:
            if _DRAWING_PART.fullmatch(name):
                boxes += len(_SHAPE_WITH_TEXT(etree.fromstring(z.read(name), xml_parser())))
    comments = sum(
        1 for sheet in workbook.worksheets for cell in sheet._cells.values() if cell.comment
    )
    return unread_notices(
        [
            (charts, "chart", "charts"),
            (comments, "comment", "comments"),
            (boxes, "text box", "text boxes"),
            (headings, "table heading", "table headings"),
        ]
    )


class XlsxIngestor:
    format = "xlsx"
    extensions = (EXTENSION,)

    def ingest(self, path: Path) -> SourceDocument:
        check_office_file(path, APP, EXTENSION)
        try:
            workbook = openpyxl.load_workbook(path, rich_text=True)
        except Exception as e:  # openpyxl raises several unrelated types for bad files
            reason = error_reason(e)
            raise IngestError(f"{path.name} is not a readable {APP} file ({reason})") from e
        blocks: list[Block] = []
        headings = 0
        try:
            for sheet in workbook.worksheets:
                labels = _table_labels(sheet)
                # the cells that exist, row by row; iter_rows would make every empty cell of a
                # sheet whose recorded size is far larger than its content
                for _, cell in sorted(sheet._cells.items()):
                    if cell.data_type != "s" or cell.value is None:
                        continue
                    text, marks = _cell_text(cell.value)
                    if not any(c.isalpha() for c in text):
                        continue
                    if (cell.row, cell.column) in labels:
                        headings += 1
                        continue
                    index = len(blocks)
                    provenance = Provenance(
                        source_path=str(path),
                        index=index,
                        locator=cell_locator(sheet.title, cell.coordinate),
                        marks=marks,
                    )
                    blocks.append(
                        Block(
                            id=f"b{index + 1}",
                            kind=BlockKind.TABLE_CELL,
                            text=text,
                            provenance=provenance,
                        )
                    )
            notices = _unread(path, workbook, headings)
        except Exception as e:  # malformed content in a file that opens
            raise IngestError(
                f"{path.name} is not a readable {APP} file: part of it is damaged "
                f"({error_reason(e)})"
            ) from e
        return SourceDocument(
            source_path=str(path),
            format=self.format,
            sha256=file_sha256(path),
            blocks=blocks,
            notices=notices,
        )
