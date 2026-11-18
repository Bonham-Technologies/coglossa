"""Scorer HTTP contract. Mirrors the engine contract (engine/coglossa/schema/scorer_api.py);
tests/test_contract.py enforces that the two stay in sync."""

from typing import Literal

from pydantic import BaseModel, Field

MAX_BATCH = 256


class QEItem(BaseModel):
    source: str
    hypothesis: str
    reference: str | None = None  # None = reference-free (QE) mode


class QERequest(BaseModel):
    items: list[QEItem] = Field(min_length=1, max_length=MAX_BATCH)


class QEResponse(BaseModel):
    scores: list[float] = Field(
        description="MetricX error scores, 0 (best) to 25 (worst), one per item, in order"
    )


class SimilarityPair(BaseModel):
    a: str
    b: str


class SimilarityRequest(BaseModel):
    pairs: list[SimilarityPair] = Field(min_length=1, max_length=MAX_BATCH)


class SimilarityResponse(BaseModel):
    similarities: list[float] = Field(
        description="Cosine similarity in [-1, 1], one per pair, in order"
    )


class ScorerHealth(BaseModel):
    status: Literal["ok"] = "ok"
    qe_model: str | None = None
    embed_model: str | None = None
    device: str
