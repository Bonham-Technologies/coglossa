# Teams, Admins and Who Sees Which Sets (CGL-26) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sets belong to a team (or to the person who made them); people see only their teams' sets and their own, admins see everything and alone get the operator screens.

**Architecture:** The engine gains a people file reader (`coglossa/people.py`) and the access rule (`coglossa/access.py`: `Viewer`, `can_see`, `visible`, `check_visible`). Projects gain `team` and `created_by`. The API turns each request into a `Viewer` (header + optional groups header + people file) and enforces access with **one router-level dependency** that checks any `run_id` / `document_id` / `project_id` path parameter, so no route can forget it; list endpoints filter in SQL. The web app reads `admin` and `teams` from `/api/me`.

**Tech Stack:** Python 3.12, SQLAlchemy 2 async, Alembic, FastAPI, pytest (Postgres via `make db-up`); React 19, TanStack Query, Vitest 4 + MSW, Playwright; the Vercel front door (TypeScript, Vitest).

**Spec:** `docs/superpowers/specs/2026-10-18-user-roles-design.md`

## Global Constraints

- `auth.mode: none` behaves exactly as today: everyone is an admin, nothing is filtered; every existing test keeps passing unchanged in that mode.
- A set a viewer cannot see answers 404 with the **same body** as a missing id ("run <id> not found", "document <id> not found", "project <id> not found").
- Admin-only: `/api/system/doctor`, `/api/system/config`, `/api/teams`, and changing a set's `team`; they answer 403 "only an administrator can …" for others. `/api/system/tiers` stays open.
- `auth.groups_header` defaults to **unset**; when unset the groups header is ignored even if a client sends it.
- Default admin group: `coglossa-admins`. Team names: 1–64 characters of letters, digits, space, `.`, `_`, `-`. Names and teams compare case-insensitively (`str.lower()` in Python, `lower()` in SQL).
- The command line keeps full access (it never builds a `Viewer`; catalog functions default to `EVERYONE`).
- Web copy is plain words for non-technical users; colours only from CSS tokens.
- Engine and API tests run through `make` (`make test-engine`, `make test-api`) or `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest …` after `make db-up`.
- After a model or route change: `make schema` (CI checks `schema/openapi.json` and `web/src/api/schema.d.ts`), and the web fixtures must typecheck in the same commit.
- Coverage gates ≥ 95% (engine, web, hosted front door).
- Commits: conventional subjects, ending with
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012TBMbjzsQyykrdRBR7Vuv5
  ```

## Review Focus

1. **A client sends `X-Remote-Groups: coglossa-admins` itself** while `groups_header` is unset: it must stay a non-admin. Test in Task 3 (`test_a_groups_header_is_ignored_unless_configured`).
2. **Query parameters, not path parameters, name a hidden set** (`GET /api/runs?project_id=<hidden>`): it must list nothing. Test in Task 3 (`test_run_list_by_query_hides_other_teams`).
3. **The same person or team spelled in another case** ("Maria" from the proxy, "maria" in the file or `created_by`; a set moved to "Spanish Desk" when the group is "spanish desk"): same person, same team. Test in Task 2 (`test_names_and_teams_compare_case_insensitively`).
4. **A POST with a missing or invalid body on a hidden run** (e.g. `/spans/{id}/edit` with `{}`): it must answer 404, not 422, so a body error never confirms the set exists. Test in Task 3 (the enumeration test sends empty bodies).
5. **A person in no team** makes a set and sees it, while a colleague does not; an admin does. Test in Task 3 (`test_personal_sets`).

---

## File Structure

- Create `engine/coglossa/people.py`: the people file (model, loader, `People`).
- Create `engine/coglossa/access.py`: `Viewer`, `EVERYONE`, `viewer_for`, `can_see`, `visible`, `check_visible`.
- Create `engine/coglossa/store/migrations/versions/0005_project_team.py`.
- Modify `engine/coglossa/config.py` (`AuthConfig`, `load_config`, `validate_config`), `engine/coglossa/store/models.py` (`Project`), `engine/coglossa/catalog.py`, `engine/coglossa/schema/views.py` (`ProjectInfo`).
- Modify `api/coglossa_api/auth.py`, `context.py`, `app.py`, `models.py`, `routes/system.py`, `routes/projects.py`, `routes/runs.py`.
- Web: modify `src/api/system.ts`, `src/api/sets.ts`, `src/api/types.ts`, `src/settings/SettingsPage.tsx`, `src/translate/NewTranslationPage.tsx`, `src/set/AddFilesDialog.tsx`, `src/set/SetHeader.tsx`, `src/set/SetPage.tsx`, `src/test/fixtures.ts`; create `src/lib/teams.ts`, `src/set/MoveTeamDialog.tsx`.
- Hosted: `deploy/hosted/vercel/lib/auth.ts`, `lib/route.ts`, `deploy/hosted/add-user.mjs`, `deploy/hosted/coglossa.hosted.yaml`.
- e2e: `api/devstack.py`, `web/playwright.config.ts`, `web/e2e/people.yaml`, `web/e2e/roles.spec.ts`.
- Docs: `docs/operator-guide.md`, `deploy/config/coglossa.example.yaml`, `deploy/hosted/README.md`, `README.md`, the UI spec.

---

### Task 1: The people file and the auth config (engine)

**Files:**
- Create: `engine/coglossa/people.py`
- Modify: `engine/coglossa/config.py` (`AuthConfig`, `load_config`, `validate_config`)
- Test: `engine/tests/test_people.py`, `engine/tests/test_config.py`

**Interfaces:**
- Produces:
  - `people.team_key(name: str) -> str` (`name.strip().lower()`)
  - `people.TEAM_NAME_PATTERN = r"^[A-Za-z0-9._-](?:[A-Za-z0-9._ -]{0,62}[A-Za-z0-9._-])?$"`
  - `people.People` (frozen dataclass): `teams_of(name: str) -> tuple[str, ...]`, `is_admin(name: str) -> bool`, `all_teams() -> list[str]`
  - `people.NOBODY = People()`; `people.PeopleFileError(Exception)`; `people.load_people(path: Path | None) -> People`
  - `AuthConfig.groups_header: str | None = None`, `AuthConfig.admin_group: str = "coglossa-admins"`, `AuthConfig.people_file: Path | None = None` (resolved relative to the config file)

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_people.py`:

```python
import pytest

from coglossa.people import NOBODY, PeopleFileError, load_people, team_key


def _write(tmp_path, text):
    path = tmp_path / "people.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_people_file_gives_teams_and_admins(tmp_path):
    people = load_people(
        _write(
            tmp_path,
            "admins: [Ana]\nteams:\n  spanish-desk: [maria, Jose]\n  Pacific: [tevita, ana]\n",
        )
    )
    assert people.teams_of("MARIA") == ("spanish-desk",)
    assert people.teams_of("ana") == ("Pacific",)
    assert people.teams_of("nobody") == ()
    assert people.is_admin("ana") and not people.is_admin("maria")
    assert people.all_teams() == ["Pacific", "spanish-desk"]


def test_no_file_means_nobody(tmp_path):
    assert load_people(None) is NOBODY
    assert NOBODY.teams_of("x") == () and not NOBODY.is_admin("x") and NOBODY.all_teams() == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("teams: [a]\n", "teams"),
        ("admins: [ana]\nother: 1\n", "other"),
        ("teams:\n  'bad/name': [ana]\n", "bad/name"),
        ("teams:\n  ok: ana\n", "ok"),
        (":\n", "invalid YAML"),
    ],
)
def test_a_bad_people_file_is_one_line(tmp_path, text, message):
    with pytest.raises(PeopleFileError, match=message) as err:
        load_people(_write(tmp_path, text))
    assert "\n" not in str(err.value) and "people.yaml" in str(err.value)


def test_a_missing_people_file(tmp_path):
    with pytest.raises(PeopleFileError, match="not found"):
        load_people(tmp_path / "absent.yaml")


def test_team_key():
    assert team_key(" Spanish Desk ") == "spanish desk"
```

In `engine/tests/test_config.py` add (reuse its helper that writes a config; if it has none, write YAML to `tmp_path` and call `load_config`):

