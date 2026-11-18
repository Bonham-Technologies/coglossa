"""CGL-26: who sees which sets, through the API."""

import re
import uuid

import pytest
from coglossa import catalog
from coglossa.testing.seed import seed_run

from coglossa_api.auth import _SCOPED as _GUARDED
from tests.test_app import PROTECTED, _fill

AUTH = {
    "mode": "trusted_header",
    "groups_header": "X-Remote-Groups",
    "people_file": "people.yaml",
}


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
    (method, path)
    for method, path in PROTECTED
    if any(key in path for key in ("{project_id}", "{document_id}", "{run_id}"))
]


def test_scoped_routes_are_listed():
    assert len(SCOPED) > 20


# Ids the guard does not check because each names something inside the run in the same path,
# and its route looks it up within that run (CGL-31). A new id goes here only when that holds.
INSIDE_A_RUN = frozenset({"span_id", "flag_id"})


def _unguarded(routes):
    """The routes with a path parameter that the guard neither checks (auth._SCOPED) nor may
    skip: an id inside a checked run (INSIDE_A_RUN), or a parameter that names no thing (not
    `..._id`, such as an export's `kind`) on a path with a checked id. A parameter on a path
    with no checked id is always reported, whatever its name."""
    found = []
    for method, path in routes:
        names = re.findall(r"\{(\w+)(?::\w+)?\}", path)
        checked = any(name in _GUARDED for name in names)
        for name in names:
            if name in _GUARDED or (name in INSIDE_A_RUN and "run_id" in names):
                continue
            if checked and not name.endswith("_id"):
                continue
            found.append((method, path, name))
    return found


def test_every_id_in_a_path_is_guarded_or_inside_a_guarded_run():
    assert _unguarded(PROTECTED) == []
    # SCOPED, which the outsider tests above call, must name every id the guard checks.
    assert set(_GUARDED) == {"project_id", "document_id", "run_id"}


def test_the_id_check_catches_a_new_kind_of_id():
    routes = [
        ("GET", "/api/docs/{doc_id}"),
        ("GET", "/api/projects/{project_id}/guide/versions/{version_id}"),
        ("GET", "/api/spans/{span_id}"),  # a span outside a run's path
        ("GET", "/api/runs/{run_id}/spans/{span_id}"),
        ("GET", "/api/runs/{run_id}/exports/{kind}"),  # not a thing, inside a checked run
        ("GET", "/api/exports/{token}"),  # not named like an id, and nothing checked
        ("GET", "/api/files/{path:path}"),
    ]
    assert [name for _, _, name in _unguarded(routes)] == [
        "doc_id",
        "version_id",
        "span_id",
        "token",
        "path",
    ]


async def test_names_in_utf8_headers_are_read_as_utf8(make_api, tmp_path):
    """CGL-31: proxies send UTF-8; Starlette reads header bytes as latin-1. A name keeps its
    letters, is normalised (NFC), matches the people file and finds its own personal sets."""
    (tmp_path / "people.yaml").write_text(
        "admins: [root]\nteams:\n  pacific: [Jose\u0301]\n", encoding="utf-8"
    )
    composed = {"X-Remote-User": "Jos\u00e9".encode()}
    decomposed = {"X-Remote-User": "Jose\u0301".encode()}
    latin1 = {"X-Remote-User": "Jos\u00e9".encode("latin-1")}  # not UTF-8: read as latin-1
    async with make_api(auth=AUTH) as api:
        me = (await api.client.get("/api/me", headers=composed)).json()
        made = await api.client.post(
            "/api/projects", json={"name": "Mine", "target_lang": "en"}, headers=decomposed
        )
        mine = await api.client.get("/api/projects", headers=composed)
        same = await api.client.get(f"/api/projects/{made.json()['id']}", headers=latin1)
        other = await api.client.get("/api/projects", headers={"X-Remote-User": "jose"})
    assert (me["user"], me["teams"]) == ("Jos\u00e9", ["pacific"])
    assert made.status_code == 201 and made.json()["created_by"] == "Jos\u00e9"
    assert [p["name"] for p in mine.json()] == ["Mine"]
    assert same.status_code == 200
    assert other.json() == []


