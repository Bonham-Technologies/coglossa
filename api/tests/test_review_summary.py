"""CGL-112: the review summary routes are read-only and for admins only."""

import codecs

import pytest
from coglossa import catalog, review
from coglossa.testing.seed import seed_run

from tests.test_access import AUTH, as_

ROUTES = [
    "/api/runs/{run_id}/review-summary",
    "/api/runs/{run_id}/review-summary.csv",
    "/api/projects/{project_id}/review-summary",
    "/api/projects/{project_id}/review-summary.csv",
]


@pytest.fixture
def people(tmp_path):
    (tmp_path / "people.yaml").write_text(
        "admins: [root]\nteams:\n  pacific: [tevita]\n", encoding="utf-8"
    )


def _url(path, seeded):
    return path.format(run_id=seeded.run_id, project_id=seeded.project_id)


async def test_a_run_summary(make_api, db):
    seeded = await seed_run(db)
    await review.edit_span(db, seeded.run_id, seeded.span_ids[0], "Chapter 1", user="Ana")
    async with make_api() as api:
        response = await api.client.get(f"/api/runs/{seeded.run_id}/review-summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["run_id"], body["passages"], body["checked"], body["edited"]) == (
        str(seeded.run_id),
        3,
        1,
        1,
    )
    assert (body["edit_distance_total"], body["mean_edit_distance"]) == (3, 3.0)
    assert {f["code"] for f in body["flags"]} == {
        "low_meaning",
        "agent_disagreement",
        "terminology_violation",
    }


async def test_a_set_summary_per_language(make_api, db):
    seeded = await seed_run(db, project="desk")
    await seed_run(db, project="desk", filename="other.txt", target_lang="de")
    async with make_api() as api:
        response = await api.client.get(f"/api/projects/{seeded.project_id}/review-summary")
    assert response.status_code == 200, response.text
    languages = response.json()["languages"]
    assert [(lang["target_lang"], lang["passages"]) for lang in languages] == [
        ("en", 3),
        ("de", 3),
    ]


async def test_the_csv_downloads(make_api, db):
    seeded = await seed_run(db, project="desk")
    await review.accept_span(db, seeded.run_id, seeded.span_ids[0], user="Ana Lima")
    async with make_api() as api:
        run = await api.client.get(f"/api/runs/{seeded.run_id}/review-summary.csv")
        lang = await api.client.get(
            f"/api/projects/{seeded.project_id}/review-summary.csv?target_lang=en"
        )
    assert run.status_code == lang.status_code == 200
    assert run.headers["content-type"] == "text/csv; charset=utf-8"
    assert run.headers["content-disposition"].startswith(
        'attachment; filename="story.en.review-summary.csv"'
    )
    assert lang.headers["content-disposition"].startswith(
        'attachment; filename="desk.en.review-summary.csv"'
    )
    assert run.content.startswith(codecs.BOM_UTF8)
    lines = run.content.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("file,language,passage,flags")
    assert "Ana Lima" in lines[1] and len(lines) == 4


@pytest.mark.parametrize("path", ROUTES)
async def test_only_admins_see_the_summary(people, make_api, db, path):
    seeded = await seed_run(db, project="pacific-set")
    await catalog.set_team(db, seeded.project_id, "Pacific")
    async with make_api(auth=AUTH) as api:
        member = await api.client.get(_url(path, seeded), headers=as_("tevita"))
        outsider = await api.client.get(_url(path, seeded), headers=as_("maria"))
        admin = await api.client.get(_url(path, seeded), headers=as_("root"))
    # a member who sees the set is refused as on the other operator screens
    assert member.status_code == 403
    assert member.json()["detail"].startswith("only an administrator can")
    # a set the person cannot see stays invisible
    assert outsider.status_code == 404
    assert admin.status_code == 200


@pytest.mark.parametrize("path", ROUTES)
async def test_the_summary_has_no_write_path(make_api, db, path):
    seeded = await seed_run(db)
    async with make_api() as api:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = await api.client.request(method, _url(path, seeded))
            assert response.status_code == 405, (method, path)
