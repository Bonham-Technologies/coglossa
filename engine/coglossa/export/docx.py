"""DOCX exports (spec §7.5): a bilingual side-by-side table with the open flags added as Word
comments on the translation, and a clean document in the target language that keeps the
source's headings, paragraphs, list items and breaks.

Where the blocks record the structure (DOCX sources, CGL-87) the clean document also rebuilds
it: headings at their levels, tables with their rows, columns, merged cells and nested tables,
and the page header and footer, with a first-page and an even-page variant where the source has
one (CGL-117). The clean document has one section. The page header comes from the first source
section that has one (a cover section without a header leaves it to the next), and so does the
footer; the headers and footers of later sections are kept out of the page and come at the end
of the document, after the notes, so their translation is kept without repeating on every page.
Footnotes and endnotes become a marked notes section at the end: each note starts with its mark,
and each passage that cites one ends with the mark in superscript (where in the passage the
source cited it is not recorded). Table cells without a recorded position, as in older runs and
other formats, become paragraphs. Bold, italic, underlined, superscript and subscript words and
links come back on the translated words where the passage kept them (CGL-108); a passage
without them is one plain run, as before."""

import io
import re
from typing import Any

import docx
from docx.enum.table import WD_TABLE_DIRECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from coglossa.export.rows import (
    BREAK_MARKER,
    TranslatedBlock,
    export_basename,
    export_rows,
    is_rtl,
    source_name,
    translated_blocks,
)
from coglossa.schema import BlockKind, InlineMark, MarkKind, Region, Report, TableCellPosition
from coglossa.segment.markup import MarkKey, char_keys
from coglossa.text import clean_text

_W_P = qn("w:p")
COMMENT_AUTHOR = "Coglossa"
COMMENT_INITIALS = "CG"


def _save(document: Any) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _new_document(report: Report) -> Any:
    document = docx.Document()
    document.core_properties.title = clean_text(export_basename(report))
    document.core_properties.language = report.profile.target_lang
    return document


def _add_run(paragraph: Any, text: str, *, rtl: bool) -> Any:
    run = paragraph.add_run(clean_text(text))  # python-docx turns "\n" into a line break
    if rtl:
        run.font.rtl = True
    return run


def _add_marked_runs(
    paragraph: Any, part: Any, text: str, marks: list[InlineMark], *, rtl: bool
) -> list[Any]:
    """`text` as runs with its inline formatting; one plain run when it has none. A link's
    runs go inside a hyperlink to its target, which `part` (the story's part) holds."""
    if not marks:
        return [_add_run(paragraph, text, rtl=rtl)]
    keys = char_keys(len(text), marks)
    runs: list[Any] = []
    start = 0
    while start < len(text):
        end = start + 1
        while end < len(text) and keys[end] == keys[start]:
            end += 1
        runs.append(_formatted_run(paragraph, part, text[start:end], keys[start], rtl=rtl))
        start = end
    return runs


def _formatted_run(
    paragraph: Any, part: Any, text: str, key: frozenset[MarkKey], *, rtl: bool
) -> Any:
    run = _add_run(paragraph, text, rtl=rtl)
    kinds = {kind for kind, _ in key}
    font = run.font
    if MarkKind.BOLD in kinds:
        font.bold = True
    if MarkKind.ITALIC in kinds:
        font.italic = True
    if MarkKind.UNDERLINE in kinds or MarkKind.LINK in kinds:
        font.underline = True
    if MarkKind.SUPERSCRIPT in kinds:
        font.superscript = True
    elif MarkKind.SUBSCRIPT in kinds:
        font.subscript = True
    href = next((h for kind, h in sorted(key, key=str) if kind is MarkKind.LINK and h), None)
    if href is not None:
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), part.relate_to(href, RT.HYPERLINK, is_external=True))
        run._r.addprevious(link)
        link.append(run._r)
        run = Run(run._r, paragraph)
    return run


