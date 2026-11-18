from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from coglossa.config import ConfigError, load_config, validate_config
from coglossa.schema import PipelineProfile
from tests.conftest import BASE_CONFIG, build_config


def _write(tmp_path: Path, **sections) -> Path:
    path = tmp_path / "coglossa.yaml"
    path.write_text(yaml.safe_dump({**BASE_CONFIG, **sections}), encoding="utf-8")
    return path


def test_defaults():
    cfg = build_config()
    assert cfg.database.url_env == "COGLOSSA_DATABASE_URL"
    assert cfg.storage.kind == "local"
    assert cfg.storage.path == Path("/data/files")
    assert cfg.worker.concurrency == 8
    assert cfg.worker.stalled_after_s == 60.0
    assert cfg.worker.scorer_wait_s == 900.0
    assert cfg.worker.burst_scorer_wait_s == 0.0
    assert cfg.profiles.sweep == {
        PipelineProfile.SHORT: False,
        PipelineProfile.STANDARD: True,
        PipelineProfile.LONG_FORM: True,
    }


def test_sweep_overrides_merge_with_defaults():
    cfg = build_config(profiles={"sweep": {"short": True}})
    assert cfg.profiles.sweep[PipelineProfile.SHORT] is True
    assert cfg.profiles.sweep[PipelineProfile.LONG_FORM] is True


def test_database_url_comes_from_the_environment(monkeypatch):
    cfg = build_config(database={"url_env": "MY_DB"})
    monkeypatch.delenv("MY_DB", raising=False)
    assert cfg.database.url() is None
    monkeypatch.setenv("MY_DB", "postgresql://u:p@h/db")
    assert cfg.database.url() == "postgresql://u:p@h/db"
    monkeypatch.setenv("MY_DB", "")
    assert cfg.database.url() is None


def test_null_sections_are_accepted(tmp_path):
    cfg, _ = load_config(_write(tmp_path, database=None, storage=None, worker=None))
    assert cfg.storage.path == Path("/data/files")


def test_relative_storage_path_resolves_against_the_config_file(tmp_path):
    cfg, _ = load_config(_write(tmp_path, storage={"kind": "local", "path": "files"}))
    assert cfg.storage.path == tmp_path / "files"


def test_spec_storage_section_is_accepted(tmp_path):
    cfg, _ = load_config(_write(tmp_path, storage={"kind": "local", "path": "/data/files"}))
    assert cfg.storage.path == Path("/data/files")


def test_unknown_storage_kind_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="storage"):
        load_config(_write(tmp_path, storage={"kind": "s3", "path": "x"}))


def test_short_threshold_must_not_exceed_standard():
    cfg = build_config(profiles={"short_max_words": 5000, "standard_max_words": 1000})
    messages = [i.message for i in validate_config(cfg) if i.level == "error"]
    assert "profiles.short_max_words must not exceed profiles.standard_max_words" in messages


def test_worker_limits_are_validated(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, worker={"concurrency": 0}))
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, worker={"stalled_after_s": 29}))
    # procrastinate heartbeats every 10 s; a live worker must never look stalled
    cfg, _ = load_config(_write(tmp_path, worker={"stalled_after_s": 30}))
    assert cfg.worker.stalled_after_s == 30
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, worker={"scorer_wait_s": -1}))
    cfg, _ = load_config(_write(tmp_path, worker={"scorer_wait_s": 0}))
    assert cfg.worker.scorer_wait_s == 0
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, worker={"burst_scorer_wait_s": -1}))
    cfg, _ = load_config(_write(tmp_path, worker={"burst_scorer_wait_s": 300}))
    assert cfg.worker.burst_scorer_wait_s == 300


def test_the_scorer_wait_does_not_change_the_config_hash():
    from coglossa.config import config_hash

    assert config_hash(build_config()) == config_hash(build_config(worker={"scorer_wait_s": 0}))
    burst = build_config(worker={"burst_scorer_wait_s": 300})
    assert config_hash(build_config()) == config_hash(burst)


def test_config_hash_does_not_depend_on_how_the_path_is_spelled(tmp_path, monkeypatch):
    from coglossa.config import config_hash

    (tmp_path / "prompts").mkdir()
    (tmp_path / "tiers.yaml").write_text("{}\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    path = _write(
        tmp_path,
        prompts_dir="prompts",
        scoring={"language_tiers_file": "tiers.yaml"},
        storage={"path": "files"},
    )
    monkeypatch.chdir(sub)
    spellings = [path, Path("../coglossa.yaml"), sub / ".." / "coglossa.yaml"]
    loaded = [load_config(p)[0] for p in spellings]
    assert len({config_hash(cfg) for cfg in loaded}) == 1
    first = loaded[0]
    assert first.prompts_dir == (tmp_path / "prompts").resolve()
    assert first.scoring.language_tiers_file == (tmp_path / "tiers.yaml").resolve()
    assert first.storage.path == (tmp_path / "files").resolve()
    assert all(cfg == first for cfg in loaded)


