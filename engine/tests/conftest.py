import logging
from collections.abc import Iterator

import pytest

from coglossa.config import CoglossaConfig
from coglossa.llm.agent import AgentRunner, CallLog
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import Segment
from coglossa.testing.fake_backend import FakeBackend

pytest_plugins = ["coglossa.testing.pytest_db"]

# The thresholds before the October 2026 calibration; the scoring tests are written against them.
PRE_CALIBRATION_THRESHOLDS: dict = {"meaning": {"A": 70.0, "B": 72.0, "C": 75.0}, "style": 60.0}

BASE_CONFIG: dict = {
    "backends": {"fake": {"base_url": "http://127.0.0.1:9/v1", "max_concurrency": 4}},
    "models": {
        "model-a": {"backend": "fake", "context_window": 32768},
        "model-b": {"backend": "fake", "context_window": 32768},
    },
    "roles": {
        "literal_translator": {"model": "model-a"},
        "contextual_translator": {"model": "model-b"},
        "reviewer": {"model": "model-a"},
        "profiler_guide": {"model": "model-b"},
    },
    "retry": {"max_attempts": 3, "base_delay_s": 0.01, "max_delay_s": 0.02},
}


@pytest.fixture(autouse=True)
def _restore_log_handlers() -> Iterator[None]:
    """A JSON log handler a test installs writes to that test's captured stream; detach it
    afterwards so later tests don't log to a closed stream."""
    loggers = [logging.getLogger(name) for name in ("coglossa", "procrastinate")]
    saved = [(lg.handlers[:], lg.level) for lg in loggers]
    yield
    for lg, (handlers, level) in zip(loggers, saved, strict=True):
        lg.handlers = handlers
        lg.setLevel(level)


def build_config(**overrides) -> CoglossaConfig:
    data = {**BASE_CONFIG, **overrides}
    return CoglossaConfig.model_validate(data)


@pytest.fixture
def make_config():
    return build_config


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend(name="fake")


@pytest.fixture
def make_runner(make_config, fake_backend):
    def _make(cfg: CoglossaConfig | None = None, *, store_io: bool = True) -> AgentRunner:
        cfg = cfg or make_config()
        return AgentRunner(
            cfg,
            {"fake": fake_backend},
            PromptLibrary(cfg.prompts_dir),
            CallLog(store_io=store_io),
            sleep=_no_sleep,
        )

    return _make


def make_segments(*texts: str) -> list[Segment]:
    return [
        Segment(id=f"s{i + 1}", block_id="b1", start=0, end=len(t), text=t)
        for i, t in enumerate(texts)
    ]
