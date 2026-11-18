import json
from pathlib import Path

import pytest

ENGLISH = [
    "Marie arrived in Paris on 14 July 1998.",
    "The committee approved the budget, but the vote was close.",
    "It is not raining in Lagos today.",
    "Dr. Okafor said the vaccine is safe for children.",
    "The river flows north for 300 kilometres.",
    "We will meet again next year.",
    "The museum, which opened in 1901, has 12 rooms.",
    "Please speak slowly.",
]


def _lines(tag: str) -> list[str]:
    return [f"[{tag}] {line}" for line in ENGLISH]


@pytest.fixture
def mini_flores(tmp_path: Path) -> Path:
    """A tiny FLORES-200 tree with the real layout: <root>/flores200_dataset/<split>/<code>.<split>.
    Non-English sides are the English line tagged with the language, enough for alignment tests."""
    root = tmp_path / "flores"
    for split in ("dev", "devtest"):
        d = root / "flores200_dataset" / split
        d.mkdir(parents=True)
        for code in ("eng_Latn", "spa_Latn", "yor_Latn", "zho_Hant", "por_Latn"):
            lines = ENGLISH if code == "eng_Latn" else _lines(code)
            (d / f"{code}.{split}").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def fake_runner(make_config, make_runner):
    """An AgentRunner over the synthetic FakeBackend, with a model keyed "m"."""
    cfg = make_config(models={"m": {"backend": "fake", "context_window": 32768}})
    return make_runner(cfg)


@pytest.fixture
def fake_runner_echo(make_config, make_runner, fake_backend):
    """Like fake_runner, but the model hands the input text back unchanged."""
    cfg = make_config(models={"m": {"backend": "fake", "context_window": 32768}})
    fake_backend.script("inject", json.dumps({"text": "Llueve hoy."}))
    return make_runner(cfg)
