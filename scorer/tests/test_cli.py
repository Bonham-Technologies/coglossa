import os

import pytest
from fastapi.testclient import TestClient

from coglossa_scorer import cli


@pytest.fixture
def loaders(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli.MetricXQE,
        "from_pretrained",
        classmethod(lambda c, m, t, **kw: seen.setdefault("qe", (m, t, kw))),
    )
    monkeypatch.setattr(
        cli.LabseEmbedder,
        "from_pretrained",
        classmethod(lambda c, p, **kw: seen.setdefault("embed", (p, kw))),
    )
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda app, host, port: seen.setdefault("run", (app, host, port))
    )
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    return seen


def test_loads_models_offline_and_serves(loaders):
    code = cli.main(
        [
            "--qe-model",
            "/m/metricx",
            "--embed-model",
            "/m/labse",
            "--device",
            "cuda",
            "--batch-size",
            "4",
            "--port",
            "9000",
        ]
    )
    assert code == 0
    assert loaders["qe"] == ("/m/metricx", "/m/metricx", {"device": "cuda", "batch_size": 4})
    assert loaders["embed"] == ("/m/labse", {"device": "cuda"})
    _, host, port = loaders["run"]
    assert (host, port) == ("127.0.0.1", 9000)
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_separate_tokenizer_and_embed_only(loaders):
    assert cli.main(["--qe-model", "/m/x", "--qe-tokenizer", "/m/tok"]) == 0
    assert loaders["qe"][:2] == ("/m/x", "/m/tok") and "embed" not in loaders


def test_requires_a_model(loaders):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_load_failure_exits_1(monkeypatch, capsys):
    def boom(cls, path, **kw):
        raise OSError("no such directory")

    monkeypatch.setattr(cli.LabseEmbedder, "from_pretrained", classmethod(boom))
    assert cli.main(["--embed-model", "/nope"]) == 1
    assert "error: cannot load model: no such directory" in capsys.readouterr().err


def test_missing_tokenizer_dependency_exits_1(monkeypatch, capsys):
    def boom(cls, model, tokenizer, **kw):
        raise ImportError("requires the protobuf library")

    monkeypatch.setattr(cli.MetricXQE, "from_pretrained", classmethod(boom))
    assert cli.main(["--qe-model", "/m/metricx"]) == 1
    assert "error: cannot load model: requires the protobuf library" in capsys.readouterr().err


class _Embedder:
    name = "e"

    def similarity(self, pairs):
        return [1.0 for _ in pairs]


@pytest.fixture
def embedder(monkeypatch):
    monkeypatch.setattr(
        cli.LabseEmbedder, "from_pretrained", classmethod(lambda c, p, **kw: _Embedder())
    )


_PAIRS = {"pairs": [{"a": "x", "b": "y"}]}


def test_build_app_reads_the_token(embedder, monkeypatch):
    monkeypatch.setenv("COGLOSSA_SCORER_TOKEN", "tok")
    c = TestClient(cli.build_app(["--embed-model", "/m/labse"]))
    assert c.get("/health").status_code == 200
    assert c.post("/v1/similarity", json=_PAIRS).status_code == 401
    auth = {"Authorization": "Bearer tok"}
    assert c.post("/v1/similarity", json=_PAIRS, headers=auth).status_code == 200


def test_empty_token_means_no_token(embedder, monkeypatch):
    monkeypatch.setenv("COGLOSSA_SCORER_TOKEN", "")
    c = TestClient(cli.build_app(["--embed-model", "/m/labse"]))
    assert c.post("/v1/similarity", json=_PAIRS).status_code == 200


def test_build_app_load_failure_exits_1(monkeypatch, capsys):
    def boom(cls, path, **kw):
        raise OSError("gone")

    monkeypatch.setattr(cli.LabseEmbedder, "from_pretrained", classmethod(boom))
    with pytest.raises(SystemExit) as exc:
        cli.build_app(["--embed-model", "/nope"])
    assert exc.value.code == 1
    assert "error: cannot load model: gone" in capsys.readouterr().err


def test_main_runs_the_built_app(loaders):
    assert cli.main(["--embed-model", "/m/labse", "--host", "0.0.0.0"]) == 0
    app, host, _ = loaders["run"]
    assert host == "0.0.0.0" and app.title == "coglossa-scorer"
