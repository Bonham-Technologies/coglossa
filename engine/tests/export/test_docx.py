import io
from collections import Counter

import docx
import docx.document
from docx.blkcntnr import BlockItemContainer
from docx.enum.table import WD_TABLE_DIRECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt
from docx.table import Table

from coglossa.export.docx import COMMENT_AUTHOR, bilingual_docx, clean_docx
from coglossa.ingest.docx import DocxIngestor
from coglossa.schema import (
    Block,
    BlockKind,
    Chunk,
    Flag,
    FlagCode,
    Provenance,
    Region,
    Report,
    Segment,
    TableCellPosition,
)
from tests.export.conftest import make_report, make_span
from tests.ingest.docx_parts import full_document


def _open(data: bytes):
    return docx.Document(io.BytesIO(data))


def test_bilingual_table_with_open_flags_as_comments(report):
    document = _open(bilingual_docx(report))
    assert document.paragraphs[0].text == "story.txt: fr → en"
    assert document.paragraphs[0].style.name == "Heading 1"
    assert "less reliable" in document.paragraphs[1].text
    table = document.tables[0]
    assert [[c.text for c in row.cells] for row in table.rows] == [
        ["#", "Source (fr)", "Translation (en)"],
        ["1", "Chapitre un", "Chapter One"],
        ["2", "Le chat dort. Il pleut.", "The cat sleeps.\nIt rains."],
        ["3", "Fin <du> & monde.", "The end <of> & world."],
    ]
    comments = list(document.comments)
    assert [(c.text, c.author, c.initials) for c in comments] == [
        ("low_meaning: Meaning score 55 is below 70.", COMMENT_AUTHOR, "CG")
    ]


def test_bilingual_without_comments(report):
    assert list(_open(bilingual_docx(report, comments=False)).comments) == []


def test_an_empty_translation_still_carries_its_flags():
    flag = Flag(code=FlagCode.ENGINE_ERROR, explanation="No translation was produced.")
    report = make_report(
        spans=[
            make_span("p1", "s1", "s1", "Chapter One"),
            make_span("p2", "s2", "s3", "", flags=[flag]),
            make_span("p3", "s4", "s4", "End."),
        ]
    )
    document = _open(bilingual_docx(report))
    assert document.tables[0].rows[2].cells[2].text == ""
    assert [c.text for c in document.comments] == ["engine_error: No translation was produced."]


def test_clean_document_keeps_structure(report):
    document = _open(clean_docx(report))
    paragraphs = [(p.style.name, p.text) for p in document.paragraphs]
    assert paragraphs == [
        ("Heading 1", "Chapter One"),
        ("Normal", "The cat sleeps.\nIt rains."),
        ("Normal", "* * *"),
        ("List Bullet", "The end <of> & world."),
    ]
    assert document.paragraphs[2].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert document.tables == []
    assert document.core_properties.title == "story.en"
    assert document.core_properties.language == "en"


def _runs(document):
    return [r for p in document.paragraphs for r in p.runs]


def test_right_to_left_targets_mark_their_runs():
    report = make_report(target_lang="ar")
    assert all(r.font.rtl for r in _runs(_open(clean_docx(report))))
    bilingual = _open(bilingual_docx(report))
    rows = bilingual.tables[0].rows[1:]
    assert all(r.font.rtl for row in rows for r in row.cells[2].paragraphs[0].runs if r.text)
    assert not any(r.font.rtl for row in rows for r in row.cells[1].paragraphs[0].runs)
    assert not any(r.font.rtl for r in _runs(_open(clean_docx(make_report()))))


def _with_controls():
    flag = Flag(code=FlagCode.LOW_MEANING, explanation="Meaning\x0b drops\x01.")
    report = make_report(
        spans=[
            make_span("p1", "s1", "s1", "Chapter\x0cOne"),
            make_span("p2", "s2", "s3", "The cat\x0bsleeps.\x00", flags=[flag]),
            make_span("p3", "s4", "s4", "End\x1f."),
        ]
    )
    segments = [
        s.model_copy(update={"text": "Le chat\x0cdort."}) if s.id == "s2" else s
        for s in report.segments
    ]
    notices = [n.model_copy(update={"explanation": n.explanation + "\x0b"}) for n in report.notices]
    return report.model_copy(update={"segments": segments, "notices": notices})


