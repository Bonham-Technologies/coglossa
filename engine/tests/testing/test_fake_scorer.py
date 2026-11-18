import httpx
import pytest

from coglossa.schema import QEItem, SimilarityPair
from coglossa.scoring.client import HttpScorer, ScorerUnavailableError
from coglossa.testing.fake_scorer import FakeScorer, FakeScorerServer


async def test_fake_scorer_rules_and_overrides():
    scorer = FakeScorer(qe_overrides={"bad": 20.0})
    items = [
        QEItem(source="a", hypothesis="good"),
        QEItem(source="a", hypothesis="  "),
        QEItem(source="a", hypothesis="bad"),
    ]
    assert await scorer.qe(items) == [2.0, 25.0, 20.0]
    pairs = [SimilarityPair(a="x", b="y"), SimilarityPair(a="", b="y")]
    assert await scorer.similarity(pairs) == [0.9, 0.0]
    assert len(scorer.qe_calls) == 1 and len(scorer.similarity_calls) == 1
    assert (await scorer.health()).qe_model == "fake-metricx"
    await scorer.aclose()
    assert scorer.closed


async def test_fake_scorer_failure_mode():
    scorer = FakeScorer(fail=True)
    for call in (
        scorer.qe([QEItem(source="a", hypothesis="b")]),
        scorer.similarity([SimilarityPair(a="a", b="b")]),
        scorer.health(),
    ):
        with pytest.raises(ScorerUnavailableError):
            await call


async def test_server_round_trip_through_http_scorer():
    with FakeScorerServer() as server:
        scorer = HttpScorer(server.base_url, max_batch=2)
        items = [QEItem(source="a", hypothesis=h) for h in ("x", "", "y")]
        assert await scorer.qe(items) == [2.0, 25.0, 2.0]
        assert await scorer.similarity([SimilarityPair(a="a", b="b")]) == [0.9]
        assert (await scorer.health()).device == "cpu"
        assert [r["path"] for r in server.requests] == [
            "/v1/qe",
            "/v1/qe",
            "/v1/similarity",
            "/health",
        ]
        await scorer.aclose()


async def test_server_validation_404_and_failure_status():
    with FakeScorerServer() as server:
        async with httpx.AsyncClient(base_url=server.base_url) as http:
            assert (await http.post("/v1/qe", json={"items": []})).status_code == 422
            assert (await http.post("/v1/qe", content=b"{bad")).status_code == 422
            assert (await http.get("/nope")).status_code == 404
            assert (await http.post("/nope", json={})).status_code == 404
    with FakeScorerServer(fail_status=503) as server:
        scorer = HttpScorer(server.base_url)
        with pytest.raises(ScorerUnavailableError, match="HTTP 503"):
            await scorer.qe([QEItem(source="a", hypothesis="b")])
        await scorer.aclose()


def test_server_stop_without_start_closes_socket():
    server = FakeScorerServer()
    server.stop()
    server.stop()


async def test_fake_scorer_capabilities_shape_its_health():
    assert (FakeScorer().supports_qe, FakeScorer().supports_similarity) == (True, True)
    health = await FakeScorer(supports_qe=False, supports_similarity=False).health()
    assert (health.qe_model, health.embed_model) == (None, None)


async def test_fake_scorer_similarity_overrides_and_partial_failures():
    scorer = FakeScorer(similarity_overrides={("x", "y"): 0.3})
    pairs = [SimilarityPair(a="x", b="y"), SimilarityPair(a="y", b="x")]
    assert await scorer.similarity(pairs) == [0.3, 0.9]
    only_qe_fails = FakeScorer(fail_qe=True)
    with pytest.raises(ScorerUnavailableError):
        await only_qe_fails.qe([QEItem(source="a", hypothesis="b")])
    assert await only_qe_fails.similarity(pairs[:1]) == [0.9]
    only_sim_fails = FakeScorer(fail_similarity=True)
    with pytest.raises(ScorerUnavailableError):
        await only_sim_fails.similarity(pairs[:1])
    assert await only_sim_fails.qe([QEItem(source="a", hypothesis="b")]) == [2.0]


async def test_server_qe_model_can_be_absent():
    with FakeScorerServer(qe_model=None) as server:
        health = await HttpScorer(server.base_url).health()
    assert health.qe_model is None and health.embed_model == "fake-labse"


def test_the_fake_scorer_server_can_forget_requests():
    server = FakeScorerServer(keep_requests=False).start()
    try:
        with httpx.Client() as client:
            assert client.get(f"{server.base_url}/health").status_code == 200
            assert client.post(f"{server.base_url}/v1/qe", json={"items": []}).status_code == 422
        assert server.requests == []
    finally:
        server.stop()
