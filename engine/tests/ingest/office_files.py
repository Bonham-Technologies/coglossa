"""PowerPoint and Excel files built in the test (CGL-110), so no binary fixture is checked in."""

import datetime
import io
from pathlib import Path

import xlsxwriter
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt

# An OLE compound file's first bytes: how Office saves a password-protected file
OLE_HEADER = bytes.fromhex("d0cf11e0a1b11ae1") + bytes(504)


def png() -> io.BytesIO:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(out, format="PNG")
    out.seek(0)
    return out


def deck(path: Path) -> Path:
    """A title slide, then a slide with a title, formatted body text, a text box, a table with
    a merged cell, a picture, a chart, a slide number and speaker notes."""
    prs = Presentation()
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = "Rapport annuel"
    cover.placeholders[1].text = "Édition 2026"

    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Les résultats"
    body = slide.placeholders[1].text_frame
    body.text = "Le chiffre d'affaires augmente."
    p = body.add_paragraph()
    p.level = 1
    for text, bold, link in (
        ("Le ", False, None),
        ("chat", True, None),
        (" voit ", False, None),
        ("ici", False, "https://example.org/ici"),
    ):
        run = p.add_run()
        run.text = text
        if bold:
            run.font.bold = True
        if link:
            run.hyperlink.address = link
    p.add_line_break()
    p.add_run().text = "Deuxième ligne."
    body.add_paragraph()  # an empty paragraph is no block
    box = slide.shapes.add_textbox(Inches(5), Inches(5), Inches(3), Inches(1))
    box.text_frame.text = "Une note à côté."
    box.text_frame.paragraphs[0].runs[0].font.size = Pt(28)
    table = slide.shapes.add_table(2, 3, Inches(1), Inches(4), Inches(6), Inches(1)).table
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(0, 0).text = "Région"
    table.cell(0, 2).text = "Ventes"
    table.cell(1, 0).text = "Nord"
    table.cell(1, 1).text = "12"
    table.cell(1, 2).text = "Sud"
    slide.shapes.add_picture(png(), Inches(8), Inches(1), Inches(1), Inches(1))
    data = CategoryChartData()
    data.categories = ["Nord", "Sud"]
    data.add_series("Ventes", (12, 7))
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(5.5), Inches(3), Inches(2), data
    )
    slide.notes_slide.notes_text_frame.text = "Parler lentement."
    path = path / "deck.pptx"
    prs.save(path)
    return path


def workbook(path: Path) -> Path:
    """Two sheets of text, numbers, a date, a boolean, formulas, a number stored as text, rich
    text, a merged range, a comment and a chart. Written with XlsxWriter (which python-pptx
    depends on), which stores text as Excel does, in a shared strings part."""
    path = path / "budget.xlsx"
    wb = xlsxwriter.Workbook(path)
    bold = wb.add_format({"bold": True})
    dates = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws = wb.add_worksheet("Budget")
    ws.write_row("A1", ["Poste", "Montant", "Date"], bold)
    ws.write("A2", "Loyer du bureau")
    ws.write_number("B2", 1200)
    ws.write_datetime("C2", datetime.datetime(2026, 1, 31), dates)
    ws.write_rich_string("A3", "Frais ", bold, "urgents", " à payer")
    ws.write_number("B3", 80.5)
    ws.write_boolean("D3", True)
    ws.write("A4", "Total")
    ws.write_formula("B4", "=SUM(B2:B3)", None, 1280.5)
    ws.write_formula("C4", '="Mois "&TEXT(C2,"mm")', None, "Mois 01")
    ws.write_string("E1", "00123")
    ws.write_string("E2", "   ")
    ws.merge_range("A6:C6", "Validé par la direction")
    ws.write_comment("A6", "À vérifier")
    chart = wb.add_chart({"type": "column"})
    chart.add_series({"values": "=Budget!$B$2:$B$3"})
    ws.insert_chart("G2", chart)
    other = wb.add_worksheet("Notes")
    other.write("B2", "Total")
    other.write_formula("B3", "=Budget!B4*2", None, 2561)
    wb.close()
    return path