def bilingual_docx(report: Report, *, comments: bool = True) -> bytes:
    document = _new_document(report)
    src, tgt = report.profile.source_lang, report.profile.target_lang
    document.add_heading(clean_text(f"{source_name(report).name}: {src} → {tgt}"), level=1)
    for notice in report.notices:
        document.add_paragraph().add_run(clean_text(notice.explanation)).italic = True
    table = document.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    header = table.rows[0].cells
    header[0].text = "#"
    header[1].text, header[2].text = (
        clean_text(f"Source ({src})"),
        clean_text(f"Translation ({tgt})"),
    )
    source_rtl, target_rtl = is_rtl(src), is_rtl(tgt)
    for row in export_rows(report):
        cells = table.add_row().cells
        cells[0].text = str(row.number)
        _add_run(cells[1].paragraphs[0], row.source, rtl=source_rtl)
        run = _add_run(cells[2].paragraphs[0], row.span.text, rtl=target_rtl)
        if comments:
            for flag in row.open_flags:
                document.add_comment(
                    run,
                    text=clean_text(f"{flag.code.value}: {flag.explanation}"),
                    author=COMMENT_AUTHOR,
                    initials=COMMENT_INITIALS,
                )
    return _save(document)


def _is_empty(paragraph: Any) -> bool:
    return not paragraph.runs and not paragraph.text


def _empty_last_paragraph(container: Any) -> Any | None:
    """The container's last element when it is an empty paragraph, else None. Looks at that one
    element: `paragraphs` rebuilds its list from every child, which per block is quadratic."""
    element = container._element
    last = element[-1] if len(element) else None
    if last is None or last.tag != _W_P:
        return None  # the body ends with its section properties, so body text is always added
    paragraph = Paragraph(last, container)
    return paragraph if _is_empty(paragraph) else None


def _style(kind: BlockKind, level: int | None) -> str | None:
    if kind is BlockKind.HEADING:
        level = 1 if level is None else min(level, 9)
        return "Title" if level == 0 else f"Heading {level}"
    if kind is BlockKind.LIST_ITEM:
        return "List Bullet"
    return None


# The first part of a header or footer block's locator (coglossa.ingest.docx)
_FURNITURE = re.compile(r"(?:section (\d+) )?(first-page |even-page )?(?:header|footer)")


def _furniture(locator: str | None) -> tuple[int, str]:
    """The section and the variant ("", "first-page ", "even-page ") of a header or footer
    block; section 1's default for a locator that names neither, as in older runs."""
    m = _FURNITURE.fullmatch((locator or "").split(", ")[0])
    if m is None:
        return 1, ""
    return int(m.group(1) or 1), m.group(2) or ""


def _furniture_section(item: TranslatedBlock) -> tuple[Region, int] | None:
    """The region and section of a header or footer block; None for any other block."""
    provenance = item.block.provenance
    if provenance.region not in (Region.HEADER, Region.FOOTER):
        return None
    return provenance.region, _furniture(provenance.locator)[0]


def later_sections(items: list[TranslatedBlock]) -> set[int]:
    """The positions of the header and footer blocks of a section after the first one that has
    a header (or a footer): a cover section without one leaves the next section's on the page."""
    found = [_furniture_section(item) for item in items]
    first: dict[Region, int] = {}
    for where in found:
        if where is not None:
            region, section = where
            first[region] = min(first.get(region, section), section)
    return {i for i, where in enumerate(found) if where is not None and where[1] > first[where[0]]}


