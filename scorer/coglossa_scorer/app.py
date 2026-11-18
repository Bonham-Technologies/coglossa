"""HTTP API of the scorer service. Contract: schema/scorer-*.schema.json (engine-exported)."""

import hmac
import math
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from coglossa_scorer.api import (
    QERequest,
    QEResponse,
    ScorerHealth,
    SimilarityRequest,
    SimilarityResponse,
)


def _finite(values: list[float]) -> list[float]:
    if not all(math.isfinite(v) for v in values):  # never let NaN/inf reach clients as null
        raise HTTPException(status_code=500, detail="model produced a non-finite score")
    return values


def create_app(
    qe: Any | None, embedder: Any | None, *, device: str, token: str | None = None
) -> FastAPI:
    app = FastAPI(title="coglossa-scorer")
    if token:  # None or empty: no guard (an empty token would accept "Bearer ")
        expected = f"Bearer {token}".encode()

        @app.middleware("http")
        async def require_token(request: Request, call_next: Any) -> Any:
            # /health stays open: liveness checks and the engine's probe carry no token
            given = request.headers.get("authorization", "").encode()
            if request.url.path != "/health" and not hmac.compare_digest(given, expected):
                return JSONResponse({"detail": "missing or wrong scorer token"}, status_code=401)
            return await call_next(request)

    lock = threading.Lock()  # one inference at a time; endpoints run in FastAPI's threadpool

    @app.get("/health", response_model=ScorerHealth)
    def health() -> ScorerHealth:
        return ScorerHealth(
            qe_model=qe.name if qe is not None else None,
            embed_model=embedder.name if embedder is not None else None,
            device=device,
        )

    @app.post("/v1/qe", response_model=QEResponse)
    def score_qe(request: QERequest) -> QEResponse:
        if qe is None:
            raise HTTPException(status_code=503, detail="quality-estimation model not loaded")
        with lock:
            scores = qe.score([(i.source, i.hypothesis, i.reference) for i in request.items])
        return QEResponse(scores=_finite(scores))

    @app.post("/v1/similarity", response_model=SimilarityResponse)
    def similarity(request: SimilarityRequest) -> SimilarityResponse:
        if embedder is None:
            raise HTTPException(status_code=503, detail="embedding model not loaded")
        with lock:
            sims = embedder.similarity([(p.a, p.b) for p in request.pairs])
        return SimilarityResponse(similarities=_finite(sims))

    return app
