import asyncio
import uuid

import pytest
from sqlalchemy import event, select

from coglossa import catalog, service
from coglossa.ingest.base import IngestError, UnsupportedFormatError
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import (
    Agreement,
    Block,
    BlockKind,
    Chunk,
    DocumentProfile,
    Genre,
    GlossaryEntry,
    Guide,
    LLMCallRecord,
    PipelineProfile,
    Provenance,
    Reliability,
    RunError,
    RunState,
    RunStatus,
    Segment,
    Span,
    SpanScore,
)
from coglossa.stages.chunk import ChunkOutcome
from coglossa.storage import LocalStorage
from coglossa.store import outputs, runs
from coglossa.store.models import Run, utcnow
from coglossa.testing.queue import RecordingQueue
from tests.conftest import build_config

PROFILE = DocumentProfile(
    source_lang="fr",
    target_lang="en",
    mixed_language=False,
    genre=Genre.OTHER,
    tone_register="neutral",
    word_count=3,
    pipeline_profile=PipelineProfile.SHORT,
)


@pytest.fixture
def env(db, tmp_path):
    cfg = build_config()
    return db, LocalStorage(tmp_path / "files"), RecordingQueue(), cfg, PromptLibrary(None)


async def _submit(env, tmp_path, name="story.txt", text="Il pleut.", **opts):
    db, storage, queue, cfg, prompts = env
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    options = service.SubmitOptions(project="novel", target_lang="en", **opts)
    return await service.submit_run(db, storage, queue, cfg, prompts, path, options)


async def _set_state(db, run_id, state, **fields):
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        run.state = state
        for k, v in fields.items():
            setattr(run, k, v)


async def test_submit_stores_the_file_and_queues_the_run(env, tmp_path):
    db, storage, queue, cfg, prompts = env
    run_id = await _submit(env, tmp_path, source_lang="fr", checkpoint=True)
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        doc = await runs.get_document(s, run.document_id)
    assert run.state == RunState.CREATED
    assert (run.target_lang, run.source_lang, run.checkpoint) == ("en", "fr", True)
    assert run.config_snapshot == cfg.model_dump(mode="json")
    assert run.prompt_versions["sweep"] == 2
    assert doc.filename == "story.txt" and doc.format == "txt"
    assert storage.get(doc.storage_key) == b"Il pleut."
    assert doc.storage_key.endswith("/story.txt")
    assert [(j.task, j.lock, j.kwargs) for j in queue.jobs] == [
        (service.TASK_START_RUN, f"{run_id}:start", {"run_id": str(run_id)})
    ]


async def test_submit_rejects_bad_input_before_storing_anything(env, tmp_path):
    db, storage, queue, cfg, prompts = env
    with pytest.raises(UnsupportedFormatError):
        await _submit(env, tmp_path, name="image.png")
    with pytest.raises(IngestError, match="not found"):
        await service.submit_run(
            db,
            storage,
            queue,
            cfg,
            prompts,
            tmp_path / "nope.txt",
            service.SubmitOptions(project="p", target_lang="en"),
        )
    assert queue.jobs == [] and not (tmp_path / "files").exists()


async def _awaiting(env, tmp_path):
    db = env[0]
    run_id = await _submit(env, tmp_path)
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        doc = await runs.get_document(s, run.document_id)
        draft = await runs.add_guide_version(
            s,
            doc.project_id,
            Guide(summary="s", style_guide="g"),
            target_lang=run.target_lang,
            status=runs.DRAFT,
            author=runs.ENGINE,
            source_run_id=run_id,
        )
        run.guide_version_id = draft.id
        run.state = RunState.AWAITING_GUIDE_APPROVAL
    env[2].jobs.clear()
    return run_id