def test_storage_kind_defaults_to_local_and_s3_is_accepted(make_config, monkeypatch):
    assert make_config().storage.kind == "local"
    assert make_config(storage={"path": "/x"}).storage.kind == "local"
    monkeypatch.setenv("COGLOSSA_S3_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("COGLOSSA_S3_SECRET_ACCESS_KEY", "s")
    cfg = make_config(storage={"kind": "s3", "bucket": "bkt", "endpoint_url": "https://e/s3"})
    assert cfg.storage.kind == "s3" and cfg.storage.bucket == "bkt"
    assert cfg.storage.region == "us-east-1" and cfg.storage.prefix == ""


def test_s3_storage_rejects_unknown_fields_and_kinds(make_config):
    with pytest.raises(ValidationError):
        make_config(storage={"kind": "s3", "bucket": "bkt", "path": "/x"})
    with pytest.raises(ValidationError):
        make_config(storage={"kind": "gcs"})


def test_s3_storage_env_vars_must_be_set(tmp_path, monkeypatch):
    monkeypatch.delenv("COGLOSSA_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("COGLOSSA_S3_SECRET_ACCESS_KEY", raising=False)
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump({**BASE_CONFIG, "storage": {"kind": "s3", "bucket": "bkt"}}),
        encoding="utf-8",
    )
    with pytest.raises(
        ConfigError, match="storage: environment variable COGLOSSA_S3_ACCESS_KEY_ID"
    ):
        load_config(path)


def test_s3_storage_is_not_path_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("COGLOSSA_S3_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("COGLOSSA_S3_SECRET_ACCESS_KEY", "s")
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump({**BASE_CONFIG, "storage": {"kind": "s3", "bucket": "bkt"}}),
        encoding="utf-8",
    )
    cfg, _ = load_config(path)
    assert cfg.storage.kind == "s3" and cfg.storage.bucket == "bkt"


def test_worker_kick_url_is_off_by_default_and_must_be_http(make_config):
    assert make_config().worker.kick_url is None
    assert make_config(worker={"kick_url": "https://k/x"}).worker.kick_url == "https://k/x"
    with pytest.raises(ValidationError):
        make_config(worker={"kick_url": "ftp://k/x"})


def test_worker_kick_token_env_must_be_set(tmp_path, monkeypatch):
    monkeypatch.delenv("COGLOSSA_KICK_TOKEN", raising=False)
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                **BASE_CONFIG,
                "worker": {"kick_url": "https://k/", "kick_token_env": "COGLOSSA_KICK_TOKEN"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        ConfigError, match="worker: environment variable COGLOSSA_KICK_TOKEN is not set"
    ):
        load_config(path)
    monkeypatch.setenv("COGLOSSA_KICK_TOKEN", "t")
    cfg, _ = load_config(path)
    assert cfg.worker.kick_token_env == "COGLOSSA_KICK_TOKEN"


def test_extra_headers_are_off_by_default(make_config):
    cfg = make_config(scorer={"url": "http://s/"})
    assert cfg.scorer.extra_headers_env == {} and cfg.worker.extra_headers_env == {}


@pytest.mark.parametrize("section", ["scorer", "worker"])
def test_extra_header_names_must_be_http_tokens(make_config, section):
    body = {"url": "http://s/"} if section == "scorer" else {}
    body["extra_headers_env"] = {"Modal Key": "K"}
    with pytest.raises(ValidationError, match="'Modal Key' is not an HTTP header name"):
        make_config(**{section: body})


@pytest.mark.parametrize("section", ["scorer", "worker"])
def test_extra_header_variables_must_be_set(tmp_path, monkeypatch, section):
    monkeypatch.delenv("PLATFORM_SECRET", raising=False)
    monkeypatch.setenv("PLATFORM_KEY", "k")
    headers = {"Modal-Key": "PLATFORM_KEY", "Modal-Secret": "PLATFORM_SECRET"}
    body = {"extra_headers_env": headers}
    if section == "scorer":
        body["url"] = "http://s/"
    path = _write(tmp_path, **{section: body})
    with pytest.raises(
        ConfigError, match=f"{section}: environment variable PLATFORM_SECRET is not set"
    ) as raised:
        load_config(path)
    assert "PLATFORM_KEY" not in str(raised.value)
    monkeypatch.setenv("PLATFORM_SECRET", "s")
    cfg, _ = load_config(path)
    assert getattr(cfg, section).extra_headers_env == headers


def test_scorer_credentials_do_not_change_the_config_hash(monkeypatch):
    from coglossa.config import config_hash

    plain = build_config(scorer={"url": "http://s/"})
    with_credentials = build_config(
        scorer={
            "url": "http://s/",
            "api_key_env": "SCORER_TOKEN",
            "extra_headers_env": {"Modal-Key": "PLATFORM_KEY"},
        }
    )
    assert config_hash(plain) == config_hash(with_credentials)
    assert config_hash(plain) != config_hash(build_config(scorer={"url": "http://other/"}))
