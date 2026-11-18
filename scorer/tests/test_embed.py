import numpy as np
import pytest

from coglossa_scorer.embed import LabseEmbedder


class FakeEncoder:
    def __init__(self):
        self.calls = []

    def encode(self, texts, batch_size, normalize_embeddings, convert_to_numpy):
        self.calls.append((list(texts), batch_size, normalize_embeddings, convert_to_numpy))
        vectors = np.array([[len(t), t.count("a") + 1.0, 1.0] for t in texts], dtype=float)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def test_similarity_dedupes_and_handles_blanks():
    encoder = FakeEncoder()
    emb = LabseEmbedder(encoder, batch_size=16)
    sims = emb.similarity([("abc", "abc"), ("abc", "xyz"), ("", "abc"), ("abc", "  ")])
    assert sims[0] == pytest.approx(1.0)
    assert -1.0 <= sims[1] <= 1.0 and sims[1] < 1.0
    assert sims[2:] == [0.0, 0.0]
    [(texts, batch, normalize, numpy)] = encoder.calls
    assert texts == ["abc", "xyz"] and (batch, normalize, numpy) == (16, True, True)


def test_all_blank_skips_encoding():
    encoder = FakeEncoder()
    assert LabseEmbedder(encoder).similarity([("", " ")]) == [0.0]
    assert encoder.calls == []


def test_from_pretrained(monkeypatch):
    import sentence_transformers

    seen = {}

    class FakeST:
        def __init__(self, path, device, local_files_only):
            seen["args"] = (path, device, local_files_only)

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", FakeST)
    emb = LabseEmbedder.from_pretrained("/models/LaBSE", device="cpu")
    assert seen["args"] == ("/models/LaBSE", "cpu", True) and emb.name == "LaBSE"
