"""Who sees which sets (CGL-26). Admins see every set; others the sets of their teams and the
personal sets they made. Names and teams compare as people.team_key says (NFC, A-Z in any case)."""

import logging
import re
import string
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import and_, false, func, or_, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from coglossa.people import (
    NAME_SPACE,
    TEAM_NAME_PATTERN,
    People,
    TeamFilter,
    strip_name,
    team_key,
)
from coglossa.store import runs
from coglossa.store.models import Project

log = logging.getLogger("coglossa.access")
_TEAM_NAME = re.compile(TEAM_NAME_PATTERN)


@dataclass(frozen=True)
class Viewer:
    name: str | None
    teams: frozenset[str]  # team_key of each team
    admin: bool
    team_names: tuple[str, ...] = ()  # as spelled, sorted, for display


EVERYONE = Viewer(name=None, teams=frozenset(), admin=True)  # auth.mode none, and the CLI


def viewer_for(
    name: str, groups: Iterable[str], people: People, admin_group: str, *, teams: TeamFilter
) -> Viewer:
    """The proxy's groups and the people file's teams, united; the admin group is no team.
    A group a set could not carry as its team (a path, an LDAP name, accents, over 64
    characters) is left out, so it never becomes a team nobody can make sets in. So is one
    `teams` (auth.team_groups_prefix, auth.team_groups) does not allow (CGL-74): this is where
    the filter applies, so access checks, /me and the team picker see the same teams. The
    admin group still makes its members admins, whatever the filter."""
    listed = [strip_name(g) for g in groups if strip_name(g)]
    admin_key = team_key(admin_group)
    admin = people.is_admin(name) or any(team_key(g) == admin_key for g in listed)
    kept: dict[str, str] = {}
    for team in [*listed, *people.teams_of(name)]:
        if team_key(team) == admin_key or not teams.allows(team):
            continue  # a broad directory group ("Domain Users"): not worth a log line each time
        if not _TEAM_NAME.fullmatch(team):
            log.warning("group %r of %r is not a usable team name; ignored", team, name)
            continue
        kept.setdefault(team_key(team), team)
    names = tuple(sorted(kept.values(), key=team_key))
    return Viewer(name=name, teams=frozenset(kept), admin=admin, team_names=names)


def can_see(viewer: Viewer, project: Project) -> bool:
    if viewer.admin:
        return True
    if project.team is not None:
        return team_key(project.team) in viewer.teams
    return (
        viewer.name is not None
        and project.created_by is not None
        and team_key(project.created_by) == team_key(viewer.name)
    )


def _sql_key(column: ColumnElement[str | None]) -> ColumnElement[str]:
    """team_key in SQL: trimmed of the same whitespace, A-Z folded with translate(), which,
    unlike lower(), does the same under every collation. Stored names are already NFC
    (catalog.create_project; names misread as latin-1 before CGL-31 are repaired to NFC by
    revision 0006, CGL-75)."""
    trimmed = func.btrim(column, NAME_SPACE)
    return func.translate(trimmed, string.ascii_uppercase, string.ascii_lowercase)


def visible(viewer: Viewer) -> ColumnElement[bool]:
    """can_see as SQL on Project; the two must agree (tests/test_access.py)."""
    if viewer.admin:
        return true()
    conditions: list[ColumnElement[bool]] = []
    if viewer.teams:
        conditions.append(_sql_key(Project.team).in_(sorted(viewer.teams)))
    if viewer.name is not None:
        conditions.append(
            and_(Project.team.is_(None), _sql_key(Project.created_by) == team_key(viewer.name))
        )
    return or_(*conditions) if conditions else false()


async def check_visible(
    s: AsyncSession,
    viewer: Viewer,
    *,
    project_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> None:
    """Refuses, with the message a missing id gets, what the viewer cannot see. The most
    specific id decides (a run's set, else a document's, else the project)."""
    if viewer.admin:
        return
    if run_id is not None:
        run = await runs.get_run(s, run_id)
        document = await runs.get_document(s, run.document_id)
        project_id, missing = document.project_id, f"run {run_id} not found"
    elif document_id is not None:
        document = await runs.get_document(s, document_id)
        project_id, missing = document.project_id, f"document {document_id} not found"
    elif project_id is not None:
        missing = f"project {project_id} not found"
    else:
        return
    if not can_see(viewer, await runs.get_project(s, project_id)):
        raise runs.NotFoundError(missing)
