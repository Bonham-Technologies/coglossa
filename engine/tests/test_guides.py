import asyncio
import threading
import uuid

import pytest
from sqlalchemy import select, update

from coglossa import catalog, service
from coglossa.glossary_import import GlossaryFileError
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import GlossaryEntry, Guide, RunState
from coglossa.storage import LocalStorage
from coglossa.store import runs
from coglossa.store.models import Run
from coglossa.store.runs import NotFoundError
from coglossa.testing.queue import RecordingQueue
from coglossa.testing.seed import seed_run
from tests.conftest import build_config

LOCKED = Guide(
    summary="A cat sleeps while it rains.",
    style_guide="Plain.",
    glossary=[GlossaryEntry(term="Fin", rendering="The End", locked=True)],
)


async def test_versions_and_saving(db):
    seeded = await seed_run(db)
    [v1] = await catalog.guide_versions(db, seeded.project_id)
    assert (v1.version, v1.status, v1.author, v1.source_run_id) == (
        1,
        "draft",
        "engine",
        str(seeded.run_id),
    )
    saved = await catalog.save_project_guide(db, seeded.project_id, LOCKED, base_version=1)
    assert (saved.info.version, saved.info.status, saved.info.author) == (2, "approved", "user")
    assert saved.info.source_run_id is None
    assert saved.guide == LOCKED
    assert [v.version for v in await catalog.guide_versions(db, seeded.project_id)] == [2, 1]
    assert (await catalog.project_guide(db, seeded.project_id)).info.version == 2
    old = await catalog.project_guide(db, seeded.project_id, version=1)
    assert old.guide.glossary[0].term == "chat"
    with pytest.raises(NotFoundError, match="guide version 7 not found"):
        await catalog.project_guide(db, seeded.project_id, version=7)


@pytest.mark.parametrize(("base", "now"), [(None, "1"), (2, "1")])
async def test_a_stale_base_is_refused(db, base, now):
    seeded = await seed_run(db)
    with pytest.raises(service.ConflictError, match=rf"now version {now}, you edited version") as e:
        await catalog.save_project_guide(db, seeded.project_id, LOCKED, base_version=base)
    assert isinstance(e.value, service.GuideChangedError)
    assert e.value.code == "guide_changed"


