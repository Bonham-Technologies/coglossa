"""Engine side of the scorer service (spec §6.1): a small HTTP client behind a protocol.
Third parties can add scorer kinds (e.g. a licensed CometKiwi) via `coglossa.scorers`."""

import asyncio
import os
from collections.abc import Callable, Mapping
from importlib.metadata import entry_points
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from coglossa.config import CoglossaConfig, ConfigError, ScorerConfig, env_headers
from coglossa.schema import (
    QEItem,
    QERequest,
    QEResponse,
    ScorerHealth,
    SimilarityPair,
    SimilarityRequest,
    SimilarityResponse,
)


def _one_line(text: str, limit: int = 300) -> str:
    return " ".join(text.split())[:limit]


class ScorerUnavailableError(Exception):
    """The scorer could not produce scores; the affected signals become null."""


class Scorer(Protocol):
    name: str
    supports_qe: bool  # False: qe() is never called and qe signals stay null (no error)
    supports_similarity: bool

    async def qe(self, items: list[QEItem]) -> list[float]: ...

    async def similarity(self, pairs: list[SimilarityPair]) -> list[float]: ...

    async def health(self) -> ScorerHealth: ...

    async def aclose(self) -> None: ...


class HttpScorer:
    def __init__(
        self,
        url: str,
        *,
        timeout_s: float = 300.0,
        connect_timeout_s: float = 10.0,
        max_batch: int = 64,
        max_concurrency: int = 2,
        api_key: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = "http"
        self.supports_qe = True  # until health() says otherwise
        self.supports_similarity = True
        self.timeout = httpx.Timeout(timeout_s, connect=connect_timeout_s)
        self.max_concurrency = max_concurrency
        self._limit = asyncio.Semaphore(max_concurrency)
        headers = dict(extra_headers or {})
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/") + "/",
            timeout=self.timeout,
            headers=headers,
            transport=transport,
        )
        self._max_batch = max_batch

    async def qe(self, items: list[QEItem]) -> list[float]:
        scores: list[float] = []
        for start in range(0, len(items), self._max_batch):
            batch = items[start : start + self._max_batch]
            data = await self._request("POST", "v1/qe", json=QERequest(items=batch).model_dump())
            got = _parse(QEResponse, data).scores
            if len(got) != len(batch):
                raise ScorerUnavailableError(
                    f"scorer returned {len(got)} scores for {len(batch)} items"
                )
            scores.extend(got)
        return scores

    async def similarity(self, pairs: list[SimilarityPair]) -> list[float]:
        sims: list[float] = []
        for start in range(0, len(pairs), self._max_batch):
            batch = pairs[start : start + self._max_batch]
            body = SimilarityRequest(pairs=batch).model_dump()
            data = await self._request("POST", "v1/similarity", json=body)
            got = _parse(SimilarityResponse, data)
            if len(got.similarities) != len(batch):
                raise ScorerUnavailableError(
                    f"scorer returned {len(got.similarities)} similarities for {len(batch)} pairs"
                )
            sims.extend(got.similarities)
        return sims

    async def health(self) -> ScorerHealth:
        health = _parse(ScorerHealth, await self._request("GET", "health"))
        self.supports_qe = health.qe_model is not None
        self.supports_similarity = health.embed_model is not None
        return health

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with self._limit:
                resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise ScorerUnavailableError(
                _one_line(f"scorer unreachable: {type(e).__name__}: {e}")
            ) from e
        if resp.status_code >= 400:
            raise ScorerUnavailableError(f"scorer HTTP {resp.status_code}: {_one_line(resp.text)}")
        try:
            return resp.json()
        except ValueError as e:
            raise ScorerUnavailableError("scorer returned invalid JSON") from e


def _parse[M: BaseModel](model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as e:
        err = e.errors()[0]
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        raise ScorerUnavailableError(
            f"scorer sent an unexpected response: {loc}: {err['msg']}"
        ) from e


ScorerFactory = Callable[[ScorerConfig], Scorer]


def scorer_location(cfg: CoglossaConfig) -> str:
    sc = cfg.scorer
    return (sc.url or sc.kind) if sc is not None else "unknown"


async def probe_scorer(
    scorer: Scorer, cfg: CoglossaConfig
) -> tuple[ScorerHealth | None, list[str]]:
    """Ask the scorer what it has loaded; warnings are plain-language and unprefixed."""
    try:
        health = await scorer.health()
    except ScorerUnavailableError as e:
        where = scorer_location(cfg)
        return None, [f"scorer unavailable at {where}: {e}; model-based scores will be empty"]
    warnings: list[str] = []
    if health.qe_model is None:
        warnings.append("scorer has no quality-estimation model loaded; qe signals will be empty")
    if health.embed_model is None:
        warnings.append("scorer has no embedding model loaded; xsim signals will be empty")
    return health, warnings


def build_scorer(cfg: CoglossaConfig) -> Scorer | None:
    sc = cfg.scorer
    if sc is None or sc.kind == "none":
        return None
    if sc.kind == "http":
        return HttpScorer(
            sc.url or "",
            timeout_s=sc.timeout_s,
            connect_timeout_s=sc.connect_timeout_s,
            max_batch=sc.max_batch,
            max_concurrency=sc.max_concurrency,
            api_key=os.environ.get(sc.api_key_env) if sc.api_key_env else None,
            extra_headers=env_headers(sc.extra_headers_env),
        )
    plugins = {ep.name: ep for ep in entry_points(group="coglossa.scorers")}
    if sc.kind not in plugins:
        available = ", ".join(["http", "none", *sorted(plugins)])
        raise ConfigError(f"scorer: unknown kind '{sc.kind}' (available: {available})")
    factory: ScorerFactory = plugins[sc.kind].load()
    return factory(sc)
