"""Excel ingestion (CGL-110)."""

import zipfile

import pytest
from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont

from coglossa.ingest import registry
from coglossa.ingest.base import IngestError
from coglossa.ingest.xlsx import XlsxIngestor, cell_locator, parse_locator
from coglossa.schema import BlockKind, InlineMark, MarkKind
from tests.ingest.office_files import OLE_HEADER, table_workbook, workbook


def test_only_text_cells_are_read(tmp_path):
    doc = registry.ingest_file(workbook(tmp_path))
    assert doc.format == "xlsx"
    assert [(b.provenance.locator, b.text) for b in doc.blocks] == [
        ("sheet 'Budget', cell A1", "Poste"),
        ("sheet 'Budget', cell B1", "Montant"),
        ("sheet 'Budget', cell C1", "Date"),
        ("sheet 'Budget', cell A2", "Loyer du bureau"),
        ("sheet 'Budget', cell A3", "Frais urgents à payer"),
        ("sheet 'Budget', cell A4", "Total"),
        ("sheet 'Budget', cell A6", "Validé par la direction"),
        ("sheet 'Notes', cell B2", "Total"),
    ]  # never a number, date, boolean, formula, number stored as text or blank text
    assert {b.kind for b in doc.blocks} == {BlockKind.TABLE_CELL}
    assert all(b.provenance.cell is None for b in doc.blocks)
    assert doc.blocks[4].provenance.marks == [InlineMark(start=6, end=13, kind=MarkKind.BOLD)]
    assert doc.notices == [
        "Some parts of this file couldn't be read, so they aren't translated: "
        "1 chart and 1 comment."
    ]


def test_rich_text_formatting_and_text_boxes(tmp_path):
    wb = Workbook()
    wb.active["A1"] = CellRichText(
        [
            "  m",
            TextBlock(InlineFont(vertAlign="superscript"), "2"),
            " de H",
            TextBlock(InlineFont(vertAlign="subscript"), "2"),
            "O ",
            TextBlock(InlineFont(i=True, u="single"), "vite"),
            TextBlock(InlineFont(u="none"), " "),
        ]
    )
    path = tmp_path / "rich.xlsx"
    wb.save(path)
    with zipfile.ZipFile(path, "a") as z:  # a drawing with a text box, as Excel writes one
        z.writestr(
            "xl/drawings/drawing1.xml",
            '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/'
            'spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<xdr:twoCellAnchor><xdr:sp><xdr:txBody><a:p><a:r><a:t>Note</a:t></a:r></a:p>"
            "</xdr:txBody></xdr:sp></xdr:twoCellAnchor><xdr:twoCellAnchor><xdr:sp><xdr:txBody>"
            "<a:p><a:r><a:t> </a:t></a:r></a:p></xdr:txBody></xdr:sp></xdr:twoCellAnchor>"
            "</xdr:wsDr>",
        )
    doc = XlsxIngestor().ingest(path)
    assert [b.text for b in doc.blocks] == ["m2 de H2O vite"]
    assert doc.blocks[0].provenance.marks == [
        InlineMark(start=1, end=2, kind=MarkKind.SUPERSCRIPT),
        InlineMark(start=7, end=8, kind=MarkKind.SUBSCRIPT),
        InlineMark(start=10, end=14, kind=MarkKind.ITALIC),
        InlineMark(start=10, end=14, kind=MarkKind.UNDERLINE),
    ]
    assert doc.notices == [
        "Some parts of this file couldn't be read, so they aren't translated: 1 text box."
    ]


def test_locators_name_the_sheet_and_the_cell():
    assert parse_locator(cell_locator("Q1 ', cell 'x", "AB12")) == ("Q1 ', cell 'x", "AB12")
    assert parse_locator("slide 1, shape 2, paragraph 1") is None
    assert parse_locator(None) is None


def test_a_workbook_without_text_has_nothing_to_translate(tmp_path):
    wb = Workbook()
    wb.active["A1"] = 3
    wb.active["A2"] = "=A1*2"
    path = tmp_path / "numbers.xlsx"
    wb.save(path)
    with pytest.raises(IngestError, match="no translatable text"):
        registry.ingest_file(path)


def test_a_password_protected_workbook_says_so(tmp_path):
    path = tmp_path / "secret.xlsx"
    path.write_bytes(OLE_HEADER)
    with pytest.raises(IngestError, match="secret.xlsx is password-protected") as e:
        registry.ingest_file(path)
    assert "older Excel format" in str(e.value) and "save it as a .xlsx file" in str(e.value)


def test_a_file_that_is_no_workbook_is_refused(tmp_path):
    path = tmp_path / "plain.xlsx"
    path.write_bytes(b"a,b\n1,2\n")
    with pytest.raises(IngestError, match=r"^plain.xlsx is not a readable Excel file$"):
        registry.ingest_file(path)
    path = tmp_path / "other.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(IngestError, match=r"other.xlsx is not a readable Excel file \("):
        registry.ingest_file(path)


def test_a_damaged_workbook_is_refused(tmp_path, monkeypatch):
    path = workbook(tmp_path)

    def broken(path, workbook, headings):
        raise ValueError("bad drawing")

    monkeypatch.setattr("coglossa.ingest.xlsx._unread", broken)
    with pytest.raises(IngestError, match=r"part of it is damaged \(bad drawing\)"):
        registry.ingest_file(path)


def test_a_workbook_that_unpacks_to_too_much_is_refused(tmp_path, monkeypatch):
    path = workbook(tmp_path)
    monkeypatch.setattr("coglossa.ingest.base.MAX_OFFICE_UNCOMPRESSED_BYTES", 1024)
    with pytest.raises(IngestError, match="budget.xlsx is too large to translate once unpacked"):
        registry.ingest_file(path)


def test_a_tables_heading_and_totals_rows_are_not_read(tmp_path):
    """Excel requires a table's heading cells to match the names its columns keep (CGL-110)."""
    doc = registry.ingest_file(table_workbook(tmp_path))
    assert [(b.provenance.locator, b.text) for b in doc.blocks] == [
        ("sheet 'Clients', cell D1", "Hors du tableau"),
        ("sheet 'Clients', cell A2", "Dupont"),
        ("sheet 'Clients', cell B2", "Paris"),
        ("sheet 'Clients', cell A3", "Martin"),
        ("sheet 'Clients', cell B3", "Lyon"),
    ]
    assert doc.notices == [
        "Some parts of this file couldn't be read, so they aren't translated: 3 table headings."
    ]
