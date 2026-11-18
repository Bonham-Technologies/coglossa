import asyncio
import json

import httpx
import pytest

from coglossa.config import ConfigError
from coglossa.schema import QEItem, SimilarityPair
from coglossa.scoring import client as client_mod
from coglossa.scoring.client import (
    HttpScorer,
    ScorerUnavailableError,
    build_scorer,
    probe_scorer,
)
from coglossa.testing.fake_scorer import FakeScorer


def _scorer(handler, **kw) -> HttpScorer:
    return HttpScorer("http://scorer:8080", transport=httpx.MockTransport(handler), **kw)


def _items(n):
    return [QEItem(source=f"s{i}", hypothesis=f"h{i}") for i in range(n)]


async def test_qe_batches_preserve_order():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.url.path, len(body["items"])))
        return httpx.Response(
            200, json={"scores": [float(i["hypothesis"][1:]) for i in body["items"]]}
        )

    scorer = _scorer(handler, max_batch=2)
    assert await scorer.qe(_items(5)) == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert seen == [("/v1/qe", 2), ("/v1/qe", 2), ("/v1/qe", 1)]
    await scorer.aclose()


async def test_reference_mode_is_sent():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"scores": [1.0]})

    await _scorer(handler).qe([QEItem(source="", hypothesis="b", reference="r")])
    assert seen["body"]["items"][0] == {"source": "", "hypothesis": "b", "reference": "r"}


async def test_similarity_batches():
    def handler(request):
        pairs = json.loads(request.content)["pairs"]
        assert request.url.path == "/v1/similarity"
        return httpx.Response(200, json={"similarities": [0.5] * len(pairs)})

    pairs = [SimilarityPair(a="x", b="y")] * 3
    assert await _scorer(handler, max_batch=2).similarity(pairs) == [0.5, 0.5, 0.5]


async def test_empty_inputs_make_no_request():
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("no request expected")

    scorer = _scorer(handler)
    assert await scorer.qe([]) == [] and await scorer.similarity([]) == []


async def test_health():
    def handler(request):
        assert request.url.path == "/health"
        return httpx.Response(
            200, json={"status": "ok", "qe_model": "m", "embed_model": "e", "device": "cpu"}
        )

    health = await _scorer(handler).health()
    assert (health.qe_model, health.device) == ("m", "cpu")


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (lambda r: httpx.Response(503, text="loading"), "HTTP 503"),
        (lambda r: httpx.Response(200, text="not json"), "invalid JSON"),
        (lambda r: httpx.Response(200, json={"scores": "nope"}), "unexpected response"),
        (lambda r: httpx.Response(200, json={"scores": [1.0, 2.0]}), "2 scores for 1"),
    ],
)
async def test_qe_failures_raise_unavailable(handler, message):
    with pytest.raises(ScorerUnavailableError, match=message):
        await _scorer(handler).qe(_items(1))


async def test_transport_errors_raise_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ScorerUnavailableError, match="unreachable"):
        await _scorer(handler).similarity([SimilarityPair(a="a", b="b")])
    with pytest.raises(ScorerUnavailableError, match="unreachable"):
        await _scorer(handler).health()


async def test_similarity_length_mismatch():
    handler = lambda r: httpx.Response(200, json={"similarities": []})  # noqa: E731
    with pytest.raises(ScorerUnavailableError, match="0 similarities for 1"):
        await _scorer(handler).similarity([SimilarityPair(a="a", b="b")])


async def test_build_scorer(make_config, monkeypatch):
    assert build_scorer(make_config()) is None
    assert build_scorer(make_config(scorer={"kind": "none"})) is None
    http = build_scorer(make_config(scorer={"url": "http://127.0.0.1:1"}))
    assert isinstance(http, HttpScorer)
    await http.aclose()

    made = {}

    class EP:
        name = "kiwi"

        def load(self):
            return lambda sc: made.setdefault("scorer", ("kiwi", sc.url))

    monkeypatch.setattr(client_mod, "entry_points", lambda group: [EP()])
    assert build_scorer(make_config(scorer={"kind": "kiwi", "url": "u"})) == ("kiwi", "u")
    with pytest.raises(ConfigError, match="unknown kind 'nope'"):
        build_scorer(make_config(scorer={"kind": "nope", "url": "u"}))


async def test_http_error_message_is_one_line():
    scorer = _scorer(lambda r: httpx.Response(500, text="line1\nline2"))
    with pytest.raises(ScorerUnavailableError) as exc:
        await scorer.qe(_items(1))
    assert "\n" not in str(exc.value)
    assert "line1 line2" in str(exc.value)


async def test_validation_error_message_is_one_line():
    scorer = _scorer(lambda r: httpx.Response(200, json={"scores": "nope"}))
    with pytest.raises(ScorerUnavailableError) as exc:
        await scorer.qe(_items(1))
    assert "\n" not in str(exc.value)
    assert "unexpected response" in str(exc.value)


async def test_requests_share_a_concurrency_limit():
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"scores": [1.0]})

    scorer = _scorer(handler, max_concurrency=2)
    results = await asyncio.gather(*(scorer.qe(_items(1)) for _ in range(6)))
    assert results == [[1.0]] * 6 and peak == 2


