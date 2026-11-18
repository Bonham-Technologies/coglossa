"""project defaults for new runs

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("target_lang", "source_lang", "pipeline_profile")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("project", sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("project", name)
