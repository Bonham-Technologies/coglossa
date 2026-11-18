from types import SimpleNamespace

import pytest
import torch

from coglossa_scorer import qe as qe_mod
from coglossa_scorer.qe import MAX_INPUT_LENGTH, PREDICTION_TOKEN_ID, MetricXQE, make_input


def test_make_input_matches_metricx():
    assert make_input("src", "hyp", None) == "source: src candidate: hyp"
    assert make_input("", "bt", "ref") == "source:  candidate: bt reference: ref"


def test_scores_are_bounded_and_deterministic(offset_mt5, tokenizer):
    model = MetricXQE(offset_mt5, tokenizer, batch_size=2)
    items = [("Il pleut.", "It rains.", None), ("Le chat.", "The cat.", None), ("", "x", "y")]
    first = model.score(items)
    assert len(first) == 3 and all(0.0 < s < 25.0 for s in first)
    assert len(set(first)) > 1
    assert model.score(items) == first
    assert tokenizer.calls[0][1:] == (MAX_INPUT_LENGTH, True)


def test_scores_do_not_depend_on_batch_composition(offset_mt5, tokenizer):
    short = ("a", "b", None)
    long = ("a much longer source sentence " * 5, "and a long hypothesis " * 5, None)
    alone = MetricXQE(offset_mt5, tokenizer, batch_size=1).score([short])[0]
    assert 0.0 < alone < 25.0
    batched = MetricXQE(offset_mt5, tokenizer, batch_size=2)
    assert batched.score([short, long])[0] == pytest.approx(alone, abs=1e-5)
    assert batched.score([long, short])[1] == pytest.approx(alone, abs=1e-5)


class StubModel(torch.nn.Module):
    """Captures inputs; returns logits whose prediction column is fixed per row."""

    def __init__(self, values):
        super().__init__()
        self.values = values
        self.seen = []

    def forward(self, input_ids, attention_mask, decoder_input_ids):
        self.seen.append((input_ids.clone(), attention_mask.clone(), decoder_input_ids.clone()))
        logits = torch.zeros(input_ids.shape[0], 1, PREDICTION_TOKEN_ID + 1)
        logits[:, 0, PREDICTION_TOKEN_ID] = torch.tensor(self.values[: input_ids.shape[0]])
        return SimpleNamespace(logits=logits)


def test_eos_dropped_padding_masked_and_prediction_clamped(tokenizer):
    stub = StubModel([30.0, -3.0])
    scores = MetricXQE(stub, tokenizer, batch_size=2).score(
        [("a", "b", None), ("aa", "bbbb", None)]
    )
    assert scores == [25.0, 0.0]
    input_ids, mask, decoder = stub.seen[0]
    short_len = len(make_input("a", "b", None))  # chars only, EOS dropped
    assert mask[0].sum().item() == short_len
    assert input_ids[0, short_len:].eq(0).all()
    assert 1 not in input_ids[1, : mask[1].sum()].tolist()  # no EOS left
    assert decoder.eq(0).all() and decoder.shape == (2, 1)


def test_from_pretrained_is_offline_only(monkeypatch, tiny_mt5, tokenizer):
    calls = {}
    import transformers

    def fake_tok(path, **kw):
        calls["tok"] = (path, kw)
        return tokenizer

    def fake_model(path, **kw):
        calls["model"] = (path, kw)
        return tiny_mt5

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", fake_tok)
    monkeypatch.setattr(transformers.MT5ForConditionalGeneration, "from_pretrained", fake_model)
    qe = MetricXQE.from_pretrained(
        "/models/metricx-24-hybrid-xl-v2p6", "/models/mt5-xl", batch_size=4
    )
    assert qe.name == "metricx-24-hybrid-xl-v2p6" and qe.batch_size == 4
    assert calls["tok"] == ("/models/mt5-xl", {"local_files_only": True})
    assert calls["model"][1] == {"dtype": "auto", "local_files_only": True}
    assert qe_mod.MAX_SCORE == 25.0


@pytest.mark.parametrize(
    ("version", "kwarg"),
    [("4.55.4", "torch_dtype"), ("4.56.0", "dtype"), ("4.57.6", "dtype"), ("5.19.0", "dtype")],
)
def test_dtype_keyword_follows_the_transformers_version(monkeypatch, version, kwarg):
    import transformers

    monkeypatch.setattr(transformers, "__version__", version)
    assert qe_mod.dtype_kwarg() == kwarg


@pytest.fixture(scope="session")
def spiece_tokenizer_dir(tmp_path_factory):
    """A tokenizer dir shaped like google/mt5-xl's: spiece.model + config, no tokenizer.json."""
    import json

    import sentencepiece as spm

    root = tmp_path_factory.mktemp("mt5-tokenizer")
    corpus = root / "corpus.txt"
    lines = [
        "Il pleut sur la ville.",
        "It is raining over the town.",
        "Le chat dort près de la fenêtre.",
        "The cat sleeps near the window.",
        "source: candidate: reference:",
        "Quatre bougies brûlaient doucement dans la cuisine silencieuse.",
        "Four candles were burning quietly in the silent kitchen.",
        "Wir haben gestern einen langen Spaziergang am Fluss gemacht.",
        "Yesterday we took a long walk along the river.",
        "El viejo pescador miraba el horizonte cada mañana.",
        "The old fisherman watched the horizon every morning.",
    ]
    corpus.write_text("\n".join(lines * 20), encoding="utf-8")
    spm.SentencePieceTrainer.train(
        input=str(corpus),
        model_prefix=str(root / "spiece"),
        vocab_size=100,
        model_type="unigram",
        pad_id=0,
        eos_id=1,
        unk_id=2,
        bos_id=-1,
        minloglevel=2,
    )
    corpus.unlink()
    (root / "spiece.vocab").unlink()
    (root / "tokenizer_config.json").write_text(
        json.dumps({"tokenizer_class": "T5Tokenizer"}), encoding="utf-8"
    )
    return root


def test_from_pretrained_loads_a_spiece_only_tokenizer_offline(
    monkeypatch, tmp_path, tiny_mt5, spiece_tokenizer_dir
):
    import transformers

    assert not (spiece_tokenizer_dir / "tokenizer.json").exists()
    monkeypatch.setattr(
        transformers.MT5ForConditionalGeneration,
        "from_pretrained",
        classmethod(lambda cls, path, **kw: tiny_mt5),
    )
    qe = MetricXQE.from_pretrained(str(tmp_path / "metricx"), str(spiece_tokenizer_dir))
    encoded = qe.tokenizer(
        ["source: Il pleut. candidate: It rains."], max_length=64, truncation=True
    )
    ids = encoded["input_ids"][0]
    assert len(ids) > 2 and ids[-1] == qe.tokenizer.eos_token_id == 1
    assert qe.tokenizer.pad_token_id == 0
    [score] = qe.score([("Il pleut.", "It rains.", None)])
    assert 0.0 <= score <= 25.0
