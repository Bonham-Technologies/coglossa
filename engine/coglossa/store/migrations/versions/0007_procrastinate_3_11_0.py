"""procrastinate 3.11.0: fetching a job skips the lock check for jobs without a lock (CGL-106)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20 12:00:00.000000

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# procrastinate 3.11.0's only migration, vendored verbatim from the package's
# sql/migrations/ so this revision never changes. It replaces procrastinate_fetch_job_v2 and
# runs before a 3.11 worker starts ("pre"); a 3.10 worker works with either version.
_UPGRADE = Path(__file__).with_name(
    "0007_procrastinate_03.11.00_01_pre_fetch_job_skip_null_lock.sql"
)
# The function as procrastinate 3.10.0 defined it, from 0001_procrastinate_3_10_0.sql.
_DOWNGRADE = Path(__file__).with_name("0007_procrastinate_3_10_0_fetch_job_v2.sql")


def _raw(sql: str) -> None:
    # The raw psycopg connection, as in revision 0001: procrastinate's SQL is not written for
    # SQLAlchemy's placeholder parsing. It runs inside the migration's transaction.
    op.get_bind().connection.driver_connection.execute(sql)


def upgrade() -> None:
    _raw(_UPGRADE.read_text(encoding="utf-8"))


def downgrade() -> None:
    _raw(_DOWNGRADE.read_text(encoding="utf-8"))
