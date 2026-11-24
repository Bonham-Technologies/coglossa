"""procrastinate 3.12.0, pre-migration: an index on the lock of waiting and running jobs (CGL-106)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# procrastinate 3.12.0's third pre-migration, vendored verbatim: one CREATE INDEX CONCURRENTLY,
# which Postgres refuses inside a transaction. It is a revision of its own, run in an autocommit
# block (which first commits the revisions before it), so a failed build never leaves revision
# 0009 half applied. The (id, lock) index it replaces stays until revision 0011.
_UPGRADE = Path(__file__).with_name(
    "0010_procrastinate_03.12.00_03_pre_add_todo_doing_lock_index.sql"
)
_INDEX = "procrastinate_jobs_todo_doing_lock_idx_v1"

# An interrupted concurrent build leaves an invalid index of that name behind, and building it
# again fails; a build that finished before the revision was recorded leaves a valid one.
_EXISTING_INDEX = f"SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass('{_INDEX}')"


def _driver():
    # The raw psycopg connection, as in revision 0001.
    return op.get_bind().connection.driver_connection


def upgrade() -> None:
    with op.get_context().autocommit_block():
        existing = _driver().execute(_EXISTING_INDEX).fetchone()
        if existing is not None and existing[0]:
            return  # an earlier attempt built it, then stopped before recording this revision
        if existing is not None:
            _driver().execute(f"DROP INDEX CONCURRENTLY {_INDEX}")
        _driver().execute(_UPGRADE.read_text(encoding="utf-8"))


def downgrade() -> None:
    _driver().execute(f"DROP INDEX {_INDEX}")
