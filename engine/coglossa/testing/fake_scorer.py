"""Deterministic stand-ins for the scorer service: in-process (FakeScorer) and over HTTP
(FakeScorerServer, stdlib only). QE 2.0 for a non-blank hypothesis, 25.0 for a blank one;
similarity 0.9, or 0.0 when either side is blank."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pydantic import ValidationError

from coglossa.schema import QEItem, QERequest, ScorerHealth, SimilarityPair, SimilarityRequest
from coglossa.scoring.client import ScorerUnavailableError

GOOD_QE, BLANK_QE, GOOD_SIM = 2.0, 25.0, 0.9


def fake_qe(item: QEItem, overrides: dict[str, float], base: float = GOOD_QE) -> float:
    if item.hypothesis in overrides:
        return overrides[item.hypothesis]
    return base if item.hypothesis.strip() else BLANK_QE


def fake_similarity(
    pair: SimilarityPair,
    base: float = GOOD_SIM,
    overrides: dict[tuple[str, str], float] | None = None,
) -> float:
    if overrides and (pair.a, pair.b) in overrides:
        return overrides[(pair.a, pair.b)]
    return base if pair.a.strip() and pair.b.strip() else 0.0


FAKE_HEALTH = ScorerHealth(qe_model="fake-metricx", embed_model="fake-labse", device="cpu")


class FakeScorer:
    name = "fake"

    def __init__(
        self,
        *,
        qe_score: float = GOOD_QE,
        similarity: float = GOOD_SIM,
        qe_overrides: dict[str, float] | None = None,
        similarity_overrides: dict[tuple[str, str], float] | None = None,
        fail: bool = False,
        fail_qe: bool = False,
        fail_similarity: bool = False,
        supports_qe: bool = True,
        supports_similarity: bool = True,
    ) -> None:
        self.supports_qe = supports_qe
        self.supports_similarity = supports_similarity
        self._qe = qe_score
        self._sim = similarity
        self._overrides = qe_overrides or {}
        self._sim_overrides = similarity_overrides or {}
        self._fail = fail
        self._fail_qe = fail or fail_qe
        self._fail_similarity = fail or fail_similarity
        self.qe_calls: list[list[QEItem]] = []
        self.similarity_calls: list[list[SimilarityPair]] = []
        self.closed = False

    @staticmethod
    def _check(fail: bool) -> None:
        if fail:
            raise ScorerUnavailableError("fake scorer configured to fail")

    async def qe(self, items: list[QEItem]) -> list[float]:
        self.qe_calls.append(list(items))
        self._check(self._fail_qe)
        return [fake_qe(i, self._overrides, self._qe) for i in items]

    async def similarity(self, pairs: list[SimilarityPair]) -> list[float]:
        self.similarity_calls.append(list(pairs))
        self._check(self._fail_similarity)
        return [fake_similarity(p, self._sim, self._sim_overrides) for p in pairs]

    async def health(self) -> ScorerHealth:
        self._check(self._fail)
        return FAKE_HEALTH.model_copy(
            update={
                "qe_model": FAKE_HEALTH.qe_model if self.supports_qe else None,
                "embed_model": FAKE_HEALTH.embed_model if self.supports_similarity else None,
            }
        )

    async def aclose(self) -> None:
        self.closed = True


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    fail_status: int | None
    health: ScorerHealth
    requests: list[dict[str, Any]]
    keep_requests: bool


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.server.keep_requests:
            self.server.requests.append({"path": self.path})
        if self.path.rstrip("/") == "/health":
            self._send(200, self.server.health.model_dump())
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:
        if self.server.keep_requests:
            self.server.requests.append({"path": self.path})
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path not in ("/v1/qe", "/v1/similarity"):
            self._send(404, {"detail": "not found"})
            return
        if self.server.fail_status is not None:
            self._send(self.server.fail_status, {"detail": "configured failure"})
            return
        try:
            body = json.loads(raw)
            if self.path == "/v1/qe":
                req = QERequest.model_validate(body)
                self._send(200, {"scores": [fake_qe(i, {}) for i in req.items]})
            else:
                sreq = SimilarityRequest.model_validate(body)
                self._send(200, {"similarities": [fake_similarity(p) for p in sreq.pairs]})
        except (ValueError, ValidationError) as e:
            self._send(422, {"detail": str(e)})

    def _send(self, status: int, obj: dict[str, Any]) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeScorerServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        fail_status: int | None = None,
        qe_model: str | None = FAKE_HEALTH.qe_model,
        keep_requests: bool = True,
    ) -> None:
        self._server = _Server((host, port), _Handler)
        self._server.fail_status = fail_status
        self._server.health = FAKE_HEALTH.model_copy(update={"qe_model": qe_model})
        self._server.requests = []
        self._server.keep_requests = keep_requests
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self._server.requests

    def start(self) -> "FakeScorerServer":
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def stop(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join()
            self._thread = None
        self._server.server_close()

    def __enter__(self) -> "FakeScorerServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
