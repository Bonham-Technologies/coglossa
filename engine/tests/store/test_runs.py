import asyncio
import uuid

import pytest
from sqlalchemy import text

from coglossa import catalog
from coglossa.schema import (
    Block,
    BlockKind,
    Chunk,
    Guide,
    LLMCallRecord,
    Provenance,
    Segment,
)
from coglossa.store import runs
from coglossa.store.models import Document, Run


async def _run(db, name="p", target_lang="en"):
    async with db.session() as s, s.begin():
        project = await runs.get_or_create_project(s, name)
        doc = await runs.add_document(
            s,
            project.id,
            document_id=uuid.uuid4(),
            filename="a.txt",
            format="txt",
            sha256="abc",
            storage_key="projects/x/a.txt",
        )
        run = await runs.create_run(
            s,
            doc.id,
            target_lang=target_lang,
            config_hash="h1",
            config_snapshot={"k": 1},
            prompt_versions={"literal": 1},
        )
    return project, doc, run


async def test_projects_are_unique_by_name_even_under_concurrency(db):
    async def create():
        async with db.session() as s, s.begin():
            return (await runs.get_or_create_project(s, "same")).id

    ids = await asyncio.gather(*(create() for _ in range(5)))
    assert len(set(ids)) == 1
    async with db.session() as s:
        assert (await runs.find_project(s, "same")).id == ids[0]
        with pytest.raises(runs.NotFoundError, match="project 'other' not found"):
            await runs.find_project(s, "other")


async def test_source_round_trip_is_idempotent(db):
    _, doc, _ = await _run(db)
    blocks = [
        Block(
            id="b1",
            kind=BlockKind.PARAGRAPH,
            text="Il pleut. Fin.",
            provenance=Provenance(source_path="a.txt", index=0),
        )
    ]
    segments = [
        Segment(id="s1", block_id="b1", start=0, end=9, text="Il pleut."),
        Segment(id="s2", block_id="b1", start=10, end=14, text="Fin."),
    ]
    async with db.session() as s, s.begin():
        await runs.save_source(s, doc.id, blocks, segments)
        await runs.save_source(s, doc.id, blocks, segments)  # second ingest: no-op
    async with db.session() as s:
        assert await runs.load_source(s, doc.id) == (blocks, segments)
        assert (await s.get(Document, doc.id)).ingested_at is not None


async def test_large_sources_are_batched(db):
    _, doc, _ = await _run(db)
    blocks = [
        Block(
            id="b1",
            kind=BlockKind.PARAGRAPH,
            text="x",
            provenance=Provenance(source_path="a", index=0),
        )
    ]
    segments = [Segment(id=f"s{i}", block_id="b1", start=0, end=1, text="x") for i in range(2500)]
    async with db.session() as s, s.begin():
        await runs.save_source(s, doc.id, blocks, segments)
    async with db.session() as s:
        assert len((await runs.load_source(s, doc.id))[1]) == 2500


async def test_guide_versions_are_numbered_per_project(db):
    project, _, run = await _run(db)
    guide = Guide(summary="s", style_guide="g")
    async with db.session() as s, s.begin():
        v1 = await runs.add_guide_version(
            s,
            project.id,
            guide,
            target_lang="en",
            status=runs.DRAFT,
            author=runs.ENGINE,
            source_run_id=run.id,
        )
        v2 = await runs.add_guide_version(
            s, project.id, guide, target_lang="en", status=runs.APPROVED, author=runs.USER
        )
        v3 = await runs.add_guide_version(
            s, project.id, guide, target_lang="en", status=runs.DRAFT, author=runs.ENGINE
        )
    assert (v1.version, v2.version, v3.version) == (1, 2, 3)
    async with db.session() as s:
        assert (await runs.latest_guide(s, project.id, "en", approved_only=True)).id == v2.id
        assert (await runs.latest_guide(s, project.id, "en", approved_only=False)).id == v3.id
        assert await runs.load_guide(s, v1.id) == guide
        with pytest.raises(runs.NotFoundError):
            await runs.get_guide_version(s, uuid.uuid4())
        assert await runs.latest_guide(s, uuid.uuid4(), "en", approved_only=False) is None


async def test_get_run_and_lock_sees_fresh_state(db):
    _, doc, run = await _run(db)
    async with db.session() as s:
        stale = await runs.get_run(s, run.id)
        assert stale.state == "created" and stale.document_id == doc.id
        async with db.session() as other, other.begin():
            (await runs.get_run(other, run.id, lock=True)).state = "ingesting"
        fresh = await runs.get_run(s, run.id, lock=True)  # same transaction as `stale`
        assert fresh is stale and fresh.state == "ingesting"
        await s.rollback()
        with pytest.raises(runs.NotFoundError, match="not found"):
            await runs.get_run(s, uuid.uuid4())
        with pytest.raises(runs.NotFoundError):
            await runs.get_document(s, uuid.uuid4())