async def test_concurrent_saves_from_one_base_keep_exactly_one(db):
    seeded = await seed_run(db)
    results = await asyncio.gather(
        catalog.save_project_guide(db, seeded.project_id, LOCKED, base_version=1),
        catalog.save_project_guide(db, seeded.project_id, LOCKED, base_version=1),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["GuideChangedError", "GuideView"]


async def test_new_projects_have_no_guide(db):
    info = await catalog.create_project(db, "fresh", target_lang="en")
    project_id = uuid.UUID(info.id)
    assert await catalog.guide_versions(db, project_id) == []
    with pytest.raises(NotFoundError, match="has no guide yet"):
        await catalog.project_guide(db, project_id)
    first = await catalog.save_project_guide(db, project_id, LOCKED, base_version=None)
    assert first.info.version == 1
    with pytest.raises(NotFoundError):
        await catalog.guide_versions(db, uuid.uuid4())


async def test_run_guide(db, tmp_path):
    seeded = await seed_run(db)
    view = await catalog.run_guide(db, seeded.run_id)
    assert view.info.version == 1 and view.guide.summary == "A cat sleeps while it rains."
    run_id = await service.submit_bytes(
        db,
        LocalStorage(tmp_path),
        RecordingQueue(),
        build_config(),
        PromptLibrary(None),
        "a.txt",
        b"x",
        service.RunOptions(target_lang="en"),
        project="other",
    )
    with pytest.raises(service.InvalidRunStateError, match="the guide is not ready yet"):
        await catalog.run_guide(db, run_id)


async def test_rerun_sweep_uses_the_latest_approved_guide(db):
    seeded = await seed_run(db)
    saved = await catalog.save_project_guide(db, seeded.project_id, LOCKED, base_version=1)
    queue = RecordingQueue()
    message = await service.rerun_sweep(db, queue, seeded.run_id)
    assert message == "re-running the sweep with the project guide, version 2"
    async with db.session() as s:
        run = await runs.get_run(s, seeded.run_id)
        sweep = await runs.get_stage(s, seeded.run_id, "sweep")
        version = await runs.get_guide_version(s, run.guide_version_id)
    assert run.state == RunState.SWEEPING and run.finished_at is not None  # marks a re-sweep
    assert version.version == saved.info.version
    assert sweep is None
    assert [(j.task, j.lock) for j in queue.jobs] == [
        (service.TASK_FINISH_RUN, f"{seeded.run_id}:finish")
    ]


async def test_rerun_sweep_names_the_project_guide_even_a_newer_draft(db):
    """CGL-28: the sweep enforces the project guide, which may be a newer draft of a file
    translating alongside this one; the message names that version, not the approved one."""
    seeded = await seed_run(db)
    await catalog.save_project_guide(db, seeded.project_id, LOCKED, base_version=1)
    await seed_run(db, state=RunState.TRANSLATING, filename="two.txt")  # its draft: version 3
    message = await service.rerun_sweep(db, RecordingQueue(), seeded.run_id)
    assert message == "re-running the sweep with the project guide, version 3"


async def test_rerun_sweep_keeps_the_runs_guide_without_an_approved_one(db):
    seeded = await seed_run(db)
    message = await service.rerun_sweep(db, RecordingQueue(), seeded.run_id)
    assert message == "re-running the sweep with the project guide, version 1"


async def test_rerun_sweep_refusals(db):
    running = await seed_run(db, state=RunState.TRANSLATING)
    with pytest.raises(service.InvalidRunStateError, match="once the run is complete"):
        await service.rerun_sweep(db, RecordingQueue(), running.run_id)
    short = await seed_run(db)
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, short.run_id, lock=True)
        run.profile = {**run.profile, "pipeline_profile": "short"}
    with pytest.raises(service.InvalidRunStateError, match="the sweep is off for short documents"):
        await service.rerun_sweep(db, RecordingQueue(), short.run_id)
    unreadable = await seed_run(db)
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, unreadable.run_id, lock=True)
        run.config_snapshot = {**run.config_snapshot, "profiles": {"no_such_setting": 1}}
    with pytest.raises(service.InvalidRunStateError, match="can't be read") as refused:
        await service.rerun_sweep(db, RecordingQueue(), unreadable.run_id)
    assert "sweep is off" not in str(refused.value)
    async with db.session() as s, s.begin():  # a readable snapshot, an unreadable profile
        run = await runs.get_run(s, unreadable.run_id, lock=True)
        run.config_snapshot = {**run.config_snapshot, "profiles": {}}
        run.profile = {**run.profile, "pipeline_profile": "no_such_profile"}
        assert service.sweep_enabled(run) is False  # no raise
    with pytest.raises(service.InvalidRunStateError, match="can't be read"):
        await service.rerun_sweep(db, RecordingQueue(), unreadable.run_id)
    async with db.session() as s, s.begin():
        await s.execute(update(Run).where(Run.id == short.run_id).values(state="cancelled"))
    with pytest.raises(service.InvalidRunStateError, match="is cancelled"):
        await service.rerun_sweep(db, RecordingQueue(), short.run_id)


async def test_a_resweep_cannot_be_cancelled(db):
    seeded = await seed_run(db)
    await service.rerun_sweep(db, RecordingQueue(), seeded.run_id)
    with pytest.raises(service.InvalidRunStateError, match="cannot be cancelled"):
        await service.cancel_run(db, seeded.run_id)
    async with db.session() as s:
        run = await runs.get_run(s, seeded.run_id)
    assert run.state == RunState.SWEEPING and run.cancel_requested is False
    assert run.finished_at is not None