def test_control_characters_do_not_break_the_documents():
    report = _with_controls()
    bilingual = _open(bilingual_docx(report))
    assert [[c.text for c in row.cells] for row in bilingual.tables[0].rows[1:]] == [
        ["1", "Chapitre un", "Chapter\nOne"],
        ["2", "Le chat\ndort. Il pleut.", "The cat\nsleeps."],
        ["3", "Fin <du> & monde.", "End."],
    ]
    assert [c.text for c in bilingual.comments] == ["low_meaning: Meaning\n drops."]
    assert bilingual.paragraphs[1].text.endswith("review more broadly.\n")
    clean = _open(clean_docx(report))
    assert [p.text for p in clean.paragraphs] == [
        "Chapter\nOne",
        "The cat\nsleeps.",
        "* * *",
        "End.",
    ]


# CGL-87: the clean document rebuilds what the DOCX ingestor recorded.


def _translated(blocks: list[Block], target_lang: str = "en") -> Report:
    """A report with one passage per block, translated as "[en] <source>"."""
    segments = [
        Segment(id=f"s{n}", block_id=b.id, start=0, end=len(b.text), text=b.text)
        for n, b in enumerate(blocks, start=1)
    ]
    spans = [
        make_span(f"p{n}", seg.id, seg.id, f"[en] {seg.text}")
        for n, seg in enumerate(segments, start=1)
    ]
    chunk = Chunk(id="c1", segment_ids=[s.id for s in segments])
    return make_report(target_lang=target_lang).model_copy(
        update={"blocks": blocks, "segments": segments, "spans": spans, "chunks": [chunk]}
    )


def _ingested(path) -> Report:
    return _translated(DocxIngestor().ingest(path).blocks)


def _cell_content(cell) -> list:
    """A cell's paragraphs' text and nested tables' cell text, in order."""
    out = []
    for item in cell.iter_inner_content():
        if isinstance(item, Table):
            out.append([[_cell_content(c) for c in row.cells] for row in item.rows])
        else:
            out.append(item.text)
    return out


def test_the_clean_document_keeps_the_word_files_structure(tmp_path):
    document = _open(clean_docx(_ingested(full_document(tmp_path / "full.docx"))))
    body = [
        ("table", None) if isinstance(item, Table) else (item.style.name, item.text)
        for item in document.iter_inner_content()
    ]
    assert body == [
        ("Title", "[en] Annual report"),
        ("Heading 2", "[en] Background"),
        ("Normal", "[en] The figures are audited.1"),
        ("Normal", "[en] See the box."),
        ("Normal", "[en] Boxed fact."),
        ("table", None),
        ("Normal", "[en] Closing words.i"),
        ("Normal", "[en] Second section."),
        ("Normal", "1 [en] Audited by Example LLP."),
        ("Normal", "i [en] Figures are rounded."),
    ]
    cited = document.paragraphs[2].runs
    assert [(r.text, r.font.superscript) for r in cited] == [
        ("[en] The figures are audited.", None),
        ("1", True),
    ]
    note = document.paragraphs[-2].runs
    assert [(r.text, r.font.superscript) for r in note] == [
        ("1", True),
        (" ", None),
        ("[en] Audited by Example LLP.", None),
    ]
    assert all(r.font.size == Pt(9) for r in note)
    (table,) = document.tables
    assert [[_cell_content(c) for c in row.cells] for row in table.rows] == [
        [
            ["[en] Outer A"],
            ["[en] Outer B", [[["[en] Inner 1"], ["[en] Inner 2"]]], "[en] After inner"],
        ],
        [["[en] Outer C"], [""]],
    ]
    section = document.sections[0]
    assert [p.text for p in section.header.paragraphs] == ["[en] Acme Confidential"]
    assert [p.text for p in section.footer.paragraphs] == ["[en] Internal use"]