```python
def test_the_people_file_is_relative_to_the_config_and_checked(tmp_path):
    (tmp_path / "people.yaml").write_text("admins: [ana]\n", encoding="utf-8")
    cfg, _ = load_config(_config_with(tmp_path, auth={"mode": "trusted_header", "people_file": "people.yaml"}))
    assert cfg.auth.people_file == (tmp_path / "people.yaml").resolve()
    assert cfg.auth.groups_header is None and cfg.auth.admin_group == "coglossa-admins"
    with pytest.raises(ConfigError, match="people file .*not found"):
        load_config(_config_with(tmp_path, auth={"mode": "trusted_header", "people_file": "nope.yaml"}))
    with pytest.raises(ConfigError, match="groups_header"):
        load_config(_config_with(tmp_path, auth={"mode": "trusted_header", "groups_header": "bad header"}))
```

(`_config_with(tmp_path, **sections)` is the file's existing writer, or a three-line helper writing `SEED_CONFIG | sections` to `tmp_path / "coglossa.yaml"`.)

- [ ] **Step 2: Run them to see them fail**

Run: `cd engine && uv run pytest tests/test_people.py tests/test_config.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'coglossa.people'`; `extra fields not permitted` for `people_file`.

- [ ] **Step 3: Implement**

`engine/coglossa/people.py`:

```python
"""Teams and admins from the people file (`auth.people_file`, CGL-26): the fallback for a
sign-in proxy that cannot pass group names. Read at startup; a change takes effect on restart."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

TEAM_NAME_PATTERN = r"^[A-Za-z0-9._-](?:[A-Za-z0-9._ -]{0,62}[A-Za-z0-9._-])?$"
_TEAM_NAME = re.compile(TEAM_NAME_PATTERN)


def team_key(name: str) -> str:
    """People's names and team names compare case-insensitively."""
    return name.strip().lower()


class PeopleFileError(Exception):
    """The people file is missing or invalid; the message is one line."""


class _PeopleFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admins: list[str] = []
    teams: dict[str, list[str]] = {}

    @field_validator("teams")
    @classmethod
    def _team_names(cls, teams: dict[str, list[str]]) -> dict[str, list[str]]:
        for name in teams:
            if not _TEAM_NAME.fullmatch(name):
                raise ValueError(
                    f"team name '{name}': use 1-64 letters, digits, spaces, '.', '_' or '-'"
                )
        return teams


@dataclass(frozen=True)
class People:
    admins: frozenset[str] = frozenset()  # team_key of each admin's name
    teams_by_person: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def teams_of(self, name: str) -> tuple[str, ...]:
        return self.teams_by_person.get(team_key(name), ())

    def is_admin(self, name: str) -> bool:
        return team_key(name) in self.admins

    def all_teams(self) -> list[str]:
        names = {team_key(t): t for teams in self.teams_by_person.values() for t in teams}
        return sorted(names.values(), key=team_key)


NOBODY = People()


def load_people(path: Path | None) -> People:
    if path is None:
        return NOBODY
    if not path.is_file():
        raise PeopleFileError(f"people file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data = _PeopleFile.model_validate(raw)
    except yaml.YAMLError as e:
        raise PeopleFileError(f"{path}: invalid YAML: {' '.join(str(e).split())}") from e
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "top level"
        raise PeopleFileError(f"{path}: {where}: {first['msg']}") from e
    except (OSError, UnicodeDecodeError) as e:
        raise PeopleFileError(f"cannot read people file {path}: {e}") from e
    by_person: dict[str, list[str]] = {}
    for team, members in data.teams.items():
        for member in members:
            by_person.setdefault(team_key(member), []).append(team)
    return People(
        admins=frozenset(team_key(a) for a in data.admins),
        teams_by_person={k: tuple(v) for k, v in by_person.items()},
    )
```

(If `raw` is not a mapping, `model_validate` raises a `ValidationError` whose loc is empty: "top level". A YAML `teams: [a]` fails with loc `teams`; a member list given as a string fails with loc `teams.ok`.)

`AuthConfig` (config.py) gains, after `header`:

```python
    # A header in which the proxy passes the person's groups, comma-separated (CGL-26). Unset:
    # never read, even when a client sends it, so a proxy that does not set it is not bypassed.
    groups_header: str | None = None
    admin_group: str = "coglossa-admins"  # members of this group are admins (not a team)
    people_file: Path | None = None  # teams and admins, relative to the config file
```

and the header validator also validates `groups_header` when set (`@field_validator("header", "groups_header")`, returning `None` through). `load_config` resolves `people_file` like `language_tiers_file`:

```python
    if cfg.auth.people_file is not None:
        auth = cfg.auth.model_copy(update={"people_file": (base / cfg.auth.people_file).resolve()})
        cfg = cfg.model_copy(update={"auth": auth})
```

`validate_config` adds, next to the tiers-file check:

```python
    if cfg.auth.people_file is not None:
        try:
            load_people(cfg.auth.people_file)
        except PeopleFileError as e:
            add("error", str(e))
```

(import `load_people`, `PeopleFileError` from `coglossa.people`). `coglossa doctor` already reports `validate_config` errors, so a bad file shows there too.

- [ ] **Step 4: Run the tests**

Run: `cd engine && uv run pytest tests/test_people.py tests/test_config.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Full engine suite, then commit**

Run: `make test-engine` — Expected: all pass, coverage ≥ 95%. If `GET /system/config` snapshot tests in the API break on the new auth fields, they are covered in Task 3; `make test-api` here only needs to stay green (run it).

```bash
git add engine
git commit -m "feat(engine): the people file and the auth settings for teams (CGL-26)"
```

---

### Task 2: Sets belong to a team; the access rule (engine)

**Files:**
- Create: `engine/coglossa/access.py`, `engine/coglossa/store/migrations/versions/0005_project_team.py`
- Modify: `engine/coglossa/store/models.py` (`Project`), `engine/coglossa/schema/views.py` (`ProjectInfo`), `engine/coglossa/catalog.py` (`create_project`, `list_projects`, `list_runs`, `_project_info`, new `set_team`, `known_teams`)
- Test: `engine/tests/test_access.py`, `engine/tests/store/test_migrations.py`, `engine/tests/test_catalog.py`; and `test_cli_ops.py` / `test_doctor.py` head revision `"0005"`; `deploy/tests/check-app-image.sh` migration file name

**Interfaces:**
- Consumes: `people.team_key`, `people.People` (Task 1).
- Produces:
  - `access.Viewer(name: str | None, teams: frozenset[str], admin: bool, team_names: tuple[str, ...])`; `access.EVERYONE`
  - `access.viewer_for(name: str, groups: Iterable[str], people: People, admin_group: str) -> Viewer`
  - `access.can_see(viewer: Viewer, project: Project) -> bool`; `access.visible(viewer: Viewer) -> ColumnElement[bool]`
  - `access.check_visible(s, viewer, *, project_id=None, document_id=None, run_id=None) -> None` (raises `runs.NotFoundError` with the missing-id message)
  - `catalog.create_project(db, name, *, target_lang, source_lang=None, pipeline_profile=None, team: str | None = None, created_by: str | None = None)`
  - `catalog.list_projects(db, viewer: Viewer = EVERYONE)`; `catalog.list_runs(db, *, project_id=None, document_id=None, limit=100, viewer: Viewer = EVERYONE)`
  - `catalog.set_team(db, project_id, team: str | None) -> ProjectInfo`; `catalog.known_teams(db, people: People, viewer: Viewer) -> list[str]`
  - `ProjectInfo.team: str | None`, `ProjectInfo.created_by: str | None`

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_access.py`:

```python
import uuid

import pytest

from coglossa import catalog
from coglossa.access import EVERYONE, can_see, check_visible, viewer_for
from coglossa.people import NOBODY, People
from coglossa.store import runs
from coglossa.store.models import Project
from coglossa.testing.seed import seed_run

PEOPLE = People(admins=frozenset({"root"}), teams_by_person={"maria": ("spanish-desk",)})


def _viewer(name, groups=()):
    return viewer_for(name, groups, PEOPLE, "coglossa-admins")


def test_a_viewer_unites_the_proxy_groups_and_the_file():
    v = _viewer("Maria", [" Pacific ", "", "coglossa-admins"])
    assert v.admin and v.name == "Maria"
    assert v.team_names == ("Pacific", "spanish-desk") and v.teams == {"pacific", "spanish-desk"}
    assert _viewer("root").admin and not _viewer("ben").admin
    assert _viewer("ben").teams == frozenset()
    assert EVERYONE.admin and EVERYONE.name is None


@pytest.mark.parametrize(
    ("team", "created_by", "viewer", "seen"),
    [
        ("Spanish-Desk", None, ("maria", ()), True),  # their team, any case
        ("pacific", None, ("maria", ()), False),
        (None, "MARIA", ("maria", ()), True),  # their personal set
        (None, "ben", ("maria", ()), False),
        (None, None, ("maria", ()), False),  # a set from before teams: admins only
        (None, None, ("root", ()), True),
    ],
)
async def test_names_and_teams_compare_case_insensitively(db, team, created_by, viewer, seen):
    info = await catalog.create_project(
        db, f"p-{uuid.uuid4()}", target_lang="en", team=team, created_by=created_by
    )
    v = _viewer(*viewer)
    listed = {p.id for p in await catalog.list_projects(db, v)}
    async with db.session() as s:
        project = await s.get(Project, uuid.UUID(info.id))
        assert can_see(v, project) is seen
        if seen:
            await check_visible(s, v, project_id=project.id)
        else:
            with pytest.raises(runs.NotFoundError, match=f"^project {info.id} not found$"):
                await check_visible(s, v, project_id=project.id)
    assert (info.id in listed) is seen


async def test_runs_and_documents_take_their_sets_visibility(db):
    seeded = await seed_run(db, project="desk-set")
    await catalog.set_team(db, seeded.project_id, "pacific")
    outsider = _viewer("maria")
    async with db.session() as s:
        with pytest.raises(runs.NotFoundError, match=f"^run {seeded.run_id} not found$"):
            await check_visible(s, outsider, run_id=seeded.run_id)
        with pytest.raises(runs.NotFoundError, match=f"^document {seeded.document_id} not found$"):
            await check_visible(s, outsider, document_id=seeded.document_id)
        missing = uuid.uuid4()
        with pytest.raises(runs.NotFoundError, match=f"^run {missing} not found$"):
            await check_visible(s, outsider, run_id=missing)
        await check_visible(s, _viewer("ben", ["Pacific"]), run_id=seeded.run_id)
    assert await catalog.list_runs(db, viewer=outsider) == []
    assert await catalog.list_runs(db, project_id=seeded.project_id, viewer=outsider) == []
    assert len(await catalog.list_runs(db, viewer=_viewer("ben", ["pacific"]))) == 1


async def test_known_teams(db):
    await catalog.create_project(db, "a", target_lang="en", team="Pacific")
    await catalog.create_project(db, "b", target_lang="en", team="pacific")  # same team
    teams = await catalog.known_teams(db, PEOPLE, _viewer("x", ["Delta"]))
    assert teams == ["Delta", "Pacific", "spanish-desk"]
    assert await catalog.known_teams(db, NOBODY, EVERYONE) == ["Pacific"]
```

In `engine/tests/test_catalog.py`:

```python
async def test_a_set_records_its_team_and_creator_and_can_move(db):
    info = await catalog.create_project(db, "Atlas", target_lang="en", team="Pacific", created_by="ana")
    assert (info.team, info.created_by) == ("Pacific", "ana")
    moved = await catalog.set_team(db, uuid.UUID(info.id), None)
    assert (moved.team, moved.created_by) == (None, "ana")
    with pytest.raises(NotFoundError):
        await catalog.set_team(db, uuid.uuid4(), "x")
```

Migration test (`tests/store/test_migrations.py`): head becomes `"0005"` in `test_migration_state_is_current`; add

```python
def test_0005_leaves_existing_sets_without_a_team(pg_admin):
    url = create_test_database(pg_admin)
    engine = create_engine(to_sqlalchemy_url(url), poolclass=NullPool)
    try:
        upgrade(url, "0004")
        with engine.begin() as conn:
            pid = _project(conn, "old", "en")
        upgrade(url)
        with engine.begin() as conn:
            row = conn.execute(
                sa_text("SELECT team, created_by FROM project WHERE id = :id"), {"id": pid}
            ).one()
        assert tuple(row) == (None, None)
    finally:
        engine.dispose()
        drop_test_database(pg_admin, url)
```

Also update the head revision in `tests/test_cli_ops.py` (`"at revision 0005"`) and `tests/test_doctor.py` (`"0005"`).

- [ ] **Step 2: Run them to see them fail**

Run: `cd engine && uv run pytest tests/test_access.py tests/test_catalog.py tests/store/test_migrations.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: coglossa.access`; `create_project() got an unexpected keyword argument 'team'`; head is 0004.

- [ ] **Step 3: Implement**

`0005_project_team.py`:

```python
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
```

`models.Project` gains (after `pipeline_profile`):

```python
    # CGL-26: the team whose members see the set; None: personal to `created_by` (admins only
    # when that is None too, as for sets made before teams)
    team: Mapped[str | None]
    created_by: Mapped[str | None]
```

`engine/coglossa/access.py`:

```python
"""Who sees which sets (CGL-26). Admins see every set; others the sets of their teams and the
personal sets they made. Names and teams compare case-insensitively (people.team_key)."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import and_, false, func, or_, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from coglossa.people import People, team_key
from coglossa.store import runs
from coglossa.store.models import Project


@dataclass(frozen=True)
class Viewer:
    name: str | None
    teams: frozenset[str]  # team_key of each team
    admin: bool
    team_names: tuple[str, ...] = ()  # as spelled, sorted, for display


EVERYONE = Viewer(name=None, teams=frozenset(), admin=True)  # auth.mode none, and the CLI


def viewer_for(name: str, groups: Iterable[str], people: People, admin_group: str) -> Viewer:
    """The proxy's groups and the people file's teams, united; the admin group is no team."""
    groups = [g.strip() for g in groups if g.strip()]
    admin_key = team_key(admin_group)
    admin = people.is_admin(name) or any(team_key(g) == admin_key for g in groups)
    teams: dict[str, str] = {}
    for team in [*groups, *people.teams_of(name)]:
        if team_key(team) != admin_key:
            teams.setdefault(team_key(team), team)
    names = tuple(sorted(teams.values(), key=team_key))
    return Viewer(name=name, teams=frozenset(teams), admin=admin, team_names=names)


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


def visible(viewer: Viewer) -> ColumnElement[bool]:
    """can_see as SQL on Project; the two must agree (tests/test_access.py)."""
    if viewer.admin:
        return true()
    conditions: list[ColumnElement[bool]] = []
    if viewer.teams:
        conditions.append(func.lower(func.trim(Project.team)).in_(sorted(viewer.teams)))
    if viewer.name is not None:
        conditions.append(
            and_(
                Project.team.is_(None),
                func.lower(func.trim(Project.created_by)) == team_key(viewer.name),
            )
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
        doc = await runs.get_document(s, run.document_id)
        project, missing = await runs.get_project(s, doc.project_id), f"run {run_id} not found"
    elif document_id is not None:
        doc = await runs.get_document(s, document_id)
        project, missing = await runs.get_project(s, doc.project_id), f"document {document_id} not found"
    elif project_id is not None:
        project, missing = await runs.get_project(s, project_id), f"project {project_id} not found"
    else:
        return
    if not can_see(viewer, project):
        raise runs.NotFoundError(missing)
```

(`get_run`/`get_document`/`get_project` already raise `NotFoundError` with exactly those messages for a missing id; check `get_run`'s message is `f"run {run_id} not found"` — runs.py line ~356 — and keep them identical.)

`schema/views.py` `ProjectInfo` gains after `pipeline_profile`:

```python
    team: str | None  # CGL-26: whose members see it; None: personal to created_by
    created_by: str | None  # who made it; None for sets from before teams or auth.mode none
```

`catalog.py`:
- `_project_info` sets `team=project.team, created_by=project.created_by`.
- `create_project(..., team: str | None = None, created_by: str | None = None)` passes both to `Project(...)`.
- `list_projects(db, viewer: Viewer = EVERYONE)`: `_projects_query().where(access.visible(viewer)).order_by(Project.name)`.
- `list_runs(..., viewer: Viewer = EVERYONE)`: `query = select(Run, Document).join(Document, Run.document_id == Document.id).join(Project, Project.id == Document.project_id).where(access.visible(viewer))` then the existing filters.
- New:

```python
async def set_team(db: Database, project_id: uuid.UUID, team: str | None) -> ProjectInfo:
    """CGL-26: an admin moves a set into a team (None: personal to its creator)."""
    async with db.session() as s, s.begin():
        project = await runs.lock_project(s, project_id)
        project.team = team
    return await get_project(db, project_id)


async def known_teams(db: Database, people: People, viewer: Viewer) -> list[str]:
    """Teams to suggest when moving a set: the people file's, those of existing sets and the
    viewer's own, one spelling each."""
    async with db.session() as s:
        on_sets = await s.scalars(select(Project.team).where(Project.team.is_not(None)).distinct())
        found = [*people.all_teams(), *on_sets, *viewer.team_names]
    names: dict[str, str] = {}
    for team in found:
        names.setdefault(team_key(team), team)
    return sorted(names.values(), key=team_key)
```

(imports: `from coglossa import access`, `from coglossa.access import EVERYONE, Viewer`, `from coglossa.people import People, team_key`; avoid an import cycle: `access` imports `store.runs`, not `catalog`.)

Also: `deploy/tests/check-app-image.sh` checks `store/migrations/versions/0005_project_team.py`.

- [ ] **Step 4: Run the tests**

Run: `cd engine && uv run pytest tests/test_access.py tests/test_catalog.py tests/store/test_migrations.py tests/test_cli_ops.py tests/test_doctor.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Schema, suites, commit**

Run: `make schema`, then add `team: null, created_by: null` to the `project` and `otherProject` fixtures in `web/src/test/fixtures.ts`; `cd web && pnpm typecheck`. Run `make test-engine && make test-api` — Expected: all pass.

```bash
git add engine schema web/src/api/schema.d.ts web/src/test/fixtures.ts deploy/tests/check-app-image.sh
git commit -m "feat(engine): sets belong to a team; who sees which sets (CGL-26)"
```

---

### Task 3: The API enforces it

**Files:**
- Modify: `api/coglossa_api/auth.py`, `api/coglossa_api/context.py` (`ApiContext.people`), `api/coglossa_api/app.py` (load people; router dependencies), `api/coglossa_api/models.py` (`CreateProject.team`, `UpdateProject`), `api/coglossa_api/routes/system.py` (`Me`, admin-only routes, `GET /teams`), `api/coglossa_api/routes/projects.py` (list, create, patch), `api/coglossa_api/routes/runs.py` (list)
- Test: `api/tests/test_access.py` (new), `api/tests/test_app.py` (`/me`), `api/tests/test_projects.py`

**Interfaces:**
- Consumes: Task 1 (`load_people`, `AuthConfig.groups_header/admin_group/people_file`), Task 2 (`Viewer`, `viewer_for`, `check_visible`, `EVERYONE`, catalog functions).
- Produces:
  - `auth.current_viewer(request) -> Viewer`; `ViewerDep = Annotated[Viewer, Depends(current_viewer)]`; `auth.guard_ids(request, viewer)`; `auth.require_admin(viewer)`; `User` stays (the name)
  - HTTP: `GET /api/me` → `{user, admin, teams, max_upload_mb, logout_url}`; `GET /api/teams` (admin) → `list[str]`; `POST /api/projects` body `team`; `PATCH /api/projects/{id}` body `{name?, team?}` (team change admin-only).

- [ ] **Step 1: Write the failing tests**

`api/tests/test_access.py`:

```python
import re
import uuid

import pytest
from coglossa import catalog
from coglossa.testing.seed import seed_run

from tests.test_app import PROTECTED, _fill

AUTH = {"mode": "trusted_header", "groups_header": "X-Remote-Groups", "people_file": "people.yaml"}


@pytest.fixture
def people(tmp_path):
    (tmp_path / "people.yaml").write_text(
        "admins: [root]\nteams:\n  pacific: [tevita]\n", encoding="utf-8"
    )


def as_(name, groups=""):
    return {"X-Remote-User": name, "X-Remote-Groups": groups}


async def _desk_set(db):
    seeded = await seed_run(db, project="pacific-set")
    await catalog.set_team(db, seeded.project_id, "Pacific")
    return seeded


def _real(path, seeded):
    ids = {
        "project_id": str(seeded.project_id),
        "document_id": str(seeded.document_id),
        "run_id": str(seeded.run_id),
        "span_id": str(seeded.span_ids[0]),
        "flag_id": str(next(iter(seeded.flag_ids.values()))),
    }
    return re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), str(uuid.uuid4())), path)