class _CleanDocument:
    def __init__(self, report: Report) -> None:
        self.document = _new_document(report)
        self.rtl = is_rtl(report.profile.target_lang)
        # read once: `sections` (and `paragraphs`) rescan the whole body on every access
        self.section = self.document.sections[0]
        self.body = self.document._body
        self.header, self.footer = self.section.header, self.section.footer
        section = self.section
        self.width = section.page_width - section.left_margin - section.right_margin
        self.tables: dict[int, Any] = {}  # table number -> the rebuilt table
        # table number -> its cells, row by row (`table.cell` lists them all on every call)
        self.grids: dict[int, list[Any]] = {}
        self.merged: set[tuple[int, int, int]] = set()
        self.marked: set[tuple[Region | None, str]] = set()  # notes whose mark is written

    def _region(self, region: Region | None, locator: str | None = None) -> Any:
        if region not in (Region.HEADER, Region.FOOTER):
            return self.body  # body text, and the notes section at its end
        name = region.value
        _, variant = _furniture(locator)
        if variant == "first-page ":
            self.section.different_first_page_header_footer = True
            return getattr(self.section, f"first_page_{name}")
        if variant == "even-page ":
            self.document.settings.odd_and_even_pages_header_footer = True
            return getattr(self.section, f"even_page_{name}")
        return self.header if region is Region.HEADER else self.footer

    def _part(self, region: Region | None, locator: str | None) -> Any:
        """The part a block's links belong to: its header's or footer's, else the document's."""
        if region in (Region.HEADER, Region.FOOTER):
            return self._region(region, locator).part
        return self.document.part

    def _paragraph(self, container: Any, style: str | None) -> Any:
        """A new paragraph at the end of `container`, or its empty last one (a new header's,
        footer's or cell's, or the one Word requires after a nested table)."""
        paragraph = _empty_last_paragraph(container)
        if paragraph is None:
            return container.add_paragraph(style=style)
        if style is not None:
            paragraph.style = style
        return paragraph

    def _table(self, cell: TableCellPosition, region: Region | None, locator: str | None) -> Any:
        table = self.tables.get(cell.table)
        if table is not None:
            return table
        if cell.parent is not None:
            outer = self._cell(cell.parent, region, locator)
            last = outer.paragraphs[-1]
            table = outer.add_table(cell.rows, cell.cols)  # and the paragraph Word requires
            if _is_empty(last):
                last._p.getparent().remove(last._p)
        elif region not in (Region.HEADER, Region.FOOTER):  # the body, or the notes after it
            table = self.body.add_table(cell.rows, cell.cols, self.width)
        else:
            container = self._region(region, locator)
            last = container.paragraphs[-1]
            table = container.add_table(cell.rows, cell.cols, self.width)
            if _is_empty(last):  # Word wants a header or footer to end with a paragraph
                last._p.addprevious(table._tbl)
            else:
                container.add_paragraph()
        table.style = "Table Grid"
        if self.rtl:  # the first column on the right, as a right-to-left reader expects
            table.table_direction = WD_TABLE_DIRECTION.RTL
        self.tables[cell.table] = table
        return table

    def _grid_cell(self, number: int, table: Any, row: int, col: int) -> Any:
        grid = self.grids.get(number)
        if grid is None:
            grid = self.grids[number] = table._cells
        return grid[(row - 1) * table._column_count + col - 1]

    def _cell(self, cell: TableCellPosition, region: Region | None, locator: str | None) -> Any:
        table = self._table(cell, region, locator)
        target = self._grid_cell(cell.table, table, cell.row, cell.col)
        key = (cell.table, cell.row, cell.col)
        if (cell.row_span > 1 or cell.col_span > 1) and key not in self.merged:
            self.merged.add(key)
            end = self._grid_cell(
                cell.table, table, cell.row + cell.row_span - 1, cell.col + cell.col_span - 1
            )
            target = target.merge(end)
            self.grids.pop(cell.table)  # the merge changed which cell each grid place holds
        return target

    def add(self, item: TranslatedBlock, *, in_body: bool = False) -> None:
        """`in_body`: write a header or footer block in the body (a later section's)."""
        kind, provenance = item.block.kind, item.block.provenance
        region, locator = (None if in_body else provenance.region), provenance.locator
        if provenance.cell is not None:
            container = self._cell(provenance.cell, region, locator)
        else:
            container = self._region(region, locator)
        paragraph = self._paragraph(container, _style(kind, provenance.level))
        if kind is BlockKind.BREAK:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _add_run(paragraph, BREAK_MARKER, rtl=self.rtl)  # keeps the paragraph's direction
            return
        runs = []
        note = provenance.note
        if note is not None and (region, note) not in self.marked:
            self.marked.add((region, note))
            mark = _add_run(paragraph, note, rtl=self.rtl)
            mark.font.superscript = True
            runs += [mark, _add_run(paragraph, " ", rtl=self.rtl)]
        if item.marks:
            part = self._part(region, locator)
            runs += _add_marked_runs(paragraph, part, item.text, item.marks, rtl=self.rtl)
        else:
            runs.append(_add_run(paragraph, item.text, rtl=self.rtl))
        for ref in provenance.note_refs:
            mark = _add_run(paragraph, ref, rtl=self.rtl)
            mark.font.superscript = True
            runs.append(mark)
        if kind is BlockKind.FOOTNOTE:
            for run in runs:
                run.font.size = Pt(9)


def clean_docx(report: Report) -> bytes:
    clean = _CleanDocument(report)
    items = translated_blocks(report)
    later = later_sections(items)
    for i, item in enumerate(items):
        if i not in later:
            clean.add(item)
    for i in sorted(later):  # later sections' headers and footers, out of the page header
        clean.add(items[i], in_body=True)
    return _save(clean.document)
