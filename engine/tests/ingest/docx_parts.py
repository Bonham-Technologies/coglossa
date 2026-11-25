"""Builds DOCX test files with the parts python-docx has no API for (CGL-87): footnotes and
endnotes, text boxes, charts and equations, written as raw OOXML."""

from typing import Any

import docx
from docx.enum.style import WD_STYLE_TYPE
from docx.opc.constants import CONTENT_TYPE as CT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NAMESPACES = (
    f'xmlns:w="{W}" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)


def _run(inner: str) -> Any:
    return parse_xml(f"<w:r {_NAMESPACES}>{inner}</w:r>")


def _paragraphs(texts: list[str]) -> str:
    return "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in texts)


def _note_paragraphs(kind: str, texts: list[str]) -> str:
    """Word starts a note with its number (a w:footnoteRef run), then the text."""
    first, *rest = texts
    return (
        f'<w:p><w:pPr><w:pStyle w:val="{kind.capitalize()}Text"/></w:pPr>'
        f'<w:r><w:{kind}Ref/></w:r><w:r><w:t xml:space="preserve"> {first}</w:t></w:r></w:p>'
        + _paragraphs(rest)
    )


def add_notes(document: Any, notes: dict[int, str], *, kind: str = "footnote") -> None:
    """A footnotes (or endnotes) part with Word's two separator notes and one note per id."""
    separators = (
        f'<w:{kind} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:{kind}>'
        f'<w:{kind} w:type="continuationSeparator" w:id="0">'
        f"<w:p><w:r><w:continuationSeparator/></w:r></w:p></w:{kind}>"
    )
    body = "".join(
        f'<w:{kind} w:id="{note_id}">{_note_paragraphs(kind, text.split("\n"))}</w:{kind}>'
        for note_id, text in notes.items()
    )
    xml = f"<w:{kind}s {_NAMESPACES}>{separators}{body}</w:{kind}s>"
    content_type = CT.WML_FOOTNOTES if kind == "footnote" else CT.WML_ENDNOTES
    reltype = RT.FOOTNOTES if kind == "footnote" else RT.ENDNOTES
    part = Part(PackURI(f"/word/{kind}s.xml"), content_type, xml.encode(), document.part.package)
    document.part.relate_to(part, reltype)


def add_note_reference(paragraph: Any, note_id: int, *, kind: str = "footnote") -> None:
    paragraph._p.append(_run(f'<w:{kind}Reference w:id="{note_id}"/>'))


def add_text_box(paragraph: Any, texts: list[str], *, fallback: bool = True) -> None:
    """A text box the way Word writes it: a drawing shape, with a VML copy for old readers."""
    content = f"<w:txbxContent>{_paragraphs(texts)}</w:txbxContent>"
    choice = (
        '<mc:Choice Requires="wps"><w:drawing><wp:anchor><a:graphic>'
        '<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        f"<wps:wsp><wps:txbx>{content}</wps:txbx></wps:wsp>"
        "</a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>"
    )
    vml = f"<w:pict><v:shape><v:textbox>{content}</v:textbox></v:shape></w:pict>"
    alternate = (
        f"<mc:AlternateContent>{choice}<mc:Fallback>{vml}</mc:Fallback></mc:AlternateContent>"
    )
    paragraph._p.append(_run(alternate if fallback else vml))


def add_chart(paragraph: Any) -> None:
    paragraph._p.append(
        _run(
            "<w:drawing><wp:inline><a:graphic>"
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            '<c:chart r:id="rId999"/></a:graphicData></a:graphic></wp:inline></w:drawing>'
        )
    )


def add_diagram(paragraph: Any) -> None:
    paragraph._p.append(
        _run(
            "<w:drawing><wp:inline><a:graphic>"
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">'
            '<dgm:relIds r:dm="rId1" r:lo="rId2" r:qs="rId3" r:cs="rId4"/>'
            "</a:graphicData></a:graphic></wp:inline></w:drawing>"
        )
    )


def add_embedded_object(paragraph: Any) -> None:
    paragraph._p.append(_run('<w:object><o:OLEObject ProgID="Excel.Sheet.12"/></w:object>'))


def add_equation(paragraph: Any, text: str = "x=1") -> None:
    math = parse_xml(f"<m:oMath {_NAMESPACES}><m:r><m:t>{text}</m:t></m:r></m:oMath>")
    paragraph._p.append(math)


def add_tracked_insertion(paragraph: Any, text: str) -> None:
    paragraph._p.append(
        parse_xml(
            f'<w:ins {_NAMESPACES} w:id="1" w:author="A" w:date="2026-01-01T00:00:00Z">'
            f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:ins>'
        )
    )
    paragraph._p.append(
        parse_xml(
            f'<w:del {_NAMESPACES} w:id="2" w:author="A" w:date="2026-01-01T00:00:00Z">'
            "<w:r><w:delText>gone</w:delText></w:r></w:del>"
        )
    )


def add_content_control(document: Any, text: str) -> None:
    """A block-level content control (w:sdt) holding one paragraph, at the end of the body."""
    sdt = parse_xml(
        f"<w:sdt {_NAMESPACES}><w:sdtPr/><w:sdtContent>{_paragraphs([text])}</w:sdtContent></w:sdt>"
    )
    document.element.body.sectPr.addprevious(sdt)


def add_custom_xml(document: Any, text: str) -> None:
    """A paragraph wrapped in a custom XML element, at the end of the body."""
    wrapped = parse_xml(
        f'<w:customXml {_NAMESPACES} w:element="clause">{_paragraphs([text])}</w:customXml>'
    )
    document.element.body.sectPr.addprevious(wrapped)


def add_imported_chunk(document: Any) -> None:
    """A w:altChunk: a part of another format (HTML, RTF) Word merges in when it opens the file."""
    document.element.body.sectPr.addprevious(parse_xml(f'<w:altChunk {_NAMESPACES} r:id="rId77"/>'))


def _cells(texts: list[str]) -> str:
    return "".join(f"<w:tc>{_paragraphs([t])}</w:tc>" for t in texts)


def wrap_rows(table: Any, rows: list[list[str]]) -> None:
    """Rows inside a content control (a repeating section), at the end of the table."""
    inner = "".join(f"<w:tr>{_cells(cells)}</w:tr>" for cells in rows)
    table._tbl.append(
        parse_xml(f"<w:sdt {_NAMESPACES}><w:sdtPr/><w:sdtContent>{inner}</w:sdtContent></w:sdt>")
    )


def wrap_cell(row: Any, text: str) -> None:
    """A cell inside a content control, at the end of the row."""
    row._tr.append(
        parse_xml(
            f"<w:sdt {_NAMESPACES}><w:sdtPr/><w:sdtContent>{_cells([text])}</w:sdtContent></w:sdt>"
        )
    )


def full_document(path: Any) -> Any:
    """A Word file with a header and footer, a title and a heading, a cited footnote and
    endnote, a text box, a table with a nested table, and a second section."""
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "Acme Confidential"
    d.sections[0].footer.paragraphs[0].text = "Internal use"
    d.add_heading("Annual report", level=0)
    d.add_heading("Background", level=2)
    cited = d.add_paragraph("The figures are audited.")
    add_note_reference(cited, 1)
    anchor = d.add_paragraph("See the box.")
    add_text_box(anchor, ["Boxed fact."])
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Outer A"
    outer = table.cell(0, 1)
    outer.text = "Outer B"
    inner = outer.add_table(rows=1, cols=2)
    inner.cell(0, 0).text = "Inner 1"
    inner.cell(0, 1).text = "Inner 2"
    outer.paragraphs[-1].text = "After inner"
    table.cell(1, 0).text = "Outer C"
    closing = d.add_paragraph("Closing words.")
    add_note_reference(closing, 2, kind="endnote")
    add_notes(d, {1: "Audited by Example LLP."})
    add_notes(d, {2: "Figures are rounded."}, kind="endnote")
    # a second section whose own header repeats the first one's text; its footer is linked
    section = d.add_section()
    section.header.is_linked_to_previous = False
    section.header.paragraphs[0].text = "Acme Confidential"
    d.add_paragraph("Second section.")
    d.save(path)
    return path


def add_hyperlink(paragraph: Any, part: Any, text: str, url: str) -> None:
    """A link to `url` around one run; the target is in `part`'s relationships."""
    rid = part.relate_to(url, RT.HYPERLINK, is_external=True)
    paragraph._p.append(
        parse_xml(
            f'<w:hyperlink {_NAMESPACES} r:id="{rid}"><w:r><w:t>{text}</w:t></w:r></w:hyperlink>'
        )
    )


def add_field_link(paragraph: Any, text: str, instruction: str) -> None:
    """A link made with a simple field, `HYPERLINK "url"` (CGL-129)."""
    paragraph._p.append(
        parse_xml(
            f"<w:fldSimple {_NAMESPACES} w:instr='{instruction}'>"
            f"<w:r><w:t>{text}</w:t></w:r></w:fldSimple>"
        )
    )


def add_complex_field_link(paragraph: Any, texts: list[str], instruction: str) -> None:
    """A link made with a complex field: begin, the instruction in two pieces, separate, one
    run per text as the result, end (CGL-129)."""
    half = len(instruction) // 2
    for inner in (
        '<w:fldChar w:fldCharType="begin"/>',
        f'<w:instrText xml:space="preserve">{instruction[:half]}</w:instrText>',
        f'<w:instrText xml:space="preserve">{instruction[half:]}</w:instrText>',
        '<w:fldChar w:fldCharType="separate"/>',
        *(f'<w:t xml:space="preserve">{t}</w:t>' for t in texts),
        '<w:fldChar w:fldCharType="end"/>',
    ):
        paragraph._p.append(_run(inner))


def add_character_style(document: Any, name: str, based_on: str, *, bold: bool | None) -> Any:
    """A character style based on another, with its own bold setting or none (CGL-129)."""
    style = document.styles.add_style(name, WD_STYLE_TYPE.CHARACTER)
    style.base_style = document.styles[based_on]
    style.font.bold = bold
    return style
