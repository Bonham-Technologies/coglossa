import pytest
from coglossa.testing.postgres import create_test_database, drop_test_database
from fastapi import FastAPI

from coglossa_api import main as main_module
from coglossa_api.main import main


@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module.uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    return calls


def test_main_serves_the_app(write_config, served):
    path = write_config()
    assert main(["--config", str(path), "--host", "0.0.0.0", "--port", "9000"]) == 0
    [(app, options)] = served
    assert isinstance(app, FastAPI)
    assert options == {"host": "0.0.0.0", "port": 9000}


def test_defaults_listen_on_localhost(write_config, served, monkeypatch, tmp_path):
    write_config()
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    assert served[0][1] == {"host": "127.0.0.1", "port": 8080}


def test_missing_config_refuses_to_start(tmp_path, served, capsys):
    assert main(["--config", str(tmp_path / "nope.yaml")]) == 1
    assert "error: config file not found" in capsys.readouterr().err
    assert served == []


def test_unmigrated_database_refuses_to_start(write_config, served, capsys, pg_admin, monkeypatch):
    empty = create_test_database(pg_admin)
    try:
        monkeypatch.setenv("COGLOSSA_DATABASE_URL", empty)
        assert main(["--config", str(write_config())]) == 1
    finally:
        drop_test_database(pg_admin, empty)
    assert "coglossa db upgrade" in capsys.readouterr().err
    assert served == []


def test_unwritable_storage_refuses_to_start(write_config, served, capsys, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    path = write_config(storage={"path": str(blocker / "files")})
    assert main(["--config", str(path)]) == 1
    assert "not writable" in capsys.readouterr().err
    assert served == []


def test_a_web_dir_without_index_refuses_to_start(write_config, served, capsys, tmp_path):
    path = write_config(api={"web_dir": str(tmp_path / "empty")})
    assert main(["--config", str(path)]) == 1
    assert capsys.readouterr().err.strip() == (
        f"error: api.web_dir {tmp_path / 'empty'} has no index.html; build the web app "
        "(cd web && pnpm build) or remove api.web_dir"
    )
    assert served == []


def test_config_warnings_are_printed(write_config, served, capsys):
    single = {
        "models": {"model-a": {"backend": "fake", "context_window": 32768}},
        "roles": {
            "literal_translator": {"model": "model-a"},
            "contextual_translator": {"model": "model-a"},
            "reviewer": {"model": "model-a"},
            "profiler_guide": {"model": "model-a"},
            "judge": {"model": "model-a"},
        },
    }
    assert main(["--config", str(write_config(**single))]) == 0
    assert "warning: judge must use a different model" in capsys.readouterr().err


@pytest.fixture
def built(monkeypatch):
    """The configs `main` hands to create_app."""
    configs = []
    real = main_module.create_app

    def spy(cfg, path, **kw):
        configs.append(cfg)
        return real(cfg, path, **kw)

    monkeypatch.setattr(main_module, "create_app", spy)
    return configs


def test_web_dir_overrides_the_configured_one(write_config, served, built, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html></html>", encoding="utf-8")
    assert main(["--config", str(write_config()), "--web-dir", str(web)]) == 0
    assert built[0].api.web_dir == web.resolve()


def test_a_web_dir_argument_without_index_refuses_to_start(write_config, served, capsys, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["--config", str(write_config()), "--web-dir", str(empty)]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"error: api.web_dir {empty.resolve()} has no index.html")
    assert served == []


def test_the_config_defaults_to_the_environment(write_config, served, built, monkeypatch, tmp_path):
    path = write_config(api={"progress_poll_s": 0.5})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("COGLOSSA_CONFIG", str(path))
    assert main([]) == 0
    assert built[0].api.progress_poll_s == 0.5


NAMED_USERS = {"mode": "trusted_header", "header": "X-Remote-User"}
EXPOSED = "warning: named users (auth.mode: trusted_header) without auth.proxy_secret_env"


def _start(write_config, monkeypatch, capsys, host, bind=None, **auth) -> list[str]:
    """CGL-61: starts coglossa-api on `host` with COGLOSSA_HTTP_BIND (where compose publishes
    the app) set to `bind`, or unset when None; returns the exposure warnings it printed."""
    if bind is None:
        monkeypatch.delenv("COGLOSSA_HTTP_BIND", raising=False)
    else:
        monkeypatch.setenv("COGLOSSA_HTTP_BIND", bind)
    path = write_config(auth={**NAMED_USERS, **auth})
    assert main(["--config", str(path), "--host", host]) == 0
    return [line for line in capsys.readouterr().err.splitlines() if line.startswith(EXPOSED)]


@pytest.mark.parametrize(
    ("host", "bind"),
    [
        ("0.0.0.0", None),  # a bare host, every interface
        ("0.0.0.0", "0.0.0.0"),  # compose's default publish address
        ("0.0.0.0", ""),  # empty: compose falls back to 0.0.0.0 too
        ("0.0.0.0", "192.168.1.20"),  # published on one LAN address
        ("10.0.0.5", None),
        ("10.0.0.5", "127.0.0.1"),  # listening on a LAN address: reachable there regardless
    ],
)
def test_named_users_reachable_without_the_proxy_warn_once(
    write_config, served, monkeypatch, capsys, host, bind
):
    [line] = _start(write_config, monkeypatch, capsys, host, bind)
    assert "anyone who can reach" in line and "any user name" in line
    assert "X-Coglossa-Proxy-Secret" in line and "COGLOSSA_HTTP_BIND=127.0.0.1" in line
    assert served  # a warning, not a refusal


@pytest.mark.parametrize(
    ("host", "bind"),
    [
        ("127.0.0.1", None),  # the default: only this machine
        ("::1", None),
        ("localhost", "0.0.0.0"),  # listening on loopback: nothing else can connect
        ("0.0.0.0", "127.0.0.1"),  # in a container, published on loopback only
        ("0.0.0.0", "[::1]"),
    ],
)
def test_named_users_on_loopback_do_not_warn(write_config, served, monkeypatch, capsys, host, bind):
    assert _start(write_config, monkeypatch, capsys, host, bind) == []
    assert served


def test_a_proxy_secret_silences_the_warning(write_config, served, monkeypatch, capsys):
    monkeypatch.setenv("COGLOSSA_PROXY_SECRET", "s3cret-for-the-test")
    warnings = _start(
        write_config, monkeypatch, capsys, "0.0.0.0", proxy_secret_env="COGLOSSA_PROXY_SECRET"
    )
    assert warnings == []


def test_no_warning_without_named_users(write_config, served, monkeypatch, capsys):
    monkeypatch.delenv("COGLOSSA_HTTP_BIND", raising=False)
    assert main(["--config", str(write_config()), "--host", "0.0.0.0"]) == 0
    assert "proxy_secret_env" not in capsys.readouterr().err


def test_the_warning_names_the_header_the_middleware_checks():
    from coglossa.config import PROXY_SECRET_HEADER as named_in_warnings

    from coglossa_api.proxy import PROXY_SECRET_HEADER

    assert named_in_warnings == PROXY_SECRET_HEADER