SCOPED = [
    (m, p) for m, p in PROTECTED if any(k in p for k in ("{project_id}", "{document_id}", "{run_id}"))
]


def test_scoped_routes_are_listed():
    assert len(SCOPED) > 20


@pytest.mark.parametrize(("method", "path"), SCOPED)
async def test_an_outsider_gets_not_found_on_every_route_of_a_set(people, make_api, db, method, path):
    seeded = await _desk_set(db)
    async with make_api(auth=AUTH) as api:
        url = _real(path, seeded)
        hidden = await api.client.request(method, url, headers=as_("maria", "spanish-desk"), json={})
        missing = await api.client.request(method, _fill(path), headers=as_("root"), json={})
    assert hidden.status_code == 404, (path, hidden.text)
    kind = "run" if "{run_id}" in path else "document" if "{document_id}" in path else "project"
    assert re.fullmatch(rf"{kind} [0-9a-f-]+ not found", hidden.json()["detail"]), hidden.json()
    if missing.status_code == 404:  # a missing id of the same kind reads the same
        assert missing.json()["detail"].split(" ")[0] == hidden.json()["detail"].split(" ")[0]


async def test_team_members_and_admins_see_the_set(people, make_api, db):
    seeded = await _desk_set(db)
    async with make_api(auth=AUTH) as api:
        member = await api.client.get(f"/api/runs/{seeded.run_id}", headers=as_("tevita"))
        by_group = await api.client.get(f"/api/runs/{seeded.run_id}", headers=as_("x", "PACIFIC"))
        admin = await api.client.get(f"/api/runs/{seeded.run_id}", headers=as_("root"))
        listed = await api.client.get("/api/projects", headers=as_("maria", "spanish-desk"))
    assert member.status_code == by_group.status_code == admin.status_code == 200
    assert listed.json() == []


