import pytest

from coglossa.bench.config import SystemConfig
from coglossa.bench.fingerprint import output_hash
from coglossa.config import CoglossaConfig, ConfigError
from tests.conftest import BASE_CONFIG

PIPELINE = SystemConfig(name="p", kind="pipeline", config="x.yaml")
BASELINE = SystemConfig(name="b", kind="baseline", config="x.yaml", model="model-b")
EXTRA_BACKEND = {"base_url": "http://other/v1"}


def deep(base: dict, path: list[str], value) -> dict:
    out = {**base}
    if len(path) == 1:
        out[path[0]] = value
    else:
        out[path[0]] = deep(out.get(path[0]) or {}, path[1:], value)
    return out


DATA = {
    **BASE_CONFIG,
    "backends": {**BASE_CONFIG["backends"], "spare": EXTRA_BACKEND},
    "models": {**BASE_CONFIG["models"], "model-c": {"backend": "spare", "context_window": 4096}},
}


def variant(path: list[str], value) -> CoglossaConfig:
    """The base config with one nested field set."""
    return CoglossaConfig.model_validate(deep(DATA, path, value))


def base() -> CoglossaConfig:
    return CoglossaConfig.model_validate(DATA)


def test_hash_is_sixteen_hex_characters_and_stable():
    h = output_hash(base(), PIPELINE)
    assert len(h) == 16 and int(h, 16) >= 0
    assert output_hash(base(), PIPELINE) == h


HARMLESS = [
    (["scoring", "thresholds", "style"], 0.123),
    (["scoring", "language_tiers_file"], "tiers.yaml"),
    (["scoring", "qe_max_chars"], 123),
    (["backends", "fake", "timeout_s"], 7.0),
    (["backends", "fake", "max_concurrency"], 9),
    (["backends", "fake", "api_key_env"], "SOME_KEY"),
    (["retry", "max_attempts"], 9),
    (["audit", "store_llm_io"], False),
    (["worker", "concurrency"], 9),
    (["scorer"], {"url": "http://scorer:1"}),
]


@pytest.mark.parametrize("spec", [PIPELINE, BASELINE], ids=["pipeline", "baseline"])
@pytest.mark.parametrize(
    "path,value", HARMLESS, ids=lambda x: ".".join(x) if isinstance(x, list) else ""
)
def test_harmless_edits_leave_the_hash(spec, path, value):
    assert output_hash(variant(path, value), spec) == output_hash(base(), spec)


@pytest.mark.parametrize(
    "path,value",
    [
        (["models", "model-a", "temperature"], 0.9),
        (["models", "model-a", "extra_body"], {"reasoning": {"enabled": False}}),
        (["backends", "fake", "base_url"], "http://elsewhere/v1"),
        (["roles", "reviewer", "model"], "model-b"),
        (["roles", "judge"], {"model": "model-a"}),
        (["roles", "roundtrip"], {"model": "model-a"}),
        (["chunking", "target_words"], 123),
        (["profiles", "short_max_words"], 11),
    ],
    ids=lambda x: ".".join(x) if isinstance(x, list) else "",
)
def test_shaping_edits_change_the_pipeline_hash(path, value):
    assert output_hash(variant(path, value), PIPELINE) != output_hash(base(), PIPELINE)


def test_backend_kind_changes_the_hash():
    changed = variant(["backends", "fake", "kind"], "other")
    assert output_hash(changed, PIPELINE) != output_hash(base(), PIPELINE)


def write_prompts(root, **texts):
    for pid, text in texts.items():
        (root / pid).mkdir(parents=True)
        (root / pid / "v1.j2").write_text(text, encoding="utf-8")
    return root


def test_prompt_text_edits_change_the_pipeline_hash(tmp_path):
    prompts = write_prompts(tmp_path / "a", contextual="one")
    before = output_hash(variant(["prompts_dir"], str(prompts)), PIPELINE)
    (prompts / "contextual" / "v1.j2").write_text("two", encoding="utf-8")
    assert output_hash(variant(["prompts_dir"], str(prompts)), PIPELINE) != before


def test_prompts_dir_location_does_not_matter(tmp_path):
    first = write_prompts(tmp_path / "a", contextual="one")
    second = write_prompts(tmp_path / "b", contextual="one")
    for spec in (PIPELINE, BASELINE):
        assert output_hash(variant(["prompts_dir"], str(first)), spec) == output_hash(
            variant(["prompts_dir"], str(second)), spec
        )


