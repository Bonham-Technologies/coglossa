from coglossa.glossary_import import MAX_FIELD_CHARS
from coglossa.schema import Guide, RunState
from coglossa.store import runs
from coglossa.testing.seed import seed_run

from coglossa_api.models import SHORT_MAX

LOCKED = {
    "summary": "A cat sleeps while it rains.",
    "style_guide": "Plain.",
    "glossary": [{"term": "Fin", "rendering": "The End", "locked": True}],
}


async def test_run_guide_and_approval(api, db):
    seeded = await seed_run(db, state=RunState.AWAITING_GUIDE_APPROVAL)
    view = (await api.client.get(f"/api/runs/{seeded.run_id}/guide")).json()
    assert view["info"]["version"] == 1 and view["guide"]["glossary"][0]["term"] == "chat"
    approved = await api.client.post(
        f"/api/runs/{seeded.run_id}/guide/approve", json={"guide": LOCKED}
    )
    assert approved.status_code == 202
    assert approved.json()["message"] == "guide version 2 approved; translation queued"
    assert approved.json()["progress"]["state"] == "translating"
    assert [j.task for j in api.queue.jobs] == ["coglossa.translate_run"]
    view = (await api.client.get(f"/api/runs/{seeded.run_id}/guide")).json()
    assert (view["info"]["version"], view["info"]["author"]) == (2, "user")
    again = await api.client.post(f"/api/runs/{seeded.run_id}/guide/approve")
    assert again.status_code == 409
    assert "code" not in again.json()


async def test_approve_as_generated(api, db):
    seeded = await seed_run(db, state=RunState.AWAITING_GUIDE_APPROVAL)
    approved = await api.client.post(f"/api/runs/{seeded.run_id}/guide/approve")
    assert approved.json()["message"] == "guide version 2 approved; translation queued"
    view = (await api.client.get(f"/api/runs/{seeded.run_id}/guide")).json()
    assert view["info"]["author"] == "engine" and view["info"]["status"] == "approved"


