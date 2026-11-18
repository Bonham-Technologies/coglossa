"""Pytest plugin with the Postgres fixtures shared by the engine and API test suites; load it
with `pytest_plugins = ["coglossa.testing.pytest_db"]`. The database imports stay inside the
fixtures so tests that need no database never import SQLAlchemy or psycopg."""

import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from coglossa.store.db import Database


def admin_url() -> str:
    """The admin URL from the environment, once Postgres there answers. A database that is down
    stops the whole session with one line rather than an error for every test."""
    from coglossa.testing.postgres import TEST_DB_ENV, unreachable_message

    admin = os.environ.get(TEST_DB_ENV)
    if not admin:
        pytest.fail(
            f"{TEST_DB_ENV} is not set. Start Postgres with `make db-up` and set "
            f"{TEST_DB_ENV}=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres "
            "(`make test-engine` and `make test-api` do both).",
            pytrace=False,
        )
    message = unreachable_message(admin)
    if message:
        pytest.exit(message, returncode=pytest.ExitCode.INTERRUPTED)
    return admin


@pytest.fixture(scope="session")
def pg_admin() -> str:
    return admin_url()


@pytest.fixture(scope="session")
def pg_url(pg_admin) -> Iterator[str]:
    from coglossa.store.migrate import upgrade
    from coglossa.testing.postgres import create_test_database, drop_test_database

    url = create_test_database(pg_admin)
    upgrade(url)
    yield url
    drop_test_database(pg_admin, url)


@pytest.fixture
async def db(pg_url) -> AsyncIterator["Database"]:
    from coglossa.store.db import Database
    from coglossa.testing.postgres import truncate_all

    database = Database(pg_url)
    await truncate_all(database)
    yield database
    await database.dispose()