async def test_run_list_by_query_hides_other_teams(people, make_api, db):
    seeded = await _desk_set(db)
    async with make_api(auth=AUTH) as api:
        runs = await api.client.get(
            f"/api/runs?project_id={seeded.project_id}", headers=as_("maria")
        )
    assert runs.status_code == 200 and runs.json() == []


async def test_a_groups_header_is_ignored_unless_configured(people, make_api, db):
    seeded = await _desk_set(db)
    auth = {"mode": "trusted_header", "people_file": "people.yaml"}  # no groups_header
    async with make_api(auth=auth) as api:
        me = await api.client.get("/api/me", headers=as_("maria", "coglossa-admins,pacific"))
        run = await api.client.get(
            f"/api/runs/{seeded.run_id}", headers=as_("maria", "coglossa-admins,pacific")
        )
        config = await api.client.get("/api/system/config", headers=as_("maria", "coglossa-admins"))
    assert me.json()["admin"] is False and me.json()["teams"] == []
    assert run.status_code == 404 and config.status_code == 403


@pytest.mark.parametrize("path", ["/api/system/doctor", "/api/system/config", "/api/teams"])
async def test_operator_routes_are_for_admins(people, make_api, path):
    async with make_api(auth=AUTH) as api:
        member = await api.client.get(path, headers=as_("tevita"))
        admin = await api.client.get(path, headers=as_("x", "coglossa-admins"))
    assert member.status_code == 403
    assert member.json()["detail"].startswith("only an administrator can")
    assert admin.status_code == 200