async def test_approve_refuses_a_stale_draft(api, db):
    seeded = await seed_run(db, state=RunState.AWAITING_GUIDE_APPROVAL)
    saved = await api.client.put(
        f"/api/projects/{seeded.project_id}/guide", json={"guide": LOCKED, "base_version": None}
    )
    assert saved.status_code == 200
    url = f"/api/runs/{seeded.run_id}/guide/approve"
    stale = await api.client.post(url)
    assert stale.status_code == 409
    assert stale.json() == {
        "detail": (
            "the project guide changed since this draft (now version 2); reload it and approve "
            "the current guide"
        ),
        "code": "guide_changed",
    }
    no_body = await api.client.post(url, json={"base_version": 2})
    assert no_body.status_code == 422
    assert no_body.json()["detail"] == (
        "base_version is only used with an edited guide; send the guide you approve"
    )
    versions = (await api.client.get(f"/api/projects/{seeded.project_id}/guide/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]
    approved = await api.client.post(url, json={"guide": LOCKED, "base_version": 2})
    assert approved.status_code == 202
    assert approved.json()["message"] == "guide version 3 approved; translation queued"


async def test_approve_measures_a_draft_against_the_guide_it_was_built_from(api, db):
    """The guide stage read v2, a person saved v3 meanwhile, and the draft became v4."""
    seeded = await seed_run(db, state=RunState.AWAITING_GUIDE_APPROVAL)
    project = f"/api/projects/{seeded.project_id}/guide"
    for base in (None, 2):
        saved = await api.client.put(project, json={"guide": LOCKED, "base_version": base})
        assert saved.status_code == 200
    async with db.session() as s, s.begin():
        draft = await runs.add_guide_version(
            s,
            seeded.project_id,
            Guide(summary="Built from v2.", style_guide="Plain."),
            target_lang="en",
            status=runs.DRAFT,
            author=runs.ENGINE,
            source_run_id=seeded.run_id,
            built_from_version=2,
        )
        (await runs.get_run(s, seeded.run_id, lock=True)).guide_version_id = draft.id
    assert draft.version == 4
    url = f"/api/runs/{seeded.run_id}/guide/approve"
    stale = (
        "the project guide changed since this draft (now version 3); reload it and approve the "
        "current guide"
    )
    for body in (None, {"guide": LOCKED, "base_version": 4}, {"guide": LOCKED, "base_version": 2}):
        refused = await api.client.post(url, json=body)
        assert (refused.status_code, refused.json()) == (
            409,
            {"detail": stale, "code": "guide_changed"},
        )
    bogus = await api.client.post(url, json={"guide": LOCKED, "base_version": 9})
    assert (bogus.status_code, bogus.json()["detail"]) == (
        422,
        "base_version 9 is neither this run's draft (version 4) nor the project guide (version 3)",
    )
    assert api.queue.jobs == []
    approved = await api.client.post(url, json={"guide": LOCKED, "base_version": 3})
    assert approved.status_code == 202
    assert approved.json()["message"] == "guide version 5 approved; translation queued"


async def test_project_guide_editing(api, db):
    seeded = await seed_run(db)
    base = f"/api/projects/{seeded.project_id}/guide"
    assert (await api.client.get(base)).json()["info"]["version"] == 1
    saved = await api.client.put(base, json={"guide": LOCKED, "base_version": 1})
    assert saved.status_code == 200 and saved.json()["info"]["status"] == "approved"
    stale = await api.client.put(base, json={"guide": LOCKED, "base_version": 1})
    assert stale.status_code == 409 and "reload it" in stale.json()["detail"]
    assert stale.json()["code"] == "guide_changed"
    versions = (await api.client.get(f"{base}/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]
    old = (await api.client.get(base, params={"version": 1})).json()
    assert old["guide"]["glossary"][0]["term"] == "chat"
    assert (await api.client.get(base, params={"version": 9})).status_code == 404
    bad = await api.client.put(base, json={"guide": {"summary": "x"}, "base_version": 2})
    assert bad.status_code == 422


async def test_the_set_guide_of_each_language(api, db):
    english = await seed_run(db)
    spanish = await seed_run(db, target_lang="es")
    assert spanish.project_id == english.project_id
    base = f"/api/projects/{english.project_id}/guide"
    first = (await api.client.get(base)).json()
    assert first["info"]["target_lang"] == "en"  # the set's first language
    es = (await api.client.get(base, params={"target_lang": "es"})).json()
    assert (es["info"]["target_lang"], es["info"]["version"]) == ("es", 1)
    saved = await api.client.put(
        base, params={"target_lang": "es"}, json={"guide": LOCKED, "base_version": 1}
    )
    assert saved.status_code == 200 and saved.json()["info"]["version"] == 2
    versions = (await api.client.get(f"{base}/versions", params={"target_lang": "es"})).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert [v["version"] for v in (await api.client.get(f"{base}/versions")).json()] == [1]
    one = await api.client.get(base, params={"target_lang": "es", "version": 1})
    assert one.json()["info"]["target_lang"] == "es"


# CGL-88: importing a glossary file ---------------------------------------------------------

CSV = ("terms.csv", b"term,translation\nchat,kitty\npluie,rain\n", "text/csv")


async def test_glossary_preview_then_import(api, db):
    seeded = await seed_run(db)  # chat → cat
    base = f"/api/projects/{seeded.project_id}/guide/import"
    preview = await api.client.post(f"{base}/preview", files={"file": CSV})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["format"], body["target_lang"], body["base_version"]) == ("csv", "en", 1)
    assert (body["new"], body["identical"], body["conflicts"], body["duplicates"]) == (1, 0, 1, 0)
    assert body["terms"][0] == {
        "line": 2,
        "term": "chat",
        "rendering": "kitty",
        "status": "conflict",
        "current_rendering": "cat",
        "current_locked": False,
        "first_line": None,
    }
    versions = (await api.client.get(f"/api/projects/{seeded.project_id}/guide/versions")).json()
    assert len(versions) == 1  # the preview wrote nothing

    done = await api.client.post(
        base, files={"file": CSV}, data={"base_version": "1", "lock_all": "true"}
    )
    assert done.status_code == 200, done.text
    result = done.json()
    assert (result["added"], result["kept"], result["locked"], result["created_version"]) == (
        1,
        1,
        1,
        True,
    )
    assert (result["view"]["info"]["version"], result["view"]["info"]["author"]) == (2, "user")
    glossary = result["view"]["guide"]["glossary"]
    assert [(g["term"], g["rendering"], g["locked"]) for g in glossary] == [
        ("chat", "cat", False),
        ("pluie", "rain", True),
    ]

    stale = await api.client.post(base, files={"file": CSV}, data={"base_version": "1"})
    assert stale.status_code == 409 and stale.json()["code"] == "guide_changed"
    replaced = await api.client.post(
        base, files={"file": CSV}, data={"base_version": "2", "overwrite_conflicts": "true"}
    )
    assert replaced.json()["view"]["guide"]["glossary"][0]["rendering"] == "kitty"


async def test_glossary_import_of_another_language_and_a_tbx_source(api, db):
    seeded = await seed_run(db)
    await seed_run(db, target_lang="es")
    tbx = (
        b'<martif><termEntry><langSet xml:lang="de"><term>Katze</term></langSet>'
        b'<langSet xml:lang="fr"><term>chat</term></langSet>'
        b'<langSet xml:lang="ES"><term>gato</term></langSet></termEntry></martif>'
    )
    preview = await api.client.post(
        f"/api/projects/{seeded.project_id}/guide/import/preview",
        params={"target_lang": "es"},
        files={"file": ("base.tbx", tbx, "application/xml")},
        data={"source_lang": "de"},
    )
    body = preview.json()
    assert (body["target_lang"], body["source_lang"], body["base_version"]) == ("es", "de", 1)
    assert [(t["term"], t["rendering"]) for t in body["terms"]] == [("Katze", "gato")]


async def test_a_bad_glossary_names_the_line_and_writes_nothing(api, db):
    seeded = await seed_run(db)
    base = f"/api/projects/{seeded.project_id}/guide/import"
    bad = ("terms.csv", b"chat,cat\nchien\n", "text/csv")
    for url, data in ((f"{base}/preview", {}), (base, {"base_version": "1"})):
        response = await api.client.post(url, files={"file": bad}, data=data)
        assert response.status_code == 422
        assert response.json()["detail"].startswith("line 2 has only one column")
    xlsx = await api.client.post(base, files={"file": ("t.xlsx", b"PK", "application/zip")})
    assert "choose a .csv, .tsv or .tbx file" in xlsx.json()["detail"]
    versions = (await api.client.get(f"/api/projects/{seeded.project_id}/guide/versions")).json()
    assert len(versions) == 1


async def test_an_over_long_glossary_field_is_refused_with_its_line(api, db):
    # CGL-115: each field at most what a guide edit may hold, so the guide saves again
    assert MAX_FIELD_CHARS == SHORT_MAX
    seeded = await seed_run(db)
    base = f"/api/projects/{seeded.project_id}/guide/import"
    long = ("terms.csv", f"chien,dog\nchat,{'x' * (SHORT_MAX + 1)}\n".encode(), "text/csv")
    for url, data in ((f"{base}/preview", {}), (base, {"base_version": "1"})):
        response = await api.client.post(url, files={"file": long}, data=data)
        assert response.status_code == 422
        assert response.json()["detail"].startswith("line 2 has a translation of 2,001 characters")


async def test_a_guide_imported_into_an_empty_set_saves_again(api, db):
    made = await api.client.post("/api/projects", json={"name": "letters", "target_lang": "en"})
    project = made.json()["id"]
    base = f"/api/projects/{project}/guide"
    done = await api.client.post(f"{base}/import", files={"file": CSV})
    assert done.status_code == 200, done.text
    guide = done.json()["view"]["guide"]
    assert guide["summary"] == ""
    guide["glossary"][0]["rendering"] = "cat"
    saved = await api.client.put(base, json={"guide": guide, "base_version": 1})
    assert saved.status_code == 200, saved.text
    assert saved.json()["guide"]["glossary"][0]["rendering"] == "cat"


async def test_glossary_uploads_keep_to_the_upload_limit(make_api, db, monkeypatch):
    seeded = await seed_run(db)
    url = f"/api/projects/{seeded.project_id}/guide/import/preview"
    big = {"file": ("big.csv", b"x" * (1024 * 1024 + 1), "text/csv")}
    async with make_api(api={"max_upload_mb": 1}) as api:
        response = await api.client.post(url, files=big)
    assert response.status_code == 413
    assert response.json()["detail"] == (
        "big.csv is larger than the 1 MB upload limit (api.max_upload_mb)"
    )
    monkeypatch.setattr("coglossa_api.routes.guides.MAX_GLOSSARY_MB", 1)
    async with make_api(api={"max_upload_mb": 5}) as api:
        response = await api.client.post(url, files=big)
    assert response.status_code == 413
    assert response.json()["detail"] == (
        "big.csv is larger than the 1 MB upload limit (the limit for glossary files)"
    )
