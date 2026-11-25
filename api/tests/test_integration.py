import io

import docx
import yaml
from coglossa.config import load_config
from coglossa.testing.fake_openai import FakeOpenAIServer
from coglossa.testing.fake_scorer import FakeScorerServer
from coglossa.testing.pdf import make_pdf
from coglossa.worker.app import run_worker
from openpyxl import Workbook, load_workbook
from pptx import Presentation
from pptx.util import Inches

from coglossa_api.app import create_app
from coglossa_api.routes.exports import PPTX, XLSX, content_disposition
from tests.conftest import serve

STORY = "\n\n".join(
    [
        "Le chat dort près de la fenêtre. Il pleut sur la ville.",
        "Marie ouvre la porte. Le chat se réveille et la regarde.",
    ]
)


def _config(tmp_path, llm, scorer):
    return {
        "backends": {"local": {"base_url": llm.base_url, "max_concurrency": 2}},
        "models": {
            "m1": {"backend": "local", "name": "fake-model", "context_window": 32768},
            "m2": {"backend": "local", "name": "fake-model", "context_window": 32768},
        },
        "roles": {
            "literal_translator": {"model": "m1"},
            "contextual_translator": {"model": "m1"},
            "reviewer": {"model": "m1"},
            "profiler_guide": {"model": "m1"},
            "judge": {"model": "m2"},
            "roundtrip": {"model": "m2"},
        },
        "scorer": {"url": scorer.base_url},
        "storage": {"path": str(tmp_path / "files")},
        "api": {"progress_poll_s": 0.02},
        "profiles": {"sweep": {"short": True}},  # the story is short; re-running needs the sweep
    }


async def test_upload_review_and_export_over_http(db, tmp_path, monkeypatch, pg_url):
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    with FakeOpenAIServer() as llm, FakeScorerServer() as scorer:
        path = tmp_path / "coglossa.yaml"
        path.write_text(yaml.safe_dump(_config(tmp_path, llm, scorer)), encoding="utf-8")
        cfg, _ = load_config(path)
        async with serve(create_app(cfg, path)) as client:
            project = (
                await client.post(
                    "/api/projects",
                    json={"name": "novel", "target_lang": "en", "source_lang": "fr"},
                )
            ).json()
            uploaded = await client.post(
                f"/api/projects/{project['id']}/runs",
                files={"file": ("histoire.txt", STORY.encode(), "text/plain")},
            )
            assert uploaded.status_code == 202, uploaded.text
            run_id = uploaded.json()["id"]
            scanned = await client.post(
                f"/api/projects/{project['id']}/runs",
                files={"file": ("scan.pdf", make_pdf([["Du texte."], "image"]), "application/pdf")},
            )
            assert scanned.status_code == 202

            await run_worker(cfg, burst=True, install_signal_handlers=False)

            detail = (await client.get(f"/api/runs/{run_id}")).json()
            assert detail["run"]["state"] == "complete", detail
            assert detail["report_available"] and detail["stats"]["spans"] >= 2
            events = await client.get(f"/api/runs/{run_id}/events")
            assert '"state":"complete"' in events.text

            failed = (await client.get(f"/api/runs/{scanned.json()['id']}")).json()
            assert failed["run"]["state"] == "failed"
            assert "needs OCR (needs_ocr): page(s) 2" in failed["run"]["error"]

            spans = (await client.get(f"/api/runs/{run_id}/spans")).json()["spans"]
            first = spans[0]
            span_url = f"/api/runs/{run_id}/spans/{first['id']}"
            edited = await client.post(f"{span_url}/edit", json={"text": "Edited by a reviewer."})
            assert edited.status_code == 200
            assert edited.json()["revisions"][0]["after"] == first["text"]  # history kept
            accepted = await client.post(f"/api/runs/{run_id}/spans/{spans[-1]['id']}/accept")
            assert accepted.status_code == 200

            page = await client.get(f"/api/runs/{run_id}/exports/html")
            assert "Edited by a reviewer." in page.text
            clean = await client.get(f"/api/runs/{run_id}/exports/docx")
            text = "\n".join(p.text for p in docx.Document(io.BytesIO(clean.content)).paragraphs)
            assert "Edited by a reviewer." in text
            report = (await client.get(f"/api/runs/{run_id}/report")).json()
            assert report["spans"][0]["text"] == "Edited by a reviewer."
            assert any(r["source"] == "user" for r in report["revisions"])

            guide = (await client.get(f"/api/projects/{project['id']}/guide")).json()
            locked = {
                **guide["guide"],
                "glossary": [
                    *guide["guide"]["glossary"],
                    {"term": "chat", "rendering": "cat", "locked": True},
                ],
            }
            saved = await client.put(
                f"/api/projects/{project['id']}/guide",
                json={"guide": locked, "base_version": guide["info"]["version"]},
            )
            assert saved.status_code == 200
            swept = await client.post(f"/api/runs/{run_id}/sweep")
            assert swept.status_code == 202, swept.text
            await run_worker(cfg, burst=True, install_signal_handlers=False)
            after = (await client.get(f"/api/runs/{run_id}")).json()
            assert after["run"]["state"] in ("complete", "complete_with_errors")
            assert after["guide_version"] == saved.json()["info"]["version"]
    assert llm.requests and all(r["model"] == "fake-model" for r in llm.requests)


