import uuid

from coglossa.schema import RunError, RunState
from coglossa.stages.guide import NOT_BUILT
from coglossa.store import runs
from coglossa.testing.seed import seed_run


async def test_list_and_filter_runs(api, db):
    a = await seed_run(db, project="a")
    b = await seed_run(db, project="b")
    everything = (await api.client.get("/api/runs")).json()
    assert {r["id"] for r in everything} == {str(a.run_id), str(b.run_id)}
    only_a = (await api.client.get("/api/runs", params={"project_id": str(a.project_id)})).json()
    assert [r["id"] for r in only_a] == [str(a.run_id)]
    by_doc = await api.client.get("/api/runs", params={"document_id": str(b.document_id)})
    assert [r["id"] for r in by_doc.json()] == [str(b.run_id)]
    assert (await api.client.get("/api/runs", params={"limit": 0})).status_code == 422
    assert (await api.client.get("/api/runs", params={"limit": 501})).status_code == 422


async def test_run_detail_and_progress(api, db):
    seeded = await seed_run(db)
    detail = (await api.client.get(f"/api/runs/{seeded.run_id}")).json()
    assert detail["run"]["state"] == "complete" and detail["report_available"] is True
    assert detail["stats"]["open_flags"] == 3 and detail["guide_version"] == 1
    progress = (await api.client.get(f"/api/runs/{seeded.run_id}/progress")).json()
    assert progress["state"] == "complete" and progress["chunks_total"] == 1
    assert (await api.client.get(f"/api/runs/{uuid.uuid4()}")).status_code == 404


async def test_new_run_of_a_document(api, db):
    seeded = await seed_run(db)
    response = await api.client.post(
        f"/api/documents/{seeded.document_id}/runs", json={"target_lang": "de", "checkpoint": True}
    )
    assert response.status_code == 202
    assert (response.json()["target_lang"], response.json()["state"]) == ("de", "created")
    assert [j.task for j in api.queue.jobs] == ["coglossa.start_run"]
    missing = await api.client.post(
        f"/api/documents/{uuid.uuid4()}/runs", json={"target_lang": "de"}
    )
    assert missing.status_code == 404
    bad = await api.client.post(
        f"/api/documents/{seeded.document_id}/runs", json={"target_lang": "not a code"}
    )
    assert bad.status_code == 422


async def test_a_new_run_takes_the_project_defaults(api, db):
    seeded = await seed_run(db)
    async with db.session() as s, s.begin():
        project = await runs.get_project(s, seeded.project_id)
        project.source_lang, project.pipeline_profile = "fr", "long-form"
    url = f"/api/documents/{seeded.document_id}/runs"
    response = await api.client.post(url, json={})
    assert response.status_code == 202
    run = response.json()
    assert (run["target_lang"], run["source_lang"], run["pipeline_profile"]) == (
        "en",
        "fr",
        "long-form",
    )
    override = (await api.client.post(url, json={"target_lang": "de", "source_lang": "it"})).json()
    assert (override["target_lang"], override["source_lang"]) == ("de", "it")
    async with db.session() as s, s.begin():
        (await runs.get_project(s, seeded.project_id)).target_lang = None
    refused = await api.client.post(url, json={})
    assert refused.status_code == 422
    assert refused.json()["detail"] == "choose a target language: project 'demo' has no default"


async def test_cancel(api, db):
    waiting = await seed_run(db, state=RunState.AWAITING_GUIDE_APPROVAL)
    result = (await api.client.post(f"/api/runs/{waiting.run_id}/cancel")).json()
    assert result["message"] == "cancelled" and result["progress"]["state"] == "cancelled"
    running = await seed_run(db, project="other", state=RunState.TRANSLATING)
    result = (await api.client.post(f"/api/runs/{running.run_id}/cancel")).json()
    assert result["message"] == (
        "cancel requested; running work stops after the current chunk or stage"
    )
    again = await api.client.post(f"/api/runs/{waiting.run_id}/cancel")
    assert again.status_code == 409 and "already cancelled" in again.json()["detail"]


async def test_retry_and_sweep(api, db):
    done = await seed_run(db)
    refused = await api.client.post(f"/api/runs/{done.run_id}/retry")
    assert refused.status_code == 409 and "nothing to retry" in refused.json()["detail"]
    failed = await seed_run(db, project="other", state=RunState.FAILED)
    retried = await api.client.post(f"/api/runs/{failed.run_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["message"] == "restarting from the failed stage"
    swept = await api.client.post(f"/api/runs/{done.run_id}/sweep")
    assert swept.status_code == 202
    assert swept.json()["message"] == "re-running the sweep with the project guide, version 1"
    assert swept.json()["progress"]["state"] == "sweeping"
    busy = await api.client.post(f"/api/runs/{done.run_id}/sweep")
    assert busy.status_code == 409
    assert [j.task for j in api.queue.jobs] == ["coglossa.start_run", "coglossa.finish_run"]


async def test_run_detail_names_problems_with_the_whole_file(api, db):
    """CGL-80: a guide that couldn't be built reaches the app in plain words."""
    seeded = await seed_run(db, state=RunState.COMPLETE_WITH_ERRORS)
    clean = (await api.client.get(f"/api/runs/{seeded.run_id}")).json()
    assert clean["problems"] == []
    async with db.session() as s, s.begin():
        row = await runs.get_stage(s, seeded.run_id, "guide")
        error = RunError(
            stage="guide",
            role="guide",
            message=f"{NOT_BUILT} (model output was cut off); translating without one",
        )
        runs.finish_stage(row, {"errors": [error.model_dump()]})
    detail = (await api.client.get(f"/api/runs/{seeded.run_id}")).json()
    assert detail["problems"] == [
        {
            "stage": "guide",
            "text": "The translation guide for this file couldn't be built, "
            "so names and terms may vary.",
        }
    ]
    assert detail["progress"]["retryable"] is False
    assert "cut off" not in str(detail)
