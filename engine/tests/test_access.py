import uuid

import pytest

from coglossa import catalog
from coglossa.access import EVERYONE, can_see, check_visible, viewer_for
from coglossa.people import ALL_TEAMS, NOBODY, People, TeamFilter
from coglossa.store import runs
from coglossa.store.models import Project
from coglossa.testing.seed import seed_run

PEOPLE = People(admins=frozenset({"root"}), teams_by_person={"maria": ("spanish-desk",)})


def _viewer(name, groups=(), teams=ALL_TEAMS, people=PEOPLE):
    return viewer_for(name, groups, people, "coglossa-admins", teams=teams)


def test_a_viewer_unites_the_proxy_groups_and_the_file():
    v = _viewer("Maria", [" Pacific ", "", "coglossa-admins"])
    assert v.admin and v.name == "Maria"
    assert v.team_names == ("Pacific", "spanish-desk")
    assert v.teams == {"pacific", "spanish-desk"}
    assert _viewer("root").admin and not _viewer("ben").admin
    assert _viewer("ben").teams == frozenset()
    assert EVERYONE.admin and EVERYONE.name is None


def test_a_group_with_a_non_ascii_space_is_not_the_admin_group():
    """CGL-31: headers are read as UTF-8, so a group can end in U+00A0 or U+3000; only ASCII
    whitespace is trimmed, so such a group is neither the admin group nor a team."""
    v = _viewer("ben", ["coglossa-admins\u00a0", "pacific\u3000", " desk\t"])
    assert not v.admin and v.teams == {"desk"}


