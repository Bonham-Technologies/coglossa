"""Inline formatting end to end (CGL-108): a source's bold, italic, links and superscripts come
back on the translated words in the clean Word download, a broken answer leaves the passage
plain and flagged, markers appear in no export, and older runs export as before."""

import io
import json
import re
from datetime import UTC, datetime

import docx
from docx.oxml.ns import qn

from coglossa.export.docx import bilingual_docx, clean_docx
from coglossa.export.html import render_html
from coglossa.export.tmx import tmx_bytes
from coglossa.pipeline import RunOptions, run_document
from coglossa.schema import FlagCode, InlineMark, MarkKind, Region, Report, SpanFormatting
from tests.export.conftest import make_report, make_span
from tests.ingest.docx_parts import add_hyperlink

MARKER = re.compile(r"<\s*/?\s*\d+\s*/?\s*>")


async def _no_sleep(_):
    return None


async def _translate(path, cfg, fake_backend) -> Report:
    report = await run_document(
        path,
        cfg,
        {"fake": fake_backend},
        RunOptions(target_lang="en", source_lang="fr"),
        sleep=_no_sleep,
        now=lambda: datetime(2026, 11, 24, tzinfo=UTC),
    )
    assert isinstance(report, Report)
    return report


def _runs(data: bytes, region: str = "body") -> list[tuple[str, frozenset[str]]]:
    """Each run of the document's paragraphs: its text and its formatting, a link as its target."""
    document = docx.Document(io.BytesIO(data))
    part = document.part if region == "body" else document.sections[0].header.part
    root = document.element.body if region == "body" else document.sections[0].header._element
    out = []
    for r in root.iter(qn("w:r")):
        props = set()
        rpr = r.find(qn("w:rPr"))
        if rpr is not None:
            for tag, name in (("w:b", "bold"), ("w:i", "italic"), ("w:u", "underline")):
                if rpr.find(qn(tag)) is not None:
                    props.add(name)
            align = rpr.find(qn("w:vertAlign"))
            if align is not None:
                props.add(align.get(qn("w:val")))
        link = r.getparent()
        if link.tag == qn("w:hyperlink"):
            props.add("link:" + part.rels[link.get(qn("r:id"))].target_ref)
        out.append(("".join(t.text or "" for t in r.iter(qn("w:t"))), frozenset(props)))
    return out


def _formatted(runs) -> list[tuple[str, frozenset[str]]]:
    return [(text, props) for text, props in runs if props]


def _source_docx(tmp_path):
    d = docx.Document()
    p = d.add_paragraph("Le ")
    p.add_run("chat").bold = True
    p.add_run(" et ")
    p.add_run("le chien").italic = True
    p.add_run(" voient ")
    add_hyperlink(p, d.part, "ici", "https://example.org/ici")
    p.add_run(" la note")
    p.add_run("2").font.superscript = True
    p.add_run(".")
    d.add_paragraph("Rien de plus.")
    path = tmp_path / "source.docx"
    d.save(path)
    return path


async def test_a_word_files_formatting_comes_back_on_the_translated_words(
    tmp_path, make_config, fake_backend
):
    """Acceptance 1: bold, italic, a link and a superscript in one paragraph."""
    report = await _translate(_source_docx(tmp_path), make_config(), fake_backend)
    assert report.spans[0].text == "[en] Le chat et le chien voient ici la note2."
    assert _formatted(_runs(clean_docx(report))) == [
        ("chat", {"bold"}),
        ("le chien", {"italic"}),
        ("ici", {"underline", "link:https://example.org/ici"}),
        ("2", {"superscript"}),
    ]


async def test_an_html_files_formatting_comes_back_on_the_reordered_words(
    tmp_path, make_config, fake_backend
):
    """Acceptance 2: <b>, <em>, <a href> and <sup>, following the words the reviewer moved."""
    path = tmp_path / "page.html"
    path.write_text(
        "<p>Le <b>chat</b> <em>noir</em> voit <a href='https://example.org/'>ici</a> "
        "la note<sup>2</sup>.</p>",
        encoding="utf-8",
    )
    review = {
        "spans": [
            {
                "start": "s1",
                "end": "s1",
                "text": "The <2>black</2> <1>cat</1> sees note<4>2</4> <3>here</3>.",
                "agreement": "agree",
                "chosen_from": "contextual",
            }
        ]
    }
    fake_backend.script("reconcile", json.dumps(review))
    report = await _translate(path, make_config(), fake_backend)
    assert report.spans[0].text == "The black cat sees note2 here."
    assert _formatted(_runs(clean_docx(report))) == [
        ("black", {"italic"}),
        ("cat", {"bold"}),
        ("2", {"superscript"}),
        ("here", {"underline", "link:https://example.org/"}),
    ]


