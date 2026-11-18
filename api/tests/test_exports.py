import io
from pathlib import Path
from urllib.parse import quote

import coglossa
import docx
from coglossa.schema import RunState
from coglossa.testing.seed import seed_run
from lxml import etree

from coglossa_api.routes.exports import content_disposition


def test_content_disposition():
    assert content_disposition("story.en.html") == (
        "attachment; filename=\"story.en.html\"; filename*=UTF-8''story.en.html"
    )
    name = 'رواية "x".en.docx'
    assert content_disposition(name) == (
        f"attachment; filename=\"_____ _x_.en.docx\"; filename*=UTF-8''{quote(name, safe='')}"
    )


async def test_report(api, db):
    seeded = await seed_run(db)
    report = (await api.client.get(f"/api/runs/{seeded.run_id}/report")).json()
    assert report["run_id"] == str(seeded.run_id)
    assert [s["text"] for s in report["spans"]] == [
        "Chapter One",
        "The cat sleeps. It rains.",
        "End.",
    ]
    unfinished = await seed_run(db, project="other", state=RunState.TRANSLATING)
    refused = await api.client.get(f"/api/runs/{unfinished.run_id}/report")
    assert refused.status_code == 409 and "once it is complete" in refused.json()["detail"]


async def test_exports_reflect_edits(api, db):
    seeded = await seed_run(db, filename="رواية.txt")
    edit = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[0]}/edit"
    await api.client.post(edit, json={"text": "Chapter 1"})
    p2 = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[1]}"
    await api.client.post(f"{p2}/use-candidate", json={"translator": "contextual"})
    flag = seeded.flag_ids["p3:terminology_violation"]
    dismissed = await api.client.post(
        f"/api/runs/{seeded.run_id}/flags/{flag}", json={"status": "dismissed", "reason": "fine"}
    )
    assert dismissed.status_code == 200
    base = f"/api/runs/{seeded.run_id}/exports"

    page = await api.client.get(f"{base}/html")
    assert page.headers["content-type"] == "text/html; charset=utf-8"
    assert page.headers["content-disposition"] == content_disposition("رواية.en.html")
    assert "Chapter 1" in page.text and "Chapter One" not in page.text
    assert "The cat is asleep. It rains." in page.text
    assert "terminology_violation" not in page.text

    clean = await api.client.get(f"{base}/docx")
    assert clean.headers["content-disposition"] == content_disposition("رواية.en.docx")
    paragraphs = [p.text for p in docx.Document(io.BytesIO(clean.content)).paragraphs]
    assert paragraphs[0] == "Chapter 1"
    assert "The cat is asleep. It rains." in paragraphs

    bilingual = await api.client.get(f"{base}/bilingual-docx")
    document = docx.Document(io.BytesIO(bilingual.content))
    assert len(list(document.comments)) == 2  # three seeded flags, one dismissed
    assert "The cat is asleep. It rains." in [p.text for p in document.paragraphs] + [
        c.text for t in document.tables for r in t.rows for c in r.cells
    ]
    quiet = await api.client.get(f"{base}/bilingual-docx", params={"comments": "false"})
    assert list(docx.Document(io.BytesIO(quiet.content)).comments) == []

    report = await api.client.get(f"{base}/json")
    assert report.headers["content-type"] == "application/json"
    assert report.headers["content-disposition"] == content_disposition("رواية.en.report.json")
    assert report.json()["spans"][0]["text"] == "Chapter 1"

    assert (await api.client.get(f"{base}/pdf")).status_code == 422


# CGL-89: the TMX 1.4b DTD, vendored so the test needs no network (its source and licence are
# in the file's opening comment).
TMX_DTD = Path(__file__).parent / "fixtures" / "tmx14.dtd"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _valid_tmx(data: bytes) -> etree._Element:
    """Parses the file without fetching anything and validates it against the vendored DTD."""
    parser = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
    root = etree.fromstring(data, parser)
    dtd = etree.DTD(str(TMX_DTD))
    assert dtd.validate(root), dtd.error_log.filter_from_errors()
    return root


