import contextlib
import logging
import uuid

import pytest
from coglossa import catalog
from coglossa.storage import LocalStorage, StorageError
from coglossa.store import runs
from coglossa.testing.seed import seed_run
from sqlalchemy import event
from sqlalchemy.engine import Engine


async def _project(api, **body):
    data = {"name": "novel", "target_lang": "en", **body}
    response = await api.client.post("/api/projects", json=data)
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_list_get_projects(api):
    created = await _project(api, source_lang="fr", pipeline_profile="long-form")
    assert (created["name"], created["source_lang"], created["pipeline_profile"]) == (
        "novel",
        "fr",
        "long-form",
    )
    listed = (await api.client.get("/api/projects")).json()
    assert [p["id"] for p in listed] == [created["id"]]
    assert (await api.client.get(f"/api/projects/{created['id']}")).json() == created
    duplicate = await api.client.post("/api/projects", json={"name": "novel", "target_lang": "de"})
    assert duplicate.status_code == 409
    assert (await api.client.get(f"/api/projects/{uuid.uuid4()}")).status_code == 404
    assert (await api.client.get("/api/projects/not-a-uuid")).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "target_lang": "en"},
        {"name": "x", "target_lang": "english!"},
        {"name": "x", "target_lang": "en", "pipeline_profile": "huge"},
        {"name": "x"},
    ],
)
async def test_invalid_projects(api, body):
    assert (await api.client.post("/api/projects", json=body)).status_code == 422


async def test_delete_needs_the_name(api, db):
    seeded = await seed_run(db, project="demo")
    url = f"/api/projects/{seeded.project_id}"
    wrong = await api.client.delete(url, params={"confirm": "Demo"})
    assert wrong.status_code == 422
    assert wrong.json()["detail"] == "to delete project 'demo', pass confirm=demo"
    assert (await api.client.delete(url)).status_code == 422
    assert (await api.client.delete(url, params={"confirm": "demo"})).status_code == 204
    assert (await api.client.get(url)).status_code == 404


async def test_delete_answers_204_when_the_files_cannot_be_removed(api, db, monkeypatch, caplog):
    """CGL-84: the set is gone once its rows are; a file clean-up failure is logged."""
    seeded = await seed_run(db, project="demo")

    def cannot_delete(self, prefix):
        raise StorageError(f"cannot delete {prefix}: Permission denied")

    monkeypatch.setattr(LocalStorage, "delete_prefix", cannot_delete)
    url = f"/api/projects/{seeded.project_id}"
    with caplog.at_level(logging.ERROR, logger="coglossa"):
        response = await api.client.delete(url, params={"confirm": "demo"})
    assert response.status_code == 204, response.text
    assert f"projects/{seeded.project_id}/" in caplog.text
    assert (await api.client.get(url)).status_code == 404


async def test_documents_of_a_project(api, db):
    seeded = await seed_run(db)
    documents = (await api.client.get(f"/api/projects/{seeded.project_id}/documents")).json()
    assert [(d["filename"], len(d["runs"])) for d in documents] == [("story.txt", 1)]
    assert documents[0]["runs"][0]["state"] == "complete"


async def _to_check(api, project_id) -> list[int]:
    documents = (await api.client.get(f"/api/projects/{project_id}/documents")).json()
    return [r["to_check"] for d in documents for r in d["runs"]]


async def test_each_run_in_the_documents_list_counts_its_passages_to_check(api, db):
    # CGL-73: the passages the "To check" list shows (open flags), counted per run
    seeded = await seed_run(db)  # p2 has two open flags, p3 one
    spans = f"/api/runs/{seeded.run_id}/spans"
    listed = (await api.client.get(spans, params={"flagged": "true"})).json()
    assert await _to_check(api, seeded.project_id) == [listed["total"]] == [2]
    accepted = await api.client.post(f"{spans}/{seeded.span_ids[1]}/accept")
    assert accepted.status_code == 200, accepted.text
    assert await _to_check(api, seeded.project_id) == [1]
    p3_flag = seeded.flag_ids["p3:terminology_violation"]
    dismissed = await api.client.post(
        f"/api/runs/{seeded.run_id}/flags/{p3_flag}", json={"status": "dismissed", "reason": "ok"}
    )
    assert dismissed.status_code == 200, dismissed.text
    assert await _to_check(api, seeded.project_id) == [0]
    # a run with no passages yet has nothing to check
    languages = f"/api/projects/{seeded.project_id}/languages"
    assert (await api.client.post(languages, json={"target_lang": "es"})).status_code == 202
    assert await _to_check(api, seeded.project_id) == [0, 0]


async def test_upload_queues_a_run_with_project_defaults(api):
    project = await _project(api, source_lang="fr", pipeline_profile="short")
    response = await api.client.post(
        f"/api/projects/{project['id']}/runs",
        files={"file": ("chapitre un.txt", b"Il pleut.", "text/plain")},
        data={"checkpoint": "false"},
    )
    assert response.status_code == 202, response.text
    run = response.json()
    assert (run["filename"], run["state"], run["target_lang"]) == (
        "chapitre un.txt",
        "created",
        "en",
    )
    assert (run["source_lang"], run["pipeline_profile"]) == ("fr", "short")
    assert [(j.task, j.kwargs) for j in api.queue.jobs] == [
        ("coglossa.start_run", {"run_id": run["id"]})
    ]
    stored = [p for p in api.storage_root.rglob("*") if p.is_file()]
    assert [p.name for p in stored] == ["chapitre_un.txt"]