def _deck() -> bytes:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Le marché du livre"
    slide.placeholders[1].text = "Les ventes augmentent chaque année."
    table = slide.shapes.add_table(1, 2, Inches(1), Inches(5), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "Le prix baisse."
    slide.notes_slide.notes_text_frame.text = "Le chat dort sur la table."
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def _workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Ventes"
    ws["A1"] = "Le chat dort près de la fenêtre."
    ws["B1"] = 42
    ws["C1"] = "=B1*2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


async def test_powerpoint_and_excel_files_come_back_translated(db, tmp_path, monkeypatch, pg_url):
    """CGL-110: a deck and a workbook are uploaded, translated and downloaded in their own
    format; a run of another format cannot be downloaded as one."""
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    with FakeOpenAIServer() as llm, FakeScorerServer() as scorer:
        path = tmp_path / "coglossa.yaml"
        path.write_text(yaml.safe_dump(_config(tmp_path, llm, scorer)), encoding="utf-8")
        cfg, _ = load_config(path)
        async with serve(create_app(cfg, path)) as client:
            project = (
                await client.post(
                    "/api/projects",
                    json={"name": "office", "target_lang": "en", "source_lang": "fr"},
                )
            ).json()
            runs = {}
            for name, data in (
                ("Rapport.pptx", _deck()),
                ("Ventes.xlsx", _workbook()),
                ("histoire.txt", STORY.encode()),
            ):
                uploaded = await client.post(
                    f"/api/projects/{project['id']}/runs", files={"file": (name, data)}
                )
                assert uploaded.status_code == 202, uploaded.text
                runs[name] = uploaded.json()["id"]
            await run_worker(cfg, burst=True, install_signal_handlers=False)

            deck_run = runs["Rapport.pptx"]
            spans = (await client.get(f"/api/runs/{deck_run}/spans")).json()["spans"]
            assert len(spans) == 4
            got = await client.get(f"/api/runs/{deck_run}/exports/pptx")
            assert got.status_code == 200, got.text
            assert got.headers["content-type"] == PPTX
            assert got.headers["content-disposition"] == content_disposition("Rapport.en.pptx")
            slide = Presentation(io.BytesIO(got.content)).slides[0]
            texts = [slide.shapes.title.text, slide.placeholders[1].text]
            table = next(s for s in slide.shapes if s.has_table).table
            texts += [table.cell(0, 0).text, slide.notes_slide.notes_text_frame.text]
            assert texts == [s["text"] for s in spans]
            assert table.cell(0, 1).text == ""

            book_run = runs["Ventes.xlsx"]
            spans = (await client.get(f"/api/runs/{book_run}/spans")).json()["spans"]
            got = await client.get(f"/api/runs/{book_run}/exports/xlsx")
            assert got.status_code == 200, got.text
            assert got.headers["content-type"] == XLSX
            assert got.headers["content-disposition"] == content_disposition("Ventes.en.xlsx")
            ws = load_workbook(io.BytesIO(got.content))["Ventes"]
            assert [ws["A1"].value, ws["B1"].value, ws["C1"].value] == [
                spans[0]["text"],
                42,
                "=B1*2",
            ]

            refused = await client.get(f"/api/runs/{runs['histoire.txt']}/exports/xlsx")
            assert refused.status_code == 422
            assert refused.json()["detail"] == (
                "only a translation of an Excel file can be downloaded as one"
            )
            refused = await client.get(f"/api/runs/{deck_run}/exports/xlsx")
            assert refused.status_code == 422
