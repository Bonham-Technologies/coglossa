"""the project-guide version each guide-stage draft was built from

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("guide_version", sa.Column("built_from_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("guide_version", "built_from_version")
