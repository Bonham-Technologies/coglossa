from types import SimpleNamespace

import pytest
import torch
from transformers import MT5Config, MT5ForConditionalGeneration


class FakeTokenizer:
    """Char-code tokenizer: ids 2.. for characters, EOS = 1, pad = 0."""

    pad_token_id = 0

    def __init__(self):
        self.calls = []

    def __call__(self, texts, max_length, truncation):
        self.calls.append((list(texts), max_length, truncation))
        return {
            "input_ids": [[2 + ord(c) % 1000 for c in t][: max_length - 1] + [1] for t in texts]
        }


@pytest.fixture(scope="session")
def tiny_mt5():
    torch.manual_seed(0)
    config = MT5Config(
        vocab_size=250112,
        d_model=8,
        d_kv=4,
        d_ff=16,
        num_layers=1,
        num_decoder_layers=1,
        num_heads=2,
        decoder_start_token_id=0,
        pad_token_id=0,
        eos_token_id=1,
        tie_word_embeddings=False,
    )
    return MT5ForConditionalGeneration(config).eval()


@pytest.fixture
def tokenizer():
    return FakeTokenizer()


class OffsetModel(torch.nn.Module):
    """Wraps a model and shifts its logits so the QE read-out lands inside (0, 25)."""

    def __init__(self, inner, offset=12.5):
        super().__init__()
        self.inner = inner
        self.offset = offset

    def forward(self, input_ids, attention_mask, decoder_input_ids):
        out = self.inner(
            input_ids=input_ids, attention_mask=attention_mask, decoder_input_ids=decoder_input_ids
        )
        return SimpleNamespace(logits=out.logits + self.offset)


@pytest.fixture(scope="session")
def offset_mt5(tiny_mt5):
    return OffsetModel(tiny_mt5).eval()
