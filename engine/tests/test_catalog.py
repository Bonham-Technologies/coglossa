import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update

from coglossa import catalog, service
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import FlagCode, PipelineProfile, Reliability, RunState
from coglossa.storage import LocalStorage
from coglossa.store import runs
from coglossa.store.models import Run, SpanScoreRow
from coglossa.store.review import run_span_ids
from coglossa.store.runs import NotFoundError
from coglossa.testing.queue import RecordingQueue
from coglossa.testing.seed import seed_run
from tests.conftest import build_config


async def test_create_and_read_projects(db):
    info = await catalog.create_project(
        db, "  Novel  ", target_lang="en", pipeline_profile=PipelineProfile.LONG_FORM
    )
    assert (info.name, info.target_lang, info.source_lang) == ("Novel", "en", None)
    assert info.pipeline_profile is PipelineProfile.LONG_FORM
    assert (info.documents, info.runs, info.guide_version) == (0, 0, None)
    assert await catalog.get_project(db, uuid.UUID(info.id)) == info
    with pytest.raises(service.ConflictError, match="a project named 'Novel' already exists"):
        await catalog.create_project(db, "Novel", target_lang="de")
    with pytest.raises(NotFoundError, match="not found"):
        await catalog.get_project(db, uuid.uuid4())


async def test_project_names_lose_control_characters(db):
    info = await catalog.create_project(db, "No\x00vel\x01 one", target_lang="en")
    assert info.name == "Novel one"


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("   ", "a project needs a name"),
        ("\x00\x01", "a project needs a name"),
        ("x" * 201, "at most 200 characters"),
    ],
)
async def test_invalid_project_names(db, name, message):
    with pytest.raises(service.InvalidRequestError, match=message):
        await catalog.create_project(db, name, target_lang="en")


async def test_document_project(db):
    seeded = await seed_run(db)
    project = await catalog.document_project(db, seeded.document_id)
    assert (project.id, project.name) == (str(seeded.project_id), "demo")
    with pytest.raises(NotFoundError, match="not found"):
        await catalog.document_project(db, uuid.uuid4())


async def test_projects_list_counts(db):
    await catalog.create_project(db, "b-empty", target_lang="de", source_lang="fr")
    seeded = await seed_run(db, project="a-demo")
    projects = await catalog.list_projects(db)
    assert [(p.name, p.documents, p.runs, p.guide_version) for p in projects] == [
        ("a-demo", 1, 1, 1),
        ("b-empty", 0, 0, None),
    ]
    assert projects[0].id == str(seeded.project_id)
    assert projects[0].target_lang == "en"  # the seed sets the default on a new project


async def test_documents_and_runs_newest_first(db):
    first = await seed_run(db)
    second = await seed_run(db)
    queue = RecordingQueue()
    rerun = await service.submit_existing(
        db,
        queue,
        build_config(),
        PromptLibrary(None),
        first.document_id,
        service.RunOptions(target_lang="de", source_lang="fr"),
    )
    documents = await catalog.list_documents(db, first.project_id)
    assert [d.id for d in documents] == [str(second.document_id), str(first.document_id)]
    assert all(d.ingested and d.filename == "story.txt" and d.format == "txt" for d in documents)
    assert [r.id for r in documents[1].runs] == [str(rerun), str(first.run_id)]
    fresh = documents[1].runs[0]
    assert (fresh.state, fresh.target_lang, fresh.source_lang) == (RunState.CREATED, "de", "fr")
    assert fresh.pipeline_profile is None
    done = documents[1].runs[1]
    assert (done.source_lang, done.pipeline_profile) == ("fr", PipelineProfile.STANDARD)
    assert done.finished_at is not None and done.error is None
    # CGL-73: passages with an open flag (p2, p3); none yet on a run still waiting to start
    assert [(r.id, r.to_check) for r in documents[1].runs] == [(str(rerun), 0), (done.id, 2)]
    with pytest.raises(NotFoundError):
        await catalog.list_documents(db, uuid.uuid4())
    empty = await catalog.create_project(db, "empty", target_lang="de")
    assert await catalog.list_documents(db, uuid.UUID(empty.id)) == []


async def test_run_summaries_keep_the_order_asked(db):
    a = await seed_run(db, project="a")
    b = await seed_run(db, project="b")
    asked = [b.run_id, uuid.uuid4(), a.run_id]  # a run that does not exist is left out
    summaries = await catalog.run_summaries(db, asked)
    assert [r.id for r in summaries] == [str(b.run_id), str(a.run_id)]
    assert summaries[1] == (await catalog.run_detail(db, a.run_id)).run
    assert await catalog.run_summaries(db, []) == []


