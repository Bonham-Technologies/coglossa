from coglossa import catalog
from coglossa.schema import RunState
from coglossa.store.models import ReviewEvent
from coglossa.testing.seed import seed_run
from sqlalchemy import select


async def test_span_pages_with_filters(api, db):
    seeded = await seed_run(db)
    url = f"/api/runs/{seeded.run_id}/spans"
    page = (await api.client.get(url)).json()
    assert page["total"] == 3 and [s["number"] for s in page["spans"]] == [1, 2, 3]
    flagged = (await api.client.get(url, params={"flagged": "true", "max_style": 75})).json()
    assert [s["number"] for s in flagged["spans"]] == [2]
    coded = (await api.client.get(url, params={"code": "terminology_violation"})).json()
    assert [s["number"] for s in coded["spans"]] == [3]
    paged = (await api.client.get(url, params={"offset": 2, "limit": 1})).json()
    assert (paged["total"], [s["number"] for s in paged["spans"]]) == (3, [3])
    for bad in ({"min_meaning": 101}, {"code": "nonsense"}, {"limit": 0}, {"offset": -1}):
        assert (await api.client.get(url, params=bad)).status_code == 422, bad


async def test_span_detail_and_edit(api, db):
    seeded = await seed_run(db)
    url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[0]}"
    detail = (await api.client.get(url)).json()
    assert detail["text"] == "Chapter One" and len(detail["candidates"]) == 2
    edited = await api.client.post(f"{url}/edit", json={"text": "Chapter 1", "reason": "style"})
    assert edited.status_code == 200
    assert (edited.json()["text"], edited.json()["edited"]) == ("Chapter 1", True)
    assert [r["after"] for r in edited.json()["revisions"]] == ["Chapter One", "Chapter 1"]
    blank = await api.client.post(f"{url}/edit", json={"text": "  "})
    assert (
        blank.status_code == 422 and blank.json()["detail"] == "the translation must not be empty"
    )


async def test_a_stale_edit_answers_409_with_the_current_text(api, db):
    # CGL-36
    seeded = await seed_run(db)
    url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[1]}"
    base = "The cat sleeps. It rains."
    first = await api.client.post(f"{url}/edit", json={"text": "The cat naps.", "base": base})
    assert first.status_code == 200
    for path, body in (
        ("edit", {"text": "The cat dozes.", "base": base}),
        ("use-candidate", {"translator": "contextual", "base": base}),
    ):
        stale = await api.client.post(f"{url}/{path}", json=body)
        assert stale.status_code == 409, path
        assert stale.json()["code"] == "passage_changed"
        assert stale.json()["current_text"] == "The cat naps."
        assert "someone else changed this passage" in stale.json()["detail"]
    assert (await api.client.get(url)).json()["text"] == "The cat naps."


async def test_control_characters_in_bodies_are_not_a_server_error(api, db):
    seeded = await seed_run(db)
    url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[0]}/edit"
    edited = await api.client.post(url, json={"text": "Chapter\u0000 1", "reason": "\u0000"})
    assert edited.status_code == 200 and edited.json()["text"] == "Chapter 1"
    nul = await api.client.post(url, json={"text": "\u0000"})
    assert nul.status_code == 422
    assert nul.json()["detail"] == "the translation must not be empty"
    project = await api.client.post("/api/projects", json={"name": "a\u0000b", "target_lang": "en"})
    assert project.status_code == 201 and project.json()["name"] == "ab"


async def test_use_candidate_accept_and_flags(api, db):
    seeded = await seed_run(db)
    p2 = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[1]}"
    used = (await api.client.post(f"{p2}/use-candidate", json={"translator": "contextual"})).json()
    assert used["text"] == "The cat is asleep. It rains."
    missing = await api.client.post(f"{p2}/use-candidate", json={"translator": "nobody"})
    assert missing.status_code == 422
    accepted = (await api.client.post(f"{p2}/accept")).json()
    assert {f["status"] for f in accepted["flags"]} == {"accepted"}
    flag = seeded.flag_ids["p3:terminology_violation"]
    flag_url = f"/api/runs/{seeded.run_id}/flags/{flag}"
    no_reason = await api.client.post(flag_url, json={"status": "dismissed"})
    assert no_reason.status_code == 422
    assert no_reason.json()["detail"] == "dismissing a flag needs a reason"
    dismissed = await api.client.post(flag_url, json={"status": "dismissed", "reason": "fine"})
    assert dismissed.json()["flags"][0]["dismiss_reason"] == "fine"
    assert (await api.client.post(flag_url, json={"status": "resolved"})).status_code == 422
    other = await api.client.post(
        f"/api/runs/{seeded.run_id}/flags/999999", json={"status": "open"}
    )
    assert other.status_code == 404


