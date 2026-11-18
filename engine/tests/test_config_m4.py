import pytest
import yaml
from pydantic import ValidationError

from coglossa.config import ConfigError, config_hash, load_config
from tests.conftest import BASE_CONFIG


def test_api_and_auth_defaults(make_config):
    cfg = make_config()
    assert cfg.api.max_upload_mb == 200
    assert cfg.api.progress_poll_s == 1.0
    assert cfg.auth.mode == "none"
    assert cfg.auth.header == "X-Remote-User"


def test_trusted_header_auth(make_config):
    cfg = make_config(auth={"mode": "trusted_header", "header": "X-Forwarded-User"})
    assert cfg.auth.mode == "trusted_header"
    assert cfg.auth.header == "X-Forwarded-User"


def test_header_names_are_http_tokens(make_config):
    cfg = make_config(auth={"mode": "trusted_header", "header": "x_user.name!#$%&'*+^`|~9"})
    assert cfg.auth.header == "x_user.name!#$%&'*+^`|~9"
    with pytest.raises(ValidationError, match="must be an HTTP header name"):
        make_config(auth={"header": "X Remote"})


def test_null_sections_take_defaults(make_config):
    cfg = make_config(api=None, auth=None)
    assert cfg.api.max_upload_mb == 200
    assert cfg.auth.mode == "none"


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth": {"mode": "oauth"}},
        {"auth": {"mode": "trusted_header", "header": ""}},
        {"auth": {"mode": "trusted_header", "header": "X Remote User"}},
        {"auth": {"mode": "trusted_header", "header": " X-Remote-User"}},
        {"auth": {"mode": "trusted_header", "header": "X-User:"}},
        {"auth": {"mode": "none", "users": ["a"]}},
        {"api": {"max_upload_mb": 0}},
        {"api": {"progress_poll_s": 0}},
        {"api": {"progress_poll_s": 61}},
    ],
)
def test_invalid_api_and_auth_settings(make_config, overrides):
    with pytest.raises(ValidationError):
        make_config(**overrides)


def test_config_hash_ignores_api_and_auth(make_config):
    tuned = make_config(
        api={"max_upload_mb": 5, "progress_poll_s": 2},
        auth={"mode": "trusted_header", "header": "X-User"},
    )
    assert config_hash(make_config()) == config_hash(tuned)


def test_web_dir_is_resolved_against_the_config_file(tmp_path):
    (tmp_path / "conf").mkdir()
    path = tmp_path / "conf" / "coglossa.yaml"
    path.write_text(yaml.safe_dump({**BASE_CONFIG, "api": {"web_dir": "../web/dist"}}))
    cfg, _ = load_config(path)
    assert cfg.api.web_dir == (tmp_path / "web" / "dist").resolve()
    plain = tmp_path / "plain.yaml"
    plain.write_text(yaml.safe_dump(BASE_CONFIG))
    assert load_config(plain)[0].api.web_dir is None


def test_proxy_secret_and_logout_url_default_off(make_config):
    cfg = make_config()
    assert cfg.auth.proxy_secret_env is None and cfg.auth.logout_url is None


def test_proxy_secret_env_must_be_set(tmp_path, monkeypatch):
    monkeypatch.delenv("COGLOSSA_PROXY_SECRET", raising=False)
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump({**BASE_CONFIG, "auth": {"proxy_secret_env": "COGLOSSA_PROXY_SECRET"}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="auth: environment variable COGLOSSA_PROXY_SECRET"):
        load_config(path)
    monkeypatch.setenv("COGLOSSA_PROXY_SECRET", "s" * 32)
    cfg, _ = load_config(path)
    assert cfg.auth.proxy_secret_env == "COGLOSSA_PROXY_SECRET"


def test_scorer_api_key_env_must_be_set(tmp_path, monkeypatch):
    monkeypatch.delenv("COGLOSSA_SCORER_TOKEN", raising=False)
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                **BASE_CONFIG,
                "scorer": {"url": "https://s/", "api_key_env": "COGLOSSA_SCORER_TOKEN"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        ConfigError, match="scorer: environment variable COGLOSSA_SCORER_TOKEN is not set"
    ):
        load_config(path)
    monkeypatch.setenv("COGLOSSA_SCORER_TOKEN", "t")
    cfg, _ = load_config(path)
    assert cfg.scorer.api_key_env == "COGLOSSA_SCORER_TOKEN"
