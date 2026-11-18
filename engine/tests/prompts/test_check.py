import pytest

from coglossa.config import ConfigError
from coglossa.prompts.check import validate_prompts
from coglossa.prompts.library import PromptLibrary
from tests.conftest import BASE_CONFIG, build_config


def test_builtin_prompts_are_valid():
    cfg = build_config()
    validate_prompts(cfg, PromptLibrary())


def test_unknown_translator_prompt():
    cfg = build_config(
        roles={
            **build_config().roles.model_dump(mode="json"),
            "extra_translators": [{"name": "x", "prompt": "nope", "model": "model-b"}],
        }
    )
    with pytest.raises(ConfigError, match="prompt 'nope'"):
        validate_prompts(cfg, PromptLibrary())


def test_override_template_with_undefined_variable(tmp_path):
    (tmp_path / "reconcile").mkdir()
    (tmp_path / "reconcile" / "v2.j2").write_text("Hello {{ nonsense }}", encoding="utf-8")
    with pytest.raises(ConfigError, match="prompt 'reconcile'.*nonsense"):
        validate_prompts(build_config(), PromptLibrary(tmp_path))


def test_override_template_with_syntax_error(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "v2.j2").write_text("{% if %}", encoding="utf-8")
    with pytest.raises(ConfigError, match="prompt 'profile'"):
        validate_prompts(build_config(), PromptLibrary(tmp_path))


def test_judge_prompt_checked_only_when_role_set(make_config, tmp_path):
    cfg = make_config(roles={**BASE_CONFIG["roles"], "judge": {"model": "model-b"}})
    validate_prompts(cfg, PromptLibrary())  # the built-in judge template renders
    broken = tmp_path / "judge"
    broken.mkdir()
    (broken / "v2.j2").write_text("{{ nope }}", encoding="utf-8")
    with pytest.raises(ConfigError, match="prompt 'judge'"):
        validate_prompts(cfg, PromptLibrary(tmp_path))
    validate_prompts(make_config(), PromptLibrary(tmp_path))  # no judge role -> not checked


def test_back_translate_prompt_checked_only_when_role_set(make_config, tmp_path):
    models = {**BASE_CONFIG["models"], "model-c": {"backend": "fake", "context_window": 32768}}
    cfg = make_config(
        models=models, roles={**BASE_CONFIG["roles"], "roundtrip": {"model": "model-c"}}
    )
    validate_prompts(cfg, PromptLibrary())  # the built-in back_translate template renders
    broken = tmp_path / "back_translate"
    broken.mkdir()
    (broken / "v2.j2").write_text("{{ nope }}", encoding="utf-8")
    with pytest.raises(ConfigError, match="prompt 'back_translate'"):
        validate_prompts(cfg, PromptLibrary(tmp_path))
    validate_prompts(make_config(), PromptLibrary(tmp_path))  # no roundtrip role -> not checked


def test_run_prompt_ids_lists_optional_roles_when_set(make_config):
    from coglossa.prompts.check import run_prompt_ids

    cfg = make_config()
    assert run_prompt_ids(cfg) == [
        "profile",
        "guide",
        "guide_merge",
        "reconcile",
        "sweep",
        "literal",
        "contextual",
    ]
    roles = {
        **cfg.roles.model_dump(),
        "judge": {"model": "model-b"},
        "roundtrip": {"model": "model-b"},
    }
    with_roles = make_config(roles=roles)
    assert run_prompt_ids(with_roles)[-2:] == ["judge", "back_translate"]


def test_validate_prompts_renders_sweep(make_config, tmp_path):
    import pytest

    from coglossa.config import ConfigError
    from coglossa.prompts.check import validate_prompts
    from coglossa.prompts.library import PromptLibrary

    (tmp_path / "sweep").mkdir()
    (tmp_path / "sweep" / "v9.j2").write_text("{{ missing_variable }}", encoding="utf-8")
    with pytest.raises(ConfigError, match="sweep"):
        validate_prompts(make_config(), PromptLibrary(tmp_path))
