import json
from pathlib import Path

from coglossa_api.openapi import main, openapi_document

COMMITTED = Path(__file__).resolve().parents[2] / "schema" / "openapi.json"


def test_committed_openapi_matches_the_app():
    """Fails when a route or model changes but `make schema` was not run."""
    expected = json.dumps(openapi_document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert COMMITTED.read_text(encoding="utf-8") == expected, (
        "schema/openapi.json is stale — run `make schema` and commit the result"
    )


def test_every_screen_has_its_routes():
    paths = openapi_document()["paths"]
    for path in (
        "/api/health",
        "/api/projects",
        "/api/projects/{project_id}/runs",
        "/api/runs/{run_id}/events",
        "/api/runs/{run_id}/spans",
        "/api/runs/{run_id}/flags/{flag_id}",
        "/api/runs/{run_id}/exports/{kind}",
        "/api/projects/{project_id}/guide",
        "/api/system/doctor",
    ):
        assert path in paths, path


def test_main_writes_the_document(tmp_path):
    out = tmp_path / "openapi.json"
    assert main([str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["info"]["title"] == "Coglossa"