async def test_list_runs_filters(db):
    a = await seed_run(db, project="a")
    b = await seed_run(db, project="b")
    everything = await catalog.list_runs(db)
    assert {r.id for r in everything} == {str(a.run_id), str(b.run_id)}
    assert [r.id for r in await catalog.list_runs(db, project_id=a.project_id)] == [str(a.run_id)]
    assert [r.id for r in await catalog.list_runs(db, document_id=b.document_id)] == [str(b.run_id)]
    assert len(await catalog.list_runs(db, limit=1)) == 1
    assert await catalog.list_runs(db, project_id=uuid.uuid4()) == []


async def test_run_detail_of_a_finished_run(db):
    seeded = await seed_run(db)
    detail = await catalog.run_detail(db, seeded.run_id)
    assert detail.run.id == str(seeded.run_id) and detail.run.state is RunState.COMPLETE
    assert detail.progress.chunks_total == 1 and detail.progress.chunks_done["score"] == 1
    assert detail.profile.source_lang == "fr"
    assert detail.language_tier is Reliability.B
    assert detail.notices == [] and detail.problems == []
    assert detail.summary == "A cat sleeps while it rains."
    assert detail.guide_version == 1
    assert detail.stats.model_dump() == {
        "spans": 3,
        "open_flags": 3,
        "flags_by_code": {"agent_disagreement": 1, "low_meaning": 1, "terminology_violation": 1},
        "edited_spans": 0,
    }
    assert detail.report_available is True


async def test_run_detail_notices(db):
    seeded = await seed_run(db)
    async with db.session() as s, s.begin():
        await s.execute(update(Run).where(Run.id == seeded.run_id).values(language_tier="C"))
    detail = await catalog.run_detail(db, seeded.run_id)
    assert [n.code for n in detail.notices] == [FlagCode.LOW_RELIABILITY_LANGUAGE]
    async with db.session() as s, s.begin():
        await s.execute(
            update(SpanScoreRow)
            .where(SpanScoreRow.span_id.in_(run_span_ids(seeded.run_id)))
            .values(meaning=None, style=None)
        )
    detail = await catalog.run_detail(db, seeded.run_id)
    assert [n.code for n in detail.notices] == [FlagCode.UNSCORED]


async def test_run_detail_of_a_new_run(db, tmp_path):
    queue = RecordingQueue()
    run_id = await service.submit_bytes(
        db,
        LocalStorage(tmp_path),
        queue,
        build_config(),
        PromptLibrary(None),
        "story.txt",
        b"Il pleut.",
        service.RunOptions(target_lang="en"),
        project="p",
    )
    detail = await catalog.run_detail(db, run_id)
    assert detail.profile is None and detail.language_tier is None
    assert detail.notices == [] and detail.summary is None and detail.guide_version is None
    assert detail.stats.spans == 0 and detail.stats.flags_by_code == {}
    assert detail.report_available is False
    assert detail.sweep_enabled is False  # not profiled yet
    with pytest.raises(NotFoundError):
        await catalog.run_detail(db, uuid.uuid4())


async def test_seed_values(db):
    seeded = await seed_run(db, state=RunState.SWEEPING)
    assert len(seeded.span_ids) == 3
    assert sorted(seeded.flag_ids) == [
        "p2:agent_disagreement",
        "p2:low_meaning",
        "p3:terminology_violation",
    ]
    async with db.session() as s:
        run = await runs.get_run(s, seeded.run_id)
    assert run.state == RunState.SWEEPING and run.finished_at is None


async def _snapshot_profiles(db, run_id, profiles):
    async with db.session() as s, s.begin():
        run = await s.get(Run, run_id)
        run.config_snapshot = {**run.config_snapshot, "profiles": profiles}


async def test_run_detail_says_whether_the_sweep_runs(db):
    seeded = await seed_run(db)  # a standard document; the sweep is on for standard by default
    assert (await catalog.run_detail(db, seeded.run_id)).sweep_enabled is True
    await _snapshot_profiles(db, seeded.run_id, {"sweep": {"standard": False}})
    assert (await catalog.run_detail(db, seeded.run_id)).sweep_enabled is False
    # a snapshot this version cannot read (an older version's profiles section): no offer
    await _snapshot_profiles(db, seeded.run_id, {"no_such_setting": 1})
    assert (await catalog.run_detail(db, seeded.run_id)).sweep_enabled is False