async def test_a_name_with_a_non_ascii_space_is_another_person(make_api, tmp_path):
    """CGL-31: once headers are read as UTF-8, "root" followed by U+3000 must not be trimmed
    to "root" (an admin), nor a group ending in U+00A0 to the admin group."""
    (tmp_path / "people.yaml").write_text("admins: [root]\n", encoding="utf-8")
    spaced = {"X-Remote-User": "root\u3000".encode()}
    grouped = {"X-Remote-User": "ben", "X-Remote-Groups": "coglossa-admins\u00a0".encode()}
    async with make_api(auth=AUTH) as api:
        me = (await api.client.get("/api/me", headers=spaced)).json()
        group = (await api.client.get("/api/me", headers=grouped)).json()
    assert (me["user"], me["admin"]) == ("root\u3000", False)
    assert group["admin"] is False


async def test_a_bad_body_on_a_hidden_set_still_reads_as_missing(people, make_api, db):
    """The guard runs before the body is validated, so a body the route would refuse with
    422 tells an outsider nothing (CGL-31: the order the guard's docstring describes)."""
    seeded = await _desk_set(db)
    url = f"/api/projects/{seeded.project_id}"
    async with make_api(auth=AUTH) as api:
        outsider = await api.client.patch(url, json={"name": 5}, headers=as_("maria"))
        member = await api.client.patch(url, json={"name": 5}, headers=as_("tevita"))
    assert outsider.status_code == 404 and member.status_code == 422


@pytest.mark.parametrize(("method", "path"), SCOPED)
async def test_an_outsider_gets_not_found_on_every_route_of_a_set(
    people, make_api, db, method, path
):
    seeded = await _desk_set(db)
    async with make_api(auth=AUTH) as api:
        hidden = await api.client.request(
            method, _real(path, seeded), headers=as_("maria", "spanish-desk"), json={}
        )
    assert hidden.status_code == 404, (path, hidden.text)
    kind = "run" if "{run_id}" in path else "document" if "{document_id}" in path else "project"
    assert re.fullmatch(rf"{kind} [0-9a-f-]+ not found", hidden.json()["detail"]), hidden.json()


@pytest.mark.parametrize(("method", "path"), SCOPED)
async def test_a_missing_id_reads_the_same_as_a_hidden_one(people, make_api, method, path):
    async with make_api(auth=AUTH) as api:
        missing = await api.client.request(method, _fill(path), headers=as_("maria"), json={})
    assert missing.status_code == 404, (path, missing.text)
    kind = "run" if "{run_id}" in path else "document" if "{document_id}" in path else "project"
    assert re.fullmatch(rf"{kind} [0-9a-f-]+ not found", missing.json()["detail"])


async def test_team_members_and_admins_see_the_set(people, make_api, db):
    seeded = await _desk_set(db)
    url = f"/api/runs/{seeded.run_id}"
    async with make_api(auth=AUTH) as api:
        member = await api.client.get(url, headers=as_("tevita"))
        by_group = await api.client.get(url, headers=as_("x", "PACIFIC"))
        admin = await api.client.get(url, headers=as_("root"))
        listed = await api.client.get("/api/projects", headers=as_("maria", "spanish-desk"))
        members_list = await api.client.get("/api/projects", headers=as_("tevita"))
    assert member.status_code == by_group.status_code == admin.status_code == 200
    assert listed.json() == []
    assert [p["name"] for p in members_list.json()] == ["pacific-set"]


async def test_run_list_by_query_hides_other_teams(people, make_api, db):
    seeded = await _desk_set(db)
    async with make_api(auth=AUTH) as api:
        runs = await api.client.get(
            f"/api/runs?project_id={seeded.project_id}", headers=as_("maria")
        )
        everything = await api.client.get("/api/runs", headers=as_("maria"))
    assert runs.status_code == 200 and runs.json() == [] and everything.json() == []


