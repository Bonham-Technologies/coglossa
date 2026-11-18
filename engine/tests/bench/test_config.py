from pathlib import Path

import pytest

from coglossa.bench.config import load_bench_config, parse_pair
from coglossa.config import ConfigError

MINIMAL = """
dataset:
  kind: flores200
  path: data/flores
  pairs: ["en>es"]
systems:
  - name: full
    kind: pipeline
    config: cfg/full.yaml
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "bench.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _error(tmp_path: Path, text: str) -> str:
    path = _write(tmp_path, text)
    with pytest.raises(ConfigError) as info:
        load_bench_config(path)
    message = str(info.value)
    assert "\n" not in message and message.startswith(f"{path}: ")
    return message


def test_minimal_file_resolves_paths_against_the_file(tmp_path):
    cfg = load_bench_config(_write(tmp_path, MINIMAL))
    base = tmp_path.resolve()
    assert cfg.dataset.path == base / "data/flores"
    assert cfg.systems[0].config == base / "cfg/full.yaml"
    assert cfg.dataset.parsed_pairs()[0].label == "en>es"
    assert cfg.injector is None and cfg.scorer_url is None


def test_defaults(tmp_path):
    cfg = load_bench_config(_write(tmp_path, MINIMAL))
    assert (cfg.dataset.sentences, cfg.dataset.split, cfg.dataset.seed) == (100, "devtest", 1)
    assert (cfg.tiers.a_max, cfg.tiers.b_max) == (5.0, 10.0)


def test_parallel_paths_and_injector_are_resolved(tmp_path):
    text = """
dataset:
  kind: parallel
  path: d
  pairs: ["en>es"]
  source_file: s.txt
  reference_file: r.txt
systems:
  - {name: b, kind: baseline, config: c.yaml, model: m}
injector: {config: i.yaml, model: m}
"""
    cfg = load_bench_config(_write(tmp_path, text))
    base = tmp_path.resolve()
    assert cfg.dataset.source_file == base / "s.txt"
    assert cfg.dataset.reference_file == base / "r.txt"
    assert cfg.injector is not None and cfg.injector.config == base / "i.yaml"


def test_baseline_needs_a_model(tmp_path):
    text = MINIMAL.replace("kind: pipeline", "kind: baseline")
    assert _error(tmp_path, text).endswith(
        "systems[0]: a baseline needs model: the model key it translates with"
    )


def test_duplicate_system_names(tmp_path):
    text = MINIMAL + "  - {name: full, kind: pipeline, config: x.yaml}\n"
    assert "system name 'full' is used twice" in _error(tmp_path, text)


def test_system_name_must_be_file_safe(tmp_path):
    assert "system name 'a/b'" in _error(tmp_path, MINIMAL.replace("full", "a/b"))


def test_pair_must_use_an_arrow(tmp_path):
    message = _error(tmp_path, MINIMAL.replace("en>es", "en-es"))
    assert "pairs must be written src>tgt, e.g. en>es; got 'en-es'" in message


@pytest.mark.parametrize("key", ["source_file", "reference_file"])
def test_parallel_needs_both_files(tmp_path, key):
    files = {"source_file": "s.txt", "reference_file": "r.txt"}
    del files[key]
    lines = "".join(f"  {k}: {v}\n" for k, v in files.items())
    text = MINIMAL.replace("flores200", "parallel").replace(
        'pairs: ["en>es"]', 'pairs: ["en>es"]\n' + lines.rstrip("\n")
    )
    assert f"a parallel dataset needs {key}" in _error(tmp_path, text)


def test_unknown_key_and_bad_shapes(tmp_path):
    assert "bogus" in _error(tmp_path, MINIMAL + "bogus: 1\n")
    assert "top level must be a mapping" in _error(tmp_path, "- a\n")
    assert "invalid YAML" in _error(tmp_path, "a: [\n")


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="bench config file not found"):
        load_bench_config(tmp_path / "nope.yaml")


def test_unreadable_file(tmp_path):
    path = tmp_path / "bench.yaml"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ConfigError, match="cannot read bench config file"):
        load_bench_config(path)


@pytest.mark.parametrize("text", ["en", "en>", ">es", "en>es>fr"])
def test_parse_pair_rejects(text):
    with pytest.raises(ValueError, match="pairs must be written src>tgt"):
        parse_pair(text)


def test_pair_label_and_slug():
    pair = parse_pair("en>zh-Hant")
    assert (pair.label, pair.slug) == ("en>zh-Hant", "en-zh-Hant")


def test_tier_cuts_must_be_in_order(tmp_path):
    message = _error(tmp_path, MINIMAL + "tiers: {a_max: 12, b_max: 10}\n")
    assert message.endswith("tiers: a_max (12) must not be above b_max (10)")
    cfg = load_bench_config(_write(tmp_path, MINIMAL + "tiers: {a_max: 7, b_max: 7}\n"))
    assert (cfg.tiers.a_max, cfg.tiers.b_max) == (7.0, 7.0)