async def test_upload_overrides(api):
    project = await _project(api)
    response = await api.client.post(
        f"/api/projects/{project['id']}/runs",
        files={"file": ("a.md", b"# Titre", "text/markdown")},
        data={"target_lang": "pt-BR", "source_lang": "fr", "use_project_guide": "true"},
    )
    assert response.status_code == 202
    assert response.json()["target_lang"] == "pt-BR"


@pytest.mark.parametrize(
    ("files", "data", "status", "detail"),
    [
        (
            {"file": ("big.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
            {},
            413,
            "big.txt is larger than the 1 MB upload limit (api.max_upload_mb)",
        ),
        ({"file": ("empty.txt", b"", "text/plain")}, {}, 422, "empty.txt is empty"),
        ({"file": ("notes.rtf", b"x", "text/rtf")}, {}, 422, "unsupported file type '.rtf'"),
        ({}, {}, 422, None),
        ({"file": ("", b"x", "text/plain")}, {}, 422, None),
        ({"file": ("a.txt", b"x", "text/plain")}, {"target_lang": "english!"}, 422, None),
        ({"file": ("a.txt", b"x", "text/plain")}, {"pipeline_profile": "huge"}, 422, None),
    ],
)
async def test_bad_uploads_create_nothing(make_api, db, files, data, status, detail):
    async with make_api(api={"max_upload_mb": 1, "progress_poll_s": 0.02}) as api:
        project = await _project(api)
        response = await api.client.post(
            f"/api/projects/{project['id']}/runs", files=files or None, data=data
        )
        assert response.status_code == status, response.text
        if detail is not None:
            assert response.json()["detail"].startswith(detail)
        assert api.queue.jobs == []
        assert not [p for p in api.storage_root.rglob("*") if p.is_file()]
    assert await catalog.list_runs(db) == []


async def test_upload_needs_a_target_language(api, db):
    async with db.session() as s, s.begin():
        project = await runs.get_or_create_project(s, "from-cli")
    response = await api.client.post(
        f"/api/projects/{project.id}/runs", files={"file": ("a.txt", b"x", "text/plain")}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "choose a target language: project 'from-cli' has no default"
    )
    missing = await api.client.post(
        f"/api/projects/{uuid.uuid4()}/runs", files={"file": ("a.txt", b"x", "text/plain")}
    )
    assert missing.status_code == 404


async def test_rename_a_project(api):
    project = await _project(api)
    await _project(api, name="taken")
    url = f"/api/projects/{project['id']}"
    renamed = await api.client.patch(url, json={"name": " Welcome letter "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Welcome letter"
    assert (await api.client.get(url)).json()["name"] == "Welcome letter"
    assert (await api.client.patch(url, json={"name": "taken"})).status_code == 409
    assert (await api.client.patch(url, json={"name": ""})).status_code == 422
    assert (await api.client.patch(url, json={"name": "   "})).status_code == 422
    unknown = await api.client.patch(f"/api/projects/{uuid.uuid4()}", json={"name": "x"})
    assert unknown.status_code == 404


async def test_a_name_cannot_be_cleared(api):
    """CGL-31: `name: null` is refused like a blank name, not a server error."""
    project = await _project(api)
    url = f"/api/projects/{project['id']}"
    cleared = await api.client.patch(url, json={"name": None})
    assert cleared.status_code == 422, cleared.text
    assert "name" in cleared.text
    alongside = await api.client.patch(url, json={"name": None, "team": "pacific"})
    assert alongside.status_code == 422, alongside.text
    assert (await api.client.get(url)).json()["team"] is None


async def test_a_move_and_a_taken_name_change_nothing(api):
    """CGL-31: moving and renaming is one change; a taken name refuses both."""
    project = await _project(api)
    await _project(api, name="taken")
    url = f"/api/projects/{project['id']}"
    refused = await api.client.patch(url, json={"name": "taken", "team": "pacific"})
    assert refused.status_code == 409, refused.text
    after = (await api.client.get(url)).json()
    assert (after["name"], after["team"]) == ("novel", None)
    both = await api.client.patch(url, json={"name": "Desk", "team": "pacific"})
    assert both.status_code == 200, both.text
    assert (both.json()["name"], both.json()["team"]) == ("Desk", "pacific")
    blank = await api.client.patch(url, json={"name": "  ", "team": None})
    assert blank.status_code == 422, blank.text
    assert (await api.client.get(url)).json()["team"] == "pacific"


async def test_project_summary_fields(api, db):
    seeded = await seed_run(db)
    info = (await api.client.get(f"/api/projects/{seeded.project_id}")).json()
    assert (info["files_ready"], info["files_failed"], info["files_translating"]) == (1, 0, 0)
    assert isinstance(info["open_flags"], int)
    assert info["last_activity_at"] >= info["created_at"]


async def test_a_name_is_trimmed_before_its_length_is_checked(api):
    padded = f"  {'x' * 200}  "
    created = await api.client.post("/api/projects", json={"name": padded, "target_lang": "en"})
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "x" * 200
    other = await _project(api, name="other")
    url = f"/api/projects/{other['id']}"
    renamed = await api.client.patch(url, json={"name": f"  {'y' * 200}  "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "y" * 200
    assert (await api.client.patch(url, json={"name": "z" * 201})).status_code == 422


async def test_rename_documents_its_404_and_409_in_the_openapi(api):
    from coglossa_api.openapi import openapi_document

    responses = openapi_document()["paths"]["/api/projects/{project_id}"]["patch"]["responses"]
    assert {"200", "404", "409", "422"} <= set(responses)


async def test_translate_a_set_into_another_language(api, db):
    seeded = await seed_run(db)  # one file, English
    url = f"/api/projects/{seeded.project_id}/languages"
    started = await api.client.post(url, json={"target_lang": "es"})
    assert started.status_code == 202, started.text
    assert [(r["document_id"], r["target_lang"]) for r in started.json()] == [
        (str(seeded.document_id), "es")
    ]
    assert [j.task for j in api.queue.jobs] == ["coglossa.start_run"]
    info = (await api.client.get(f"/api/projects/{seeded.project_id}")).json()
    assert [lang["target_lang"] for lang in info["languages"]] == ["en", "es"]
    again = await api.client.post(url, json={"target_lang": "es"})
    assert again.status_code == 409 and again.json()["code"] == "language_exists"
    bad = await api.client.post(url, json={"target_lang": "not a code"})
    assert bad.status_code == 422


@contextlib.contextmanager
def _reads():
    """The SELECT statements every database engine runs inside the block."""
    seen: list[str] = []

    def record(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


async def _reads_to_add_a_language(api, db, files: int, lang: str) -> int:
    seeded = await seed_run(db, project=f"set-{files}")
    for n in range(1, files):
        await seed_run(db, project=f"set-{files}", filename=f"part{n}.txt")
    api.queue.jobs.clear()
    with _reads() as seen:
        started = await api.client.post(
            f"/api/projects/{seeded.project_id}/languages", json={"target_lang": lang}
        )
    assert started.status_code == 202, started.text
    listed = started.json()
    assert [r["id"] for r in listed] == [j.kwargs["run_id"] for j in api.queue.jobs]
    assert {(r["target_lang"], r["state"], r["project_id"]) for r in listed} == {
        (lang, "created", str(seeded.project_id))
    }
    assert len({r["document_id"] for r in listed}) == files
    return len(seen)


async def test_adding_a_language_reads_the_same_for_one_file_or_many(api, db):
    # CGL-30: the runs it started are listed in one query, not loaded one by one
    one = await _reads_to_add_a_language(api, db, 1, "es")
    many = await _reads_to_add_a_language(api, db, 4, "de")
    assert many == one


async def _reads_to_list_documents(api, db, files: int) -> int:
    project = f"listed-{files}"
    seeded = await seed_run(db, project=project)
    for n in range(1, files):
        await seed_run(db, project=project, filename=f"part{n}.txt")
        await seed_run(db, project=project, filename=f"part{n}.txt", target_lang="es")
    with _reads() as seen:
        listed = await api.client.get(f"/api/projects/{seeded.project_id}/documents")
    assert listed.status_code == 200, listed.text
    runs = [r for d in listed.json() for r in d["runs"]]
    assert len(runs) == 2 * files - 1 and {r["to_check"] for r in runs} == {2}
    return len(seen)


async def test_listing_documents_reads_the_same_for_one_file_or_many(api, db):
    # CGL-73: every run's count to check comes from one query, not one per run
    one = await _reads_to_list_documents(api, db, 1)
    many = await _reads_to_list_documents(api, db, 4)
    assert many == one


async def test_a_set_without_files_has_no_language_to_add(api):
    created = await _project(api, name="empty")
    refused = await api.client.post(
        f"/api/projects/{created['id']}/languages", json={"target_lang": "es"}
    )
    assert refused.status_code == 422 and "has no files" in refused.json()["detail"]


async def test_an_upload_into_every_language_of_the_set(api, db):
    seeded = await seed_run(db)
    await api.client.post(
        f"/api/projects/{seeded.project_id}/languages", json={"target_lang": "es"}
    )
    api.queue.jobs.clear()
    uploaded = await api.client.post(
        f"/api/projects/{seeded.project_id}/runs",
        files={"file": ("b.txt", b"Le chien.", "text/plain")},
        data={"target_lang": "es", "all_languages": "true"},
    )
    assert uploaded.status_code == 202, uploaded.text
    assert uploaded.json()["target_lang"] == "es"
    document = uploaded.json()["document_id"]
    runs_of_file = (await api.client.get(f"/api/runs?document_id={document}")).json()
    assert sorted(r["target_lang"] for r in runs_of_file) == ["en", "es"]
    assert len(api.queue.jobs) == 2