async def test_chunks_round_trip_and_replace(db):
    _, _, run = await _run(db)
    chunks = [Chunk(id="c1", segment_ids=["s1", "s2"]), Chunk(id="c2", segment_ids=["s3"])]
    async with db.session() as s, s.begin():
        await runs.save_chunks(s, run.id, chunks)
        await runs.save_chunks(s, run.id, chunks[:1])
    async with db.session() as s:
        assert await runs.load_chunks(s, run.id) == chunks[:1]


async def test_stage_lifecycle(db):
    _, _, run = await _run(db)
    async with db.session() as s, s.begin():
        row = await runs.begin_stage(s, run.id, "ingest")
        assert (row.status, row.attempt, row.chunk_id) == (runs.RUNNING, 1, "")
        runs.fail_stage(row, "boom")
    async with db.session() as s, s.begin():
        row = await runs.begin_stage(s, run.id, "ingest")
        assert (row.status, row.attempt, row.error, row.error_count) == (runs.RUNNING, 2, None, 0)
        runs.finish_stage(row, {"errors": [{"stage": "x", "message": "m"}], "chunks": 2})
    async with db.session() as s:
        row = await runs.get_stage(s, run.id, "ingest")
        assert row.status == runs.COMPLETE and row.error_count == 1
        assert row.duration_ms is not None and row.finished_at is not None
        assert await runs.get_stage(s, run.id, "profile") is None


async def test_chunk_count(db):
    _, _, run = await _run(db)
    _, _, other = await _run(db, name="q")
    async with db.session() as s:
        assert await runs.chunk_count(s, run.id) == 0
    async with db.session() as s, s.begin():
        await runs.save_chunks(
            s,
            run.id,
            [Chunk(id="c1", segment_ids=["s1"]), Chunk(id="c2", segment_ids=["s2", "s3"])],
        )
        await runs.save_chunks(s, other.id, [Chunk(id="c1", segment_ids=["s1"])])
    async with db.session() as s:
        assert await runs.chunk_count(s, run.id) == 2
        assert await runs.chunk_count(s, other.id) == 1


async def test_chunk_counters(db):
    _, _, run = await _run(db)
    async with db.session() as s, s.begin():
        for cid, outcome in (("c1", {"errors": []}), ("c2", {"errors": [], "engine_error": True})):
            for stage in ("translate", "reconcile", "score"):
                runs.finish_stage(await runs.begin_stage(s, run.id, stage, cid), outcome)
        runs.fail_stage(await runs.begin_stage(s, run.id, "score", "c3"), "boom")
        await runs.begin_stage(s, run.id, "translate", "c4")
        runs.finish_stage(
            await runs.begin_stage(s, run.id, "guide"),
            {"errors": [{"stage": "guide", "message": "m"}]},
        )
    async with db.session() as s:
        assert await runs.chunk_progress(s, run.id) == {"translate": 2, "reconcile": 2, "score": 2}
        assert await runs.finished_chunk_count(s, run.id) == 3
        assert sorted(await runs.chunks_with_errors(s, run.id)) == ["c2", "c3"]
        assert await runs.total_error_count(s, run.id) == 5  # c2: 3 rows x 1, c3: 1, guide: 1
        assert [r.stage for r in await runs.stage_rows(s, run.id)][:3] == [
            "translate",
            "reconcile",
            "score",
        ]
    async with db.session() as s, s.begin():
        await runs.delete_stages(s, run.id, chunk_ids=["c2", "c3"])
        await runs.delete_stages(s, run.id, stages=["guide"])
        await runs.delete_stages(s, run.id, status=runs.RUNNING)
    async with db.session() as s:
        assert await runs.finished_chunk_count(s, run.id) == 1
        assert await runs.total_error_count(s, run.id) == 0


async def test_llm_calls_round_trip(db):
    _, _, run = await _run(db)
    records = [
        LLMCallRecord(
            role="translator:literal",
            backend="fake",
            model="m",
            prompt_id="literal",
            prompt_version=1,
            attempt=1,
            ok=True,
            tokens_in=10,
            tokens_out=5,
            request={"messages": []},
            response="{}",
        ),
        LLMCallRecord(
            role="reviewer",
            backend="fake",
            model="m",
            prompt_id="reconcile",
            prompt_version=1,
            attempt=2,
            ok=False,
            error="timeout",
        ),
    ]
    async with db.session() as s, s.begin():
        await runs.save_llm_calls(s, run.id, records, stage="chunk", chunk_id="c1")
        await runs.save_llm_calls(s, run.id, [], stage="profile")
    async with db.session() as s:
        assert await runs.load_llm_calls(s, run.id) == records


async def test_delete_project_cascades(db):
    project, _, run = await _run(db)
    async with db.session() as s, s.begin():
        await runs.delete_project(s, project.id)
    async with db.session() as s:
        assert await s.get(Run, run.id) is None