async def test_set_summary_counts_the_newest_run_of_each_file(db):
    ready = await seed_run(db, project="mixed")
    failed = await seed_run(db, project="mixed", filename="b.txt", state=RunState.FAILED)
    redone = await seed_run(db, project="mixed", filename="c.txt")
    # a new run of c.txt: only the newest run of a file counts, so c.txt is now translating
    await service.submit_existing(
        db,
        RecordingQueue(),
        build_config(),
        PromptLibrary(None),
        redone.document_id,
        service.RunOptions(target_lang="en"),
    )
    expected_flags = 0
    for run_id in (ready.run_id, failed.run_id):  # c.txt's finished run is no longer the newest
        expected_flags += (await catalog.run_detail(db, run_id)).stats.open_flags
    info = await catalog.get_project(db, ready.project_id)
    assert (info.files_ready, info.files_failed, info.files_translating) == (1, 1, 1)
    assert info.open_flags == expected_flags
    assert info.last_activity_at >= info.created_at
    [listed] = await catalog.list_projects(db)
    assert listed == info


async def test_a_run_state_the_code_does_not_know_counts_as_translating(db):
    future = await seed_run(db, project="newer")
    done = await seed_run(
        db, project="newer", filename="b.txt", state=RunState.COMPLETE_WITH_ERRORS
    )
    async with db.session() as s, s.begin():  # a state a newer version wrote
        await s.execute(update(Run).where(Run.id == future.run_id).values(state="paused"))
    info = await catalog.get_project(db, future.project_id)
    assert (info.files_ready, info.files_failed, info.files_translating) == (1, 0, 1)
    assert done.run_id != future.run_id


async def test_last_activity_follows_the_latest_run_timestamp(db):
    seeded = await seed_run(db, project="busy")
    created = (await catalog.get_project(db, seeded.project_id)).created_at
    later = created + timedelta(days=3)
    async with db.session() as s, s.begin():
        await s.execute(
            update(Run)
            .where(Run.id == seeded.run_id)
            .values(created_at=created + timedelta(days=1), finished_at=later)
        )
    assert (await catalog.get_project(db, seeded.project_id)).last_activity_at == later
    async with db.session() as s, s.begin():  # a run still going: its start counts
        await s.execute(update(Run).where(Run.id == seeded.run_id).values(finished_at=None))
    started = created + timedelta(days=1)
    assert (await catalog.get_project(db, seeded.project_id)).last_activity_at == started


async def test_set_summary_of_an_empty_project(db):
    info = await catalog.create_project(db, "empty", target_lang="en")
    assert (info.files_ready, info.files_failed, info.files_translating, info.open_flags) == (
        0,
        0,
        0,
        0,
    )
    assert info.last_activity_at == info.created_at


async def test_rename_project(db):
    first = await catalog.create_project(db, "first", target_lang="en")
    await catalog.create_project(db, "second", target_lang="en")
    renamed = await catalog.rename_project(db, uuid.UUID(first.id), "  Welcome letter\x00 ")
    assert renamed.name == "Welcome letter"
    with pytest.raises(service.ConflictError, match="a project named 'second' already exists"):
        await catalog.rename_project(db, uuid.UUID(first.id), "second")
    with pytest.raises(service.InvalidRequestError, match="a project needs a name"):
        await catalog.rename_project(db, uuid.UUID(first.id), "   ")
    with pytest.raises(NotFoundError, match="not found"):
        await catalog.rename_project(db, uuid.uuid4(), "third")


async def test_a_set_lists_its_languages_with_their_guides_and_counts_each(db):
    english = await seed_run(db, target_lang="en")
    spanish = await seed_run(db, target_lang="es", state=RunState.TRANSLATING)
    async with db.session() as s, s.begin():  # the Spanish run is of the same file
        (await runs.get_run(s, spanish.run_id, lock=True)).document_id = english.document_id
    info = await catalog.get_project(db, english.project_id)
    assert [(lang.target_lang, lang.guide_version) for lang in info.languages] == [
        ("en", 1),
        ("es", 1),
    ]
    assert info.guide_version == 1
    assert (info.files_ready, info.files_translating) == (1, 1)  # one file, two languages
    listed = {p.id: p for p in await catalog.list_projects(db)}
    assert listed[str(english.project_id)].languages == info.languages


async def test_a_new_set_lists_its_default_language_without_a_guide(db):
    info = await catalog.create_project(db, "Atlas", target_lang="fr")
    assert [(lang.target_lang, lang.guide_version) for lang in info.languages] == [("fr", None)]


async def test_a_set_records_its_team_and_creator_and_can_move(db):
    info = await catalog.create_project(
        db, "Atlas", target_lang="en", team="Pacific", created_by="ana"
    )
    assert (info.team, info.created_by) == ("Pacific", "ana")
    moved = await catalog.set_team(db, uuid.UUID(info.id), None)
    assert (moved.team, moved.created_by) == (None, "ana")
    with pytest.raises(NotFoundError):
        await catalog.set_team(db, uuid.uuid4(), "x")
