"""The Excel download (CGL-110): the translation written back into a copy of the workbook."""

import io
import re
import zipfile

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock

from coglossa.export.office import ExportError
from coglossa.export.xlsx import translated_xlsx
from coglossa.ingest import registry
from coglossa.schema import InlineMark, MarkKind, Report, SpanFormatting
from tests.export.conftest import translated_report
from tests.ingest.office_files import OLE_HEADER, workbook


def _report_of(data: bytes, tmp_path, name: str = "budget") -> Report:
    path = tmp_path / f"{name}.xlsx"
    path.write_bytes(data)
    blocks = registry.ingest_file(path).blocks
    return translated_report(blocks, data, fmt="xlsx", name=name)


def _cells(data: bytes) -> dict[tuple[str, str], tuple]:
    """Every cell of every sheet: its value, its type and its number format."""
    wb = load_workbook(io.BytesIO(data))
    return {
        (ws.title, c.coordinate): (c.value, c.data_type, c.number_format, c.font.b)
        for ws in wb.worksheets
        for row in ws.iter_rows()
        for c in row
    }


def _shape(data: bytes) -> list:
    wb = load_workbook(io.BytesIO(data))
    return [
        (ws.title, ws.dimensions, sorted(str(r) for r in ws.merged_cells.ranges))
        for ws in wb.worksheets
    ]