async def test_tiers_stay_open(people, make_api):
    async with make_api(auth=AUTH) as api:
        assert (await api.client.get("/api/system/tiers", headers=as_("tevita"))).status_code == 200


async def test_me_says_who_and_what(people, make_api):
    async with make_api(auth=AUTH) as api:
        me = (await api.client.get("/api/me", headers=as_("Tevita", "Delta"))).json()
    assert me == {
        "user": "Tevita",
        "admin": False,
        "teams": ["Delta", "pacific"],
        "max_upload_mb": 200,
        "logout_url": None,
    }


async def test_personal_sets(people, make_api):
    async with make_api(auth=AUTH) as api:
        made = await api.client.post(
            "/api/projects", json={"name": "Mine", "target_lang": "en"}, headers=as_("maria")
        )
        mine = await api.client.get("/api/projects", headers=as_("MARIA"))
        theirs = await api.client.get("/api/projects", headers=as_("ben"))
        admin = await api.client.get("/api/projects", headers=as_("root"))
    assert made.status_code == 201
    assert (made.json()["team"], made.json()["created_by"]) == (None, "maria")
    assert [p["name"] for p in mine.json()] == ["Mine"]
    assert theirs.json() == [] and [p["name"] for p in admin.json()] == ["Mine"]


async def test_making_and_moving_sets_between_teams(people, make_api):
    async with make_api(auth=AUTH) as api:
        ok = await api.client.post(
            "/api/projects",
            json={"name": "Desk", "target_lang": "en", "team": "PACIFIC"},
            headers=as_("tevita"),
        )
        refused = await api.client.post(
            "/api/projects",
            json={"name": "Other", "target_lang": "en", "team": "delta"},
            headers=as_("tevita"),
        )
        pid = ok.json()["id"]
        rename = await api.client.patch(
            f"/api/projects/{pid}", json={"name": "Desk 2"}, headers=as_("tevita")
        )
        move = await api.client.patch(
            f"/api/projects/{pid}", json={"team": "delta"}, headers=as_("tevita")
        )
        moved = await api.client.patch(
            f"/api/projects/{pid}", json={"team": "delta"}, headers=as_("root")
        )
        teams = await api.client.get("/api/teams", headers=as_("root"))
        empty = await api.client.patch(f"/api/projects/{pid}", json={}, headers=as_("root"))
    assert ok.status_code == 201 and ok.json()["team"] == "PACIFIC"
    assert refused.status_code == 422 and "not in team 'delta'" in refused.json()["detail"]
    assert rename.json()["name"] == "Desk 2"
    assert move.status_code == 403
    assert moved.json()["team"] == "delta"
    assert teams.json() == ["delta", "pacific"]
    assert empty.status_code == 422
```

In `api/tests/test_app.py`, `test_me_without_auth` and `test_trusted_header_auth` now expect the new fields: `{"user": None, "admin": True, "teams": [], "max_upload_mb": 200, "logout_url": None}` and `{"user": "ana", "admin": False, "teams": [], "max_upload_mb": 200, "logout_url": None}` (signature updates only).

`write_config` writes the config to `tmp_path`, so `people.yaml` in `tmp_path` resolves relative to it.

- [ ] **Step 2: Run them to see them fail**

Run: `make db-up && cd api && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/test_access.py tests/test_app.py -q`
Expected: FAIL — outsiders get 200; `/me` lacks `admin`; `/api/teams` 404.

- [ ] **Step 3: Implement**

`api/coglossa_api/auth.py`:

```python
"""Spec §3 `auth` and CGL-26: who is asking, and what they may see. `none`: everyone is an
admin. `trusted_header`: a proxy authenticates people and passes the name in `auth.header`
(and, when `auth.groups_header` is set, their groups); the people file adds teams and admins."""

import uuid
from typing import Annotated

from coglossa.access import EVERYONE, Viewer, check_visible, viewer_for
from fastapi import Depends, HTTPException, Request

from coglossa_api.context import get_context

_SCOPED = ("run_id", "document_id", "project_id")


def current_viewer(request: Request) -> Viewer:
    ctx = get_context(request)
    auth = ctx.cfg.auth
    if auth.mode == "none":
        return EVERYONE
    user = (request.headers.get(auth.header) or "").strip()
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"missing {auth.header} header: sign in through the proxy in front of Coglossa",
        )
    groups = (request.headers.get(auth.groups_header) or "").split(",") if auth.groups_header else []
    return viewer_for(user, groups, ctx.people, auth.admin_group)


ViewerDep = Annotated[Viewer, Depends(current_viewer)]


def current_user(viewer: ViewerDep) -> str | None:
    return viewer.name


User = Annotated[str | None, Depends(current_user)]


async def guard_ids(request: Request, viewer: ViewerDep) -> None:
    """Every route naming a run, document or project: what the viewer cannot see answers as a
    missing id does. A router dependency, so no route can forget it; it runs before the body
    is read, so a bad body never tells an outsider the set exists."""
    if viewer.admin:
        return
    ids: dict[str, uuid.UUID] = {}
    for key in _SCOPED:
        value = request.path_params.get(key)
        if value is None:
            continue
        try:
            ids[key] = uuid.UUID(str(value))
        except ValueError:
            return  # the route answers 422 for a malformed id
    if ids:
        async with get_context(request).db.session() as s:
            await check_visible(s, viewer, **ids)


def require_admin(viewer: ViewerDep) -> None:
    if not viewer.admin:
        raise HTTPException(status_code=403, detail="only an administrator can see or change this")
```

`context.py`: `ApiContext` gains `people: People` (import from `coglossa.people`). `app.py`: in the lifespan, `people=load_people(cfg.auth.people_file)`; the protected router: `dependencies=[Depends(current_viewer), Depends(guard_ids)]` (import from `coglossa_api.auth`).

`routes/system.py`:

```python
class Me(BaseModel):
    user: str | None  # None when auth.mode is none
    admin: bool  # CGL-26: sees every set and the operator screens
    teams: list[str]  # the teams whose sets this person sees
    max_upload_mb: int  # the upload limit (here, since /system/config is for admins)
    logout_url: str | None = None  # the proxy's sign-out address, or None


@router.get("/me")
async def get_me(viewer: ViewerDep, ctx: Ctx) -> Me:
    return Me(
        user=viewer.name,
        admin=viewer.admin,
        teams=list(viewer.team_names),
        max_upload_mb=ctx.cfg.api.max_upload_mb,
        logout_url=ctx.cfg.auth.logout_url,
    )
```

`doctor` and `config` get `dependencies=[Depends(require_admin)]` on their decorators, plus:

```python
@router.get("/teams", dependencies=[Depends(require_admin)])
async def teams(viewer: ViewerDep, ctx: Ctx) -> list[str]:
    """CGL-26: team names to suggest when moving a set."""
    return await catalog.known_teams(ctx.db, ctx.people, viewer)
```

`models.py`:

```python
TeamName = Annotated[str, Field(pattern=TEAM_NAME_PATTERN)]  # from coglossa.people


class CreateProject(BaseModel):
    ...
    team: TeamName | None = None  # CGL-26: None: personal to the person who makes it