async def test_approve_the_generated_guide(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _awaiting(env, tmp_path)
    assert await service.run_guide(db, run_id) == Guide(summary="s", style_guide="g")
    assert await service.approve_guide(db, queue, run_id) == 2
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        version = await runs.get_guide_version(s, run.guide_version_id)
    assert run.state == RunState.TRANSLATING
    assert (version.version, version.status, version.author) == (2, "approved", "engine")
    assert [j.task for j in queue.jobs] == [service.TASK_TRANSLATE_RUN]


async def test_approve_an_edited_guide(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _awaiting(env, tmp_path)
    edited = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    await service.approve_guide(db, queue, run_id, edited)
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        version = await runs.get_guide_version(s, run.guide_version_id)
    assert version.author == "user" and Guide.model_validate(version.content) == edited
    with pytest.raises(service.InvalidRunStateError, match="awaiting guide approval"):
        await service.approve_guide(db, queue, run_id)


async def _versions(db, run_id):
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        doc = await runs.get_document(s, run.document_id)
        latest = await runs.latest_guide(s, doc.project_id, run.target_lang, approved_only=False)
        return RunState(run.state), latest.version


async def test_another_runs_draft_does_not_block_approval(env, tmp_path):
    db, _, queue, _, _ = env
    first = await _awaiting(env, tmp_path)
    second = await _awaiting(env, tmp_path)  # a newer draft (v2) in the same project
    assert await service.approve_guide(db, queue, first) == 3  # M4a refused this: v2 was "latest"
    with pytest.raises(service.ConflictError, match="now version 3"):
        await service.approve_guide(db, queue, second)  # a person approved v3 after its draft


async def test_approved_guides_are_cleaned(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _awaiting(env, tmp_path)
    dirty = Guide(summary="s\x00", style_guide="house\x0c")
    await service.approve_guide(db, queue, run_id, dirty)
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        version = await runs.get_guide_version(s, run.guide_version_id)
    assert Guide.model_validate(version.content) == Guide(summary="s", style_guide="house\n")


async def test_approve_refuses_when_the_project_guide_changed(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _awaiting(env, tmp_path)
    async with db.session() as s:
        project_id = (
            await runs.get_document(s, (await runs.get_run(s, run_id)).document_id)
        ).project_id
    edited = Guide(
        summary="s", style_guide="house", glossary=[GlossaryEntry(term="a", rendering="b")]
    )
    await catalog.save_project_guide(db, project_id, edited, base_version=None)
    message = (
        "the project guide changed since this draft (now version 2); reload it and approve the "
        "current guide"
    )
    for kwargs in ({}, {"guide": edited}, {"guide": edited, "base_version": 1}):
        with pytest.raises(service.GuideChangedError) as e:
            await service.approve_guide(db, queue, run_id, **kwargs)
        assert str(e.value) == message
    assert await _versions(db, run_id) == (RunState.AWAITING_GUIDE_APPROVAL, 2)
    assert queue.jobs == []
    with pytest.raises(service.InvalidRequestError) as refused:
        await service.approve_guide(db, queue, run_id, base_version=2)
    assert str(refused.value) == (
        "base_version is only used with an edited guide; send the guide you approve"
    )
    assert await _versions(db, run_id) == (RunState.AWAITING_GUIDE_APPROVAL, 2)
    assert queue.jobs == []
    assert await service.approve_guide(db, queue, run_id, edited, base_version=2) == 3
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        version = await runs.get_guide_version(s, run.guide_version_id)
    assert run.state == RunState.TRANSLATING and version.version == 3
    assert Guide.model_validate(version.content) == edited


async def test_run_guide_before_one_exists(env, tmp_path):
    run_id = await _submit(env, tmp_path)
    with pytest.raises(service.InvalidRunStateError, match="not ready"):
        await service.run_guide(env[0], run_id)


async def test_cancel(env, tmp_path):
    db = env[0]
    created = await _submit(env, tmp_path)
    assert await service.cancel_run(db, created) is RunState.CANCELLED
    running = await _submit(env, tmp_path)
    await _set_state(db, running, RunState.TRANSLATING)
    assert await service.cancel_run(db, running) is RunState.TRANSLATING
    async with db.session() as s:
        assert (await runs.get_run(s, running)).cancel_requested is True
    with pytest.raises(service.InvalidRunStateError, match="already cancelled"):
        await service.cancel_run(db, created)
    with pytest.raises(runs.NotFoundError):
        await service.cancel_run(db, uuid.uuid4())


@pytest.mark.parametrize(
    ("state", "match"),
    [
        (RunState.COMPLETE, "nothing to retry"),
        (RunState.CANCELLED, "submit a new run"),
        (RunState.AWAITING_GUIDE_APPROVAL, "approve the guide"),
    ],
)
async def test_retry_refusals(env, tmp_path, state, match):
    db, _, queue, _, _ = env
    run_id = await _submit(env, tmp_path)
    await _set_state(db, run_id, state)
    with pytest.raises(service.InvalidRunStateError, match=match):
        await service.retry_run(db, queue, run_id)


@pytest.mark.parametrize(
    ("state", "task"),
    [
        (RunState.CREATED, service.TASK_START_RUN),
        (RunState.PROFILING, service.TASK_START_RUN),
        (RunState.TRANSLATING, service.TASK_TRANSLATE_RUN),
        (RunState.SWEEPING, service.TASK_FINISH_RUN),
    ],
)
async def test_retry_resumes_an_active_run(env, tmp_path, state, task):
    db, _, queue, _, _ = env
    run_id = await _submit(env, tmp_path)
    queue.jobs.clear()
    await _set_state(db, run_id, state)
    assert await service.retry_run(db, queue, run_id) == "resumed"
    assert [j.task for j in queue.jobs] == [task]


async def test_retry_a_failed_run_restarts_the_failed_stage(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _submit(env, tmp_path)
    queue.jobs.clear()
    async with db.session() as s, s.begin():
        runs.finish_stage(await runs.begin_stage(s, run_id, "ingest"), {"errors": []})
        runs.fail_stage(await runs.begin_stage(s, run_id, "profile"), "profiling failed: x")
    await _set_state(db, run_id, RunState.FAILED, error="profiling failed: x")
    assert await service.retry_run(db, queue, run_id) == "restarting from the failed stage"
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        assert (run.state, run.error) == (RunState.CREATED, None)
        assert await runs.get_stage(s, run_id, "profile") is None
        assert (await runs.get_stage(s, run_id, "ingest")).status == runs.COMPLETE
    assert [j.task for j in queue.jobs] == [service.TASK_START_RUN]


async def _complete_run(env, tmp_path, *, chunk_errors=True):
    """A finished two-chunk run written straight to the store."""
    db = env[0]
    run_id = await _submit(env, tmp_path, text="Il pleut. Fin.")
    blocks = [
        Block(
            id="b1",
            kind=BlockKind.PARAGRAPH,
            text="Il pleut. Fin.",
            provenance=Provenance(source_path="story.txt", index=0),
        )
    ]
    segments = [
        Segment(id="s1", block_id="b1", start=0, end=9, text="Il pleut."),
        Segment(id="s2", block_id="b1", start=10, end=14, text="Fin."),
    ]
    guide = Guide(summary="s", style_guide="g")

    def outcome(cid, sid, text, errors):
        span = Span(
            id=f"{cid}.1",
            chunk_id=cid,
            start=sid,
            end=sid,
            text=text,
            agreement=Agreement.AGREE,
            chosen_from="literal",
            score=SpanScore(
                signals={"qe": 0.9}, meaning=90.0, style=None, reliability=Reliability.A
            ),
        )
        return ChunkOutcome(
            chunk_id=cid,
            spans=[span],
            proposed_terms=[[GlossaryEntry(term="pluie", rendering="rain")]],
            errors=errors,
        )

    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        doc = await runs.get_document(s, run.document_id)
        await runs.save_source(s, doc.id, blocks, segments)
        await runs.save_chunks(
            s, run_id, [Chunk(id="c1", segment_ids=["s1"]), Chunk(id="c2", segment_ids=["s2"])]
        )
        version = await runs.add_guide_version(
            s,
            doc.project_id,
            guide,
            target_lang="en",
            status=runs.DRAFT,
            author=runs.ENGINE,
            source_run_id=run_id,
        )
        run.guide_version_id = version.id
        run.profile = PROFILE.model_dump(mode="json")
        run.language_tier = "A"
        bad = [RunError(stage="judge", chunk_id="c2", message="judge timed out")]
        for k, (cid, sid, text, errors) in enumerate(
            [("c1", "s1", "It rains.", []), ("c2", "s2", "End.", bad if chunk_errors else [])]
        ):
            out = outcome(cid, sid, text, errors)
            await outputs.save_chunk_outcome(s, run_id, k, out)
            runs.finish_stage(
                await runs.begin_stage(s, run_id, "score", cid), out.model_dump(mode="json")
            )
        runs.finish_stage(await runs.begin_stage(s, run_id, "sweep"), {"errors": [], "revised": 0})
        await runs.save_llm_calls(
            s,
            run_id,
            [
                LLMCallRecord(
                    role="reviewer",
                    backend="fake",
                    model="m",
                    prompt_id="reconcile",
                    prompt_version=1,
                    attempt=1,
                    ok=True,
                    tokens_in=3,
                    tokens_out=4,
                )
            ],
            stage="chunk",
            chunk_id="c1",
        )
        run.state = RunState.COMPLETE_WITH_ERRORS if chunk_errors else RunState.COMPLETE
    return run_id


async def test_progress(env, tmp_path):
    run_id = await _complete_run(env, tmp_path)
    progress = await service.run_progress(env[0], run_id)
    assert progress.state is RunState.COMPLETE_WITH_ERRORS
    assert progress.chunks_total == 2
    assert progress.chunks_done == {"translate": 0, "reconcile": 0, "score": 2}
    assert progress.chunks_with_errors == 1 and progress.error is None


async def test_progress_counts_chunks_without_loading_them(env, tmp_path, monkeypatch):
    run_id = await _complete_run(env, tmp_path)
    expected = await service.run_progress(env[0], run_id)
    real, calls = runs.chunk_count, []

    async def counting(*args):
        calls.append(args)
        return await real(*args)

    async def no_load(*args, **kwargs):
        raise AssertionError("run_progress must not load chunk rows")

    monkeypatch.setattr(runs, "chunk_count", counting)
    monkeypatch.setattr(runs, "load_chunks", no_load)
    progress = await service.run_progress(env[0], run_id)
    assert progress == expected and progress.chunks_total == 2
    assert len(calls) == 1


async def test_build_report(env, tmp_path):
    db = env[0]
    run_id = await _complete_run(env, tmp_path)
    async with db.session() as s:
        submitted_hash = (await runs.get_run(s, run_id)).config_hash
    report = await service.build_report(db, run_id)
    assert report.run_id == str(run_id)
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    assert report.document.source_path == "story.txt"
    assert [(s.id, s.text) for s in report.spans] == [("p1", "It rains."), ("p2", "End.")]
    assert [e.message for e in report.errors] == ["judge timed out"]
    assert [g.term for g in report.guide_proposals] == ["pluie"]
    assert report.usage.tokens_in == 3 and report.usage.calls == 1
    assert report.config.hash == submitted_hash  # the snapshot taken at submission
    assert report.config.prompt_versions["sweep"] == 2
    assert report.config.roles["reviewer"] == "model-a"
    assert report.language_tier is Reliability.A
    assert report.notices == []
    assert len(report.segments) == 2 and [c.id for c in report.chunks] == ["c1", "c2"]


async def test_report_needs_a_finished_run(env, tmp_path):
    run_id = await _submit(env, tmp_path)
    with pytest.raises(service.InvalidRunStateError, match="once it is complete"):
        await service.build_report(env[0], run_id)


async def test_retry_failed_chunks(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _complete_run(env, tmp_path)
    queue.jobs.clear()
    assert await service.retry_run(db, queue, run_id) == "retrying 1 chunk(s)"
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        assert run.state == RunState.TRANSLATING
        assert await runs.get_stage(s, run_id, "score", "c2") is None
        assert await runs.get_stage(s, run_id, "sweep") is None
        assert (await runs.get_stage(s, run_id, "score", "c1")).status == runs.COMPLETE
        assert [sp.chunk_id for sp in (await outputs.load_spans(s, run_id)).spans] == ["c1"]
    assert [j.task for j in queue.jobs] == [service.TASK_TRANSLATE_RUN]


async def test_retrying_failed_chunks_keeps_finished_at_and_refuses_cancel(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _complete_run(env, tmp_path)
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        run.finished_at = finished = utcnow()
    await service.retry_run(db, queue, run_id)
    with pytest.raises(service.InvalidRunStateError, match="reworking a finished translation"):
        await service.cancel_run(db, run_id)
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
    assert (run.state, run.finished_at, run.cancel_requested) == (
        RunState.TRANSLATING,
        finished,
        False,
    )


async def _add_stage_errors(db, run_id, stage, *, fail=False):
    async with db.session() as s, s.begin():
        row = await runs.begin_stage(s, run_id, stage)
        if fail:
            runs.fail_stage(row, f"{stage} broke")
        else:
            error = RunError(stage=stage, message=f"{stage} note").model_dump()
            runs.finish_stage(row, {"errors": [error]})


async def test_retry_refuses_when_only_earlier_stages_have_errors(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _complete_run(env, tmp_path, chunk_errors=False)
    await _add_stage_errors(db, run_id, "config")
    await _add_stage_errors(db, run_id, "guide")
    await _set_state(db, run_id, RunState.COMPLETE_WITH_ERRORS)
    assert (await service.run_progress(db, run_id)).retryable is False
    queue.jobs.clear()
    with pytest.raises(service.InvalidRunStateError, match=r"\(config, guide\) come from earlier"):
        await service.retry_run(db, queue, run_id)
    assert queue.jobs == []
    async with db.session() as s:
        assert (await runs.get_run(s, run_id)).state == RunState.COMPLETE_WITH_ERRORS


@pytest.mark.parametrize("fail", [False, True], ids=["sweep-note", "sweep-failed"])
async def test_retry_reruns_a_sweep_with_errors(env, tmp_path, fail):
    db, _, queue, _, _ = env
    run_id = await _complete_run(env, tmp_path, chunk_errors=False)
    await _add_stage_errors(db, run_id, "config")
    await _add_stage_errors(db, run_id, "sweep", fail=fail)
    await _set_state(db, run_id, RunState.COMPLETE_WITH_ERRORS)
    assert (await service.run_progress(db, run_id)).retryable is True
    assert await service.retry_run(db, queue, run_id) == "re-running the sweep"


async def test_progress_says_whether_a_retry_can_help(env, tmp_path):
    db = env[0]
    with_chunk_errors = await _complete_run(env, tmp_path)
    assert (await service.run_progress(db, with_chunk_errors)).retryable is True
    clean = await _complete_run(env, tmp_path, chunk_errors=False)
    assert (await service.run_progress(db, clean)).retryable is False
    await _set_state(db, clean, RunState.FAILED, error="boom")
    assert (await service.run_progress(db, clean)).retryable is True
    await _set_state(db, clean, RunState.TRANSLATING)
    assert (await service.run_progress(db, clean)).retryable is False


async def test_delete_project_removes_rows_and_files(env, tmp_path):
    db, storage, _, _, _ = env
    run_id = await _submit(env, tmp_path)
    await service.delete_project(db, storage, "novel")
    async with db.session() as s:
        with pytest.raises(runs.NotFoundError):
            await runs.get_run(s, run_id)
    assert not any((tmp_path / "files" / "projects").iterdir())
    with pytest.raises(runs.NotFoundError, match="project 'novel' not found"):
        await service.delete_project(db, storage, "novel")


def test_recording_queue_honours_locks():
    import asyncio

    queue = RecordingQueue()

    async def go():
        await queue.defer("t", lock="a", run_id="1")
        await queue.defer("t", lock="a", run_id="1")
        await queue.defer("t", run_id="2")
        await queue.defer("t", run_id="2")

    asyncio.run(go())
    assert len(queue.jobs) == 3 and len(queue.attempts) == 4
    assert queue.pop().kwargs == {"run_id": "1"}
    assert len(queue.jobs) == 2


async def test_retry_clears_a_pending_cancel_request(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _submit(env, tmp_path)
    queue.jobs.clear()
    await _set_state(db, run_id, RunState.FAILED, error="boom", cancel_requested=True)
    await service.retry_run(db, queue, run_id)
    async with db.session() as s:
        assert (await runs.get_run(s, run_id)).cancel_requested is False
    assert [j.task for j in queue.jobs] == [service.TASK_START_RUN]


async def test_a_failed_chunk_counts_as_done(env, tmp_path):
    db = env[0]
    run_id = await _complete_run(env, tmp_path)
    async with db.session() as s, s.begin():
        runs.fail_stage(await runs.begin_stage(s, run_id, "score", "c2"), "internal error: x")
    progress = await service.run_progress(db, run_id)
    assert progress.chunks_done["score"] == 2
    assert progress.chunks_with_errors == 1


async def test_build_report_without_llm_calls_keeps_usage(env, tmp_path):
    db = env[0]
    run_id = await _complete_run(env, tmp_path)
    full = await service.build_report(db, run_id)
    lean = await service.build_report(db, run_id, include_llm_calls=False)
    assert len(full.llm_calls) == 1 and lean.llm_calls == []
    assert lean.usage == full.usage
    assert lean.usage.calls == 1


async def test_approve_refuses_a_base_version_that_is_neither_the_draft_nor_the_project_guide(
    env, tmp_path
):
    db, _, queue, _, _ = env
    run_id = await _awaiting(env, tmp_path)  # draft v1, no project guide
    edited = Guide(summary="s", style_guide="house")
    for base, project in ((9, "none"), (2, "none")):
        with pytest.raises(service.InvalidRequestError) as e:
            await service.approve_guide(db, queue, run_id, edited, base_version=base)
        assert str(e.value) == (
            f"base_version {base} is neither this run's draft (version 1) nor the project "
            f"guide (version {project})"
        )
    async with db.session() as s:
        project_id = (
            await runs.get_document(s, (await runs.get_run(s, run_id)).document_id)
        ).project_id
    await catalog.save_project_guide(db, project_id, edited, base_version=None)  # v2
    with pytest.raises(service.InvalidRequestError, match="base_version 3 is neither"):
        await service.approve_guide(db, queue, run_id, edited, base_version=3)
    assert await _versions(db, run_id) == (RunState.AWAITING_GUIDE_APPROVAL, 2)
    assert queue.jobs == []


async def test_a_finished_runs_draft_does_not_block_approval(env, tmp_path):
    """Chapter 1 waits at the checkpoint while chapter 2, run without one, finishes: chapter 2's
    draft becomes the project guide (nothing is approved yet), and chapter 1 is still approved as
    it is. Only a person's approval or save outranks a draft."""
    db, _, queue, _, _ = env
    waiting = await _awaiting(env, tmp_path)  # draft v1, built without a project guide
    finished = await _awaiting(env, tmp_path)  # draft v2 ...
    await _set_state(db, finished, RunState.COMPLETE)  # ... is now the project guide
    assert await service.approve_guide(db, queue, waiting) == 3


async def test_an_edit_started_from_a_finished_runs_draft_is_approved(env, tmp_path):
    db, _, queue, _, _ = env
    waiting = await _awaiting(env, tmp_path)
    finished = await _awaiting(env, tmp_path)
    await _set_state(db, finished, RunState.COMPLETE)
    edited = Guide(summary="s", style_guide="from the project guide")
    assert await service.approve_guide(db, queue, waiting, edited, base_version=2) == 3
    saved = await service.run_guide(db, waiting)
    assert saved.style_guide == "from the project guide"  # the edit, not the draft (CGL-54)


async def test_guide_conflicts_carry_a_code(env, tmp_path):
    db, _, queue, _, _ = env
    first = await _awaiting(env, tmp_path)
    second = await _awaiting(env, tmp_path)
    await service.approve_guide(db, queue, first)  # a person approved v3 after second's draft
    with pytest.raises(service.GuideChangedError) as e:
        await service.approve_guide(db, queue, second)
    assert e.value.code == "guide_changed"
    assert service.InvalidRunStateError("x").code is None


# ---------------------------------------------------------------- a set's languages (CGL-24)


async def _upload(env, name, text, target_lang="en", **kwargs):
    db, storage, queue, cfg, prompts = env
    opts = service.RunOptions(target_lang=target_lang)
    return await service.submit_bytes(
        db, storage, queue, cfg, prompts, name, text.encode(), opts, project="set", **kwargs
    )


async def test_add_language_starts_every_file_once(env):
    db, _, queue, cfg, prompts = env
    first = await _upload(env, "a.txt", "Le chat.")
    await _upload(env, "b.txt", "Le chien.")
    project_id = uuid.UUID((await catalog.run_detail(db, first)).run.project_id)
    queue.jobs.clear()
    wanted = service.RunOptions(target_lang="es", checkpoint=True, use_project_guide=True)
    started = await service.add_language(db, queue, cfg, prompts, project_id, wanted)
    assert len(started) == 2
    async with db.session() as s:
        made = [await runs.get_run(s, r) for r in started]
    assert {r.target_lang for r in made} == {"es"}
    assert all(r.checkpoint is False and not r.use_project_guide for r in made)
    assert [j.kwargs["run_id"] for j in queue.jobs] == [str(r) for r in started]
    with pytest.raises(service.LanguageExistsError) as refused:
        await service.add_language(
            db, queue, cfg, prompts, project_id, service.RunOptions(target_lang="ES")
        )
    assert refused.value.code == "language_exists"


async def test_add_language_needs_files(env):
    db, _, queue, cfg, prompts = env
    info = await catalog.create_project(db, "empty", target_lang="en")
    with pytest.raises(service.InvalidRequestError, match="has no files"):
        await service.add_language(
            db, queue, cfg, prompts, uuid.UUID(info.id), service.RunOptions(target_lang="es")
        )
    assert queue.jobs == []


async def test_an_upload_can_start_in_every_language_of_the_set(env):
    db, _, queue, cfg, prompts = env
    first = await _upload(env, "a.txt", "Le chat.")
    project_id = uuid.UUID((await catalog.run_detail(db, first)).run.project_id)
    for lang in ("de", "es"):
        await service.add_language(
            db, queue, cfg, prompts, project_id, service.RunOptions(target_lang=lang)
        )
    queue.jobs.clear()
    run_id = await _upload(env, "b.txt", "Le chien.", target_lang="ES", all_languages=True)
    async with db.session() as s:
        doc_id = (await runs.get_run(s, run_id)).document_id
        langs = list(await s.scalars(select(Run.target_lang).where(Run.document_id == doc_id)))
    assert (await catalog.run_detail(db, run_id)).run.target_lang == "es"
    assert sorted(langs) == ["de", "en", "es"]
    assert [j.kwargs["run_id"] for j in queue.jobs][0] == str(run_id)
    assert len(queue.jobs) == 3


async def test_a_run_takes_the_sets_spelling_of_its_language(env):
    db, _, queue, cfg, prompts = env
    first = await _upload(env, "a.txt", "Le chat.")
    project_id = uuid.UUID((await catalog.run_detail(db, first)).run.project_id)
    await service.add_language(
        db, queue, cfg, prompts, project_id, service.RunOptions(target_lang="pt_BR")
    )
    async with db.session() as s:
        doc_id = (await runs.get_run(s, first)).document_id
    again = await service.submit_existing(
        db, queue, cfg, prompts, doc_id, service.RunOptions(target_lang="PT-br")
    )
    uploaded = await _upload(env, "b.txt", "Le chien.", target_lang="EN")
    assert (await catalog.run_detail(db, again)).run.target_lang == "pt_BR"
    assert (await catalog.run_detail(db, uploaded)).run.target_lang == "en"
    info = await catalog.get_project(db, project_id)
    assert [lang.target_lang for lang in info.languages] == ["en", "pt_BR"]


async def test_an_upload_waits_for_a_language_being_added(env):
    """A language added while a file uploads reaches that file too: the upload's runs are
    decided under the project lock (CGL-24)."""
    db, _, _, cfg, prompts = env
    first = await _upload(env, "a.txt", "Le chat.")
    project_id = uuid.UUID((await catalog.run_detail(db, first)).run.project_id)
    async with db.session() as s:
        doc_id = (await runs.get_run(s, first)).document_id
    async with db.session() as s, s.begin():  # a language being added, not yet committed
        await runs.lock_project(s, project_id)
        await runs.create_run(
            s,
            doc_id,
            target_lang="de",
            config_hash="h",
            config_snapshot={},
            prompt_versions={},
        )
        upload = asyncio.create_task(_upload(env, "b.txt", "Le chien.", all_languages=True))
        await asyncio.sleep(0.3)
        assert not upload.done()
    run_id = await upload
    async with db.session() as s:
        new_doc = (await runs.get_run(s, run_id)).document_id
        langs = list(await s.scalars(select(Run.target_lang).where(Run.document_id == new_doc)))
    assert sorted(langs) == ["de", "en"]


async def test_add_language_rolls_back_when_a_run_cannot_be_made(env, monkeypatch):
    """One transaction: a failure on the second file leaves no run for the first, and nothing is
    queued (CGL-30)."""
    db, _, queue, cfg, prompts = env
    first = await _upload(env, "a.txt", "Le chat.")
    await _upload(env, "b.txt", "Le chien.")
    project_id = uuid.UUID((await catalog.run_detail(db, first)).run.project_id)
    queue.jobs.clear()
    made = 0
    real = service._new_run

    async def fail_second(*args, **kwargs):
        nonlocal made
        made += 1
        if made == 2:
            raise RuntimeError("the database went away")
        return await real(*args, **kwargs)

    monkeypatch.setattr(service, "_new_run", fail_second)
    with pytest.raises(RuntimeError, match="went away"):
        await service.add_language(
            db, queue, cfg, prompts, project_id, service.RunOptions(target_lang="es")
        )
    assert made == 2
    async with db.session() as s:
        langs = list(await s.scalars(select(Run.target_lang)))
    assert sorted(langs) == ["en", "en"]
    assert queue.jobs == []
    info = await catalog.get_project(db, project_id)
    assert [lang.target_lang for lang in info.languages] == ["en"]
    monkeypatch.setattr(service, "_new_run", real)  # nothing half-made is left in the way
    spanish = service.RunOptions(target_lang="es")
    assert len(await service.add_language(db, queue, cfg, prompts, project_id, spanish)) == 2


async def test_approve_a_second_languages_checkpoint(env):
    """A checkpoint in a set's second language is measured against that language's guide only:
    the first language's approved versions neither block it nor share its numbers (CGL-30)."""
    db, _, queue, cfg, prompts = env
    first = await _upload(env, "a.txt", "Le chat.")
    project_id = uuid.UUID((await catalog.run_detail(db, first)).run.project_id)
    async with db.session() as s, s.begin():
        for _ in range(3):  # the first language has moved on to version 3
            await runs.add_guide_version(
                s,
                project_id,
                Guide(summary="en", style_guide="g"),
                target_lang="en",
                status=runs.APPROVED,
                author=runs.USER,
            )
    [spanish] = await service.add_language(
        db, queue, cfg, prompts, project_id, service.RunOptions(target_lang="es")
    )
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, spanish, lock=True)
        draft = await runs.add_guide_version(
            s,
            project_id,
            Guide(summary="es", style_guide="g"),
            target_lang="es",
            status=runs.DRAFT,
            author=runs.ENGINE,
            source_run_id=spanish,
        )
        run.guide_version_id = draft.id
        run.state = RunState.AWAITING_GUIDE_APPROVAL
    queue.jobs.clear()
    assert draft.version == 1
    assert await service.approve_guide(db, queue, spanish) == 2
    async with db.session() as s:
        run = await runs.get_run(s, spanish)
        approved = await runs.get_guide_version(s, run.guide_version_id)
        english = await runs.latest_guide(s, project_id, "en", approved_only=True)
    assert run.state == RunState.TRANSLATING
    assert (approved.target_lang, approved.version, approved.status) == ("es", 2, "approved")
    assert Guide.model_validate(approved.content).summary == "es"
    assert (english.version, Guide.model_validate(english.content).summary) == (3, "en")
    assert [j.kwargs["run_id"] for j in queue.jobs] == [str(spanish)]


async def test_build_report_without_llm_calls_reads_usage_only(env, tmp_path, monkeypatch):
    """CGL-63: a download that drops the model calls must not load their request/response."""
    db = env[0]
    run_id = await _complete_run(env, tmp_path)

    def call(role, tokens_in, tokens_out):
        return LLMCallRecord(
            role=role,
            backend="fake",
            model="m",
            prompt_id="p",
            prompt_version=1,
            attempt=1,
            ok=tokens_in is not None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    # More roles, interleaved, and calls with no token counts (failed or unmetered).
    async with db.session() as s, s.begin():
        await runs.save_llm_calls(
            s,
            run_id,
            [call("translator:a", 5, None), call("reviewer", None, None), call("judge", 7, 2)],
            stage="sweep",
        )
    full = await service.build_report(db, run_id)
    assert full.usage.calls == 4 and len(full.llm_calls) == 4
    assert (full.usage.tokens_in, full.usage.tokens_out) == (15, 6)
    assert full.usage.by_role["reviewer"] == {"calls": 2, "tokens_in": 3, "tokens_out": 4}

    async def boom(*_a, **_k):
        raise AssertionError("load_llm_calls must not run when calls are dropped")

    monkeypatch.setattr(runs, "load_llm_calls", boom)
    lean = await service.build_report(db, run_id, include_llm_calls=False)
    assert lean.llm_calls == []
    assert lean.usage == full.usage
    assert list(lean.usage.by_role) == list(full.usage.by_role)
    assert lean.usage.model_dump_json() == full.usage.model_dump_json()


async def test_load_llm_usage_selects_no_payload_columns(env, tmp_path):
    db = env[0]
    run_id = await _complete_run(env, tmp_path)
    statements: list[str] = []

    @event.listens_for(db.engine.sync_engine, "before_cursor_execute")
    def _grab(_c, _cur, statement, *_rest):
        statements.append(statement)

    try:
        async with db.session() as s:
            rows = await runs.load_llm_usage(s, run_id)
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", _grab)
    assert len(rows) == 1
    sql = " ".join(statements).lower()
    assert "llm_call" in sql and "request" not in sql and "response" not in sql
