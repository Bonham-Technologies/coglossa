import pytest
from fastapi.testclient import TestClient

from coglossa_scorer.app import create_app


class FakeQE:
    name = "fake-metricx"

    def __init__(self):
        self.calls = []

    def score(self, items):
        self.calls.append(list(items))
        return [float(len(h) % 25) for _, h, _ in items]


class FakeEmbedder:
    name = "fake-labse"

    def similarity(self, pairs):
        return [1.0 if a == b else 0.5 for a, b in pairs]


@pytest.fixture
def client():
    qe = FakeQE()
    app = create_app(qe, FakeEmbedder(), device="cpu")
    with TestClient(app) as c:
        c.qe = qe
        yield c


def test_health(client):
    assert client.get("/health").json() == {
        "status": "ok",
        "qe_model": "fake-metricx",
        "embed_model": "fake-labse",
        "device": "cpu",
    }


def test_qe_passes_reference_through(client):
    resp = client.post(
        "/v1/qe",
        json={
            "items": [
                {"source": "s", "hypothesis": "abc"},
                {"source": "", "hypothesis": "x", "reference": "r"},
            ]
        },
    )
    assert resp.status_code == 200 and resp.json() == {"scores": [3.0, 1.0]}
    assert client.qe.calls == [[("s", "abc", None), ("", "x", "r")]]


def test_similarity(client):
    resp = client.post(
        "/v1/similarity", json={"pairs": [{"a": "x", "b": "x"}, {"a": "x", "b": "y"}]}
    )
    assert resp.json() == {"similarities": [1.0, 0.5]}


@pytest.mark.parametrize("body", [{"items": []}, {"items": [{"source": "s"}]}, {}])
def test_qe_validation(client, body):
    assert client.post("/v1/qe", json=body).status_code == 422


def test_batch_limit(client):
    items = [{"source": "s", "hypothesis": "h"}] * 257
    assert client.post("/v1/qe", json={"items": items}).status_code == 422


def test_missing_models_are_503():
    with TestClient(create_app(None, None, device="cpu")) as c:
        assert c.get("/health").json()["qe_model"] is None
        r = c.post("/v1/qe", json={"items": [{"source": "s", "hypothesis": "h"}]})
        assert r.status_code == 503 and "not loaded" in r.json()["detail"]
        r = c.post("/v1/similarity", json={"pairs": [{"a": "x", "b": "y"}]})
        assert r.status_code == 503


class NaNModels:
    name = "nan"

    def score(self, items):
        return [float("nan")] * len(items)

    def similarity(self, pairs):
        return [0.5] + [float("inf")] * (len(pairs) - 1)


def test_non_finite_model_output_is_a_500():
    with TestClient(create_app(NaNModels(), NaNModels(), device="cpu")) as c:
        r = c.post("/v1/qe", json={"items": [{"source": "s", "hypothesis": "h"}]})
        assert r.status_code == 500
        assert r.json()["detail"] == "model produced a non-finite score"
        pairs = [{"a": "x", "b": "y"}, {"a": "x", "b": "z"}]
        r = c.post("/v1/similarity", json={"pairs": pairs})
        assert r.status_code == 500
        assert r.json()["detail"] == "model produced a non-finite score"


@pytest.fixture
def secured():
    app = create_app(FakeQE(), FakeEmbedder(), device="cpu", token="tok")
    with TestClient(app) as c:
        yield c


_QE_BODY = {"items": [{"source": "s", "hypothesis": "abc"}]}


def test_token_leaves_health_open(secured):
    assert secured.get("/health").status_code == 200


def test_token_refuses_a_missing_header(secured):
    resp = secured.post("/v1/qe", json=_QE_BODY)
    assert resp.status_code == 401
    assert resp.json() == {"detail": "missing or wrong scorer token"}


def test_token_refuses_a_wrong_token(secured):
    resp = secured.post("/v1/qe", json=_QE_BODY, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_token_accepts_the_right_token(secured):
    resp = secured.post("/v1/qe", json=_QE_BODY, headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200 and resp.json() == {"scores": [3.0]}


def test_token_guards_similarity_too(secured):
    resp = secured.post("/v1/similarity", json={"pairs": [{"a": "x", "b": "x"}]})
    assert resp.status_code == 401


def test_no_token_means_open(client):
    assert client.post("/v1/qe", json=_QE_BODY).status_code == 200


def test_an_empty_token_installs_no_guard():
    app = create_app(FakeQE(), FakeEmbedder(), device="cpu", token="")
    with TestClient(app) as c:
        assert c.post("/v1/qe", json=_QE_BODY).status_code == 200