class UpdateProject(BaseModel):
    """Rename a set and/or (admins) move it to another team; at least one field."""

    name: ProjectName | None = None
    team: TeamName | None = None  # None (sent explicitly): personal to its creator

    @model_validator(mode="after")
    def _something(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("send a name or a team")
        return self
```

(Rename `RenameProject` to `UpdateProject` at its uses; keep `ProjectName`.)

`routes/projects.py`:
- `list_projects(ctx, viewer: ViewerDep)` → `catalog.list_projects(ctx.db, viewer)`.
- `create_project(body, ctx, viewer: ViewerDep)`:

```python
    if body.team is not None and not viewer.admin and team_key(body.team) not in viewer.teams:
        raise service.InvalidRequestError(f"you are not in team '{body.team}'")
    return await catalog.create_project(
        ctx.db, body.name, target_lang=..., source_lang=..., pipeline_profile=...,
        team=body.team, created_by=viewer.name,
    )
```

- `rename_project(project_id, body: UpdateProject, ctx, viewer: ViewerDep)`:

```python
    info = None
    if "team" in body.model_fields_set:
        if not viewer.admin:
            raise HTTPException(status_code=403, detail="only an administrator can move a set to another team")
        info = await catalog.set_team(ctx.db, project_id, body.team)
    if body.name is not None:
        info = await catalog.rename_project(ctx.db, project_id, body.name)
    return info  # model_validator guarantees one of the two ran
```

(update the docstring: "Rename a set; admins can also move it to another team (403 for others).")
- `routes/runs.py` `list_runs(..., viewer: ViewerDep)` passes `viewer=viewer`.

- [ ] **Step 4: Run the API suite**

Run: `make test-api`
Expected: PASS (the enumeration test passes for every scoped route; existing tests unchanged in `none` mode).

- [ ] **Step 5: Schema, web types, commit**

Run: `make schema`; in `web/src/test/fixtures.ts` add a `me` fixture if one exists with `admin: true, teams: [], max_upload_mb: 200`; `cd web && pnpm typecheck` (fix only type errors from the renamed body type `UpdateProject` in `src/api/sets.ts`'s rename mutation, which keeps sending `{ name }`).

```bash
git add api schema web/src
git commit -m "feat(api): every request sees only its teams' sets; operator routes for admins (CGL-26)"
```

---

### Task 4: The web app by role

**Files:**
- Create: `web/src/lib/teams.ts`, `web/src/lib/teams.test.ts`, `web/src/set/MoveTeamDialog.tsx`, `web/src/set/MoveTeamDialog.test.tsx`
- Modify: `web/src/api/system.ts` (`useMe` type, `useTeams`), `web/src/api/projects.ts` (`useMoveProject`), `web/src/api/types.ts` (`Me`), `web/src/settings/SettingsPage.tsx`, `web/src/translate/NewTranslationPage.tsx` (Team dropdown; `uploadLimit(me)`), `web/src/set/AddFilesDialog.tsx` (`uploadLimit(me)`), `web/src/set/SetHeader.tsx` (team label; Move to team…), `web/src/set/SetPage.tsx` (dialog state), `web/src/test/fixtures.ts` (`me`)
- Test: `web/src/settings/SettingsPage.test.tsx`, `web/src/translate/NewTranslationPage.test.tsx`, `web/src/set/SetPage.test.tsx`

**Interfaces:**
- Consumes: `/api/me` `{user, admin, teams, max_upload_mb, logout_url}`, `GET /api/teams`, `PATCH /api/projects/{id}` `{team}`, `POST /api/projects` `{team}` (Task 3); `ProjectInfo.team`, `ProjectInfo.created_by`.
- Produces:
  - `uploadLimit(me: Me | undefined): number | null`
  - `teams.rememberedTeam(teams: string[]): string` (the remembered team if still one of `teams`, else `teams[0] ?? ""`); `teams.rememberTeam(team: string): void` (`localStorage` key `coglossa.team`, wrapped in try/catch)
  - `teamLabel(project: ProjectInfo, me: Me): string | null` (in `SetHeader.tsx`, exported for tests)
  - `useTeams()` (enabled only for admins), `useMoveProject(projectId)`

- [ ] **Step 1: Write the failing tests**

`web/src/lib/teams.test.ts`:

```ts
import { rememberedTeam, rememberTeam } from "./teams";

beforeEach(() => localStorage.clear());

it("remembers the last team while the person is still in it", () => {
  expect(rememberedTeam(["a", "b"])).toBe("a");
  rememberTeam("b");
  expect(rememberedTeam(["a", "b"])).toBe("b");
  expect(rememberedTeam(["a"])).toBe("a");
  rememberTeam("");
  expect(rememberedTeam(["a", "b"])).toBe(""); // Only me was the last choice
  expect(rememberedTeam([])).toBe("");
});

it("works without storage", () => {
  const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  expect(rememberedTeam(["a"])).toBe("a");
  spy.mockRestore();
});
```

`SettingsPage.test.tsx` add (with the file's own render helper; `me` from fixtures):

```ts
it("shows a translator only Appearance", async () => {
  server.use(http.get("*/api/me", () => HttpResponse.json({ ...me, admin: false, teams: ["desk"] })));
  renderSettings();
  expect(await screen.findByRole("heading", { name: "Appearance" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Status" })).not.toBeInTheDocument();
  expect(screen.queryByText("Technical details")).not.toBeInTheDocument();
});
```

(and the existing Settings tests serve `me` with `admin: true`; if they do not serve `/api/me`, add it to their `server.use`.)

`NewTranslationPage.test.tsx` add:

```ts
it("asks which team when the person has two, and sends it", async () => {
  let body: unknown;
  server.use(
    http.get("*/api/me", () => HttpResponse.json({ ...me, admin: false, teams: ["desk-a", "desk-b"] })),
    http.post("*/api/projects", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(project, { status: 201 });
    }),
  );
  const { user } = renderPage();
  await user.selectOptions(await screen.findByLabelText("Team"), "desk-b");
  // …choose a file and a target language as the file's other tests do…
  await user.click(screen.getByRole("button", { name: "Translate" }));
  await vi.waitFor(() => expect(body).toMatchObject({ team: "desk-b" }));
  expect(screen.getByRole("option", { name: "Only me" })).toBeInTheDocument();
});

it("uses the one team without asking, and none for a person without teams", async () => {
  // teams: ["desk-a"] → no "Team" field, body.team "desk-a"; teams: [] → no field, body.team null
});
```

(Write the second test out in full with two renders; reuse the first test's file and language steps.)

`SetPage.test.tsx` add:

```ts
it("labels the set's team and lets an admin move it", async () => {
  let sent: unknown;
  serve([documentSummary]);
  server.use(
    http.get("*/api/me", () => HttpResponse.json({ ...me, admin: true, teams: ["desk-a"] })),
    http.get("*/api/projects/p-1", () => HttpResponse.json({ ...project, team: "desk-a" })),
    http.get("*/api/teams", () => HttpResponse.json(["desk-a", "desk-b"])),
    http.patch("*/api/projects/p-1", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...project, team: "desk-b" });
    }),
  );
  const { user } = open();
  expect(await screen.findByText(/desk-a · French → English/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "More actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Move to team…" }));
  const dialog = screen.getByRole("dialog", { name: "Move to team" });
  await user.clear(within(dialog).getByLabelText("Team"));
  await user.type(within(dialog).getByLabelText("Team"), "desk-b");
  await user.click(within(dialog).getByRole("button", { name: "Move" }));
  await vi.waitFor(() => expect(sent).toEqual({ team: "desk-b" }));
});

it("does not offer Move to team to a translator, nor label a one-team person's sets", async () => {
  serve([documentSummary]);
  server.use(http.get("*/api/me", () => HttpResponse.json({ ...me, admin: false, teams: ["desk-a"] })));
  const { user } = open();
  expect(await screen.findByText(/^French → English/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "More actions" }));
  expect(screen.queryByRole("menuitem", { name: "Move to team…" })).not.toBeInTheDocument();
});
```

Pure test for `teamLabel` (in `SetPage.test.tsx` or a new `SetHeader.test.ts`):

```ts
it("names whose set it is", () => {
  const admin = { ...me, admin: true, user: "ana", teams: [] };
  expect(teamLabel({ ...project, team: "desk" }, admin)).toBe("desk");
  expect(teamLabel({ ...project, team: null, created_by: "ana" }, admin)).toBe("Only you");
  expect(teamLabel({ ...project, team: null, created_by: "ben" }, admin)).toBe("Personal to ben");
  expect(teamLabel({ ...project, team: null, created_by: null }, admin)).toBe("No team");
  expect(teamLabel({ ...project, team: "desk" }, { ...me, admin: false, teams: ["desk"] })).toBeNull();
});
```

`fixtures.ts`: `export const me: Me = { user: null, admin: true, teams: [], max_upload_mb: 200, logout_url: null };` and the default MSW handler in `test/server.ts` (if it serves `/api/me`) returns it.

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && pnpm vitest run src/lib/teams.test.ts src/settings src/translate src/set --coverage.enabled=false`
Expected: FAIL — module `./teams` missing; no Team field; no Move to team.

