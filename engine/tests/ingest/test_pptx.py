"""PowerPoint ingestion (CGL-110)."""

import io
import zipfile

import pytest
from pptx import Presentation
from pptx.oxml import parse_xml
from pptx.oxml.ns import nsdecls
from pptx.util import Inches

from coglossa.ingest import registry
from coglossa.ingest.base import IngestError
from coglossa.ingest.pptx import PptxIngestor
from coglossa.schema import BlockKind, InlineMark, MarkKind
from tests.ingest.office_files import OLE_HEADER, deck, odd_deck


def test_slides_tables_and_notes_are_read_in_order(tmp_path):
    doc = registry.ingest_file(deck(tmp_path))
    assert doc.format == "pptx"
    assert [(b.kind, b.text) for b in doc.blocks] == [
        (BlockKind.HEADING, "Rapport annuel"),
        (BlockKind.PARAGRAPH, "Édition 2026"),
        (BlockKind.HEADING, "Les résultats"),
        (BlockKind.PARAGRAPH, "Le chiffre d'affaires augmente."),
        (BlockKind.PARAGRAPH, "Le chat voit ici\nDeuxième ligne."),
        (BlockKind.PARAGRAPH, "Une note à côté."),
        (BlockKind.TABLE_CELL, "Région"),
        (BlockKind.TABLE_CELL, "Ventes"),
        (BlockKind.TABLE_CELL, "Nord"),
        (BlockKind.TABLE_CELL, "12"),
        (BlockKind.TABLE_CELL, "Sud"),
        (BlockKind.PARAGRAPH, "Parler lentement."),
    ]
    by_text = {b.text: b.provenance for b in doc.blocks}
    assert by_text["Rapport annuel"].level == 0  # a title slide's title
    assert by_text["Les résultats"].level == 1
    assert by_text["Parler lentement."].locator == "slide 2 notes, paragraph 1"
    region = by_text["Région"].cell
    assert (region.table, region.rows, region.cols, region.row, region.col) == (1, 2, 3, 1, 1)
    assert (region.row_span, region.col_span) == (1, 2)
    assert by_text["Ventes"].locator == "slide 2, table 1 row 1 col 3, paragraph 1"
    assert by_text["Le chat voit ici\nDeuxième ligne."].marks == [
        InlineMark(start=3, end=7, kind=MarkKind.BOLD),
        InlineMark(start=13, end=16, kind=MarkKind.LINK, href="https://example.org/ici"),
    ]
    assert doc.notices == [
        "Some parts of this file couldn't be read, so they aren't translated: 1 chart."
    ]


def test_groups_formatting_and_what_cannot_be_read(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    box = group.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1))
    p = box.text_frame.paragraphs[0]
    for text, attr, value in (
        ("H", None, None),
        ("2", "baseline", "-25000"),
        ("O x", None, None),
        ("2", "baseline", "30000"),
        (" ", None, None),
        ("vite", "i", "1"),
        (" ", None, None),
        ("fort", "u", "sng"),
        (" ", None, None),
        ("ici", None, None),
    ):
        run = p.add_run()
        run.text = text
        if attr:
            run._r.get_or_add_rPr().set(attr, value)
    p.runs[-1].hyperlink.address = "javascript:alert(1)"  # no link a file may hold
    number = slide.shapes.add_textbox(Inches(1), Inches(3), Inches(1), Inches(1))
    number.text_frame.text = "   "
    frame = slide.shapes.add_textbox(Inches(1), Inches(4), Inches(1), Inches(1))
    frame._element.getparent().replace(frame._element, _diagram_frame())
    path = tmp_path / "group.pptx"
    prs.save(path)
    doc = PptxIngestor().ingest(path)
    assert [b.text for b in doc.blocks] == ["H2O x2 vite fort ici"]
    assert doc.blocks[0].provenance.marks == [
        InlineMark(start=1, end=2, kind=MarkKind.SUBSCRIPT),
        InlineMark(start=5, end=6, kind=MarkKind.SUPERSCRIPT),
        InlineMark(start=7, end=11, kind=MarkKind.ITALIC),
        InlineMark(start=12, end=16, kind=MarkKind.UNDERLINE),
    ]
    assert doc.notices == [
        "Some parts of this file couldn't be read, so they aren't translated: 1 diagram."
    ]


