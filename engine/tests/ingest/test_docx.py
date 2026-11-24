import docx
import pytest
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from coglossa.ingest.base import IngestError
from coglossa.ingest.docx import DocxIngestor
from coglossa.schema import BlockKind, MarkKind, Region, TableCellPosition
from tests.ingest.docx_parts import (
    add_chart,
    add_content_control,
    add_custom_xml,
    add_diagram,
    add_embedded_object,
    add_equation,
    add_hyperlink,
    add_imported_chunk,
    add_note_reference,
    add_notes,
    add_text_box,
    add_tracked_insertion,
    full_document,
    wrap_cell,
    wrap_rows,
)


def test_docx_in_document_order(tmp_path):
    d = docx.Document()
    d.add_heading("Report", level=1)
    d.add_paragraph("Intro paragraph.")
    d.add_paragraph("")  # empty paragraphs are skipped
    d.add_paragraph("A bullet", style="List Bullet")
    table = d.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Left"
    table.cell(0, 1).text = ""
    d.add_paragraph("* * *")
    d.add_paragraph("After the table.")
    path = tmp_path / "r.docx"
    d.save(path)

    doc = DocxIngestor().ingest(path)
    assert doc.format == "docx"
    assert [(b.kind, b.text) for b in doc.blocks] == [
        (BlockKind.HEADING, "Report"),
        (BlockKind.PARAGRAPH, "Intro paragraph."),
        (BlockKind.LIST_ITEM, "A bullet"),
        (BlockKind.TABLE_CELL, "Left"),
        (BlockKind.BREAK, "* * *"),
        (BlockKind.PARAGRAPH, "After the table."),
    ]
    assert doc.blocks[3].provenance.locator == "table 1 row 1 col 1"


def test_merged_cells_are_not_duplicated(tmp_path):
    d = docx.Document()
    table = d.add_table(rows=1, cols=2)
    merged = table.cell(0, 0).merge(table.cell(0, 1))
    merged.text = "Spans both"
    path = tmp_path / "m.docx"
    d.save(path)
    assert [b.text for b in DocxIngestor().ingest(path).blocks] == ["Spans both"]