async def test_connect_timeout_is_separate():
    scorer = _scorer(lambda r: httpx.Response(200), timeout_s=300, connect_timeout_s=5)
    assert scorer.timeout == httpx.Timeout(300, connect=5)
    await scorer.aclose()


async def test_build_scorer_passes_limits(make_config):
    cfg = make_config(
        scorer={"url": "http://127.0.0.1:1", "max_concurrency": 3, "connect_timeout_s": 2}
    )
    http = build_scorer(cfg)
    assert http.max_concurrency == 3 and http.timeout == httpx.Timeout(300, connect=2)
    await http.aclose()


@pytest.mark.parametrize(
    ("qe_model", "embed_model", "expected"),
    [("m", "e", (True, True)), (None, "e", (False, True)), ("m", None, (True, False))],
)
async def test_health_updates_capabilities(qe_model, embed_model, expected):
    body = {"status": "ok", "qe_model": qe_model, "embed_model": embed_model, "device": "cpu"}
    scorer = _scorer(lambda r: httpx.Response(200, json=body))
    assert (scorer.supports_qe, scorer.supports_similarity) == (True, True)
    await scorer.health()
    assert (scorer.supports_qe, scorer.supports_similarity) == expected


@pytest.mark.parametrize(
    ("supports_qe", "supports_similarity", "expected"),
    [
        (True, True, []),
        (
            False,
            True,
            ["scorer has no quality-estimation model loaded; qe signals will be empty"],
        ),
        (
            True,
            False,
            ["scorer has no embedding model loaded; xsim signals will be empty"],
        ),
    ],
)
async def test_probe_scorer_warns_about_missing_models(
    make_config, supports_qe, supports_similarity, expected
):
    cfg = make_config(scorer={"url": "http://127.0.0.1:1"})
    scorer = FakeScorer(supports_qe=supports_qe, supports_similarity=supports_similarity)
    health, warnings = await probe_scorer(scorer, cfg)
    assert warnings == expected
    assert health == await scorer.health()


@pytest.mark.parametrize(
    ("scorer_cfg", "where"),
    [({"url": "http://127.0.0.1:1"}, "http://127.0.0.1:1"), (None, "unknown")],
)
async def test_probe_scorer_reports_an_unavailable_scorer(make_config, scorer_cfg, where):
    cfg = make_config(scorer=scorer_cfg) if scorer_cfg else make_config()
    health, warnings = await probe_scorer(FakeScorer(fail=True), cfg)
    assert health is None
    assert warnings == [
        f"scorer unavailable at {where}: fake scorer configured to fail; "
        "model-based scores will be empty"
    ]


async def test_http_scorer_sends_the_bearer_token():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "ok", "device": "cpu"})

    scorer = HttpScorer("http://s/", api_key="tok", transport=httpx.MockTransport(handler))
    await scorer.health()
    assert seen[0].headers["authorization"] == "Bearer tok"
    await scorer.aclose()


async def test_http_scorer_sends_no_token_by_default():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "ok", "device": "cpu"})

    scorer = HttpScorer("http://s/", transport=httpx.MockTransport(handler))
    await scorer.health()
    assert "authorization" not in seen[0].headers
    await scorer.aclose()


async def test_build_scorer_reads_the_key(monkeypatch, make_config):
    monkeypatch.setenv("SCORER_TOKEN", "tok")
    cfg = make_config(scorer={"url": "http://s/", "api_key_env": "SCORER_TOKEN"})
    scorer = build_scorer(cfg)
    assert scorer is not None and scorer._client.headers["authorization"] == "Bearer tok"
    await scorer.aclose()


async def test_build_scorer_without_a_key_sends_none(make_config):
    scorer = build_scorer(make_config(scorer={"url": "http://s/"}))
    assert scorer is not None and "authorization" not in scorer._client.headers
    await scorer.aclose()


async def test_build_scorer_sends_the_extra_headers(monkeypatch, make_config):
    monkeypatch.setenv("PLATFORM_KEY", "wk-1")
    monkeypatch.setenv("PLATFORM_SECRET", "ws-1")
    monkeypatch.setenv("SCORER_TOKEN", "tok")
    cfg = make_config(
        scorer={
            "url": "http://s/",
            "api_key_env": "SCORER_TOKEN",
            "extra_headers_env": {"Modal-Key": "PLATFORM_KEY", "Modal-Secret": "PLATFORM_SECRET"},
        }
    )
    scorer = build_scorer(cfg)
    assert scorer is not None
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "ok", "device": "cpu"})

    # the same headers on a client with a recording transport
    recorded = HttpScorer(
        "http://s/",
        api_key="tok",
        extra_headers={"Modal-Key": "wk-1", "Modal-Secret": "ws-1"},
        transport=httpx.MockTransport(handler),
    )
    await recorded.health()
    for client_headers in (scorer._client.headers, seen[0].headers):
        assert client_headers["modal-key"] == "wk-1"
        assert client_headers["modal-secret"] == "ws-1"
        assert client_headers["authorization"] == "Bearer tok"
    await scorer.aclose()
    await recorded.aclose()