async def test_a_dropped_marker_leaves_the_passage_plain_and_flagged(
    tmp_path, make_config, fake_backend
):
    """Acceptance 3: the run completes; the passage is plain and says why."""
    path = tmp_path / "page.html"
    path.write_text("<p>Le <b>chat</b> dort.</p><p>Il <i>pleut</i>.</p>", encoding="utf-8")
    review = {
        "spans": [
            {"start": "s1", "end": "s1", "text": "The cat sleeps.", "agreement": "agree",
             "chosen_from": "literal"},
            {"start": "s2", "end": "s2", "text": "It <2>rains</2>.", "agreement": "agree",
             "chosen_from": "literal"},
        ]
    }  # fmt: skip
    fake_backend.script("reconcile", json.dumps(review))
    report = await _translate(path, make_config(), fake_backend)
    assert report.errors == []
    first, second = report.spans
    assert [f.code for f in first.flags] == [FlagCode.FORMATTING_LOST]
    assert second.flags == []
    assert _formatted(_runs(clean_docx(report))) == [("rains", {"italic"})]


async def test_no_marker_reaches_an_export_or_a_stored_text(tmp_path, make_config, fake_backend):
    """Acceptance 4: TMX, HTML, both Word files, and every text the app shows."""
    report = await _translate(_source_docx(tmp_path), make_config(), fake_backend)
    # the prompts did carry them
    assert any("<1>chat</1>" in json.dumps(c.request) for c in report.llm_calls if c.request)
    shown = [s.text for s in report.spans] + [c.text for c in report.candidates]
    assert not any(MARKER.search(t) for t in shown)
    assert not MARKER.search(render_html(report))
    assert not MARKER.search(tmx_bytes(report).decode("utf-8"))
    for data in (clean_docx(report), bilingual_docx(report)):
        assert not any(MARKER.search(text) for text, _ in _runs(data))


def test_an_older_run_exports_one_plain_run_per_passage():
    """Acceptance 5: blocks and passages without formatting, as stored before CGL-108."""
    report = make_report()
    assert all(not b.provenance.marks for b in report.blocks)
    runs = _runs(clean_docx(report))
    assert [text for text, _ in runs] == [
        "Chapter One",
        "The cat sleeps.It rains.",  # one run with a line break
        "* * *",
        "The end <of> & world.",
    ]
    assert all(props == set() for _, props in runs)


def test_formatting_of_an_edited_passage_is_dropped():
    marks = [InlineMark(start=4, end=7, kind=MarkKind.BOLD)]
    kept = make_span("p1", "s1", "s1", "The cat.")
    kept.formatting = SpanFormatting(text="The cat.", marks=marks)
    edited = make_span("p2", "s2", "s3", "A dog sleeps.")
    edited.formatting = SpanFormatting(text="The dog sleeps.", marks=marks)
    report = make_report(spans=[kept, edited, make_span("p3", "s4", "s4", "End.")])
    assert _formatted(_runs(clean_docx(report))) == [("cat", {"bold"})]


def test_links_in_a_header_belong_to_the_header():
    report = make_report()
    header = report.blocks[0].model_copy(
        update={
            "provenance": report.blocks[0].provenance.model_copy(update={"region": Region.HEADER})
        }
    )
    report.blocks[0] = header
    report.spans[0].formatting = SpanFormatting(
        text="Chapter One",
        marks=[InlineMark(start=8, end=11, kind=MarkKind.LINK, href="https://example.org/1")],
    )
    data = clean_docx(report)
    assert _formatted(_runs(data, "header")) == [
        ("One", {"underline", "link:https://example.org/1"})
    ]


def test_a_stored_link_to_a_script_is_written_without_its_target():
    report = make_report()
    report.spans[0].formatting = SpanFormatting(
        text="Chapter One",
        marks=[InlineMark(start=8, end=11, kind=MarkKind.LINK, href="javascript:alert(1)")],
    )
    data = clean_docx(report)
    assert _formatted(_runs(data)) == [("One", {"underline"})]
    assert b"javascript" not in data
