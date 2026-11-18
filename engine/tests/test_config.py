import hashlib

import pytest
import yaml

from coglossa.config import (
    _OPERATIONAL_SECTIONS,
    _SCORER_CREDENTIALS,
    JUDGE_MAX_OUTPUT_TOKENS,
    AuthConfig,
    CoglossaConfig,
    ConfigError,
    StructuredOutput,
    config_hash,
    is_loopback,
    load_config,
    proxy_bypass_address,
    published_address,
    validate_config,
)
from coglossa.schema import PipelineProfile
from tests.conftest import BASE_CONFIG


def _write(tmp_path, data) -> object:
    path = tmp_path / "coglossa.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_defaults(make_config):
    cfg = make_config()
    assert cfg.models["model-a"].structured_output is StructuredOutput.JSON_SCHEMA
    assert cfg.retry.max_attempts == 3
    assert cfg.chunking.target_words == 1500
    assert cfg.profiles.checkpoint[PipelineProfile.LONG_FORM] is True
    assert cfg.profiles.checkpoint[PipelineProfile.SHORT] is False
    assert cfg.audit.store_llm_io is True


def test_translators_builtin_then_extras(make_config):
    cfg = make_config(
        roles={
            **BASE_CONFIG["roles"],
            "extra_translators": [{"name": "second", "prompt": "contextual", "model": "model-a"}],
        }
    )
    names = [(t.name, t.prompt, t.model) for t in cfg.roles.translators()]
    assert names == [
        ("literal", "literal", "model-a"),
        ("contextual", "contextual", "model-b"),
        ("second", "contextual", "model-a"),
    ]


def test_valid_config_has_no_issues(make_config):
    assert validate_config(make_config()) == []


def test_unknown_backend_and_model_are_errors(make_config):
    cfg = make_config(
        models={"model-a": {"backend": "nope", "context_window": 8192}},
    )
    messages = [i.message for i in validate_config(cfg) if i.level == "error"]
    assert any("unknown backend 'nope'" in m for m in messages)
    assert any("unknown model 'model-b'" in m for m in messages)


def test_judge_equal_reviewer_is_error_with_two_models(make_config):
    roles = {**BASE_CONFIG["roles"], "judge": {"model": "model-a"}}
    issues = validate_config(make_config(roles=roles))
    assert [(i.level, "judge" in i.message) for i in issues] == [("error", True)]


def test_roundtrip_using_translator_model_is_error(make_config):
    roles = {**BASE_CONFIG["roles"], "roundtrip": {"model": "model-b"}}
    issues = validate_config(make_config(roles=roles))
    assert [(i.level, "roundtrip" in i.message) for i in issues] == [("error", True)]


def test_diversity_rules_downgrade_to_warnings_with_one_model(make_config):
    one = {"m": {"backend": "fake", "context_window": 8192}}
    roles = {
        "literal_translator": {"model": "m"},
        "contextual_translator": {"model": "m"},
        "reviewer": {"model": "m"},
        "profiler_guide": {"model": "m"},
        "judge": {"model": "m"},
        "roundtrip": {"model": "m"},
    }
    issues = validate_config(make_config(models=one, roles=roles))
    assert {i.level for i in issues} == {"warning"}
    assert len(issues) == 2


def test_duplicate_translator_names_are_error(make_config):
    roles = {
        **BASE_CONFIG["roles"],
        "extra_translators": [{"name": "literal", "prompt": "literal", "model": "model-a"}],
    }
    assert any(
        "duplicate translator name 'literal'" in i.message
        for i in validate_config(make_config(roles=roles))
    )


def test_missing_api_key_env_is_error(make_config, monkeypatch):
    monkeypatch.delenv("COGLOSSA_TEST_KEY", raising=False)
    backends = {"fake": {"base_url": "http://x/v1", "api_key_env": "COGLOSSA_TEST_KEY"}}
    issues = validate_config(make_config(backends=backends))
    assert any("COGLOSSA_TEST_KEY" in i.message for i in issues)
    monkeypatch.setenv("COGLOSSA_TEST_KEY", "secret")
    assert validate_config(make_config(backends=backends)) == []


def test_target_words_above_max_is_error(make_config):
    issues = validate_config(make_config(chunking={"target_words": 3000, "max_words": 2000}))
    assert any("target_words" in i.message for i in issues)


def test_load_config_ok_and_resolves_prompts_dir(tmp_path):
    path = _write(tmp_path, {**BASE_CONFIG, "prompts_dir": "my-prompts"})
    cfg, warnings = load_config(path)
    assert warnings == []
    assert cfg.prompts_dir == tmp_path / "my-prompts"