def _units(root: etree._Element) -> list[tuple[str, list[tuple[str, str]]]]:
    return [
        (tu.get("tuid"), [(tuv.get(XML_LANG), tuv.findtext("seg")) for tuv in tu.findall("tuv")])
        for tu in root.iter("tu")
    ]


async def test_tmx_export_has_a_unit_per_passage(api, db):
    seeded = await seed_run(db, filename="رواية.txt", target_lang="ar")
    arabic = "الفصل الأول — «القطّ» & <النهاية>"
    edit = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[0]}/edit"
    assert (await api.client.post(edit, json={"text": arabic})).status_code == 200
    base = f"/api/runs/{seeded.run_id}/exports"

    memory = await api.client.get(f"{base}/tmx")
    assert memory.status_code == 200
    assert memory.headers["content-type"] == "application/x-tmx+xml"
    assert memory.headers["content-disposition"] == content_disposition("رواية.ar.tmx")
    root = _valid_tmx(memory.content)
    header = root.find("header")
    assert header.get("srclang") == "fr" and header.get("segtype") == "block"
    assert header.get("creationtool") == header.get("o-tmf") == "Coglossa"
    assert header.get("creationtoolversion") == coglossa.__version__
    assert (header.get("adminlang"), header.get("datatype")) == ("en", "plaintext")
    assert _units(root) == [
        ("p1", [("fr", "Chapitre un"), ("ar", arabic)]),
        ("p2", [("fr", "Le chat dort. Il pleut."), ("ar", "The cat sleeps. It rains.")]),
        ("p3", [("fr", "Fin."), ("ar", "End.")]),
    ]
    assert [tu.get("segtype") for tu in root.iter("tu")] == ["sentence", "block", "sentence"]
    assert "&amp; &lt;النهاية&gt;" in memory.content.decode("utf-8")


async def test_tmx_export_of_checked_passages_only(api, db):
    seeded = await seed_run(db)
    p1, p2, p3 = (f"/api/runs/{seeded.run_id}/spans/{span}" for span in seeded.span_ids)
    await api.client.post(f"{p1}/accept")
    await api.client.post(f"{p2}/edit", json={"text": "The cat is asleep; it is raining."})
    flag = seeded.flag_ids["p3:terminology_violation"]
    await api.client.post(
        f"/api/runs/{seeded.run_id}/flags/{flag}", json={"status": "dismissed", "reason": "fine"}
    )
    base = f"/api/runs/{seeded.run_id}/exports/tmx"

    checked = await api.client.get(base, params={"reviewed_only": "true"})
    assert checked.headers["content-disposition"] == content_disposition("story.en.checked.tmx")
    assert _units(_valid_tmx(checked.content)) == [
        ("p1", [("fr", "Chapitre un"), ("en", "Chapter One")]),
        ("p2", [("fr", "Le chat dort. Il pleut."), ("en", "The cat is asleep; it is raining.")]),
    ]
    everything = await api.client.get(base, params={"reviewed_only": "false"})
    assert [u[0] for u in _units(_valid_tmx(everything.content))] == ["p1", "p2", "p3"]

    unreviewed = await seed_run(db, project="other")
    empty = await api.client.get(
        f"/api/runs/{unreviewed.run_id}/exports/tmx", params={"reviewed_only": "true"}
    )
    assert _units(_valid_tmx(empty.content)) == []


async def test_tmx_export_needs_a_complete_run(api, db):
    unfinished = await seed_run(db, state=RunState.TRANSLATING)
    for params in ({}, {"reviewed_only": "true"}):
        refused = await api.client.get(f"/api/runs/{unfinished.run_id}/exports/tmx", params=params)
        assert refused.status_code == 409 and "once it is complete" in refused.json()["detail"]