@pytest.mark.parametrize(
    ("team", "created_by", "viewer", "seen"),
    [
        ("Spanish-Desk", None, ("maria", ()), True),  # their team, any case
        ("pacific", None, ("maria", ()), False),
        (None, "MARIA", ("maria", ()), True),  # their personal set
        (None, "ben", ("maria", ()), False),
        (None, None, ("maria", ()), False),  # a set from before teams: admins only
        (None, None, ("root", ()), True),
        ("pacific", None, ("nobody-in-particular", ()), False),  # no teams, no own sets
        # CGL-31: names outside A-Z. The database must agree with Python whatever its collation.
        (None, "\u00c9milie", ("\u00c9milie", ()), True),
        (None, "\u00c9MILIE", ("\u00c9milie", ()), True),  # A-Z in any case
        (None, "\u00e9milie", ("\u00c9milie", ()), False),  # other letters as written
        (None, "Jose\u0301", ("Jos\u00e9", ()), True),  # e + accent is \u00e9 (NFC)
        (None, "strasse", ("Stra\u00dfe", ()), False),  # no full case folding
        (None, "maria", ("maria\u3000", ()), False),  # only ASCII spaces are trimmed
        (None, "maria\u00a0", ("maria", ()), False),
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
        doc = seeded.document_id
        with pytest.raises(runs.NotFoundError, match=f"^document {doc} not found$"):
            await check_visible(s, outsider, document_id=doc)
        missing = uuid.uuid4()
        with pytest.raises(runs.NotFoundError, match=f"^run {missing} not found$"):
            await check_visible(s, outsider, run_id=missing)
        await check_visible(s, outsider)  # nothing named: nothing to refuse
        await check_visible(s, _viewer("ben", ["Pacific"]), run_id=seeded.run_id)
        await check_visible(s, EVERYONE, run_id=seeded.run_id)
    assert await catalog.list_runs(db, viewer=outsider) == []
    assert await catalog.list_runs(db, project_id=seeded.project_id, viewer=outsider) == []
    assert len(await catalog.list_runs(db, viewer=_viewer("ben", ["pacific"]))) == 1
    assert len(await catalog.list_runs(db)) == 1  # the CLI sees everything


async def test_known_teams(db):
    await catalog.create_project(db, "a", target_lang="en", team="Pacific")
    await catalog.create_project(db, "b", target_lang="en", team="pacific")  # same team
    teams = await catalog.known_teams(db, PEOPLE, _viewer("x", ["Delta"]), ALL_TEAMS)
    assert teams == ["Delta", "Pacific", "spanish-desk"]
    assert await catalog.known_teams(db, NOBODY, EVERYONE, ALL_TEAMS) == ["Pacific"]


async def test_known_teams_leave_out_what_the_filter_does(db):
    """CGL-74: the picker offers only teams the filter allows, from every source."""
    await catalog.create_project(db, "a", target_lang="en", team="Domain Users")
    await catalog.create_project(db, "b", target_lang="en", team="coglossa-pacific")
    await catalog.create_project(db, "c", target_lang="en", team="coglossa-admins")
    people = People(teams_by_person={"maria": ("spanish-desk", "Coglossa-ES")})
    only = TeamFilter.of("coglossa-", None, "coglossa-admins")
    viewer = _viewer("x", ["Delta", "coglossa-delta"], only)
    teams = await catalog.known_teams(db, people, viewer, only)
    assert teams == ["coglossa-delta", "Coglossa-ES", "coglossa-pacific"]


def test_groups_that_cannot_be_teams_are_left_out():
    """A proxy may send groups in any shape (Keycloak paths, LDAP names, accents): only names a
    set can carry become teams; the admin group still counts (review finding)."""
    v = viewer_for(
        "maria",
        ["/translators/es", "Équipe", "x" * 65, "pacific", "coglossa-admins"],
        NOBODY,
        "coglossa-admins",
        teams=ALL_TEAMS,
    )
    assert v.team_names == ("pacific",) and v.admin


# CGL-74: auth.team_groups_prefix and auth.team_groups decide which groups become teams.
GROUPS = ["Domain Users", " Coglossa-Pacific ", "coglossa-admins", "Spanish-Desk", "coglossa-"]


def test_without_a_team_filter_every_group_is_a_team():
    v = _viewer("ben", GROUPS)
    assert v.admin
    assert v.team_names == ("coglossa-", "Coglossa-Pacific", "Domain Users", "Spanish-Desk")


def test_a_prefix_keeps_only_the_groups_that_start_with_it():
    """Compared as team_key compares: ASCII ends trimmed, A-Z in any case. The admin group
    starts with the prefix and still is no team, but still makes its members admins."""
    v = _viewer("ben", GROUPS, TeamFilter.of("COGLOSSA-", None))
    assert v.admin
    assert v.team_names == ("coglossa-", "Coglossa-Pacific")
    assert v.teams == {"coglossa-", "coglossa-pacific"}
    assert not TeamFilter.of("coglossa-", None).allows("xcoglossa-desk")


def test_an_active_filter_never_allows_the_admin_group():
    """So the picker and set creation never offer it either; without a filter, unchanged."""
    only = TeamFilter.of("coglossa-", ["Coglossa-Admins", "desk"], "coglossa-admins")
    assert not only.allows(" COGLOSSA-ADMINS ") and only.allows("coglossa-es")
    assert only.allows("Desk")
    assert TeamFilter.of(None, None, "coglossa-admins").allows("coglossa-admins")


def test_an_allowlist_keeps_only_the_groups_on_it():
    v = _viewer("ben", GROUPS, TeamFilter.of(None, ["spanish-desk ", "Coglossa-Admins", "x"]))
    assert v.admin and v.team_names == ("Spanish-Desk",)
    # NFC, as every name compares: a decomposed accent matches the composed one
    assert TeamFilter.of(None, ["Jose\u0301"]).allows("Jos\u00e9")
    assert not TeamFilter.of(None, ["strasse"]).allows("Stra\u00dfe")


def test_a_group_matches_if_it_has_the_prefix_or_is_on_the_list():
    v = _viewer("ben", GROUPS, TeamFilter.of("coglossa-", ["spanish-desk"]))
    assert v.team_names == ("coglossa-", "Coglossa-Pacific", "Spanish-Desk")


def test_an_empty_allowlist_lets_no_group_be_a_team():
    v = _viewer("ben", GROUPS, TeamFilter.of(None, []))
    assert v.admin and v.team_names == () and v.teams == frozenset()


def test_the_filter_applies_to_the_people_files_teams_too():
    """One rule for what a team is, whichever source names it (doctor warns about people
    file teams it leaves out)."""
    people = People(teams_by_person={"maria": ("spanish-desk", "coglossa-es")})
    v = _viewer("maria", ["Domain Users"], TeamFilter.of("coglossa-", None), people)
    assert v.team_names == ("coglossa-es",) and not v.admin


async def test_a_filtered_out_group_grants_no_access_to_its_teams_set(db):
    """A set already in a broad group's team: with the filter on, that group no longer shows
    it to its members (admins still see it and can move it)."""
    info = await catalog.create_project(db, "everyone-set", target_lang="en", team="Domain Users")
    member = _viewer("ben", ["Domain Users"], TeamFilter.of("coglossa-", None))
    before = _viewer("ben", ["Domain Users"])
    assert member.teams == frozenset() and before.teams == {"domain users"}
    assert info.id not in {p.id for p in await catalog.list_projects(db, member)}
    assert info.id in {p.id for p in await catalog.list_projects(db, before)}
    async with db.session() as s:
        project = await s.get(Project, uuid.UUID(info.id))
        assert not can_see(member, project)
        with pytest.raises(runs.NotFoundError, match=f"^project {info.id} not found$"):
            await check_visible(s, member, project_id=project.id)
