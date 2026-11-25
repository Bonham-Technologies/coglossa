"""passages left as written: code and text marked not to translate (CGL-111)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-25 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("span", sa.Column("kept", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("span", "kept")
