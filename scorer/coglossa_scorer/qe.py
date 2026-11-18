"""MetricX-24 quality estimation (google-research/metricx, Apache-2.0).

MetricX's MT5ForRegression is stock mT5 with a regression read-out: decoder input [0], logit of
token 250089 (<extra_id_10>) clamped to [0, 25]; inputs drop their trailing EOS token.
MT5ForConditionalGeneration has the same parameters and forward pass, so MetricX checkpoints
load into it directly and no model code is vendored."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import torch

PREDICTION_TOKEN_ID = 250089
MAX_INPUT_LENGTH = 1536
MAX_SCORE = 25.0


def dtype_kwarg() -> str:
    """transformers 4.56 renamed `torch_dtype` to `dtype` (the old name now warns)."""
    import transformers

    major, minor = (int(part) for part in transformers.__version__.split(".")[:2])
    return "dtype" if (major, minor) >= (4, 56) else "torch_dtype"


def make_input(source: str, hypothesis: str, reference: str | None) -> str:
    text = f"source: {source} candidate: {hypothesis}"
    return text if reference is None else f"{text} reference: {reference}"


class MetricXQE:
    def __init__(
        self,
        model: Any,
        tokenizer: Any,
        *,
        device: str = "cpu",
        batch_size: int = 8,
        max_input_length: int = MAX_INPUT_LENGTH,
        name: str = "metricx-24",
    ) -> None:
        self.model = model.to(device).eval()
        self.tokenizer = tokenizer
        self.device = device
        self.batch_size = batch_size
        self.max_input_length = max_input_length
        self.name = name

    @classmethod
    def from_pretrained(
        cls, model_path: str, tokenizer_path: str, *, device: str = "cpu", batch_size: int = 8
    ) -> "MetricXQE":
        from transformers import AutoTokenizer, MT5ForConditionalGeneration

        tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
        model = MT5ForConditionalGeneration.from_pretrained(
            model_path, **{dtype_kwarg(): "auto"}, local_files_only=True
        )
        return cls(
            model, tokenizer, device=device, batch_size=batch_size, name=Path(model_path).name
        )

    def _encode(self, texts: list[str]) -> tuple[torch.Tensor, torch.Tensor]:
        encoded = self.tokenizer(texts, max_length=self.max_input_length, truncation=True)
        sequences = [seq[:-1] for seq in encoded["input_ids"]]  # MetricX drops the EOS token
        width = max(1, *(len(s) for s in sequences))
        input_ids = torch.full(
            (len(sequences), width), self.tokenizer.pad_token_id, dtype=torch.long
        )
        mask = torch.zeros((len(sequences), width), dtype=torch.long)
        for row, seq in enumerate(sequences):
            input_ids[row, : len(seq)] = torch.tensor(seq, dtype=torch.long)
            mask[row, : len(seq)] = 1
        return input_ids.to(self.device), mask.to(self.device)

    def score(self, items: Sequence[tuple[str, str, str | None]]) -> list[float]:
        scores: list[float] = []
        with torch.inference_mode():
            for start in range(0, len(items), self.batch_size):
                batch = items[start : start + self.batch_size]
                input_ids, mask = self._encode([make_input(*item) for item in batch])
                decoder = torch.zeros((len(batch), 1), dtype=torch.long, device=self.device)
                logits = self.model(
                    input_ids=input_ids, attention_mask=mask, decoder_input_ids=decoder
                ).logits
                predictions = logits[:, 0, PREDICTION_TOKEN_ID].float().clamp(0, MAX_SCORE)
                scores.extend(float(p) for p in predictions.tolist())
        return scores
