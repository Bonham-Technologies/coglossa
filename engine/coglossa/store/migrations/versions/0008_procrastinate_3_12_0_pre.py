"""procrastinate 3.12.0, pre-migrations: lock modes, and a retry that never strands a lock (CGL-106)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# procrastinate 3.12.0's first two pre-migrations, vendored verbatim from the package's
# sql/migrations/ so this revision never changes. "Pre" means a 3.10 or 3.11 worker or API
# still works against the result: they add the lock_mode column, procrastinate_defer_jobs_v2
# and procrastinate_defer_periodic_job_v3 beside the functions older code calls, and replace
# procrastinate_fetch_job_v2 and procrastinate_retry_job_v2 keeping their signatures. The
# third pre-migration builds an index concurrently, outside a transaction: revision 0009.
_UPGRADE = [
    Path(__file__).with_name("0008_procrastinate_03.12.00_01_pre_add_lock_mode.sql"),
    Path(__file__).with_name(
        "0008_procrastinate_03.12.00_02_pre_retry_job_fail_on_queueing_lock_conflict.sql"
    ),
]
# procrastinate 3.11.0's objects: its procrastinate_fetch_job_v2 as revision 0007 installed it,
# then the rest.
_DOWNGRADE = [
    Path(__file__).with_name("0007_procrastinate_03.11.00_01_pre_fetch_job_skip_null_lock.sql"),
    Path(__file__).with_name("0008_procrastinate_3_11_0_objects.sql"),
]


def _raw(sql: str) -> None:
    # The raw psycopg connection, as in revision 0001: procrastinate's SQL is not written for
    # SQLAlchemy's placeholder parsing. It runs inside the migration's transaction.
    op.get_bind().connection.driver_connection.execute(sql)


def upgrade() -> None:
    for path in _UPGRADE:
        _raw(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    for path in _DOWNGRADE:
        _raw(path.read_text(encoding="utf-8"))