def test_merged_cells_and_tables_in_headers_are_rebuilt(tmp_path):
    d = docx.Document()
    section = d.sections[0]
    grid = section.header.add_table(rows=1, cols=2, width=section.page_width)
    grid.cell(0, 0).text = "Left head"
    section.header.add_paragraph("Under the grid")
    table = d.add_table(rows=3, cols=3)
    table.cell(0, 0).merge(table.cell(1, 1)).text = "Square"
    table.cell(2, 2).text = "Corner"
    path = tmp_path / "merged.docx"
    d.save(path)
    document = _open(clean_docx(_ingested(path)))
    rebuilt = document.tables[0]
    assert rebuilt.cell(0, 0)._tc is rebuilt.cell(1, 1)._tc
    assert rebuilt.cell(0, 0).text == "[en] Square"
    assert rebuilt.cell(2, 2).text == "[en] Corner"
    assert rebuilt.cell(2, 0).text == ""
    header = document.sections[0].header
    head_table = header.tables[0]
    assert [c.text for c in head_table.rows[0].cells] == ["[en] Left head", ""]
    assert [type(i).__name__ for i in header.iter_inner_content()] == ["Table", "Paragraph"]
    assert header.paragraphs[-1].text == "[en] Under the grid"


def test_a_nested_table_first_in_its_cell_and_a_header_heading(tmp_path):
    d = docx.Document()
    section = d.sections[0]
    section.header.paragraphs[0].text = "Head title"
    section.header.paragraphs[0].style = "Heading 1"
    grid = section.header.add_table(rows=1, cols=1, width=section.page_width)
    grid.cell(0, 0).text = "Head cell"
    outer = d.add_table(rows=1, cols=1).cell(0, 0)
    outer.add_table(rows=1, cols=1).cell(0, 0).text = "Only inside"
    path = tmp_path / "first.docx"
    d.save(path)
    document = _open(clean_docx(_ingested(path)))
    cell = document.tables[0].cell(0, 0)
    assert _cell_content(cell) == [[[["[en] Only inside"]]], ""]  # no empty line above
    header = document.sections[0].header
    assert [(p.style.name, p.text) for p in header.paragraphs] == [
        ("Heading 1", "[en] Head title"),
        ("Normal", ""),  # Word wants a header to end with a paragraph
    ]
    assert [type(i).__name__ for i in header.iter_inner_content()] == [
        "Paragraph",
        "Table",
        "Paragraph",
    ]


def test_a_table_cell_without_a_position_stays_a_paragraph():
    """Runs from before CGL-87, and formats that record no table structure."""
    cell = Block(
        id="b1",
        kind=BlockKind.TABLE_CELL,
        text="Gauche",
        provenance=Provenance(source_path="t.docx", index=0, locator="table 1 row 1 col 1"),
    )
    document = _open(clean_docx(_translated([cell])))
    assert [p.text for p in document.paragraphs] == ["[en] Gauche"]
    assert document.tables == []


def test_headings_deeper_than_word_offers_are_kept_at_its_deepest():
    heading = Block(
        id="b1",
        kind=BlockKind.HEADING,
        text="Tief",
        provenance=Provenance(source_path="t.docx", index=0, level=12),
    )
    document = _open(clean_docx(_translated([heading])))
    assert document.paragraphs[0].style.name == "Heading 9"


def test_the_rebuilt_structure_keeps_right_to_left_runs(tmp_path):
    report = _translated(DocxIngestor().ingest(full_document(tmp_path / "f.docx")).blocks, "ar")
    document = _open(clean_docx(report))
    cells = [p for t in document.tables for row in t.rows for c in row.cells for p in c.paragraphs]
    runs = [r for p in [*document.paragraphs, *cells] for r in p.runs]
    assert runs and all(r.font.rtl for r in runs)
    assert all(t.table_direction == WD_TABLE_DIRECTION.RTL for t in document.tables)
    assert _open(clean_docx(_ingested(tmp_path / "f.docx"))).tables[0].table_direction is None


