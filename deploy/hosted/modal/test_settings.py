from pathlib import Path

import pytest
import yaml

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from settings import HERE, HOSTED_TEMPLATE, check_kick, render_config, repo_root

ENV = {
    "COGLOSSA_SCORER_URL": "https://ws--coglossa-scorer.modal.run",
    "COGLOSSA_S3_ENDPOINT": "https://ref.storage.supabase.co/storage/v1/s3",
    "COGLOSSA_S3_REGION": "ca-central-1",
    "COGLOSSA_KICK_URL": "https://ws--coglossa-kick.modal.run",
}


def test_render_fills_every_placeholder(tmp_path):
    out = render_config(HOSTED_TEMPLATE, tmp_path / "c.yaml", ENV)
    text = out.read_text(encoding="utf-8")
    assert "${" not in text
    data = yaml.safe_load(text)
    assert data["worker"]["kick_url"] == ENV["COGLOSSA_KICK_URL"]
    assert data["roles"]["judge"]["model"] != data["roles"]["reviewer"]["model"]


def test_a_missing_value_names_the_variable(tmp_path):
    with pytest.raises(KeyError, match="COGLOSSA_KICK_URL"):
        render_config(HOSTED_TEMPLATE, tmp_path / "c.yaml", {k: v for k, v in ENV.items() if k != "COGLOSSA_KICK_URL"})


def test_the_rendered_config_is_valid(tmp_path, monkeypatch):
    coglossa_config = pytest.importorskip("coglossa.config")
    for var in [
        "OPENROUTER_API_KEY", "COGLOSSA_SCORER_TOKEN", "COGLOSSA_S3_ACCESS_KEY_ID",
        "COGLOSSA_S3_SECRET_ACCESS_KEY", "COGLOSSA_KICK_TOKEN", "COGLOSSA_PROXY_SECRET",
        "COGLOSSA_MODAL_KEY", "COGLOSSA_MODAL_SECRET",
    ]:
        monkeypatch.setenv(var, "x" * 32)
    path = render_config(HOSTED_TEMPLATE, tmp_path / "c.yaml", ENV)
    cfg, issues = coglossa_config.load_config(path)
    assert cfg.storage.kind == "s3" and not [i for i in issues if i.level == "error"]
    modal_auth = {"Modal-Key": "COGLOSSA_MODAL_KEY", "Modal-Secret": "COGLOSSA_MODAL_SECRET"}
    assert cfg.scorer.extra_headers_env == modal_auth
    assert cfg.worker.extra_headers_env == modal_auth
    # the worker always drains in burst mode; this wait covers the GPU scorer's cold start
    assert cfg.worker.burst_scorer_wait_s == 300


def test_check_kick():
    assert check_kick("Bearer abc", "abc")
    assert not check_kick("Bearer abd", "abc")
    assert not check_kick("abc", "abc")
    assert not check_kick(None, "abc")
    assert not check_kick("", "")  # no empty-token bypass of the "Bearer " prefix


def test_the_rendered_region_comes_from_the_environment(tmp_path):
    out = render_config(HOSTED_TEMPLATE, tmp_path / "c.yaml", ENV)
    assert yaml.safe_load(out.read_text(encoding="utf-8"))["storage"]["region"] == "ca-central-1"


def test_the_kick_endpoint_signature_works_under_fastapi():
    # Same signature style as kick() in app.py: a Header parameter resolvable in module globals.
    web = FastAPI()

    @web.post("/")
    def kick(authorization: str | None = Header(None)):
        if not check_kick(authorization, "tok"):
            return JSONResponse({"detail": "wrong kick token"}, status_code=401)
        return {"spawned": True}

    client = TestClient(web)
    right = client.post("/", headers={"Authorization": "Bearer tok"})
    assert (right.status_code, right.json()) == (200, {"spawned": True})
    assert client.post("/", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/").status_code == 401


def test_repo_root_locally_and_in_a_container():
    assert repo_root(HERE / "app.py", local=True) == HERE.parents[2]
    assert (repo_root(HERE / "app.py", local=True) / "engine" / "pyproject.toml").is_file()
    # Modal mounts app.py at /root/app.py: there is no repository around it
    assert repo_root("/root/app.py", local=False) == Path("/")
