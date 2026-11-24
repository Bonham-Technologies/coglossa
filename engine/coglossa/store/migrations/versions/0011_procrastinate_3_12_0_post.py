"""procrastinate 3.12.0, post-migrations: drop what only 3.11 and older called (CGL-106)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# procrastinate 3.12.0's post-migrations, vendored verbatim. "Post" means no 3.11 or older
# worker or API may still be running: the first drops procrastinate_defer_jobs_v1, its
# composite type and procrastinate_defer_periodic_job_v2, which such code calls to queue a job;
# the second drops the (id, lock) index that revision 0010's index replaces. That one is a
# DROP INDEX CONCURRENTLY, so it runs in an autocommit block, which commits the first. Both use
# IF EXISTS, so a run that stopped between them can run again.
_UPGRADE = Path(__file__).with_name("0011_procrastinate_03.12.00_50_post_add_lock_mode.sql")
_UPGRADE_CONCURRENTLY = Path(__file__).with_name(
    "0011_procrastinate_03.12.00_51_post_drop_id_lock_index.sql"
)
# The dropped objects as they stood after revision 0010.
_DOWNGRADE = Path(__file__).with_name("0011_procrastinate_3_12_0_pre_objects.sql")


def _raw(sql: str) -> None:
    # The raw psycopg connection, as in revision 0001.
    op.get_bind().connection.driver_connection.execute(sql)


def upgrade() -> None:
    _raw(_UPGRADE.read_text(encoding="utf-8"))
    with op.get_context().autocommit_block():
        _raw(_UPGRADE_CONCURRENTLY.read_text(encoding="utf-8"))


def downgrade() -> None:
    _raw(_DOWNGRADE.read_text(encoding="utf-8"))