def test_load_config_accepts_reserved_future_sections(tmp_path):
    data = {**BASE_CONFIG, "scorer": {"url": "http://scorer:8080"}, "auth": {"mode": "none"}}
    cfg, _ = load_config(_write(tmp_path, data))
    assert cfg.scorer.url == "http://scorer:8080"


def test_load_config_rejects_unknown_keys(tmp_path):
    with pytest.raises(ConfigError, match="backendz"):
        load_config(_write(tmp_path, {**BASE_CONFIG, "backendz": {}}))


def test_load_config_rejects_non_mapping(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(path)


def test_load_config_rejects_invalid_yaml(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("backends: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(path)


def test_load_config_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")


def test_load_config_raises_on_validation_errors(tmp_path):
    roles = {**BASE_CONFIG["roles"], "judge": {"model": "model-a"}}
    with pytest.raises(ConfigError, match="judge"):
        load_config(_write(tmp_path, {**BASE_CONFIG, "roles": roles}))


def test_load_config_returns_warnings(tmp_path):
    data = {
        "backends": BASE_CONFIG["backends"],
        "models": {"m": {"backend": "fake", "context_window": 8192}},
        "roles": {
            k: {"model": "m"}
            for k in (
                "literal_translator",
                "contextual_translator",
                "reviewer",
                "profiler_guide",
                "judge",
            )
        },
    }
    _, warnings = load_config(_write(tmp_path, data))
    assert [w.level for w in warnings] == ["warning"]


def test_config_hash_is_stable_and_sensitive(make_config):
    assert config_hash(make_config()) == config_hash(make_config())
    assert config_hash(make_config()) != config_hash(make_config(retry={"max_attempts": 9}))
    assert len(config_hash(make_config())) == 16


def test_config_hash_covers_the_tiers_file_content(make_config, tmp_path):
    tiers = tmp_path / "tiers.yaml"
    tiers.write_text("default: C\n", encoding="utf-8")
    cfg = make_config(scoring={"language_tiers_file": tiers})
    before = config_hash(cfg)
    assert config_hash(cfg) == before
    tiers.write_text("default: B\n", encoding="utf-8")
    assert config_hash(cfg) != before


def test_config_hash_without_a_tiers_file_is_unchanged_by_the_feature(make_config):
    cfg = make_config()
    assert cfg.scoring.language_tiers_file is None
    expected = hashlib.sha256(
        cfg.model_dump_json(
            exclude={
                **dict.fromkeys(_OPERATIONAL_SECTIONS, True),
                "scorer": _SCORER_CREDENTIALS,
                "backends": {"__all__": {"stream"}},  # left at its default (CGL-71)
            }
        ).encode()
    ).hexdigest()[:16]
    assert config_hash(cfg) == expected


def test_backends_stream_unless_told_not_to(make_config):
    cfg = make_config()
    assert cfg.backends["fake"].stream is True
    off = make_config(backends={"fake": {"base_url": "http://127.0.0.1:9/v1", "stream": False}})
    assert off.backends["fake"].stream is False


def test_config_hash_of_a_config_that_does_not_set_stream_is_unchanged(make_config):
    # CGL-71: the value before backends had `stream`, so runs and bench results are not redone
    assert config_hash(make_config()) == "dfc0dcacda21530e"
    on = make_config(backends={"fake": {"base_url": "http://127.0.0.1:9/v1", "stream": True}})
    assert config_hash(on) == "dfc0dcacda21530e"
    off = make_config(backends={"fake": {"base_url": "http://127.0.0.1:9/v1", "stream": False}})
    assert config_hash(off) != "dfc0dcacda21530e"


def test_config_hash_tolerates_an_unreadable_tiers_file(make_config, tmp_path):
    cfg = make_config(scoring={"language_tiers_file": tmp_path / "missing.yaml"})
    assert len(config_hash(cfg)) == 16


def test_config_hash_ignores_operational_sections(make_config, tmp_path):
    base = make_config()
    tuned = base.model_copy(
        update={
            "worker": base.worker.model_copy(update={"concurrency": base.worker.concurrency + 1}),
            "storage": base.storage.model_copy(update={"path": tmp_path / "elsewhere"}),
        }
    )
    assert tuned.worker != base.worker and tuned.storage != base.storage
    assert config_hash(tuned) == config_hash(base)
    reviewer = base.roles.reviewer.model_copy(update={"model": "other-model"})
    changed = base.model_copy(
        update={"roles": base.roles.model_copy(update={"reviewer": reviewer})}
    )
    assert config_hash(changed) != config_hash(base)


def test_load_config_rejects_non_utf8(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(bad)


def test_load_config_rejects_unreadable(tmp_path, monkeypatch):
    path = tmp_path / "c.yaml"
    path.write_text("x: 1", encoding="utf-8")

    def deny(self, *a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(type(path), "read_text", deny)
    with pytest.raises(ConfigError, match="cannot read.*denied"):
        load_config(path)


def test_validation_errors_are_one_line(tmp_path):
    path = tmp_path / "coglossa.yaml"
    path.write_text(
        "backends: {a: {base_url: 'http://x/v1', max_concurrency: 0}}\n"
        "models: {m: {backend: a, context_window: 10}}\n"
        "roles: {literal_translator: {model: m}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as e:
        load_config(path)
    message = str(e.value)
    assert "\n" not in message
    assert message.startswith(f"{path}: backends.a.max_concurrency: ")
    assert "greater than or equal to 1" in message
    assert message.endswith("more problems)")


def test_semantic_errors_are_one_line(tmp_path):
    path = _write(
        tmp_path,
        {
            **BASE_CONFIG,
            "scoring": {"length_ratio_band": [2.0, 1.0], "mqm_penalties": {"minor": -1}},
        },
    )
    with pytest.raises(ConfigError) as e:
        load_config(path)
    message = str(e.value)
    assert "\n" not in message
    assert message.startswith(f"{path}: scoring.length_ratio_band")
    assert message.endswith("(and 1 more problem)")


@pytest.mark.parametrize(
    ("key", "tail"),
    [
        ("stream", "cannot be set"),
        ("messages", "cannot be set"),
        ("response_format", "cannot be set"),
        ("temperature", "is set by Coglossa; use the model's own setting"),
        ("max_tokens", "is set by Coglossa; use the model's own setting"),
        ("model", "is set by Coglossa; use the model's own setting"),
    ],
)
def test_extra_body_reserved_keys_are_one_line_errors(tmp_path, key, tail):
    models = {"m": {"backend": "fake", "context_window": 4096, "extra_body": {key: True}}}
    path = _write(tmp_path, {**BASE_CONFIG, "models": {**BASE_CONFIG["models"], **models}})
    with pytest.raises(ConfigError) as e:
        load_config(path)
    message = str(e.value)
    assert "\n" not in message
    assert f"models.m.extra_body: Value error, '{key}' {tail}" in message


def test_extra_body_accepts_server_options():
    body = {"chat_template_kwargs": {"enable_thinking": False}, "reasoning": {"enabled": False}}
    models = {"model-a": {"backend": "fake", "context_window": 4096, "extra_body": body}}
    cfg = CoglossaConfig.model_validate(
        {**BASE_CONFIG, "models": {**BASE_CONFIG["models"], **models}}
    )
    assert cfg.models["model-a"].extra_body == body


def _with_auth(tmp_path, **auth):
    return _write(tmp_path, {**BASE_CONFIG, "auth": {"mode": "trusted_header", **auth}})


def test_the_people_file_is_relative_to_the_config_and_checked(tmp_path):
    (tmp_path / "people.yaml").write_text("admins: [ana]\n", encoding="utf-8")
    cfg, _ = load_config(_with_auth(tmp_path, people_file="people.yaml"))
    assert cfg.auth.people_file == (tmp_path / "people.yaml").resolve()
    assert cfg.auth.groups_header is None and cfg.auth.admin_group == "coglossa-admins"
    with pytest.raises(ConfigError, match="people file not found"):
        load_config(_with_auth(tmp_path, people_file="nope.yaml"))
    with pytest.raises(ConfigError, match="groups_header"):
        load_config(_with_auth(tmp_path, groups_header="bad header"))
    cfg, _ = load_config(_with_auth(tmp_path, groups_header="X-Remote-Groups"))
    assert cfg.auth.groups_header == "X-Remote-Groups"


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("127.200.0.9", True),
        ("::1", True),
        ("[::1]", True),
        ("localhost", True),
        ("LocalHost", True),
        ("0.0.0.0", False),
        ("::", False),
        ("10.0.0.5", False),
        ("coglossa.example.internal", False),  # a name could resolve anywhere
        ("", False),
    ],
)
def test_is_loopback(host, loopback):
    assert is_loopback(host) is loopback


def test_published_address(monkeypatch):
    """CGL-61: a loopback listen address wins, then COGLOSSA_HTTP_BIND, then the listen
    address, then compose's default."""
    monkeypatch.delenv("COGLOSSA_HTTP_BIND", raising=False)
    assert published_address() == "0.0.0.0"
    assert published_address("10.0.0.5") == "10.0.0.5"
    monkeypatch.setenv("COGLOSSA_HTTP_BIND", "")
    assert published_address() == "0.0.0.0"
    monkeypatch.setenv("COGLOSSA_HTTP_BIND", "127.0.0.1")
    assert published_address("0.0.0.0") == "127.0.0.1"
    assert published_address("::") == "127.0.0.1"
    # a specific address is reachable there, whatever compose's publish address says
    assert published_address("10.0.0.5") == "10.0.0.5"
    monkeypatch.setenv("COGLOSSA_HTTP_BIND", "0.0.0.0")
    assert published_address("127.0.0.1") == "127.0.0.1"


def test_proxy_bypass_address(monkeypatch):
    monkeypatch.setenv("COGLOSSA_HTTP_BIND", "0.0.0.0")
    named = AuthConfig(mode="trusted_header")
    assert proxy_bypass_address(named, "0.0.0.0") == "0.0.0.0"
    assert proxy_bypass_address(named, "127.0.0.1") is None
    assert proxy_bypass_address(AuthConfig(), "0.0.0.0") is None  # auth: none
    secret = AuthConfig(mode="trusted_header", proxy_secret_env="COGLOSSA_PROXY_SECRET")
    assert proxy_bypass_address(secret, "0.0.0.0") is None
    monkeypatch.setenv("COGLOSSA_HTTP_BIND", "127.0.0.1")
    assert proxy_bypass_address(named, "0.0.0.0") is None


def test_a_people_file_team_named_like_the_admin_group_is_a_warning(tmp_path):
    """CGL-31: such a team grants nothing (the admin group is no team, and the file names
    admins under `admins`), so say so rather than ignore it silently."""
    (tmp_path / "people.yaml").write_text(
        "admins: [ana]\nteams:\n  Coglossa-Admins: [maria]\n  desk: [jose]\n", encoding="utf-8"
    )
    _, issues = load_config(_with_auth(tmp_path, people_file="people.yaml"))
    assert [(i.level, i.message) for i in issues] == [
        (
            "warning",
            "people file: team 'Coglossa-Admins' has the name of auth.admin_group, so it gives"
            " its members nothing: list admins under 'admins'",
        )
    ]
    (tmp_path / "people.yaml").write_text("teams:\n  desk: [jose]\n", encoding="utf-8")
    assert load_config(_with_auth(tmp_path, people_file="people.yaml"))[1] == []


def test_team_group_options(tmp_path):
    """CGL-74: unset by default (every group is a team); a prefix and an allowlist load into
    one filter; values a team name could never match are refused."""
    cfg, _ = load_config(_with_auth(tmp_path))
    assert cfg.auth.team_groups_prefix is None and cfg.auth.team_groups is None
    assert not cfg.auth.team_filter().active
    cfg, issues = load_config(
        _with_auth(tmp_path, team_groups_prefix="Coglossa-", team_groups=["Spanish Desk"])
    )
    assert issues == []
    only = cfg.auth.team_filter()
    assert only.allows("coglossa-pacific") and only.allows("spanish desk")
    assert not only.allows("Domain Users")
    for bad in ("", " coglossa-", "coglossa/", "Équipe-"):
        with pytest.raises(ConfigError, match="team_groups_prefix"):
            load_config(_with_auth(tmp_path, team_groups_prefix=bad))
    for bad in ("", "/translators/es", "x" * 65):
        with pytest.raises(ConfigError, match="team_groups"):
            load_config(_with_auth(tmp_path, team_groups=["desk", bad]))


def test_an_allowlisted_admin_group_is_a_warning(tmp_path):
    """The admin group is never a team (CGL-74 keeps CGL-31's rule), so listing it does
    nothing."""
    _, issues = load_config(_with_auth(tmp_path, team_groups=["desk", " Coglossa-Admins"]))
    assert [(i.level, i.message) for i in issues] == [
        (
            "warning",
            "auth.team_groups lists ' Coglossa-Admins', the name of auth.admin_group: that group"
            " makes its members admins and is never a team",
        )
    ]
    cfg, _ = load_config(_with_auth(tmp_path, team_groups=["desk", " Coglossa-Admins"]))
    assert cfg.auth.team_rule() == "are 'desk'"
    assert not cfg.auth.team_filter().allows("coglossa-admins")
    cfg, _ = load_config(_with_auth(tmp_path, team_groups=["coglossa-admins"]))
    assert cfg.auth.team_rule() is None


def _limited(model_limit: int | None, **role_limits) -> dict:
    """BASE_CONFIG with a judge, a roundtrip and an extra translator; every model's
    max_output_tokens is model_limit, and role_limits sets roles' own."""
    model = {"backend": "fake", "context_window": 32768}
    if model_limit is not None:
        model["max_output_tokens"] = model_limit
    roles = {
        **BASE_CONFIG["roles"],
        "judge": {"model": "model-b"},
        "roundtrip": {"model": "model-c"},
        "extra_translators": [{"name": "third", "prompt": "literal", "model": "model-a"}],
    }
    for role, limit in role_limits.items():
        if role == "third":
            roles["extra_translators"] = [
                {**roles["extra_translators"][0], "max_output_tokens": limit}
            ]
        else:
            roles[role] = {**roles[role], "max_output_tokens": limit}
    models = dict.fromkeys(("model-a", "model-b", "model-c"), model)
    return {**BASE_CONFIG, "models": models, "roles": roles}


def _output_limits(data: dict) -> dict[str, int | None]:
    """Each role's output limit, the judge's with its default, as the stages ask for them."""
    cfg = CoglossaConfig.model_validate(data)
    return {
        label: cfg.output_limit(role, JUDGE_MAX_OUTPUT_TOKENS if label == "judge" else None)
        for label, role in cfg.roles.configured()
    }


def test_a_role_takes_its_own_output_limit_and_the_judge_a_lower_default():
    assert JUDGE_MAX_OUTPUT_TOKENS == 8192
    assert _output_limits(_limited(16384)) == {
        "translator:literal": 16384,
        "translator:contextual": 16384,
        "translator:third": 16384,
        "reviewer": 16384,
        "profiler_guide": 16384,
        "judge": 8192,
        "roundtrip": 16384,
    }
    own = _limited(16384, judge=16384, reviewer=4000, literal_translator=2000, third=3000)
    assert _output_limits(own) == {
        "translator:literal": 2000,
        "translator:contextual": 16384,
        "translator:third": 3000,
        "reviewer": 4000,
        "profiler_guide": 16384,
        "judge": 16384,
        "roundtrip": 16384,
    }
    # the judge's default is never above the model's limit
    assert _output_limits(_limited(4096))["judge"] == 4096
    # a model without a limit: the judge still stops at its default, the others as today
    unlimited = _output_limits(_limited(None))
    assert unlimited["judge"] == 8192
    assert {unlimited[k] for k in unlimited if k != "judge"} == {None}
    # ... but leaves half a small context window for its prompt
    small = _limited(None)
    small["models"] = {k: {**m, "context_window": 8192} for k, m in small["models"].items()}
    assert _output_limits(small)["judge"] == 4096


def test_a_role_limit_above_the_models_is_an_error(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_config(_write(tmp_path, _limited(16384, judge=32768)))
    assert str(e.value).endswith(
        "role 'judge': max_output_tokens 32768 is above model 'model-b''s 16384; "
        "raise models.model-b.max_output_tokens or lower the role's"
    )
    with pytest.raises(ConfigError, match="role 'translator:third'"):
        load_config(_write(tmp_path, _limited(16384, third=20000)))
    with pytest.raises(ConfigError, match="roles.reviewer.max_output_tokens"):
        load_config(_write(tmp_path, _limited(16384, reviewer=0)))
    cfg, _ = load_config(_write(tmp_path, _limited(None, judge=32768)))  # no model limit
    assert cfg.output_limit(cfg.roles.judge, JUDGE_MAX_OUTPUT_TOKENS) == 32768


def test_a_config_without_role_limits_keeps_its_hash_and_snapshot():
    # hashes from before roles had max_output_tokens (CGL-57): runs and bench results that
    # were made with these configs do not read as made with other settings
    assert config_hash(CoglossaConfig.model_validate(BASE_CONFIG)) == "dfc0dcacda21530e"
    cfg = CoglossaConfig.model_validate(_limited(16384))
    snapshot = cfg.model_dump(mode="json")
    assert "max_output_tokens" not in str(snapshot["roles"])
    assert CoglossaConfig.model_validate(snapshot) == cfg  # an older snapshot loads as it was
    limited = CoglossaConfig.model_validate(_limited(16384, judge=4000))
    assert config_hash(limited) != config_hash(cfg)
    assert CoglossaConfig.model_validate(limited.model_dump(mode="json")) == limited
