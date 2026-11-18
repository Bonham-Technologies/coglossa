"""set owners saved under a name misread as latin-1 get their name back (CGL-75)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-12 12:00:00.000000

"""

import logging
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("coglossa.migrations")


def _repaired(name: str) -> str:
    """Before CGL-31 the proxy's identity header was read as latin-1, so "José", sent in UTF-8,
    was saved as "JosÃ©". Such a name goes back to what the proxy sent, in NFC, which is how the
    API reads the same bytes now (coglossa_api.auth.identity_header). A name with a character
    outside latin-1 (saved since CGL-31), or whose latin-1 bytes are not UTF-8 (a proxy that
    sends latin-1), is no misreading and stays as it is; so does ASCII, which reads the same
    either way. Running it again (after a downgrade) leaves repaired names alone, except one
    that itself looks like mojibake (made of pairs such as "Ã©"), which no real name does."""
    try:
        sent = name.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return name
    return unicodedata.normalize("NFC", sent) if sent != name else name


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, created_by FROM project WHERE created_by IS NOT NULL")
    ).all()
    changed = 0
    for project_id, name in rows:
        fixed = _repaired(name)
        if fixed != name:
            conn.execute(
                sa.text("UPDATE project SET created_by = :fixed WHERE id = :id"),
                {"fixed": fixed, "id": project_id},
            )
            changed += 1
    log.info("repaired %d set owner names saved misread as latin-1", changed)


def downgrade() -> None:
    # Deliberately a no-op: the repaired names are the ones people sign in with, and code from
    # before this revision (since CGL-31) reads names the same way, so there is nothing to undo.
    pass