async def test_retrying_a_sweep_error_keeps_the_earlier_revisions(db):
    from coglossa.store.models import RevisionRow

    seeded = await seed_run(db)
    async with db.session() as s, s.begin():
        s.add(
            RevisionRow(
                span_id=seeded.span_ids[0], source="sweep", before="a", after="b", reason="r"
            )
        )
        row = await runs.get_stage(s, seeded.run_id, "sweep")
        runs.fail_stage(row, "sweep broke")
        row.error_count = 1
        (await runs.get_run(s, seeded.run_id)).state = RunState.COMPLETE_WITH_ERRORS
    queue = RecordingQueue()
    assert await service.retry_run(db, queue, seeded.run_id) == "re-running the sweep"
    async with db.session() as s:
        run = await runs.get_run(s, seeded.run_id)
        sweep = await runs.get_stage(s, seeded.run_id, "sweep")
        count = len(
            list(
                await s.scalars(
                    select(RevisionRow).where(
                        RevisionRow.span_id == seeded.span_ids[0], RevisionRow.source == "sweep"
                    )
                )
            )
        )
    assert run.state == RunState.SWEEPING and run.finished_at is not None and sweep is None
    assert count == 1
    assert [(j.task, j.lock) for j in queue.jobs] == [
        (service.TASK_FINISH_RUN, f"{seeded.run_id}:finish")
    ]


async def test_drafts_of_unfinished_runs_are_not_the_project_guide(db):
    waiting = await seed_run(db, state=RunState.AWAITING_GUIDE_APPROVAL)
    [draft] = await catalog.guide_versions(db, waiting.project_id)
    assert (draft.version, draft.status) == (1, "draft")
    with pytest.raises(NotFoundError, match="has no guide yet"):
        await catalog.project_guide(db, waiting.project_id)
    assert (await catalog.get_project(db, waiting.project_id)).guide_version is None
    assert (await catalog.project_guide(db, waiting.project_id, version=1)).info.version == 1
    with pytest.raises(service.ConflictError, match="now version none, you edited version 1"):
        await catalog.save_project_guide(db, waiting.project_id, LOCKED, base_version=1)
    saved = await catalog.save_project_guide(db, waiting.project_id, LOCKED, base_version=None)
    assert saved.info.version == 2
    assert (await catalog.get_project(db, waiting.project_id)).guide_version == 2


async def test_the_project_guide_is_the_newest_usable_version(db):
    seeded = await seed_run(db)  # complete; its guide v1 is an engine draft
    assert (await catalog.project_guide(db, seeded.project_id)).info.version == 1
    for state in (RunState.FAILED, RunState.CANCELLED):
        await seed_run(db, project="demo", state=state)  # seed_run's default project
    assert (await catalog.project_guide(db, seeded.project_id)).info.version == 1
    assert (await catalog.get_project(db, seeded.project_id)).guide_version == 1
    await seed_run(db, project="demo", state=RunState.TRANSLATING)  # its draft is v4
    versions = await catalog.guide_versions(db, seeded.project_id)
    assert [v.version for v in versions] == [4, 3, 2, 1]
    assert (await catalog.project_guide(db, seeded.project_id)).info.version == 4
    assert (await catalog.get_project(db, seeded.project_id)).guide_version == 4
    # a panel opened before that draft landed is refused rather than dropping its entries
    with pytest.raises(service.ConflictError, match="now version 4, you edited version 1"):
        await catalog.save_project_guide(db, seeded.project_id, LOCKED, base_version=1)


async def test_saved_guides_are_cleaned(db):
    seeded = await seed_run(db)
    dirty = LOCKED.model_copy(update={"summary": "Rain\x00 falls.\x0cEnd."})
    saved = await catalog.save_project_guide(db, seeded.project_id, dirty, base_version=1)
    assert saved.guide.summary == "Rain falls.\nEnd."