def test_stray_prompt_entries_are_skipped(tmp_path):
    prompts = write_prompts(tmp_path / "a", contextual="one")
    plain = output_hash(variant(["prompts_dir"], str(prompts)), PIPELINE)
    (prompts / "empty").mkdir()
    (prompts / "notes.txt").write_text("hi", encoding="utf-8")
    assert output_hash(variant(["prompts_dir"], str(prompts)), PIPELINE) == plain


def test_baseline_covers_only_its_model_backend_and_contextual_prompt(tmp_path):
    literal = write_prompts(tmp_path / "a", literal="one", contextual="ctx")
    cfg = variant(["prompts_dir"], str(literal))
    h = output_hash(cfg, BASELINE)
    # roles, and a backend the baseline's model does not use
    assert output_hash(variant(["roles", "reviewer", "model"], "model-b"), BASELINE) == output_hash(
        base(), BASELINE
    )
    assert output_hash(variant(["backends", "spare", "base_url"], "http://x/v1"), BASELINE) == (
        output_hash(base(), BASELINE)
    )
    (literal / "literal" / "v1.j2").write_text("edited", encoding="utf-8")
    assert output_hash(cfg, BASELINE) == h
    (literal / "contextual" / "v1.j2").write_text("edited", encoding="utf-8")
    assert output_hash(cfg, BASELINE) != h


@pytest.mark.parametrize(
    "path,value",
    [
        (["models", "model-b", "temperature"], 0.9),
        (["backends", "fake", "base_url"], "http://elsewhere/v1"),
        (["chunking", "target_words"], 123),
    ],
    ids=lambda x: ".".join(x) if isinstance(x, list) else "",
)
def test_baseline_model_backend_and_chunking_change_the_hash(path, value):
    assert output_hash(variant(path, value), BASELINE) != output_hash(base(), BASELINE)


def test_baseline_model_key_is_covered():
    other = SystemConfig(name="b", kind="baseline", config="x.yaml", model="model-a")
    assert output_hash(base(), other) != output_hash(base(), BASELINE)


def test_baseline_with_unknown_model_is_a_config_error():
    bad = SystemConfig(name="b", kind="baseline", config="x.yaml", model="nope")
    with pytest.raises(ConfigError, match="nope"):
        output_hash(base(), bad)


def with_models(**extra) -> dict:
    return {
        **DATA,
        "models": {**DATA["models"], **extra},
    }


def test_baseline_hash_covers_the_chunk_budget_a_reviewer_sets():
    small = {"backend": "fake", "context_window": 16384}
    same = {"backend": "fake", "context_window": 32768}
    before = output_hash(base(), BASELINE)
    smaller = deep(with_models(**{"model-r": small}), ["roles", "reviewer", "model"], "model-r")
    assert output_hash(CoglossaConfig.model_validate(smaller), BASELINE) != before
    swapped = deep(with_models(**{"model-r": same}), ["roles", "reviewer", "model"], "model-r")
    assert output_hash(CoglossaConfig.model_validate(swapped), BASELINE) == before


def test_baseline_hash_changes_with_a_roundtrip_output_limit_only_when_it_caps_chunks():
    def roundtrip_limited(limit: int) -> CoglossaConfig:
        model = {"backend": "fake", "context_window": 32768, "max_output_tokens": limit}
        data = deep(with_models(**{"model-r": model}), ["roles", "roundtrip"], {"model": "model-r"})
        return CoglossaConfig.model_validate(data)

    before = output_hash(base(), BASELINE)
    # (32768 - 3000) / 4.5 = 6615 tokens a chunk; 16384 / 1.5 leaves more, 4000 / 1.5 less
    assert output_hash(roundtrip_limited(16384), BASELINE) == before
    assert output_hash(roundtrip_limited(4000), BASELINE) != before


def test_baseline_hash_changes_with_a_translators_own_limit_only_when_it_caps_chunks():
    def translator_limited(limit: int) -> CoglossaConfig:
        role = {"model": "model-a", "max_output_tokens": limit}
        return CoglossaConfig.model_validate(deep(DATA, ["roles", "literal_translator"], role))

    before = output_hash(base(), BASELINE)
    assert output_hash(translator_limited(16384), BASELINE) == before
    assert output_hash(translator_limited(4000), BASELINE) != before


@pytest.mark.parametrize("spec", [PIPELINE, BASELINE])
def test_render_version_changes_the_hash(spec, monkeypatch):
    import coglossa.bench.fingerprint as fp

    cfg = CoglossaConfig.model_validate(DATA)
    before = output_hash(cfg, spec)
    monkeypatch.setattr(fp, "RENDER_VERSION", fp.RENDER_VERSION + 1)
    assert output_hash(cfg, spec) != before