async def test_add_to_guide(api, db):
    seeded = await seed_run(db)
    url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[2]}/guide"
    added = await api.client.post(
        url, json={"glossary": {"term": "Fin", "rendering": "The End", "locked": True}}
    )
    assert added.status_code == 200
    assert added.json()["info"]["version"] == 2
    entity = await api.client.post(
        url, json={"entity": {"name": "Minou", "rendering": "Kitty", "kind": "person"}}
    )
    assert entity.json()["guide"]["entities"][0]["name"] == "Minou"
    both = {
        "glossary": {"term": "a", "rendering": "b"},
        "entity": {"name": "c", "rendering": "d", "kind": "x"},
    }
    assert (await api.client.post(url, json=both)).status_code == 422
    assert (await api.client.post(url, json={})).status_code == 422


async def test_this_form_is_fine(api, db):
    """CGL-109: a reviewer accepts the form a passage uses; the guide gains it in a new version."""
    from coglossa.store.models import FlagRow

    seeded = await seed_run(db)
    flag = seeded.flag_ids["p3:terminology_violation"]
    violation = {"term": "Fin", "expected": "The End", "kind": "glossary", "locked": True}
    async with db.session() as s, s.begin():
        (await s.get(FlagRow, flag)).evidence = {"violations": [violation]}
    guide_url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[2]}/guide"
    entry = {"term": "Fin", "rendering": "The End", "locked": True}
    assert (await api.client.post(guide_url, json={"glossary": entry})).status_code == 200
    url = f"/api/runs/{seeded.run_id}/flags/{flag}/accept-form"
    for bad in ({"term": "Fin", "form": ""}, {"term": "Fin"}, {"term": "", "form": "End"}):
        assert (await api.client.post(url, json=bad)).status_code == 422, bad
    unused = await api.client.post(url, json={"term": "Fin", "form": "Ending"})
    assert unused.status_code == 422
    assert unused.json()["detail"] == "the passage does not use “Ending”"
    long = await api.client.post(url, json={"term": "Fin", "form": "x" * 2001})
    assert long.status_code == 422
    accepted = await api.client.post(url, json={"term": "Fin", "form": "End"})
    assert accepted.status_code == 200, accepted.text
    assert [(f["status"], f["dismiss_reason"]) for f in accepted.json()["flags"]] == [
        ("dismissed", "This form is fine: “End”")
    ]
    guide = (await api.client.get(f"/api/projects/{seeded.project_id}/guide")).json()
    assert guide["info"]["version"] == 3
    assert guide["guide"]["glossary"][-1]["forms"] == ["End"]
    assert guide["guide"]["glossary"][0]["forms"] == []  # entries without forms read as empty


async def test_guide_entries_take_forms(api, db):
    seeded = await seed_run(db)
    url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[2]}/guide"
    entry = {"term": "Vertrag", "rendering": "contract", "source_forms": ["Verträge", " "]}
    added = await api.client.post(url, json={"glossary": entry})
    assert added.json()["guide"]["glossary"][-1]["source_forms"] == ["Verträge"]
    long = {"term": "a", "rendering": "b", "forms": ["x" * 2001]}
    too_long = await api.client.post(url, json={"glossary": long})
    assert too_long.status_code == 422
    assert "A form of a term's translation is too long" in too_long.text


async def test_actions_wait_for_the_sweep(api, db):
    seeded = await seed_run(db, state=RunState.SWEEPING)
    url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[0]}/edit"
    response = await api.client.post(url, json={"text": "Chapter 1"})
    assert response.status_code == 409
    assert response.json()["detail"].endswith("try again when it finishes")


async def test_actions_need_a_complete_run(api, db):
    seeded = await seed_run(db, state=RunState.CANCELLED)
    url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[0]}/edit"
    response = await api.client.post(url, json={"text": "Chapter 1"})
    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"run {seeded.run_id} is cancelled; review actions are available once the run is complete"
    )


async def test_the_signed_in_user_is_logged(make_api, db):
    seeded = await seed_run(db)
    await catalog.set_team(db, seeded.project_id, "desk")  # ana's team (CGL-26)
    auth = {"mode": "trusted_header", "groups_header": "X-Remote-Groups"}
    async with make_api(auth=auth) as api:
        url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[0]}/accept"
        assert (await api.client.post(url)).status_code == 401
        signed_in = {"X-Remote-User": "ana", "X-Remote-Groups": "desk"}
        response = await api.client.post(url, headers=signed_in)
        assert response.status_code == 200
    async with db.session() as s:
        [event] = list(await s.scalars(select(ReviewEvent)))
    assert (event.action, event.payload["user"]) == ("accept", "ana")
