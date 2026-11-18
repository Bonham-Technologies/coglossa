"""Throwaway Postgres databases for tests of the engine and the API. A test session creates
one database from the admin URL in COGLOSSA_TEST_DATABASE_URL, migrates it and drops it at the
end; `truncate_all` empties it between tests. `python -m coglossa.testing.postgres` checks the
database is up before a suite starts (the Makefile runs it), so a stopped server costs one line
instead of an error for every test."""

import os
import sys
import uuid

import psycopg
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url

from coglossa.store.db import Database, error_reason, redact, to_libpq_url
from coglossa.store.models import Base

TEST_DB_ENV = "COGLOSSA_TEST_DATABASE_URL"
PROCRASTINATE_TABLES = (
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_jobs",
    "procrastinate_workers",
)
CHECK_TIMEOUT_S = 3
HOW_TO_START = "Start it with `make db-up`, or in a cloud session `pg_ctlcluster 16 main start`."


def unreachable_message(admin: str, timeout_s: int = CHECK_TIMEOUT_S) -> str | None:
    """None when Postgres at the admin URL takes a connection within timeout_s; otherwise one
    line naming the URL (password masked), the driver's reason and how to start it."""
    try:
        with psycopg.connect(to_libpq_url(admin), connect_timeout=timeout_s):
            return None
    except psycopg.Error as e:
        reason = error_reason(e)
    return f"The test database at {redact(admin)} is not reachable ({reason}). {HOW_TO_START}"


def main() -> int:
    admin = os.environ.get(TEST_DB_ENV)
    message = unreachable_message(admin) if admin else f"{TEST_DB_ENV} is not set. {HOW_TO_START}"
    if message:
        print(message, file=sys.stderr)
        return 1
    return 0


def create_test_database(admin: str) -> str:
    name = f"coglossa_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(to_libpq_url(admin), autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return make_url(admin).set(database=name).render_as_string(hide_password=False)


def drop_test_database(admin: str, url: str) -> None:
    name = make_url(url).database
    with psycopg.connect(to_libpq_url(admin), autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


async def truncate_all(db: Database) -> None:
    tables = [t.name for t in Base.metadata.sorted_tables] + list(PROCRASTINATE_TABLES)
    async with db.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))


if __name__ == "__main__":
    sys.exit(main())