- [ ] **Step 3: Implement**

`web/src/lib/teams.ts`:

```ts
const KEY = "coglossa.team";

/** CGL-26: the team a new set goes to, remembered between visits; "" means Only me. */
export function rememberedTeam(teams: string[]): string {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(KEY);
  } catch {
    saved = null; // storage blocked: fall back to the first team
  }
  if (saved === "" || (saved !== null && teams.includes(saved))) return saved;
  return teams[0] ?? "";
}

export function rememberTeam(team: string): void {
  try {
    localStorage.setItem(KEY, team);
  } catch {
    // storage blocked: nothing to remember
  }
}
```

`api/types.ts`: `export type Me = S["Me"];`. `api/system.ts`:

```ts
/** CGL-26: team names to suggest when moving a set (admins only). */
export function useTeams(enabled: boolean) {
  return useQuery({
    queryKey: ["teams"],
    queryFn: () => unwrap(client.GET("/api/teams")),
    enabled,
  });
}
```

`api/projects.ts`:

```ts
export function useMoveProject(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (team: string | null) =>
      unwrap(
        client.PATCH("/api/projects/{project_id}", {
          params: { path: { project_id: projectId } },
          body: { team },
        }),
      ),
    onSuccess: (info) => {
      queryClient.setQueryData(projectKeys.one(projectId), info);
      return queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
    },
  });
}
```

`NewTranslationPage.tsx`: `uploadLimit` becomes

```ts
export function uploadLimit(me: Me | undefined): number | null {
  return me?.max_upload_mb ?? null;
}
```

(`const me = useMe();` replaces `useConfig()` here and in `AddFilesDialog.tsx`; `maxMb = uploadLimit(me.data)`). Team state and field:

```tsx
const teams = me.data?.teams ?? [];
const [team, setTeam] = useState<string | null>(null); // null: not chosen yet
const chosenTeam = team ?? rememberedTeam(teams);
...
{teams.length > 1 ? (
  <div className="field">
    <label htmlFor={teamId}>Team</label>
    <select id={teamId} value={chosenTeam} onChange={(e) => setTeam(e.target.value)}>
      {teams.map((t) => (
        <option key={t} value={t}>{t}</option>
      ))}
      <option value="">Only me</option>
    </select>
  </div>
) : null}
```

and in `translate()`: `const sendTeam = teams.length === 0 ? null : teams.length === 1 ? teams[0]! : chosenTeam || null;` `create.mutate({ name: …, ...options, team: sendTeam }, { onSuccess: … rememberTeam(chosenTeam) … })` (remember only when the field was shown).

`SettingsPage.tsx`:

```tsx
export function SettingsPage() {
  const me = useMe();
  const admin = me.data?.admin === true;
  ...
      <Appearance />
      {admin ? <AdminSections /> : null}
```

where `AdminSections` holds today's `useDoctorStatus`/`useDoctor` calls and renders `Status` and `TechnicalDetails` (so the checks are never requested for a translator).

`SetHeader.tsx`:

```ts
/** CGL-26: whose set this is, for people who see more than one team's sets. */
export function teamLabel(project: ProjectInfo, me: Me): string | null {
  if (!me.admin && me.teams.length < 2) return null;
  if (project.team) return project.team;
  if (project.created_by === null) return "No team";
  return project.created_by === me.user ? "Only you" : `Personal to ${project.created_by}`;
}
```

The subtitle renders `{label ? `${label} · ` : ""}` before the language pair (`const me = useMe(); const label = me.data ? teamLabel(project, me.data) : null;`). `MoreMenu` gets `admin: boolean` and `onMoveTeam: () => void`; when `admin`, after the other items:

```tsx
<button role="menuitem" type="button" onClick={() => { close(); onMoveTeam(); }}>
  Move to team…
</button>
```

`MoveTeamDialog.tsx`:

```tsx
import { useId, useState } from "react";
import { useMoveProject } from "../api/projects";
import { useTeams } from "../api/system";
import type { ProjectInfo } from "../api/types";
import { Dialog } from "../components/Dialog";
import { ErrorMessage } from "../components/ErrorMessage";

type Props = { project: ProjectInfo; open: boolean; onClose: () => void };

function MoveTeamForm({ project, onClose }: Omit<Props, "open">) {
  const teams = useTeams(true);
  const move = useMoveProject(project.id);
  const [team, setTeam] = useState(project.team ?? "");
  const inputId = useId();
  const listId = useId();
  return (
    <div>
      <h2>Move to team</h2>
      <p className="muted">People in the team see this translation. Leave it empty to keep it for the person who made it.</p>
      <div className="field">
        <label htmlFor={inputId}>Team</label>
        <input id={inputId} list={listId} value={team} onChange={(e) => setTeam(e.target.value)} />
        <datalist id={listId}>
          {(teams.data ?? []).map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
      </div>
      {move.isError ? <ErrorMessage error={move.error} /> : null}
      <div className="row end">
        <button type="button" onClick={onClose}>Cancel</button>
        <button
          type="button"
          className="primary"
          disabled={move.isPending}
          onClick={() => move.mutate(team.trim() || null, { onSuccess: onClose })}
        >
          Move
        </button>
      </div>
    </div>
  );
}

/** CGL-26: an admin moves a set into a team. */
export function MoveTeamDialog({ open, onClose, ...rest }: Props) {
  return (
    <Dialog open={open} label="Move to team" onClose={onClose}>
      <MoveTeamForm {...rest} onClose={onClose} />
    </Dialog>
  );
}
```

`SetPage.tsx`: `const [moving, setMoving] = useState(false);` pass `onMoveTeam={() => setMoving(true)}` to `SetHeader` and render `<MoveTeamDialog project={p} open={moving} onClose={() => setMoving(false)} />`.

`MoveTeamDialog.test.tsx`: an error from the PATCH (422 "team name …") is shown in the dialog; an empty box sends `{ team: null }`.

- [ ] **Step 4: Run the web suite**

Run: `cd web && pnpm typecheck && pnpm lint && pnpm test`
Expected: PASS, coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add web
git commit -m "feat(web): teams on new translations, Settings by role, Move to team (CGL-26)"
```

---

### Task 5: The hosted front door passes groups

**Files:**
- Modify: `deploy/hosted/vercel/lib/auth.ts` (`Account`, `parseUsers`), `deploy/hosted/vercel/lib/route.ts` (`Env.users`, `decide`, `forward`, `STRIPPED_HEADERS`), `deploy/hosted/add-user.mjs` (`--groups`), `deploy/hosted/coglossa.hosted.yaml` (`groups_header`)
- Test: `deploy/hosted/vercel/lib/auth.test.ts`, `route.test.ts`, `add-user.test.ts`

**Interfaces:**
- Produces: `type Account = { hash: string; groups: string[] }`; `parseUsers(json) -> Map<string, Account>`; `forward(request, url, account: { name: string; groups: string[] }, env)`; the forwarded header `x-remote-groups` (comma-joined; empty string for none).

- [ ] **Step 1: Write the failing tests**

`auth.test.ts`:

```ts
it("reads users with groups, and plain hashes as users without", () => {
  const users = parseUsers(
    JSON.stringify({ ana: "pbkdf2_sha256$1$a$b", ci: { hash: "pbkdf2_sha256$1$c$d", groups: ["coglossa-admins"] } }),
  );
  expect(users.get("ana")).toEqual({ hash: "pbkdf2_sha256$1$a$b", groups: [] });
  expect(users.get("ci")).toEqual({ hash: "pbkdf2_sha256$1$c$d", groups: ["coglossa-admins"] });
});

it("rejects a malformed account", () => {
  for (const bad of [{ ci: { groups: [] } }, { ci: { hash: "h", groups: "x" } }, { ci: { hash: "h", groups: ["a,b"] } }]) {
    expect(parseUsers(JSON.stringify(bad)).size).toBe(0);
  }
});
```

`route.test.ts`:

```ts
it("forwards the signed-in user's groups and never the client's", async () => {
  // reuse the file's signed-in helper; the account has groups ["coglossa-admins", "demo"]
  const request = new Request("https://site/api/me", {
    headers: { cookie: await sessionCookie("ci"), "x-remote-groups": "forged" },
  });
  const decision = await decide(request, envWith({ ci: { hash: H, groups: ["coglossa-admins", "demo"] } }), NOW);
  expect(decision.kind).toBe("forward");
  if (decision.kind === "forward") expect(decision.headers.get("x-remote-groups")).toBe("coglossa-admins,demo");
});

