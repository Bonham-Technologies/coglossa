"""The configs the bundle ships (deploy/config) load and validate (spec §3)."""

from pathlib import Path

import pytest

from coglossa.bench.config import load_bench_config
from coglossa.bench.flores import FloresData
from coglossa.config import load_config

DEPLOY_CONFIG = Path(__file__).resolve().parents[2] / "deploy" / "config"


@pytest.mark.parametrize("name", ["coglossa.example.yaml", "coglossa.demo.yaml"])
def test_the_bundled_configs_load(name):
    cfg, issues = load_config(DEPLOY_CONFIG / name)
    assert [i for i in issues if i.level == "error"] == []
    assert cfg.scorer is not None and cfg.scorer.url == "http://scorer:8080"
    assert cfg.storage.path == Path("/data/files")
    assert cfg.api.web_dir is None  # the image passes --web-dir
    assert cfg.database.url_env == "COGLOSSA_DATABASE_URL"  # compose sets it


def test_the_demo_uses_the_fake_model_server():
    cfg, _ = load_config(DEPLOY_CONFIG / "coglossa.demo.yaml")
    assert {b.base_url for b in cfg.backends.values()} == {"http://fake-openai:8000/v1"}
    assert cfg.roles.judge is not None and cfg.roles.roundtrip is not None


DEPLOY_TESTS = DEPLOY_CONFIG.parent / "tests"


def test_the_bundled_bench_config_loads():
    cfg = load_bench_config(DEPLOY_CONFIG / "bench.example.yaml")
    assert cfg.dataset.kind == "flores200"
    assert cfg.dataset.path == Path("/opt/coglossa/bench/flores200")  # the worker's mount
    assert cfg.dataset.pairs == ["en>es", "en>zh-Hans", "en>ru", "en>ar", "en>hi", "en>sw", "en>yo"]
    assert sorted(s.kind for s in cfg.systems) == ["baseline", "pipeline"]
    # the worker's config mount (resolved, so /private/etc on macOS)
    assert all(s.config.as_posix().endswith("/etc/coglossa/coglossa.yaml") for s in cfg.systems)
    assert cfg.scorer_url == "http://scorer:8080"


def test_the_airgap_bench_config_reads_the_mini_flores_tree():
    """deploy/tests/bench.airgap.yaml runs in the air-gap test on deploy/tests/flores-mini/,
    shipped as the bundle's bench/flores200/."""
    cfg = load_bench_config(DEPLOY_TESTS / "bench.airgap.yaml")
    assert cfg.dataset.path == Path("/opt/coglossa/bench/flores200")
    assert len(cfg.dataset.pairs) == 2 and cfg.dataset.sentences == 4
    assert {s.kind for s in cfg.systems} == {"pipeline", "baseline"}
    demo = load_config(DEPLOY_CONFIG / "coglossa.demo.yaml")[0]
    assert all(s.model in demo.models for s in cfg.systems if s.kind == "baseline")
    assert cfg.injector is not None and cfg.injector.model in demo.models
    mini = DEPLOY_TESTS / "flores-mini"
    assert (mini / "LICENSE.txt").is_file() and (mini / "ATTRIBUTION.txt").is_file()
    flores = FloresData(mini)
    for pair in cfg.dataset.parsed_pairs():
        for split in FloresData.splits:
            rows = flores.pair(pair.src, pair.tgt, split)
            assert len(rows) >= cfg.dataset.sentences
            assert all(src.strip() and ref.strip() for src, ref in rows)