async def test_a_groups_header_is_ignored_unless_configured(people, make_api, db):
    seeded = await _desk_set(db)
    auth = {"mode": "trusted_header", "people_file": "people.yaml"}  # no groups_header
    forged = as_("maria", "coglossa-admins,pacific")
    async with make_api(auth=auth) as api:
        me = await api.client.get("/api/me", headers=forged)
        run = await api.client.get(f"/api/runs/{seeded.run_id}", headers=forged)
        config = await api.client.get("/api/system/config", headers=forged)
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
        tiers = await api.client.get("/api/system/tiers", headers=as_("tevita"))
    assert tiers.status_code == 200


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
    body = {"name": "Mine", "target_lang": "en"}
    async with make_api(auth=AUTH) as api:
        made = await api.client.post("/api/projects", json=body, headers=as_("maria"))
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
        url = f"/api/projects/{pid}"
        rename = await api.client.patch(url, json={"name": "Desk 2"}, headers=as_("tevita"))
        move = await api.client.patch(url, json={"team": "delta"}, headers=as_("tevita"))
        moved = await api.client.patch(url, json={"team": "delta"}, headers=as_("root"))
        teams = await api.client.get("/api/teams", headers=as_("root"))
        empty = await api.client.patch(url, json={}, headers=as_("root"))
        bad = await api.client.patch(url, json={"team": "a/b"}, headers=as_("root"))
        personal = await api.client.patch(url, json={"team": None}, headers=as_("root"))
    assert ok.status_code == 201 and ok.json()["team"] == "PACIFIC"
    assert refused.status_code == 422 and "not in team 'delta'" in refused.json()["detail"]
    assert rename.json()["name"] == "Desk 2"
    assert move.status_code == 403
    assert moved.json()["team"] == "delta"
    assert teams.json() == ["delta", "pacific"]
    assert empty.status_code == bad.status_code == 422
    assert personal.json()["team"] is None


async def test_an_admin_can_make_a_set_in_any_team(people, make_api):
    async with make_api(auth=AUTH) as api:
        made = await api.client.post(
            "/api/projects",
            json={"name": "Any", "target_lang": "en", "team": "omega"},
            headers=as_("root"),
        )
    assert made.status_code == 201 and made.json()["team"] == "omega"


async def test_a_person_whose_only_group_cannot_be_a_team_still_makes_sets(people, make_api):
    headers = as_("maria", "/translators/es")
    async with make_api(auth=AUTH) as api:
        me = (await api.client.get("/api/me", headers=headers)).json()
        made = await api.client.post(
            "/api/projects", json={"name": "Mine", "target_lang": "en"}, headers=headers
        )
    assert me["teams"] == []
    assert made.status_code == 201 and made.json()["team"] is None


# CGL-74: with auth.team_groups_prefix (or auth.team_groups) only matching groups are teams.
FILTERED = {**AUTH, "team_groups_prefix": "coglossa-", "team_groups": ["pacific"]}


async def test_a_filtered_out_group_is_no_team(people, make_api, db):
    broad = await seed_run(db, project="everyone-set")
    await catalog.set_team(db, broad.project_id, "Domain Users")
    desk = await seed_run(db, project="desk-set")
    await catalog.set_team(db, desk.project_id, "Coglossa-Desk")
    headers = as_("ben", "Domain Users,coglossa-desk")
    async with make_api(auth=FILTERED) as api:
        me = (await api.client.get("/api/me", headers=headers)).json()
        listed = await api.client.get("/api/projects", headers=headers)
        hidden = await api.client.get(f"/api/runs/{broad.run_id}", headers=headers)
        shown = await api.client.get(f"/api/runs/{desk.run_id}", headers=headers)
        member = await api.client.get("/api/me", headers=as_("tevita", "Domain Users"))
    assert me["teams"] == ["coglossa-desk"] and me["admin"] is False
    assert [p["name"] for p in listed.json()] == ["desk-set"]
    assert hidden.status_code == 404 and shown.status_code == 200
    assert member.json()["teams"] == ["pacific"]  # the people file's team is on the list


