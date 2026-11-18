import json
import re

import yaml

from coglossa.cli import main
from coglossa.testing.fake_openai import FakeOpenAIServer
from coglossa.testing.fake_scorer import FakeScorerServer

TEXT = "\n\n".join(" ".join(["Le chat dort près de la fenêtre."] * 20) for _ in range(6))


def test_queued_run_over_http_respects_backend_concurrency(
    db, tmp_path, monkeypatch, pg_url, capsys
):
    """Spec §7.2: the worker never sends a backend more simultaneous requests than its
    max_concurrency, even with several chunk jobs in flight."""
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    with FakeOpenAIServer(delay_s=0.05) as llm, FakeScorerServer() as scorer:
        cfg = {
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
            "chunking": {"target_words": 100, "max_words": 200},
            "scorer": {"url": scorer.base_url},
            "worker": {"concurrency": 4},
            "storage": {"path": str(tmp_path / "files")},
        }
        config = tmp_path / "coglossa.yaml"
        config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        doc = tmp_path / "book.txt"
        doc.write_text(TEXT, encoding="utf-8")
        args = ["--target-lang", "en", "--source-lang", "fr", "--config", str(config)]
        assert main(["submit", str(doc), *args]) == 0
        run_id = re.search(r"run (\S+) queued", capsys.readouterr().out).group(1)
        assert main(["worker", "--burst", "--config", str(config)]) == 0
        capsys.readouterr()
        out = tmp_path / "out"
        assert main(["report", run_id, "--out", str(out), "--config", str(config)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    assert len(report["chunks"]) >= 4
    assert all(s["score"]["meaning"] is not None for s in report["spans"])
    assert report["config"]["scorer"] is not None
    assert 1 < llm.max_in_flight <= 2
    assert any(r["path"] == "/v1/qe" for r in scorer.requests)  # scored over HTTP
