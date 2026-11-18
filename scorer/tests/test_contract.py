"""The scorer must speak exactly the contract the engine exports to schema/scorer-*.json."""

import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from coglossa_scorer import api
from coglossa_scorer.app import create_app
from tests.test_app import FakeEmbedder, FakeQE

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"


def _schema(name):
    return json.loads((SCHEMA_DIR / f"scorer-{name}.schema.json").read_text(encoding="utf-8"))


@pytest.fixture
def client():
    with TestClient(create_app(FakeQE(), FakeEmbedder(), device="cpu")) as c:
        yield c


def test_responses_match_exported_schemas(client):
    jsonschema.validate(client.get("/health").json(), _schema("health"))
    qe = client.post("/v1/qe", json={"items": [{"source": "s", "hypothesis": "h"}]}).json()
    jsonschema.validate(qe, _schema("qe-response"))
    sim = client.post("/v1/similarity", json={"pairs": [{"a": "x", "b": "y"}]}).json()
    jsonschema.validate(sim, _schema("similarity-response"))


@pytest.mark.parametrize(
    ("model", "name"),
    [
        (api.QERequest, "qe-request"),
        (api.QEResponse, "qe-response"),
        (api.SimilarityRequest, "similarity-request"),
        (api.SimilarityResponse, "similarity-response"),
        (api.ScorerHealth, "health"),
    ],
)
def test_scorer_models_match_engine_contract(model, name):
    ours = json.loads(json.dumps(model.model_json_schema(), sort_keys=True))
    assert ours == _schema(name), f"coglossa_scorer.api.{model.__name__} drifted from {name}"