async def _wait_for_a_lock_wait(db):
    """Until a session of this test database is waiting for a lock (deadlines a hung test)."""
    query = text(
        "SELECT count(*) FROM pg_stat_activity"
        " WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    for _ in range(500):
        async with db.session() as s:
            if await s.scalar(query):
                return
        await asyncio.sleep(0.01)
    raise AssertionError("no session waited for a lock")


async def test_deleting_a_project_during_a_guide_save_cannot_deadlock(db):
    """A guide save locks the run, then the project (approve_guide, add_guide_entry and the
    guide stage's save all do). Deleting the project takes its runs first, then the project, so
    the delete waits for the save instead of the two waiting on each other (CGL-28)."""
    project, _, run = await _run(db)
    async with db.session() as save, save.begin():
        await runs.get_run(save, run.id, lock=True)
        deleting = asyncio.create_task(_delete_project(db, project.id))
        await _wait_for_a_lock_wait(db)  # the delete waits for this save's run lock
        # in the other order the delete would hold the project here, and Postgres would fail
        # one side with a deadlock
        await asyncio.wait_for(runs.lock_project(save, project.id), timeout=10)
    await asyncio.wait_for(deleting, timeout=10)
    async with db.session() as s:
        assert await s.get(Run, run.id) is None


async def _delete_project(db, project_id):
    async with db.session() as s, s.begin():
        await runs.delete_project(s, project_id)


async def _set_state(db, run_id, state):
    async with db.session() as s, s.begin():
        (await runs.get_run(s, run_id, lock=True)).state = state


async def _draft(db, project_id, run_id, summary, status=runs.DRAFT, target_lang="en"):
    async with db.session() as s, s.begin():
        return await runs.add_guide_version(
            s,
            project_id,
            Guide(summary=summary, style_guide="g"),
            target_lang=target_lang,
            status=status,
            author=runs.ENGINE,
            source_run_id=run_id,
        )


async def _project_guide(db, project_id, target_lang="en"):
    """prior_guide's version and the catalog's (its SQL mirror); they must always agree."""
    async with db.session() as s:
        row = await runs.prior_guide(s, project_id, target_lang)
    info = await catalog.get_project(db, project_id)
    listed = {lang.target_lang: lang.guide_version for lang in info.languages}
    assert listed.get(target_lang) == (row.version if row else None)
    return row


async def test_prior_guide_is_the_newest_approved_or_usable_draft(db):
    project, _, first = await _run(db)
    _, _, second = await _run(db)
    _, _, third = await _run(db)
    assert await _project_guide(db, project.id) is None
    finished = await _draft(db, project.id, first.id, "finished")
    await _set_state(db, first.id, "complete_with_errors")
    for state in ("failed", "cancelled", "awaiting_guide_approval"):  # never, however recent
        await _set_state(db, second.id, state)
        await _draft(db, project.id, second.id, state)
        assert (await _project_guide(db, project.id)).id == finished.id, state
    await _set_state(db, third.id, "translating")
    running = await _draft(db, project.id, third.id, "still translating")
    assert (await _project_guide(db, project.id)).id == running.id
    approved = await _draft(db, project.id, second.id, "approved", status=runs.APPROVED)
    assert (await _project_guide(db, project.id)).id == approved.id  # approved, whatever its run
    newer = await _draft(db, project.id, third.id, "built on the approved one")
    assert (await _project_guide(db, project.id)).id == newer.id  # the newest usable wins


async def test_guide_versions_count_and_resolve_per_language(db):
    project, _, english = await _run(db)
    _, _, spanish = await _run(db, target_lang="es")
    await _set_state(db, english.id, "complete")
    await _set_state(db, spanish.id, "complete")
    en1 = await _draft(db, project.id, english.id, "english")
    es1 = await _draft(db, project.id, spanish.id, "spanish", target_lang="es")
    en2 = await _draft(db, project.id, english.id, "english again")
    assert (en1.version, es1.version, en2.version) == (1, 1, 2)
    async with db.session() as s:
        assert (await runs.prior_guide(s, project.id, "en")).id == en2.id
        assert (await runs.prior_guide(s, project.id, "es")).id == es1.id
        assert await runs.prior_guide(s, project.id, "de") is None
        assert (await runs.latest_guide(s, project.id, "es", approved_only=False)).id == es1.id


def test_language_keys_and_order():
    assert runs.language_key("pt_BR") == runs.language_key("PT-br") == "pt-br"
    assert runs.ordered_languages("en", ["es", "EN", "fr", "es"]) == ["en", "es", "fr"]
    assert runs.ordered_languages(None, ["de", "it"]) == ["de", "it"]
    assert runs.ordered_languages(None, []) == []


async def test_set_languages_puts_the_default_first_then_first_runs(db):
    project, _, _ = await _run(db, target_lang="es")  # a CLI project: no default language
    await _run(db, target_lang="de")
    await _run(db, target_lang="ES")
    async with db.session() as s:
        row = await runs.get_project(s, project.id)
        assert await runs.set_languages(s, row) == ["es", "de"]
        row.target_lang = "fr"
        assert await runs.set_languages(s, row) == ["fr", "es", "de"]
