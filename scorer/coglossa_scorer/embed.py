"""LaBSE cross-lingual sentence similarity (sentence-transformers/LaBSE, Apache-2.0)."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np


class LabseEmbedder:
    def __init__(self, model: Any, *, batch_size: int = 32, name: str = "labse") -> None:
        self.model = model
        self.batch_size = batch_size
        self.name = name

    @classmethod
    def from_pretrained(
        cls, path: str, *, device: str = "cpu", batch_size: int = 32
    ) -> "LabseEmbedder":
        import sentence_transformers

        model = sentence_transformers.SentenceTransformer(
            path, device=device, local_files_only=True
        )
        return cls(model, batch_size=batch_size, name=Path(path).name)

    def similarity(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        texts = list(dict.fromkeys(t for pair in pairs for t in pair if t.strip()))
        if not texts:
            return [0.0] * len(pairs)
        vectors = self.model.encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True, convert_to_numpy=True
        )
        index = {t: i for i, t in enumerate(texts)}
        sims: list[float] = []
        for a, b in pairs:
            if not a.strip() or not b.strip():
                sims.append(0.0)
                continue
            cos = float(np.dot(vectors[index[a]], vectors[index[b]]))
            sims.append(max(-1.0, min(1.0, cos)))
        return sims
