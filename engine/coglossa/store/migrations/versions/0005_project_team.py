"""a set's team and the person who made it (CGL-26)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing sets get neither: only admins see them until one moves them into a team.
    op.add_column("project", sa.Column("team", sa.Text(), nullable=True))
    op.add_column("project", sa.Column("created_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("project", "created_by")
    op.drop_column("project", "team")