def test_corrupt_docx_is_ingest_error(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a zip")
    with pytest.raises(IngestError, match="broken.docx is not a readable DOCX"):
        DocxIngestor().ingest(path)


@pytest.mark.parametrize("bad", ["w:gridSpan", "w:gridBefore"])
def test_a_damaged_part_inside_a_docx_is_ingest_error(tmp_path, bad):
    """A file that opens but holds malformed markup fails with a plain message, not a crash."""
    d = docx.Document()
    table = d.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Cell"
    if bad == "w:gridSpan":
        properties = table.cell(0, 0)._tc.get_or_add_tcPr()
    else:
        properties = table.rows[0]._tr.get_or_add_trPr()
    properties.append(parse_xml(f'<{bad} {nsdecls("w")} w:val="abc"/>'))
    path = tmp_path / "damaged.docx"
    d.save(path)
    with pytest.raises(IngestError, match="damaged.docx is not a readable DOCX file: part of it"):
        DocxIngestor().ingest(path)


# CGL-87: footnotes, endnotes, headers, footers, text boxes and nested tables are read, with
# the structure the clean export rebuilds; what still cannot be read is named in a notice.


def test_every_part_of_a_word_file_is_read_in_reading_order(tmp_path):
    doc = DocxIngestor().ingest(full_document(tmp_path / "full.docx"))
    got = [(b.kind, b.text, b.provenance.region, b.provenance.locator) for b in doc.blocks]
    assert got == [
        (BlockKind.PARAGRAPH, "Acme Confidential", Region.HEADER, "header"),
        (BlockKind.HEADING, "Annual report", None, None),
        (BlockKind.HEADING, "Background", None, None),
        (BlockKind.PARAGRAPH, "The figures are audited.", None, None),
        (BlockKind.PARAGRAPH, "See the box.", None, None),
        (BlockKind.PARAGRAPH, "Boxed fact.", None, "text box 1"),
        (BlockKind.TABLE_CELL, "Outer A", None, "table 1 row 1 col 1"),
        (BlockKind.TABLE_CELL, "Outer B", None, "table 1 row 1 col 2"),
        (BlockKind.TABLE_CELL, "Inner 1", None, "table 1 row 1 col 2, table 2 row 1 col 1"),
        (BlockKind.TABLE_CELL, "Inner 2", None, "table 1 row 1 col 2, table 2 row 1 col 2"),
        (BlockKind.TABLE_CELL, "After inner", None, "table 1 row 1 col 2"),
        (BlockKind.TABLE_CELL, "Outer C", None, "table 1 row 2 col 1"),
        (BlockKind.PARAGRAPH, "Closing words.", None, None),
        (BlockKind.PARAGRAPH, "Second section.", None, None),
        (BlockKind.FOOTNOTE, "Audited by Example LLP.", Region.FOOTNOTE, "footnote 1"),
        (BlockKind.FOOTNOTE, "Figures are rounded.", Region.ENDNOTE, "endnote i"),
        (BlockKind.PARAGRAPH, "Internal use", Region.FOOTER, "footer"),
    ]
    assert [b.provenance.index for b in doc.blocks] == list(range(len(doc.blocks)))
    assert all(b.provenance.source_path == str(tmp_path / "full.docx") for b in doc.blocks)
    assert doc.notices == []
    by_text = {b.text: b.provenance for b in doc.blocks}
    assert by_text["Annual report"].level == 0
    assert by_text["Background"].level == 2
    assert by_text["The figures are audited."].note_refs == ["1"]
    assert by_text["Closing words."].note_refs == ["i"]
    assert by_text["Audited by Example LLP."].note == "1"
    assert by_text["Figures are rounded."].note == "i"
    outer_b = by_text["Outer B"].cell
    assert outer_b == TableCellPosition(table=1, rows=2, cols=2, row=1, col=2)
    assert by_text["Inner 2"].cell == TableCellPosition(
        table=2, rows=1, cols=2, row=1, col=2, parent=outer_b
    )
    assert by_text["After inner"].cell == outer_b


def test_merged_cells_record_their_span(tmp_path):
    d = docx.Document()
    table = d.add_table(rows=3, cols=3)
    table.cell(0, 0).merge(table.cell(1, 1)).text = "Square"
    table.cell(2, 2).text = "Corner"
    path = tmp_path / "spans.docx"
    d.save(path)
    blocks = DocxIngestor().ingest(path).blocks
    square = TableCellPosition(table=1, rows=3, cols=3, row=1, col=1, row_span=2, col_span=2)
    assert [(b.text, b.provenance.cell) for b in blocks] == [
        ("Square", square),
        ("Corner", TableCellPosition(table=1, rows=3, cols=3, row=3, col=3)),
    ]


def test_a_note_of_several_paragraphs_and_an_uncited_note(tmp_path):
    d = docx.Document()
    add_note_reference(d.add_paragraph("Cited."), 3)
    add_notes(d, {2: "Never cited.", 3: "First part.\nSecond part."})
    path = tmp_path / "notes.docx"
    d.save(path)
    blocks = DocxIngestor().ingest(path).blocks
    assert [(b.text, b.provenance.note, b.provenance.note_refs) for b in blocks] == [
        ("Cited.", None, ["1"]),
        ("First part.", "1", []),
        ("Second part.", "1", []),
        ("Never cited.", "2", []),
    ]


def test_header_and_footer_variants_and_tables(tmp_path):
    d = docx.Document()
    section = d.sections[0]
    section.different_first_page_header_footer = True
    section.first_page_header.paragraphs[0].text = "Cover header"
    section.header.paragraphs[0].text = "Running header"
    section.even_page_header.paragraphs[0].text = "Unused even header"  # odd/even is off
    grid = section.footer.add_table(rows=1, cols=1, width=section.page_width)
    grid.cell(0, 0).text = "Footer cell"
    d.add_paragraph("Body.")
    path = tmp_path / "hf.docx"
    d.save(path)
    blocks = DocxIngestor().ingest(path).blocks
    assert [(b.text, b.provenance.region, b.provenance.locator) for b in blocks] == [
        ("Running header", Region.HEADER, "header"),
        ("Cover header", Region.HEADER, "first-page header"),
        ("Body.", None, None),
        ("Footer cell", Region.FOOTER, "footer, table 1 row 1 col 1"),
    ]


def test_even_page_headers_are_read_when_the_file_uses_them(tmp_path):
    d = docx.Document()
    d.settings.odd_and_even_pages_header_footer = True
    d.sections[0].even_page_header.paragraphs[0].text = "Even header"
    d.add_paragraph("Body.")
    path = tmp_path / "even.docx"
    d.save(path)
    blocks = DocxIngestor().ingest(path).blocks
    assert [(b.text, b.provenance.locator) for b in blocks] == [
        ("Even header", "even-page header"),
        ("Body.", None),
    ]


def test_text_in_content_controls_tracked_insertions_and_old_text_boxes(tmp_path):
    d = docx.Document()
    edited = d.add_paragraph("Kept ")
    add_tracked_insertion(edited, "and inserted.")
    old_box = d.add_paragraph("Old box:")
    add_text_box(old_box, ["Legacy box."], fallback=False)
    add_content_control(d, "Inside a control.")
    add_custom_xml(d, "Inside custom XML.")
    table = d.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Cell text"
    add_text_box(table.cell(0, 0).paragraphs[0], ["Box in a cell."])
    table.cell(0, 0).add_paragraph("More cell text")
    path = tmp_path / "wrapped.docx"
    d.save(path)
    blocks = DocxIngestor().ingest(path).blocks
    in_cell = "table 1 row 1 col 1"
    assert [(b.kind, b.text, b.provenance.locator) for b in blocks] == [
        (BlockKind.PARAGRAPH, "Kept and inserted.", None),
        (BlockKind.PARAGRAPH, "Old box:", None),
        (BlockKind.PARAGRAPH, "Legacy box.", "text box 1"),
        (BlockKind.PARAGRAPH, "Inside a control.", None),
        (BlockKind.PARAGRAPH, "Inside custom XML.", None),
        (BlockKind.TABLE_CELL, "Cell text", in_cell),
        (BlockKind.PARAGRAPH, "Box in a cell.", f"{in_cell}, text box 2"),
        (BlockKind.TABLE_CELL, "More cell text", in_cell),
    ]
    assert blocks[6].provenance.cell == blocks[5].provenance.cell


def test_content_that_cannot_be_read_is_named_in_a_notice(tmp_path):
    d = docx.Document()
    para = d.add_paragraph("Results ")
    add_chart(para)
    add_chart(d.add_paragraph())
    add_equation(para)
    add_diagram(d.add_paragraph("Org chart"))
    add_embedded_object(d.add_paragraph())
    add_imported_chunk(d)
    add_imported_chunk(d)
    d.add_comment(para.runs[0], text="Check this", author="Ann")
    path = tmp_path / "charts.docx"
    d.save(path)
    doc = DocxIngestor().ingest(path)
    assert [b.text for b in doc.blocks] == ["Results", "Org chart"]
    assert doc.notices == [
        "Some parts of this file couldn't be read, so they aren't translated: 2 charts, "
        "1 diagram, 1 embedded object, 2 embedded documents, 1 equation and 1 comment."
    ]


def test_one_thing_that_cannot_be_read(tmp_path):
    d = docx.Document()
    add_chart(d.add_paragraph("Sales"))
    path = tmp_path / "chart.docx"
    d.save(path)
    assert DocxIngestor().ingest(path).notices == [
        "Some parts of this file couldn't be read, so they aren't translated: 1 chart."
    ]


def test_a_repeated_header_is_read_once_but_repeats_within_one_are_kept(tmp_path):
    """Sections that repeat a header (a table in it too) add nothing; a header's own repeated
    text, such as two cells that say the same, and a header that differs are all kept."""
    d = docx.Document()
    for n, label in enumerate(["Draft", "Draft", "Final"]):
        section = d.sections[0] if n == 0 else d.add_section()
        section.header.is_linked_to_previous = False
        section.header.paragraphs[0].text = "Acme"
        grid = section.header.add_table(rows=1, cols=2, width=section.page_width)
        grid.cell(0, 0).text = grid.cell(0, 1).text = label
        add_chart(section.header.add_paragraph())
        d.add_paragraph(f"Section {n + 1}.")
    path = tmp_path / "repeat.docx"
    d.save(path)
    doc = DocxIngestor().ingest(path)
    header = [b for b in doc.blocks if b.provenance.region is Region.HEADER]
    assert [(b.text, b.provenance.locator) for b in header] == [
        ("Acme", "header"),
        ("Draft", "header, table 1 row 1 col 1"),
        ("Draft", "header, table 1 row 1 col 2"),
        ("Acme", "section 3 header"),
        ("Final", "section 3 header, table 2 row 1 col 1"),
        ("Final", "section 3 header, table 2 row 1 col 2"),
    ]
    assert [b.id for b in doc.blocks] == [f"b{n}" for n in range(1, len(doc.blocks) + 1)]
    assert doc.notices == [
        "Some parts of this file couldn't be read, so they aren't translated: 2 charts."
    ]


def test_rows_and_cells_in_content_controls_are_read(tmp_path):
    """A repeating section wraps table rows (or cells) in a content control."""
    d = docx.Document()
    table = d.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Name", "Role"
    wrap_rows(table, [["Ann", "Chair"]])
    wrap_cell(table.rows[0], "Notes")
    path = tmp_path / "repeating.docx"
    d.save(path)
    blocks = DocxIngestor().ingest(path).blocks
    assert [(b.text, b.provenance.locator) for b in blocks] == [
        ("Name", "table 1 row 1 col 1"),
        ("Role", "table 1 row 1 col 2"),
        ("Notes", "table 1 row 1 col 3"),
        ("Ann", "table 1 row 2 col 1"),
        ("Chair", "table 1 row 2 col 2"),
    ]
    assert {(b.provenance.cell.rows, b.provenance.cell.cols) for b in blocks} == {(2, 3)}


def test_a_header_that_differs_only_in_what_cannot_be_read_is_still_counted(tmp_path):
    """A header with no text, or the same text, is not a repeat when it holds other content."""
    d = docx.Document()
    section = d.sections[0]
    section.header.is_linked_to_previous = False  # an empty primary header
    section.different_first_page_header_footer = True
    add_embedded_object(section.first_page_header.paragraphs[0])
    later = d.add_section()
    later.header.is_linked_to_previous = False
    later.header.paragraphs[0].text = "Acme"
    final = d.add_section()
    final.header.is_linked_to_previous = False
    final.header.paragraphs[0].text = "Acme"
    add_chart(final.header.paragraphs[0])
    d.add_paragraph("Body.")
    path = tmp_path / "empty-header.docx"
    d.save(path)
    doc = DocxIngestor().ingest(path)
    assert doc.notices == [
        "Some parts of this file couldn't be read, so they aren't translated: 1 chart and "
        "1 embedded object."
    ]


def _marked(block) -> list[tuple[MarkKind, str | None, str]]:
    return [(m.kind, m.href, block.text[m.start : m.end]) for m in block.provenance.marks]


def test_inline_formatting_is_recorded_as_marks(tmp_path):
    """CGL-108: bold, italic, a link and a superscript in one paragraph."""
    d = docx.Document()
    p = d.add_paragraph("  The ")
    p.add_run("cat").bold = True
    p.add_run(" and ")
    p.add_run("the dog").italic = True
    p.add_run(" see ")
    add_hyperlink(p, d.part, "here", "https://example.org/a")
    p.add_run(" and H")
    p.add_run("2").font.subscript = True
    p.add_run("O")
    p.add_run("3").font.superscript = True
    p.add_run(" ")
    p.add_run("under").underline = True
    p.add_run(" ")
    p.add_run("strong").style = "Strong"  # bold by its character style
    styled_off = p.add_run(" not")
    styled_off.style = "Strong"
    styled_off.bold = False  # the run's own formatting wins over its style
    d.add_paragraph("Plain text.")
    d.add_paragraph().add_run("* * *").bold = True
    path = tmp_path / "f.docx"
    d.save(path)

    blocks = DocxIngestor().ingest(path).blocks
    assert blocks[0].text == "The cat and the dog see here and H2O3 under strong not"
    assert _marked(blocks[0]) == [
        (MarkKind.BOLD, None, "cat"),
        (MarkKind.ITALIC, None, "the dog"),
        (MarkKind.LINK, "https://example.org/a", "here"),
        (MarkKind.SUBSCRIPT, None, "2"),
        (MarkKind.SUPERSCRIPT, None, "3"),
        (MarkKind.UNDERLINE, None, "under"),
        (MarkKind.BOLD, None, "strong"),
    ]
    assert blocks[1].provenance.marks == []  # no formatting
    assert blocks[2].kind is BlockKind.BREAK and blocks[2].provenance.marks == []


def test_marks_follow_cells_notes_and_headers(tmp_path):
    d = docx.Document()
    cell = d.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "First line"
    second = cell.add_paragraph("Second ")
    second.add_run("bold").bold = True
    note_paragraph = d.add_paragraph("Body")
    add_note_reference(note_paragraph, 1)
    add_notes(d, {1: "A note"})
    header = d.sections[0].header.paragraphs[0]
    header.text = "Visit "
    add_hyperlink(header, d.sections[0].header.part, "us", "https://example.org/h")
    path = tmp_path / "c.docx"
    d.save(path)

    blocks = {b.text: b for b in DocxIngestor().ingest(path).blocks}
    assert _marked(blocks["First line\nSecond bold"]) == [(MarkKind.BOLD, None, "bold")]
    assert _marked(blocks["Visit us"]) == [(MarkKind.LINK, "https://example.org/h", "us")]
    assert blocks["A note"].provenance.marks == []