async def test_a_set_without_any_language_has_no_guide_and_refuses_a_save(db):
    async with db.session() as s, s.begin():
        project = await runs.get_or_create_project(s, "cli")  # no default, no runs
    with pytest.raises(NotFoundError, match="has no guide yet"):
        await catalog.project_guide(db, project.id)
    assert await catalog.guide_versions(db, project.id) == []
    empty = Guide(summary="", style_guide="")
    with pytest.raises(service.InvalidRequestError, match="choose a target language"):
        await catalog.save_project_guide(db, project.id, empty, base_version=None)
    with pytest.raises(service.InvalidRequestError, match="choose a target language"):
        await catalog.preview_glossary_import(db, project.id, GLOSSARY)
    saved = await catalog.save_project_guide(
        db, project.id, Guide(summary="", style_guide="x"), base_version=None, target_lang="de"
    )
    assert (saved.info.version, saved.info.target_lang) == (1, "de")


async def test_a_guide_saved_under_another_spelling_is_the_sets_language(db):
    seeded = await seed_run(db, project="spelled", target_lang="es")
    saved = await catalog.save_project_guide(
        db, seeded.project_id, Guide(summary="", style_guide="x"), base_version=1, target_lang="ES"
    )
    assert (saved.info.target_lang, saved.info.version) == ("es", 2)
    view = await catalog.project_guide(db, seeded.project_id, target_lang="ES")
    assert view.info.version == 2


# CGL-88: importing a glossary file ---------------------------------------------------------

GLOSSARY = catalog.GlossaryFile(
    b"term,translation\nchat,kitty\npluie,rain\npluie,rainfall\n", "t.csv"
)


async def test_a_glossary_preview_writes_nothing(db):
    seeded = await seed_run(db)  # guide v1: chat → cat
    preview = await catalog.preview_glossary_import(db, seeded.project_id, GLOSSARY)
    assert (preview.format, preview.target_lang, preview.source_lang) == ("csv", "en", None)
    assert (preview.base_version, preview.new, preview.identical) == (1, 1, 0)
    assert (preview.conflicts, preview.locked_conflicts, preview.duplicates) == (1, 0, 1)
    assert [(t.line, t.status, t.current_rendering) for t in preview.terms] == [
        (2, "conflict", "cat"),
        (3, "new", None),
        (4, "duplicate", "rain"),
    ]
    assert [v.version for v in await catalog.guide_versions(db, seeded.project_id)] == [1]


async def test_a_glossary_import_adds_a_user_version_and_keeps_conflicts(db):
    seeded = await seed_run(db)
    result = await catalog.import_glossary(
        db, seeded.project_id, GLOSSARY, base_version=1, lock_all=True
    )
    assert result.created_version and (result.added, result.updated, result.kept) == (1, 0, 1)
    assert (result.locked, result.duplicates) == (1, 1)
    info = result.view.info
    assert (info.version, info.status, info.author) == (2, "approved", "user")
    glossary = [(g.term, g.rendering, g.locked) for g in result.view.guide.glossary]
    assert glossary == [("chat", "cat", False), ("pluie", "rain", True)]
    assert result.view.guide.summary == "A cat sleeps while it rains."  # the rest is kept
    assert (await catalog.project_guide(db, seeded.project_id)).info.version == 2

    again = await catalog.import_glossary(db, seeded.project_id, GLOSSARY, base_version=2)
    assert not again.created_version and again.view.info.version == 2


async def test_a_glossary_import_overwrites_a_locked_conflict_only_when_asked(db):
    seeded = await seed_run(db)
    await catalog.save_project_guide(db, seeded.project_id, LOCKED, base_version=1)  # Fin locked
    file = catalog.GlossaryFile(b"Fin\tThe end\n", "t.tsv")
    preview = await catalog.preview_glossary_import(db, seeded.project_id, file)
    assert (preview.conflicts, preview.locked_conflicts) == (1, 1)
    kept = await catalog.import_glossary(db, seeded.project_id, file, base_version=2)
    assert not kept.created_version and kept.view.guide.glossary == LOCKED.glossary
    done = await catalog.import_glossary(
        db, seeded.project_id, file, base_version=2, overwrite_conflicts=True
    )
    (entry,) = done.view.guide.glossary
    assert (entry.rendering, entry.locked, done.view.info.version) == ("The end", True, 3)


