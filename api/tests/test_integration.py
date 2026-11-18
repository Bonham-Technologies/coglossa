import io

import docx
import yaml
from coglossa.config import load_config
from coglossa.testing.fake_openai import FakeOpenAIServer
from coglossa.testing.fake_scorer import FakeScorerServer
from coglossa.testing.pdf import make_pdf
from coglossa.worker.app import run_worker

from coglossa_api.app import create_app
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