it("sends an empty groups header for a user without groups", async () => {
  // as above with a plain-hash user: header present and ""
});
```

(Use the existing test helpers the file has for building `Env` and session cookies; write both tests in full.)

`add-user.test.ts`:

```ts
it("adds groups with --groups", () => {
  const r = spawnSync(process.execPath, ["--experimental-strip-types", "--no-warnings", script, "ci", "--groups", "coglossa-admins,demo"], { input: "pw\npw\n", encoding: "utf8" });
  expect(r.status).toBe(0);
  const users = JSON.parse(`{${r.stdout}}`);
  expect(users.ci.groups).toEqual(["coglossa-admins", "demo"]);
  expect(users.ci.hash).toMatch(/^pbkdf2_sha256\$600000\$/);
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd deploy/hosted/vercel && pnpm test`
Expected: FAIL — `users.get("ana")` is a string; no `x-remote-groups`.

- [ ] **Step 3: Implement**

`auth.ts`:

```ts
export type Account = { hash: string; groups: string[] };
const GROUP = /^[A-Za-z0-9._-](?:[A-Za-z0-9._ -]{0,62}[A-Za-z0-9._-])?$/;

function account(value: unknown): Account | null {
  if (typeof value === "string") return { hash: value, groups: [] };
  if (typeof value !== "object" || value === null) return null;
  const { hash, groups = [] } = value as { hash?: unknown; groups?: unknown };
  if (typeof hash !== "string" || !Array.isArray(groups)) return null;
  if (!groups.every((g) => typeof g === "string" && GROUP.test(g))) return null;
  return { hash, groups: groups as string[] };
}

/** Parses COGLOSSA_USERS: name → password hash, or name → {hash, groups} (CGL-26). Anything
 * malformed yields an empty map (which the front door treats as misconfigured). */
export function parseUsers(json: string | undefined): Map<string, Account> {
  try {
    const data: unknown = JSON.parse(json ?? "");
    if (typeof data !== "object" || data === null || Array.isArray(data)) return new Map();
    const users = new Map<string, Account>();
    for (const [name, value] of Object.entries(data as Record<string, unknown>)) {
      const parsed = account(value);
      if (parsed === null || !isValidName(name)) return new Map();
      users.set(name, parsed);
    }
    return users;
  } catch {
    return new Map();
  }
}
```

`route.ts`: `users: Map<string, Account>`; the password check uses `env.users.get(name)?.hash`; `STRIPPED_HEADERS` gains `"x-remote-groups"` (and keeps `x-remote-user` if listed); `decide` ends with `return forward(request, url, { name: user, groups: env.users.get(user)!.groups }, env);`; `forward` sets

```ts
  headers.set("x-remote-user", account.name);
  headers.set("x-remote-groups", account.groups.join(","));
```

`add-user.mjs`: parse `--groups a,b` after the name (split on commas, trim, drop empty, validate with the same pattern, else exit 1 with "group names: letters, digits, spaces, '.', '_' or '-'"); print `"<name>": {"hash": "<hash>", "groups": [...]}` (via `JSON.stringify`) when groups are given, otherwise the existing line.

`coglossa.hosted.yaml` `auth:` gains `groups_header: X-Remote-Groups`.

- [ ] **Step 4: Run the front door's tests**

Run: `cd deploy/hosted/vercel && pnpm test && pnpm typecheck`
Expected: PASS, coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add deploy/hosted
git commit -m "feat(hosted): the front door passes each user's groups (CGL-26)"
```

---

### Task 6: End to end, and the documents

**Files:**
- Modify: `api/devstack.py` (`--people`), `web/playwright.config.ts`
- Create: `web/e2e/people.yaml`, `web/e2e/roles.spec.ts`
- Modify: `docs/operator-guide.md`, `deploy/config/coglossa.example.yaml`, `deploy/hosted/README.md`, `README.md`, `docs/superpowers/specs/2026-10-16-coglossa-ui-ux-design.md`

- [ ] **Step 1: Wire the dev stack**

`devstack.py`: `parser.add_argument("--people", type=Path, help="a people file: sign in by the X-Remote-User header (CGL-26)")`; `config(...)` takes `people: Path | None` and, when set, adds `"auth": {"mode": "trusted_header", "people_file": str(people.resolve())}`.

`web/e2e/people.yaml`:

```yaml
admins: [e2e-admin]
teams:
  desk-a: [ana]
  desk-b: [ben]
```

`playwright.config.ts`: the webServer command gains `--people ../web/e2e/people.yaml`; `use` gains `extraHTTPHeaders: { "X-Remote-User": "e2e-admin" }` (the existing tests run as an admin, as they ran in `none` mode).

- [ ] **Step 2: Write the e2e test**

`web/e2e/roles.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

const STORY = "Le chat dort près de la fenêtre.";

test("a set of one team is not found by another", async ({ browser }) => {
  const ana = await browser.newContext({ extraHTTPHeaders: { "X-Remote-User": "ana" } });
  const page = await ana.newPage();
  await page.goto("/");
  await page.getByLabel("Choose files").setInputFiles({
    name: `desk-a-${Date.now()}.txt`,
    mimeType: "text/plain",
    buffer: Buffer.from(STORY, "utf-8"),
  });
  await page.getByLabel("To", { exact: true }).selectOption({ label: "Other language…" });
  await page.getByLabel("To: language code").fill("en");
  await expect(page.getByLabel("Team")).toHaveCount(0); // one team: no question
  await page.getByRole("button", { name: "Translate" }).click();
  await expect(page).toHaveURL(/\/t\/[0-9a-f-]+$/);
  const address = page.url();
  await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();

  const ben = await browser.newContext({ extraHTTPHeaders: { "X-Remote-User": "ben" } });
  const other = await ben.newPage();
  await other.goto(address);
  await expect(other.getByText(/not found/i)).toBeVisible();
  await other.goto("/settings");
  await expect(other.getByRole("heading", { name: "Appearance" })).toBeVisible();
  await expect(other.getByText("Technical details")).toHaveCount(0);

  const admin = await browser.newContext({ extraHTTPHeaders: { "X-Remote-User": "e2e-admin" } });
  const third = await admin.newPage();
  await third.goto(address);
  await expect(third.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(third.getByText(/desk-a · /)).toBeVisible();
  for (const context of [ana, ben, admin]) await context.close();
});
```

(If the set page for a missing set shows different words than "not found", match the app's words: the assertion follows the app's copy.)

- [ ] **Step 3: Run it**

Run: `make e2e`
Expected: every test passes, the new one included.

- [ ] **Step 4: The documents**

- `docs/operator-guide.md`, after the trusted-header section: a "Teams and admins" section — the visibility rule; `auth.groups_header` (and that the proxy must **always set** that header, to an empty value when the person has no groups, or leave `groups_header` unset); `auth.admin_group`; `auth.people_file` with the YAML example from the spec §3.3; existing sets are admin-only until moved (⋯ ▸ Move to team…); the CLI keeps full access; changes to the file need a restart.
- `deploy/config/coglossa.example.yaml`: under the commented `auth` line, commented `groups_header: X-Remote-Groups`, `admin_group: coglossa-admins`, `people_file: people.yaml`.
- `deploy/hosted/README.md`: `node --experimental-strip-types deploy/hosted/add-user.mjs <name> --groups coglossa-admins` and who is admin on the preview (`ci`), the demo login's team (`demo`).
- `README.md`: one paragraph on teams (who sees which sets, admins, where teams come from).
- UI spec: §6.2 Settings by role; §4.1 the Team field.

- [ ] **Step 5: Full verification and commit**

Run: `make lint && make test && make e2e && make deploy-check`
Expected: all pass.

```bash
git add api/devstack.py web/playwright.config.ts web/e2e README.md docs deploy
git commit -m "test(e2e): teams keep sets apart; docs for CGL-26"
```