async def test_a_glossary_import_from_a_stale_preview_or_a_bad_file_writes_nothing(db):
    seeded = await seed_run(db)
    with pytest.raises(service.GuideChangedError):
        await catalog.import_glossary(db, seeded.project_id, GLOSSARY, base_version=None)
    bad = catalog.GlossaryFile(b"chat,cat\nchien\n", "t.csv")
    with pytest.raises(GlossaryFileError, match="line 2 has only one column"):
        await catalog.import_glossary(db, seeded.project_id, bad, base_version=1)
    assert [v.version for v in await catalog.guide_versions(db, seeded.project_id)] == [1]


async def test_a_glossary_import_into_a_set_without_a_guide(db):
    info = await catalog.create_project(db, "terms", target_lang="en", source_lang="fr")
    project_id = uuid.UUID(info.id)
    tbx = (
        b'<martif><termEntry><langSet xml:lang="fr"><term>chat</term></langSet>'
        b'<langSet xml:lang="en"><term>cat</term></langSet>'
        b'<langSet xml:lang="de"><term>Katze</term></langSet></termEntry></martif>'
    )
    file = catalog.GlossaryFile(tbx, "t.tbx")
    preview = await catalog.preview_glossary_import(db, project_id, file)
    assert (preview.base_version, preview.source_lang, preview.new) == (None, "fr", 1)
    result = await catalog.import_glossary(db, project_id, file, base_version=None, lock_all=True)
    assert result.view.info.version == 1
    assert result.view.guide.glossary == [GlossaryEntry(term="chat", rendering="cat", locked=True)]
    other = catalog.GlossaryFile(tbx, "t.tbx", source_lang="de")
    preview = await catalog.preview_glossary_import(db, project_id, other)
    assert [t.term for t in preview.terms] == ["Katze"]


async def test_a_multilingual_tbx_takes_the_language_of_the_sets_documents(db):
    """A set whose source language is detected per document (the web's default) and a
    termbase in three languages: the documents' language is the terms' language."""
    seeded = await seed_run(db)  # no set source language; the run's profile says French
    later = await seed_run(db, filename="later.txt")  # not profiled yet: no language to go by
    async with db.session() as s, s.begin():
        await s.execute(update(Run).where(Run.id == later.run_id).values(profile=None))
    tbx = catalog.GlossaryFile(
        b'<martif><termEntry><langSet xml:lang="de"><term>Katze</term></langSet>'
        b'<langSet xml:lang="fr"><term>chat</term></langSet>'
        b'<langSet xml:lang="en"><term>kitty</term></langSet></termEntry></martif>',
        "t.tbx",
    )
    preview = await catalog.preview_glossary_import(db, seeded.project_id, tbx)
    assert preview.source_lang == "fr"
    assert [(t.term, t.status) for t in preview.terms] == [("chat", "conflict")]
    info = await catalog.create_project(db, "empty", target_lang="en")
    with pytest.raises(GlossaryFileError, match="several languages"):
        await catalog.preview_glossary_import(db, uuid.UUID(info.id), tbx)


async def test_a_glossary_is_read_off_the_event_loop_and_outside_the_sets_lock(db, monkeypatch):
    """Reading a large file takes seconds: it must neither stall the API's other requests
    nor hold the set's lock meanwhile (QC on CGL-88). A guide saved while the file is read
    goes through at once, and the import then finds the guide changed."""
    seeded = await seed_run(db)
    loop = asyncio.get_running_loop()
    seen: list[bool] = []
    real = catalog.parse_glossary

    def parse(*args, **kwargs):
        seen.append(threading.current_thread() is threading.main_thread())
        save = catalog.save_project_guide(db, seeded.project_id, LOCKED, base_version=1)
        asyncio.run_coroutine_threadsafe(save, loop).result(timeout=5)
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog, "parse_glossary", parse)
    with pytest.raises(service.GuideChangedError):
        await catalog.import_glossary(db, seeded.project_id, GLOSSARY, base_version=1)
    assert seen == [False]
    assert [v.version for v in await catalog.guide_versions(db, seeded.project_id)] == [2, 1]
