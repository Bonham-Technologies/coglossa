import asyncio

import pytest

from coglossa.bench.config import parse_pair
from coglossa.bench.systems import run_baseline_system, run_pipeline_system
from coglossa.bench.units import joiner
from coglossa.config import ConfigError
from coglossa.llm.types import PermanentBackendError
from coglossa.testing.fake_backend import FakeBackend
from tests.conftest import build_config

SENTENCES = ["It is not raining in Lagos today.", "Please speak slowly.", "We will meet again."]


async def test_pipeline_system_translates_every_sentence(tmp_path, fake_backend):
    out = await run_pipeline_system(
        build_config(),
        parse_pair("en>es"),
        SENTENCES,
        tmp_path,
        backends={"fake": fake_backend},
        scorer=None,
    )
    assert [u.first for u in out.units] == [0, 1, 2]
    assert all(u.text.startswith("[es] ") for u in out.units)
    assert out.calls > 0 and out.errors == []
    assert out.tokens_in > 0 and out.tokens_out > 0


async def test_baseline_is_one_translator_call_per_chunk(tmp_path, fake_backend):
    cfg = build_config()
    model = cfg.roles.contextual_translator.model
    out = await run_baseline_system(
        cfg, model, parse_pair("en>es"), SENTENCES, tmp_path, backends={"fake": fake_backend}
    )
    assert [u.text for u in out.units] == [f"[es] {s}" for s in SENTENCES]
    assert out.calls == 1  # three short sentences fit one chunk
    assert len(fake_backend.requests) == 1
    assert out.errors == [] and out.tokens_in > 0


async def test_unknown_baseline_model_is_a_config_error(tmp_path, fake_backend):
    with pytest.raises(ConfigError, match="model 'nope' is not defined in this config"):
        await run_baseline_system(
            build_config(),
            "nope",
            parse_pair("en>es"),
            SENTENCES,
            tmp_path,
            backends={"fake": fake_backend},
        )


async def test_failed_baseline_chunk_yields_empty_units_and_an_error(tmp_path, fake_backend):
    fake_backend.script("translate", PermanentBackendError("boom"))
    out = await run_baseline_system(
        build_config(),
        "model-b",
        parse_pair("en>es"),
        SENTENCES,
        tmp_path,
        backends={"fake": fake_backend},
    )
    assert [(u.first, u.text) for u in out.units] == [(0, ""), (1, ""), (2, "")]
    assert len(out.errors) == 1 and out.errors[0].startswith("translate: ")
    assert "\n" not in out.errors[0]


LONG = [" ".join([word] * 150) + "." for word in ("alpha", "bravo", "charlie")]


class Overlapping(FakeBackend):
    """Counts how many calls are in flight at once."""

    def __init__(self):
        super().__init__()
        self.in_flight = self.peak = 0

    async def chat(self, request):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.01)
        try:
            return await super().chat(request)
        finally:
            self.in_flight -= 1


async def test_baseline_chunks_are_translated_at_once(tmp_path):
    cfg = build_config(chunking={"target_words": 100, "max_words": 200, "reserve_tokens": 3000})
    backend = Overlapping()
    out = await run_baseline_system(
        cfg, "model-b", parse_pair("en>es"), LONG, tmp_path, backends={"fake": backend}
    )
    assert out.calls == 3 and backend.peak == 3  # three chunks, all in flight together
    assert [u.first for u in out.units] == [0, 1, 2] and out.errors == []
    assert all(u.text.startswith("[es] ") for u in out.units)


async def test_baseline_reraises_what_is_not_a_model_failure(tmp_path, fake_backend):
    fake_backend.script("translate", RuntimeError("a bug"))
    with pytest.raises(RuntimeError, match="a bug"):
        await run_baseline_system(
            build_config(),
            "model-b",
            parse_pair("en>es"),
            SENTENCES,
            tmp_path,
            backends={"fake": fake_backend},
        )


def test_joiner():
    assert [joiner(t) for t in ("zh", "zh-Hant", "ja-JP", "th", "lo", "km", "my", "yue")] == [
        ""
    ] * 8
    assert [joiner(t) for t in ("en", "es", "ko", "pt-BR", "zh_Hans")] == [" ", " ", " ", " ", ""]


async def test_a_baseline_into_chinese_joins_its_spans_without_a_space(tmp_path, fake_backend):
    out = await run_baseline_system(
        build_config(),
        "model-b",
        parse_pair("en>zh"),
        ["One. Two."],
        tmp_path,
        backends={"fake": fake_backend},
    )
    assert out.units[0].text == "[zh] One.[zh] Two."
