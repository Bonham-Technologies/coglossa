"""The test database check: a suite whose database is down stops at once with one line
(CGL-41), instead of erroring every test."""

import socket

import pytest

from coglossa.testing import postgres
from coglossa.testing.pytest_db import admin_url


def _closed_port() -> int:
    """A local port nothing listens on: bound once to pick it, then released."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def down_url() -> str:
    return f"postgresql://coglossa:s3cret-pw@127.0.0.1:{_closed_port()}/postgres"


def test_an_unreachable_database_gets_one_line_without_the_password(down_url):
    message = postgres.unreachable_message(down_url)
    assert message is not None
    assert "\n" not in message
    assert "s3cret-pw" not in message
    assert "postgresql://coglossa:***@127.0.0.1:" in message
    assert "is not reachable" in message
    assert "make db-up" in message
    assert "pg_ctlcluster 16 main start" in message


def test_a_reachable_database_passes(pg_admin):
    assert postgres.unreachable_message(pg_admin) is None


def test_main_exits_with_the_line_when_the_database_is_down(down_url, monkeypatch, capsys):
    monkeypatch.setenv(postgres.TEST_DB_ENV, down_url)
    assert postgres.main() == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "is not reachable" in err and "s3cret-pw" not in err


def test_main_names_the_variable_when_it_is_unset(monkeypatch, capsys):
    monkeypatch.delenv(postgres.TEST_DB_ENV, raising=False)
    assert postgres.main() == 1
    assert f"{postgres.TEST_DB_ENV} is not set" in capsys.readouterr().err


def test_main_passes_when_the_database_is_up(pg_admin, monkeypatch, capsys):
    monkeypatch.setenv(postgres.TEST_DB_ENV, pg_admin)
    assert postgres.main() == 0
    assert capsys.readouterr().err == ""


def test_the_pytest_fixture_stops_the_session_when_the_database_is_down(down_url, monkeypatch):
    monkeypatch.setenv(postgres.TEST_DB_ENV, down_url)
    with pytest.raises(pytest.exit.Exception, match="is not reachable"):
        admin_url()


def test_the_pytest_fixture_fails_when_the_variable_is_unset(monkeypatch):
    monkeypatch.delenv(postgres.TEST_DB_ENV, raising=False)
    with pytest.raises(pytest.fail.Exception, match="is not set"):
        admin_url()
