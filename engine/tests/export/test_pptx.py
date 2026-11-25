"""The PowerPoint download (CGL-110): the translation written back into a copy of the deck."""

import io
import zipfile
from datetime import UTC, datetime

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn

from coglossa.export.office import ExportError
from coglossa.export.pptx import translated_pptx
from coglossa.ingest import registry
from coglossa.pipeline import RunOptions, run_document
from coglossa.schema import InlineMark, MarkKind, Report, SpanFormatting
from tests.export.conftest import translated_report
from tests.ingest.office_files import OLE_HEADER, deck


async def _no_sleep(_):
    return None


async def _translate(path, cfg, fake_backend) -> Report:
    report = await run_document(
        path,
        cfg,
        {"fake": fake_backend},
        RunOptions(target_lang="en", source_lang="fr"),
        sleep=_no_sleep,
        now=lambda: datetime(2026, 11, 25, tzinfo=UTC),
    )
    assert isinstance(report, Report)
    return report


def _layout(prs) -> list:
    """Every slide's layout and each shape's id, kind, place and size."""
    return [
        (
            slide.slide_layout.name,
            [(s.shape_id, s.shape_type, s.left, s.top, s.width, s.height) for s in slide.shapes],
        )
        for slide in prs.slides
    ]


def _texts(prs) -> list[str]:
    out = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                out += [p.text for p in shape.text_frame.paragraphs]
            if shape.has_table:
                out += [c.text for row in shape.table.rows for c in row.cells]
        out.append(slide.notes_slide.notes_text_frame.text)
    return out


async def test_a_deck_comes_back_as_the_same_deck_translated(tmp_path, make_config, fake_backend):
    """Acceptance 1: titles, body text, a table and speaker notes are translated; slides,
    layouts, the picture and the chart stay."""
    path = deck(tmp_path)
    original = path.read_bytes()
    report = await _translate(path, make_config(), fake_backend)
    data = translated_pptx(report, original)
    source, out = Presentation(io.BytesIO(original)), Presentation(io.BytesIO(data))
    assert _layout(out) == _layout(source)
    assert _texts(out) == [
        "[en] Rapport annuel",
        "[en] Édition 2026",
        "",
        "[en] Les résultats",
        "[en] Le chiffre d'affaires augmente.",
        "[en] Le chat voit ici\vDeuxième ligne.",
        "",  # an empty paragraph stays as it was
        "[en] Une note à côté.",
        "[en] Région",
        "",  # the cell the merged one covers
        "[en] Ventes",
        "[en] Nord",
        "[en] 12",
        "[en] Sud",
        "[en] Parler lentement.",
    ]
    slide = out.slides[1]
    picture = next(s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE)
    source_picture = next(
        s for s in source.slides[1].shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE
    )
    assert picture.image.blob == source_picture.image.blob
    chart = next(s for s in slide.shapes if s.has_chart).chart
    assert list(chart.plots[0].categories) == ["Nord", "Sud"]  # charts keep their own text
    body = slide.placeholders[1].text_frame.paragraphs
    assert body[1].level == 1  # the paragraph keeps its own settings
    runs = [(r.text, r.font.bold, r.hyperlink.address) for r in body[1].runs]
    assert ("chat", True, None) in runs
    assert ("ici", None, "https://example.org/ici") in runs
    box = next(s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.TEXT_BOX)
    assert {r.font.size.pt for r in box.text_frame.paragraphs[0].runs} == {28}


def _report_of(data: bytes, tmp_path, target_lang: str = "en") -> Report:
    path = tmp_path / "source.pptx"
    path.write_bytes(data)
    blocks = registry.ingest_file(path).blocks
    return translated_report(blocks, data, fmt="pptx", target_lang=target_lang)


def _body(data: bytes):
    """The paragraphs of the second slide's body."""
    return Presentation(io.BytesIO(data)).slides[1].placeholders[1].text_frame.paragraphs


def _edited_deck(tmp_path) -> bytes:
    """The deck, with the formatted paragraph's first run saying itself that it is not bold,
    and in French."""
    prs = Presentation(deck(tmp_path))
    rpr = prs.slides[1].placeholders[1].text_frame.paragraphs[1].runs[0]._r.get_or_add_rPr()
    rpr.set("b", "0")
    rpr.set("lang", "fr-FR")
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


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