def _part(data: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read(name).decode("utf-8")


def test_text_cells_are_translated_and_nothing_else_changes(tmp_path):
    """Acceptance 2: formulas, numbers, dates and the sheets' names and sizes are unchanged."""
    original = workbook(tmp_path).read_bytes()
    report = _report_of(original, tmp_path)
    data = translated_xlsx(report, original)
    translated = {
        ("Budget", "A1"),
        ("Budget", "B1"),
        ("Budget", "C1"),
        ("Budget", "A2"),
        ("Budget", "A3"),
        ("Budget", "A4"),
        ("Budget", "A6"),
        ("Notes", "B2"),
    }
    before, after = _cells(original), _cells(data)
    assert before.keys() == after.keys()
    for key, (value, kind, number_format, bold) in before.items():
        if key in translated:
            assert after[key] == (f"[en] {value}", kind, number_format, bold), key
        else:
            assert after[key] == (value, kind, number_format, bold), key
    assert after[("Budget", "B4")][0] == "=SUM(B2:B3)"
    assert after[("Notes", "B3")][0] == "=Budget!B4*2"
    assert after[("Budget", "E1")][0] == "00123"
    assert after[("Budget", "A1")][3] is True  # the cell's own bold stays
    assert _shape(data) == _shape(original)
    with zipfile.ZipFile(io.BytesIO(original)) as a, zipfile.ZipFile(io.BytesIO(data)) as b:
        assert a.namelist() == b.namelist()
        for name in a.namelist():  # the chart, the drawing, the comment, the styles...
            if not re.fullmatch(r"xl/worksheets/sheet\d\.xml|xl/sharedStrings\.xml", name):
                assert a.read(name) == b.read(name), name
    strings = _part(data, "xl/sharedStrings.xml")
    assert "Loyer du bureau" not in strings  # the original text does not stay hidden
    assert "00123" in strings  # still used by its cell
    assert 'count="2"' in strings  # two cells still use a shared string (E1, E2)


def _with_text(report: Report, block_text: str, text: str, marks: list[InlineMark]) -> Report:
    block = next(b for b in report.blocks if b.text == block_text)
    segment = next(s for s in report.segments if s.block_id == block.id)
    formatting = SpanFormatting(text=text, marks=marks)
    spans = [
        s.model_copy(update={"text": text, "formatting": formatting})
        if s.start == segment.id
        else s
        for s in report.spans
    ]
    return report.model_copy(update={"spans": spans})


def test_words_keep_their_formatting_and_the_cells_look(tmp_path):
    original = workbook(tmp_path).read_bytes()
    marks = [
        InlineMark(start=0, end=6, kind=MarkKind.BOLD),
        InlineMark(start=7, end=12, kind=MarkKind.ITALIC),
        InlineMark(start=7, end=12, kind=MarkKind.UNDERLINE),
        InlineMark(start=14, end=15, kind=MarkKind.SUPERSCRIPT),
        InlineMark(start=17, end=18, kind=MarkKind.SUBSCRIPT),
    ]
    report = _report_of(original, tmp_path)
    report = _with_text(report, "Frais urgents à payer", "Urgent costs x2 y3 ", marks)
    report = _with_text(report, "Loyer du bureau", "Office rent", [])
    data = translated_xlsx(report, original)
    ws = load_workbook(io.BytesIO(data), rich_text=True)["Budget"]
    rich = ws["A3"].value
    assert isinstance(rich, CellRichText)
    blocks = [b for b in rich if isinstance(b, TextBlock)]
    assert [b.text for b in blocks] == ["Urgent", " ", "costs", " x", "2", " y", "3", " "]
    assert [bool(b.font.b) for b in blocks][:3] == [True, False, False]
    assert (blocks[2].font.i, blocks[2].font.u) == (True, "single")
    assert (blocks[4].font.vertAlign, blocks[6].font.vertAlign) == ("superscript", "subscript")
    assert {b.font.rFont for b in blocks} == {"Calibri"}  # the first original run's look
    assert ws["A2"].value == "Office rent"
    assert '<t xml:space="preserve">' in _part(data, "xl/worksheets/sheet1.xml")


def test_a_cell_translated_with_the_one_before_is_left_empty(tmp_path):
    original = workbook(tmp_path).read_bytes()
    report = _report_of(original, tmp_path)
    first, second = report.spans[3], report.spans[4]  # A2 and A3
    merged = first.model_copy(update={"end": second.end, "text": "[en] Rent and costs."})
    spans = [merged if s is first else s for s in report.spans if s is not second]
    data = translated_xlsx(report.model_copy(update={"spans": spans}), original)
    ws = load_workbook(io.BytesIO(data))["Budget"]
    assert (ws["A2"].value, ws["A3"].value) == ("[en] Rent and costs.", None)


def test_inline_text_and_cell_extensions(tmp_path):
    wb = Workbook()
    wb.active.title = "Feuille"
    wb.active["A1"] = "Bonjour"
    wb.active["B1"] = "Au revoir"
    buffer = io.BytesIO()
    wb.save(buffer)
    original = _rewrite(
        buffer.getvalue(),
        "xl/worksheets/sheet1.xml",
        lambda xml: xml.replace("</is></c>", "</is><extLst/></c>", 1),
    )
    report = _report_of(original, tmp_path, name="inline")
    data = translated_xlsx(report, original)
    sheet = _part(data, "xl/worksheets/sheet1.xml")
    assert "<is><t>[en] Bonjour</t></is><extLst/>" in sheet
    assert load_workbook(io.BytesIO(data))["Feuille"]["B1"].value == "[en] Au revoir"


def _rewrite(data: bytes, name: str, change) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            content = zin.read(info)
            if info.filename == name:
                content = change(content.decode("utf-8")).encode("utf-8")
            zout.writestr(info, content)
    return out.getvalue()


def test_only_the_file_that_was_translated_can_be_written_back(tmp_path):
    original = workbook(tmp_path).read_bytes()
    report = _report_of(original, tmp_path)
    as_deck = report.model_copy(
        update={"document": report.document.model_copy(update={"format": "pptx"})}
    )
    with pytest.raises(ExportError, match="only a translation of an Excel file"):
        translated_xlsx(as_deck, original)
    with pytest.raises(ExportError, match="stored copy of budget.xlsx is not the file"):
        translated_xlsx(report, original + b"x")
    for locator in (
        "sheet 'Gone', cell A1",
        "slide 1, shape 2, paragraph 1",
        "sheet 'Budget', cell Z99",
        "sheet 'Budget', cell B2",
    ):
        block = report.blocks[0]
        moved = block.model_copy(
            update={"provenance": block.provenance.model_copy(update={"locator": locator})}
        )
        changed = report.model_copy(update={"blocks": [moved, *report.blocks[1:]]})
        with pytest.raises(ExportError, match="does not match the text that was translated"):
            translated_xlsx(changed, original)


def test_a_file_that_cannot_be_written_back_says_so(tmp_path):
    original = workbook(tmp_path).read_bytes()
    report = _report_of(original, tmp_path)
    for data, message in (
        (OLE_HEADER, "budget.xlsx is password-protected"),
        (b"a,b", "budget.xlsx is not a readable Excel file"),
        (
            _rewrite(original, "xl/sharedStrings.xml", lambda xml: xml[:40]),
            "budget.xlsx could not be written back as an Excel file: part of it is damaged",
        ),
    ):
        broken = report.model_copy(
            update={"document": translated_report([], data, fmt="xlsx", name="budget").document}
        )
        with pytest.raises(ExportError, match=message):
            translated_xlsx(broken, data)