async def test_without_a_filter_every_group_is_still_a_team(people, make_api, db):
    broad = await seed_run(db, project="everyone-set")
    await catalog.set_team(db, broad.project_id, "Domain Users")
    headers = as_("ben", "Domain Users")
    async with make_api(auth=AUTH) as api:
        me = (await api.client.get("/api/me", headers=headers)).json()
        run = await api.client.get(f"/api/runs/{broad.run_id}", headers=headers)
    assert me["teams"] == ["Domain Users"] and run.status_code == 200


async def test_the_admin_group_is_no_team_whatever_the_prefix(people, make_api):
    async with make_api(auth=FILTERED) as api:
        me = (await api.client.get("/api/me", headers=as_("x", "Coglossa-Admins"))).json()
    assert me["admin"] is True and me["teams"] == []


async def test_sets_go_only_to_teams_the_filter_allows(people, make_api, db):
    seeded = await seed_run(db, project="a-set")
    await catalog.set_team(db, seeded.project_id, "Domain Users")
    url = f"/api/projects/{seeded.project_id}"
    root = as_("root")
    async with make_api(auth=FILTERED) as api:
        teams = await api.client.get("/api/teams", headers=as_("root", "coglossa-es,Staff"))
        make = await api.client.post(
            "/api/projects",
            json={"name": "Broad", "target_lang": "en", "team": "Domain Users"},
            headers=root,
        )
        move = await api.client.patch(url, json={"team": "Staff"}, headers=root)
        moved = await api.client.patch(url, json={"team": "Coglossa-ES"}, headers=root)
        allowed = await api.client.patch(url, json={"team": "PACIFIC"}, headers=root)
        personal = await api.client.patch(url, json={"team": None}, headers=root)
        to_admins = await api.client.patch(url, json={"team": "Coglossa-Admins"}, headers=root)
    assert teams.json() == ["coglossa-es", "pacific"]  # not "Domain Users", from the set
    assert to_admins.status_code == 422
    assert to_admins.json()["detail"] == (
        "'Coglossa-Admins' is the administrators' group, not a team: choose another team"
    )
    assert make.status_code == 422 and move.status_code == 422
    assert make.json()["detail"].startswith("'Domain Users' is not a team in Coglossa")
    assert moved.json()["team"] == "Coglossa-ES" and allowed.json()["team"] == "PACIFIC"
    assert personal.status_code == 200 and personal.json()["team"] is None


async def test_an_empty_allowlist_leaves_no_teams(people, make_api):
    async with make_api(auth={**AUTH, "team_groups": []}) as api:
        me = (await api.client.get("/api/me", headers=as_("tevita", "pacific"))).json()
        made = await api.client.post(
            "/api/projects",
            json={"name": "Desk", "target_lang": "en", "team": "pacific"},
            headers=as_("root"),
        )
    assert me["teams"] == []
    assert made.status_code == 422
    assert made.json()["detail"] == "'pacific' is not a team in Coglossa: there are no teams"


async def test_only_those_who_may_edit_the_guide_import_a_glossary(people, make_api, db):
    """CGL-88: importing terms changes the guide, so it needs what saving the guide needs:
    being able to see the set (there is no read-only role). An outsider's import, with a
    readable file, reads as a missing set and writes nothing; a team member's goes through."""
    seeded = await _desk_set(db)
    base = f"/api/projects/{seeded.project_id}/guide"
    upload = {"file": ("terms.csv", b"pluie,rain\n", "text/csv")}
    form = {"base_version": "1"}
    async with make_api(auth=AUTH) as api:
        outsider = [
            await api.client.post(url, files=upload, data=form, headers=as_("maria", "spanish"))
            for url in (f"{base}/import/preview", f"{base}/import")
        ]
        member = await api.client.post(
            f"{base}/import", files=upload, data=form, headers=as_("tevita")
        )
        versions = await api.client.get(f"{base}/versions", headers=as_("tevita"))
    assert [r.status_code for r in outsider] == [404, 404]
    assert all(r.json()["detail"].startswith("project ") for r in outsider)
    assert member.status_code == 200, member.text
    assert [v["version"] for v in versions.json()] == [2, 1]  # the member's import only
