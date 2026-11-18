"""each guide version belongs to one target language of its project (CGL-24)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# One spelling per language in each project (codes compare case-insensitively, with - and _
# alike): the project's default, else its earliest run's. Runs first, so the guide versions
# below take their run's unified spelling.
_KEY = "lower(replace({}, '_', '-'))"
_UNIFY_RUNS = f"""
    WITH candidates AS (
        SELECT p.id AS project_id, p.target_lang AS lang, 0 AS rank, p.created_at AS at
        FROM project p WHERE p.target_lang IS NOT NULL
        UNION ALL
        SELECT d.project_id, r.target_lang, 1, r.created_at
        FROM run r JOIN document d ON d.id = r.document_id
    ), canonical AS (
        SELECT DISTINCT ON (project_id, {_KEY.format("lang")})
            project_id, {_KEY.format("lang")} AS key, lang
        FROM candidates
        ORDER BY project_id, {_KEY.format("lang")}, rank, at
    )
    UPDATE run r SET target_lang = c.lang
    FROM document d, canonical c
    WHERE d.id = r.document_id AND c.project_id = d.project_id
      AND c.key = {_KEY.format("r.target_lang")} AND r.target_lang <> c.lang
"""

# The first rule that gives a language wins: the version's own run, the project's default, the
# project's earliest run. "und" (undetermined) is left only for a guide saved by hand in a CLI
# project with no runs and no default; no run ever reads it.
_BACKFILL = (
    """UPDATE guide_version g SET target_lang = r.target_lang
       FROM run r WHERE r.id = g.source_run_id""",
    """UPDATE guide_version g SET target_lang = p.target_lang
       FROM project p
       WHERE g.target_lang IS NULL AND p.id = g.project_id AND p.target_lang IS NOT NULL""",
    """UPDATE guide_version g SET target_lang = (
         SELECT r.target_lang FROM run r JOIN document d ON d.id = r.document_id
         WHERE d.project_id = g.project_id ORDER BY r.created_at, r.id LIMIT 1)
       WHERE g.target_lang IS NULL""",
    "UPDATE guide_version SET target_lang = 'und' WHERE target_lang IS NULL",
)


def upgrade() -> None:
    op.add_column("guide_version", sa.Column("target_lang", sa.Text(), nullable=True))
    op.execute(sa.text(_UNIFY_RUNS))
    for statement in _BACKFILL:
        op.execute(sa.text(statement))
    op.alter_column("guide_version", "target_lang", nullable=False)
    op.drop_constraint("guide_version_project_id_version_key", "guide_version", type_="unique")
    op.create_unique_constraint(
        "guide_version_project_id_target_lang_version_key",
        "guide_version",
        ["project_id", "target_lang", "version"],
    )


def downgrade() -> None:
    # Works only while no two languages of a project share a version number (before a second
    # language has a guide); otherwise the old constraint cannot be restored.
    op.drop_constraint(
        "guide_version_project_id_target_lang_version_key", "guide_version", type_="unique"
    )
    op.create_unique_constraint(
        "guide_version_project_id_version_key", "guide_version", ["project_id", "version"]
    )
    op.drop_column("guide_version", "target_lang")
