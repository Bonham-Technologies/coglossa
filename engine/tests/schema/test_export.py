import json
from pathlib import Path

from coglossa.schema import Report
from coglossa.schema.export import EXPORTED, export, main

REPO_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schema"


def test_export_writes_sorted_json_schema(tmp_path):
    paths = export(tmp_path)
    assert sorted(p.name for p in paths) == sorted(f"{n}.schema.json" for n in EXPORTED)
    assert len(paths) == 9
    data = json.loads((tmp_path / "report.schema.json").read_text())
    assert data == json.loads(json.dumps(Report.model_json_schema(), sort_keys=True))


def test_main_exports_to_given_dir(tmp_path):
    assert main([str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "guide.schema.json").exists()


def test_committed_schema_matches_models(tmp_path):
    """Fails when a model changes but `make schema` was not run (schema drift)."""
    export(tmp_path)
    for name in (f"{n}.schema.json" for n in EXPORTED):
        committed = (REPO_SCHEMA_DIR / name).read_text(encoding="utf-8")
        assert committed == (tmp_path / name).read_text(encoding="utf-8"), (
            f"schema/{name} is stale — run `make schema` and commit the result"
        )