def test_a_table_in_a_note_is_rebuilt_in_the_notes_section():
    """A footnote's table goes with the notes at the end of the body, not to a header."""
    position = TableCellPosition(table=1, rows=1, cols=2, row=1, col=2)
    cell = Block(
        id="b1",
        kind=BlockKind.TABLE_CELL,
        text="Fussnote",
        provenance=Provenance(
            source_path="t.docx", index=0, region=Region.FOOTNOTE, cell=position, note="1"
        ),
    )
    document = _open(clean_docx(_translated([cell])))
    (table,) = document.tables
    assert [c.text for c in table.rows[0].cells] == ["", "1 [en] Fussnote"]
    assert document.sections[0].header.tables == []


def test_the_clean_document_does_not_rescan_itself_for_every_block(monkeypatch):
    """`paragraphs` and `sections` rebuild their lists from the whole body on every access, so
    reading them once per block makes a long document's export quadratic (CGL-87 review)."""
    calls = Counter()

    def counted(cls, name):
        original = getattr(cls, name)

        def get(self):
            calls[name] += 1
            return original.fget(self)

        monkeypatch.setattr(cls, name, property(get))

    counted(BlockItemContainer, "paragraphs")
    counted(docx.document.Document, "paragraphs")
    counted(docx.document.Document, "sections")
    counted(Table, "_cells")
    blocks = [
        Block(
            id=f"b{n}",
            kind=BlockKind.PARAGRAPH,
            text=f"Absatz {n}.",
            provenance=Provenance(source_path="t.docx", index=n),
        )
        for n in range(200)
    ]
    for n in range(200):  # one 40 x 5 table, then 50 tables of one cell
        if n < 150:
            cell = TableCellPosition(table=1, rows=40, cols=5, row=n // 5 + 1, col=n % 5 + 1)
        else:
            cell = TableCellPosition(table=n - 148, rows=1, cols=1, row=1, col=1)
        provenance = Provenance(source_path="t.docx", index=200 + n, cell=cell)
        blocks.append(
            Block(id=f"c{n}", kind=BlockKind.TABLE_CELL, text=f"Zelle {n}", provenance=provenance)
        )
    document = _open(clean_docx(_translated(blocks)))
    assert len(document.paragraphs) == 200 and len(document.tables) == 51
    assert document.tables[0].cell(29, 4).text == "[en] Zelle 149"
    calls["_cells"] -= 51  # listing each table's cells once is fine; per block is not
    assert max(calls.values(), default=0) <= 3, calls  # a few per document, never per block


# CGL-117: header and footer variants, and later sections' headers -------------------------


def _texts(part) -> list[str]:
    return [p.text for p in part.paragraphs if p.text]


def test_first_page_even_page_and_later_section_headers_are_not_stacked(tmp_path):
    d = docx.Document()
    d.settings.odd_and_even_pages_header_footer = True
    first = d.sections[0]
    first.different_first_page_header_footer = True
    first.header.paragraphs[0].text = "Running header"
    first.first_page_header.paragraphs[0].text = "Cover header"
    first.even_page_header.paragraphs[0].text = "Even header"
    first.footer.paragraphs[0].text = "Page footer"
    first.first_page_footer.paragraphs[0].text = "Cover footer"
    first.even_page_footer.paragraphs[0].text = "Even footer"
    d.add_paragraph("Chapter one.")
    second = d.add_section()
    second.header.is_linked_to_previous = False
    second.header.paragraphs[0].text = "Chapter 2 header"
    d.add_paragraph("Chapter two.")
    path = tmp_path / "variants.docx"
    d.save(path)

    document = _open(clean_docx(_ingested(path)))
    assert len(document.sections) == 1
    section = document.sections[0]
    assert section.different_first_page_header_footer
    assert document.settings.odd_and_even_pages_header_footer
    assert _texts(section.header) == ["[en] Running header"]
    assert _texts(section.first_page_header) == ["[en] Cover header"]
    assert _texts(section.even_page_header) == ["[en] Even header"]
    assert _texts(section.footer) == ["[en] Page footer"]
    assert _texts(section.first_page_footer) == ["[en] Cover footer"]
    assert _texts(section.even_page_footer) == ["[en] Even footer"]
    # the second section's header is kept, at the end, out of the page header
    assert _texts(document) == ["[en] Chapter one.", "[en] Chapter two.", "[en] Chapter 2 header"]


def test_a_file_with_one_header_turns_no_variant_on(tmp_path):
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "Acme"
    d.sections[0].footer.paragraphs[0].text = "Page"
    d.add_paragraph("Body.")
    path = tmp_path / "one.docx"
    d.save(path)
    document = _open(clean_docx(_ingested(path)))
    section = document.sections[0]
    assert not section.different_first_page_header_footer
    assert not document.settings.odd_and_even_pages_header_footer
    assert _texts(section.header) == ["[en] Acme"]
    assert _texts(section.footer) == ["[en] Page"]
    assert _texts(document) == ["[en] Body."]


def test_a_later_sections_header_table_goes_to_the_end_too(tmp_path):
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "First"
    d.add_paragraph("Body.")
    later = d.add_section()
    later.header.is_linked_to_previous = False
    later.header.paragraphs[0].text = "Later"
    grid = later.header.add_table(rows=1, cols=2, width=later.page_width)
    grid.cell(0, 0).text, grid.cell(0, 1).text = "A", "B"
    path = tmp_path / "later.docx"
    d.save(path)
    document = _open(clean_docx(_ingested(path)))
    assert _texts(document.sections[0].header) == ["[en] First"]
    assert document.sections[0].header.tables == []
    assert _texts(document) == ["[en] Body.", "[en] Later"]
    (table,) = document.tables
    assert [c.text for c in table.rows[0].cells] == ["[en] A", "[en] B"]


def test_an_older_runs_header_locator_still_goes_to_the_page_header():
    blocks = [
        Block(
            id="b1",
            kind=BlockKind.PARAGRAPH,
            text="Old header",
            provenance=Provenance(
                source_path="x.docx", index=0, region=Region.HEADER, locator=None
            ),
        ),
        Block(
            id="b2",
            kind=BlockKind.PARAGRAPH,
            text="Body.",
            provenance=Provenance(source_path="x.docx", index=1),
        ),
    ]
    document = _open(clean_docx(_translated(blocks)))
    assert _texts(document.sections[0].header) == ["[en] Old header"]


def test_a_cover_section_without_a_header_leaves_the_next_sections_on_the_page(tmp_path):
    d = docx.Document()
    d.add_paragraph("Cover.")
    body = d.add_section()
    body.header.is_linked_to_previous = False
    body.header.paragraphs[0].text = "Running header"
    body.footer.is_linked_to_previous = False
    body.footer.paragraphs[0].text = "Running footer"
    d.add_paragraph("Body.")
    path = tmp_path / "cover.docx"
    d.save(path)
    document = _open(clean_docx(_ingested(path)))
    section = document.sections[0]
    assert _texts(section.header) == ["[en] Running header"]
    assert _texts(section.footer) == ["[en] Running footer"]
    assert _texts(document) == ["[en] Cover.", "[en] Body."]


def test_an_older_runs_header_table_cell_still_goes_to_the_page_header():
    blocks = [
        Block(
            id="b1",
            kind=BlockKind.PARAGRAPH,
            text="Cell",
            provenance=Provenance(
                source_path="x.docx",
                index=0,
                region=Region.HEADER,
                locator="header, table 1 row 1 col 1",
                cell=TableCellPosition(table=1, rows=1, cols=1, row=1, col=1),
            ),
        ),
        Block(
            id="b2",
            kind=BlockKind.PARAGRAPH,
            text="Body.",
            provenance=Provenance(source_path="x.docx", index=1),
        ),
    ]
    document = _open(clean_docx(_translated(blocks)))
    (table,) = document.sections[0].header.tables
    assert table.rows[0].cells[0].text == "[en] Cell"
    assert _texts(document) == ["[en] Body."]