def test_formatting_comes_back_on_the_translated_words(tmp_path):
    original = _edited_deck(tmp_path)
    text = "The bold cat\nsees x2 H2O here"
    marks = [
        InlineMark(start=0, end=3, kind=MarkKind.LINK, href="javascript:alert(1)"),
        InlineMark(start=4, end=8, kind=MarkKind.BOLD),
        InlineMark(start=9, end=12, kind=MarkKind.ITALIC),
        InlineMark(start=9, end=12, kind=MarkKind.UNDERLINE),
        InlineMark(start=19, end=20, kind=MarkKind.SUPERSCRIPT),
        InlineMark(start=22, end=23, kind=MarkKind.SUBSCRIPT),
        InlineMark(start=25, end=29, kind=MarkKind.LINK, href="https://example.org/en"),
    ]
    report = _with_text(
        _report_of(original, tmp_path), "Le chat voit ici\nDeuxième ligne.", text, marks
    )
    paragraph = _body(translated_pptx(report, original))[1]
    assert paragraph.text == "The bold cat\vsees x2 H2O here"
    runs = paragraph.runs
    assert [r.text for r in runs] == [
        "The",
        " ",
        "bold",
        " ",
        "cat",
        "sees x",
        "2",
        " H",
        "2",
        "O ",
        "here",
    ]
    assert runs[0].hyperlink.address is None  # no link to a script
    # the source's runs said "not bold" themselves, so the plain words still do
    assert [r.font.bold for r in runs[1:5]] == [False, True, False, False]
    assert (runs[4].font.italic, runs[4].font.underline) == (True, True)
    baselines = [r._r.find(qn("a:rPr")).get("baseline") for r in runs[5:10]]
    assert baselines == [None, "30000", None, "-25000", None]
    assert runs[10].hyperlink.address == "https://example.org/en"
    assert runs[10].font.underline is True
    assert {r._r.find(qn("a:rPr")).get("lang") for r in paragraph.runs} == {"en"}
    assert paragraph._p.find(qn("a:br")).find(qn("a:rPr")).get("lang") == "en"


def test_a_runs_look_comes_back(tmp_path):
    original = deck(tmp_path).read_bytes()
    marks = [InlineMark(start=1, end=2, kind=MarkKind.SUBSCRIPT)]
    report = _with_text(_report_of(original, tmp_path), "Une note à côté.", "H2O", marks)
    data = translated_pptx(report, original)
    box = next(
        s
        for s in Presentation(io.BytesIO(data)).slides[1].shapes
        if s.shape_type == MSO_SHAPE_TYPE.TEXT_BOX
    )
    runs = box.text_frame.paragraphs[0].runs
    assert [(r.text, r._r.find(qn("a:rPr")).get("baseline")) for r in runs] == [
        ("H", None),
        ("2", "-25000"),
        ("O", None),
    ]
    assert {r.font.size.pt for r in runs} == {28}


def test_a_paragraph_translated_with_the_one_before_is_left_empty(tmp_path):
    original = deck(tmp_path).read_bytes()
    report = _report_of(original, tmp_path)
    first = next(s for s in report.spans if s.text == "[en] Le chiffre d'affaires augmente.")
    second = report.spans[report.spans.index(first) + 1]
    merged = first.model_copy(update={"end": second.end, "text": "[en] Both together."})
    spans = [merged if s is first else s for s in report.spans if s is not second]
    data = translated_pptx(report.model_copy(update={"spans": spans}), original)
    assert [p.text for p in _body(data)] == ["[en] Both together.", "", ""]


def test_a_right_to_left_translation_marks_its_paragraphs(tmp_path):
    original = deck(tmp_path).read_bytes()
    data = translated_pptx(_report_of(original, tmp_path, target_lang="ar"), original)
    assert {p._p.pPr.get("rtl") for p in _body(data)[:2]} == {"1"}
    assert _body(data)[2]._p.pPr is None  # the empty paragraph was not read


def test_only_the_file_that_was_translated_can_be_written_back(tmp_path):
    original = deck(tmp_path).read_bytes()
    report = _report_of(original, tmp_path)
    as_word = report.model_copy(
        update={"document": report.document.model_copy(update={"format": "docx"})}
    )
    with pytest.raises(ExportError, match="only a translation of a PowerPoint file"):
        translated_pptx(as_word, original)
    with pytest.raises(ExportError, match="stored copy of source.pptx is not the file"):
        translated_pptx(report, original + b"x")
    fewer = report.model_copy(update={"blocks": report.blocks[1:]})
    with pytest.raises(ExportError, match="does not match the text that was translated"):
        translated_pptx(fewer, original)


def test_a_file_that_cannot_be_read_back_says_so(tmp_path):
    for data, message in (
        (OLE_HEADER, "source.pptx is password-protected"),
        (b"text", "source.pptx is not a readable PowerPoint file"),
        (_zip_of({"hello.txt": "hi"}), r"source.pptx is not a readable PowerPoint file \("),
    ):
        report = translated_report([], data, fmt="pptx")
        with pytest.raises(ExportError, match=message):
            translated_pptx(report, data)


def _zip_of(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return out.getvalue()