def _diagram_frame():
    return parse_xml(
        '<p:graphicFrame xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<p:nvGraphicFramePr><p:cNvPr id="9" name="Diagram"/><p:cNvGraphicFramePr/><p:nvPr/>'
        '</p:nvGraphicFramePr><p:xfrm><a:off x="0" y="0"/><a:ext cx="1" cy="1"/></p:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram"/>'
        "</a:graphic></p:graphicFrame>"
    )


def test_slide_numbers_and_dates_are_left_out(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Titre"
    for kind, text in (("sldNum", "7"), ("dt", "25/11/2026"), ("ftr", "Confidentiel")):
        box = slide.shapes.add_textbox(Inches(1), Inches(6), Inches(1), Inches(1))
        box.text_frame.text = text
        nv_pr = box._element.nvSpPr.nvPr
        nv_pr.append(parse_xml(f'<p:ph {nsdecls("p")} type="{kind}"/>'))
    path = tmp_path / "numbers.pptx"
    prs.save(path)
    assert [b.text for b in PptxIngestor().ingest(path).blocks] == ["Titre", "Confidentiel"]


def test_an_embedded_object_is_named(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_ole_object(
        io.BytesIO(b"not really a workbook"),
        "Excel.Sheet.12",
        Inches(1),
        Inches(1),
        Inches(1),
        Inches(1),
    )
    slide.shapes.add_textbox(Inches(3), Inches(3), Inches(1), Inches(1)).text_frame.text = "Oui"
    path = tmp_path / "ole.pptx"
    prs.save(path)
    assert PptxIngestor().ingest(path).notices == [
        "Some parts of this file couldn't be read, so they aren't translated: 1 embedded object."
    ]


def test_a_password_protected_deck_says_so(tmp_path):
    path = tmp_path / "secret.pptx"
    path.write_bytes(OLE_HEADER)
    with pytest.raises(IngestError, match="secret.pptx is password-protected") as e:
        registry.ingest_file(path)
    assert "remove the password" in str(e.value) and "PowerPoint" in str(e.value)


def test_a_file_that_is_no_deck_is_refused(tmp_path):
    path = tmp_path / "plain.pptx"
    path.write_bytes(b"just some text")
    with pytest.raises(IngestError, match=r"^plain.pptx is not a readable PowerPoint file$"):
        registry.ingest_file(path)
    path = tmp_path / "other.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(IngestError, match=r"other.pptx is not a readable PowerPoint file \("):
        registry.ingest_file(path)


def test_a_damaged_deck_is_refused(tmp_path, monkeypatch):
    path = deck(tmp_path)

    def broken(self, presentation):
        raise KeyError("rId9")

    monkeypatch.setattr("coglossa.ingest.pptx._Walker.deck", broken)
    with pytest.raises(IngestError, match=r"part of it is damaged \(rId9\)"):
        registry.ingest_file(path)


def test_untyped_shapes_ink_and_fields(tmp_path):
    """A shape python-pptx has no type for is read for its text and ink is passed over, instead
    of the deck being refused; a paragraph with a slide number or date field keeps its text (the
    download could not put the field back), and is named when it has words besides it."""
    doc = PptxIngestor().ingest(odd_deck(tmp_path))
    assert [b.text for b in doc.blocks] == ["Bonjour", "Fin"]
    assert doc.notices == [
        "Some parts of this file couldn't be read, so they aren't translated: "
        "1 paragraph with a slide number or date."
    ]
