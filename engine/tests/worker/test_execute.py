import asyncio
import io
import json

import docx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from coglossa import catalog, review, service
from coglossa.config import config_hash
from coglossa.export.docx import clean_docx
from coglossa.ingest.base import UNREAD_PREFIX
from coglossa.llm.types import OutputTruncatedError
from coglossa.problems import GUIDE_CHANGED_AGAIN
from coglossa.schema import (
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    Guide,
    KeepReason,
    PipelineProfile,
    RevisionSource,
    RunState,
    RunStatus,
    ScorerHealth,
)
from coglossa.scoring.tiers import LanguageTiers
from coglossa.store import outputs, runs
from coglossa.store.models import Document
from coglossa.worker import execute
from tests.ingest.docx_parts import add_chart
from tests.worker.conftest import drain, make_services, submit, tasks_sent

LONG_TEXT = "\n\n".join(" ".join(["Le chat dort."] * 45) for _ in range(4))


async def _state(svc, run_id):
    async with svc.db.session() as s:
        return RunState((await runs.get_run(s, run_id)).state)


async def test_a_run_goes_from_created_to_complete(services, tmp_path, fake_backend):
    services.scorer_health = ScorerHealth(device="cpu")
    run_id = await submit(services, tmp_path)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE
    report = await service.build_report(services.db, run_id)
    assert report.status is RunStatus.COMPLETE
    assert [s.text for s in report.spans] == ["[en] Le chat dort.", "[en] Il pleut."]
    assert report.config.scorer == ScorerHealth(device="cpu")
    assert report.usage.calls == len(fake_backend.requests)
    async with services.db.session() as s:
        stages = {(r.stage, r.chunk_id): r for r in await runs.stage_rows(s, run_id)}
        run = await runs.get_run(s, run_id)
    for key in [
        ("ingest", ""),
        ("profile", ""),
        ("guide", ""),
        ("translate", "c1"),
        ("reconcile", "c1"),
        ("score", "c1"),
        ("sweep", ""),
    ]:
        assert stages[key].status == runs.COMPLETE, key
        assert stages[key].duration_ms is not None
    assert stages[("sweep", "")].output == {"errors": [], "skipped": True}  # short profile
    assert run.started_at is not None and run.finished_at is not None
    assert ("config", "") not in stages


async def test_a_guide_that_cannot_be_built_does_not_wait_for_approval(
    services, tmp_path, fake_backend
):
    fake_backend.script("guide", *[OutputTruncatedError("cut")] * 3)
    run_id = await submit(services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE_WITH_ERRORS
    report = await service.build_report(services.db, run_id)
    assert any(e.message.endswith("; translating without one") for e in report.errors)


async def test_checkpoint_waits_for_approval(services, tmp_path, fake_backend):
    run_id = await submit(services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM)
    await drain(services)
    assert await _state(services, run_id) is RunState.AWAITING_GUIDE_APPROVAL
    assert "translate" not in tasks_sent(fake_backend)
    guide = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    await service.approve_guide(services.db, services.queue, run_id, guide)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE
    report = await service.build_report(services.db, run_id)
    assert report.guide == guide
    # long-form sweeps: the locked term is enforced and its flag resolved
    (rev,) = report.revisions
    assert rev.source is RevisionSource.SWEEP and "cat" in report.spans[0].text
    statuses = [f.status for f in report.spans[0].flags if f.code is FlagCode.TERMINOLOGY_VIOLATION]
    assert statuses == [FlagStatus.RESOLVED]


async def test_project_guide_is_reused_without_a_guide_call(services, tmp_path, fake_backend):
    first = await submit(services, tmp_path, checkpoint=True)
    await drain(services)
    await service.approve_guide(services.db, services.queue, first)
    await drain(services)
    fake_backend.requests.clear()
    second = await submit(services, tmp_path, use_project_guide=True)
    await drain(services)
    assert await _state(services, second) is RunState.COMPLETE
    assert "guide" not in tasks_sent(fake_backend)


async def test_project_guide_reuse_takes_the_set_guide_even_a_newer_draft(
    services, tmp_path, fake_backend
):
    """CGL-28: --use-project-guide reuses the set guide the other files use, the newest usable
    version, even when that is a draft newer than the latest approved one."""
    first = await submit(services, tmp_path, checkpoint=True)
    await drain(services)
    await service.approve_guide(services.db, services.queue, first)
    await drain(services)
    second = await submit(services, tmp_path, name="two.txt")
    await drain(services)
    async with services.db.session() as s:
        draft = await runs.get_guide_version(s, (await runs.get_run(s, second)).guide_version_id)
        approved = await runs.latest_guide(s, draft.project_id, "en", approved_only=True)
    assert draft.status == runs.DRAFT and draft.version > approved.version
    fake_backend.requests.clear()
    third = await submit(services, tmp_path, name="three.txt", use_project_guide=True)
    await drain(services)
    assert "guide" not in tasks_sent(fake_backend)
    async with services.db.session() as s:
        assert (await runs.get_run(s, third)).guide_version_id == draft.id


async def test_profiling_failure_fails_the_run_and_retry_recovers(services, tmp_path, fake_backend):
    fake_backend.script("profile", "not json", "not json")
    run_id = await submit(services, tmp_path)
    await drain(services)
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
        profile_row = await runs.get_stage(s, run_id, "profile")
    assert run.state == RunState.FAILED and run.error.startswith("profiling failed")
    assert profile_row.status == runs.FAILED
    assert (
        await service.retry_run(services.db, services.queue, run_id)
        == "restarting from the failed stage"
    )
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_unreadable_document_fails_the_run(services, tmp_path):
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff\xfe\x00broken")
    opts = service.SubmitOptions(project="p", target_lang="en")
    run_id = await service.submit_run(
        services.db, services.storage, services.queue, services.cfg, services.prompts, path, opts
    )
    await drain(services)
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
    assert run.state == RunState.FAILED and "bad.txt" in run.error


@pytest.mark.parametrize("filename", ["رواية.pdf", "café notes.pdf"])
async def test_ingest_failures_name_the_uploaded_file(services, filename):
    from coglossa.testing.pdf import make_pdf

    opts = service.RunOptions(target_lang="en")
    run_id = await service.submit_bytes(
        services.db,
        services.storage,
        services.queue,
        services.cfg,
        services.prompts,
        filename,
        make_pdf([["Du texte."], "image"]),
        opts,
        project="p",
    )
    await drain(services)
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
    assert run.state == RunState.FAILED
    assert run.error.startswith(f"{filename} needs OCR (needs_ocr): page(s) 2 have no text layer")


async def test_unexpected_stage_error_is_an_internal_error(services, tmp_path, monkeypatch):
    def boom(_):
        raise RuntimeError("segmenter exploded")

    monkeypatch.setattr(execute, "segment_document", boom)
    run_id = await submit(services, tmp_path)
    await drain(services)
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
    assert run.state == RunState.FAILED
    assert run.error == "internal error: RuntimeError: segmenter exploded"


async def test_rerunning_a_finished_chunk_does_nothing(services, tmp_path, fake_backend):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))  # start_run → queues c1
    job = services.queue.jobs[0]
    await execute.process_chunk_job(services, run_id, "c1")
    calls = len(fake_backend.requests)
    async with services.db.session() as s, s.begin():
        (await runs.get_run(s, run_id, lock=True)).state = RunState.TRANSLATING  # pretend in flight
    await execute.process_chunk_job(services, run_id, job.kwargs["chunk_id"])
    assert len(fake_backend.requests) == calls
    async with services.db.session() as s:
        assert len((await outputs.load_spans(s, run_id)).spans) == 2


async def test_maybe_finish_counts_chunks_without_loading_them(services, tmp_path, monkeypatch):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))
    real, calls = runs.chunk_count, []

    async def counting(*args):
        calls.append(args)
        return await real(*args)

    async def no_load(*args, **kwargs):
        raise AssertionError("_maybe_finish must not load chunk rows")

    monkeypatch.setattr(runs, "chunk_count", counting)
    monkeypatch.setattr(runs, "load_chunks", no_load)
    await execute._maybe_finish(services, run_id)  # c1 is not finished yet
    assert len(calls) == 1 and await _state(services, run_id) is RunState.TRANSLATING
    monkeypatch.undo()
    await execute.process_chunk_job(services, run_id, "c1")
    await drain(services)
    async with services.db.session() as s, s.begin():
        (await runs.get_run(s, run_id, lock=True)).state = RunState.TRANSLATING
    calls.clear()
    monkeypatch.setattr(runs, "chunk_count", counting)
    monkeypatch.setattr(runs, "load_chunks", no_load)
    await execute._maybe_finish(services, run_id)  # every chunk is finished
    assert len(calls) == 1 and await _state(services, run_id) is RunState.SWEEPING


async def test_a_chunk_interrupted_mid_flight_restarts_cleanly(services, tmp_path, monkeypatch):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))
    real = execute.process_chunk

    async def killed(*args, **kwargs):
        await kwargs["on_stage"]("translate")
        raise asyncio.CancelledError  # the worker process died here

    monkeypatch.setattr(execute, "process_chunk", killed)
    with pytest.raises(asyncio.CancelledError):
        await execute.process_chunk_job(services, run_id, "c1")
    monkeypatch.setattr(execute, "process_chunk", real)
    await execute.process_chunk_job(services, run_id, "c1")  # the re-queued job
    await drain(services)
    async with services.db.session() as s:
        assert len((await outputs.load_spans(s, run_id)).spans) == 2
        assert (await runs.get_stage(s, run_id, "translate", "c1")).attempt == 2
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_parallel_chunks_queue_finish_exactly_once(db, fake_backend, tmp_path):
    svc = make_services(
        db, fake_backend, tmp_path, chunking={"target_words": 100, "max_words": 200}
    )
    run_id = await submit(svc, tmp_path, text=LONG_TEXT)
    await execute.run_job(svc, *_pop(svc))
    chunk_jobs = list(svc.queue.jobs)
    svc.queue.jobs.clear()
    assert len(chunk_jobs) >= 2
    await asyncio.gather(*(execute.run_job(svc, j.task, j.kwargs) for j in chunk_jobs))
    finishes = [j for j in svc.queue.attempts if j.task == service.TASK_FINISH_RUN]
    assert len(finishes) == 1
    await drain(svc)
    assert await _state(svc, run_id) is RunState.COMPLETE


async def test_cancel_stops_new_work_and_keeps_results(
    services, tmp_path, fake_backend, monkeypatch
):
    svc = services
    run_id = await submit(svc, tmp_path)
    await execute.run_job(svc, *_pop(svc))
    real = execute.process_chunk

    async def cancel_mid_flight(*args, **kwargs):
        await service.cancel_run(svc.db, run_id)  # arrives while the chunk is running
        return await real(*args, **kwargs)

    monkeypatch.setattr(execute, "process_chunk", cancel_mid_flight)
    await drain(svc)
    assert await _state(svc, run_id) is RunState.CANCELLED
    async with svc.db.session() as s:
        assert len((await outputs.load_spans(s, run_id)).spans) == 2  # kept
    await execute.finish_run(svc, run_id)  # a late job never revives the run
    await execute.translate_run(svc, run_id)
    assert await _state(svc, run_id) is RunState.CANCELLED


async def test_cancel_before_any_chunk_runs(services, tmp_path, fake_backend):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))
    before = len(fake_backend.requests)
    await service.cancel_run(services.db, run_id)
    await drain(services)
    assert len(fake_backend.requests) == before
    assert await _state(services, run_id) is RunState.CANCELLED


async def test_a_run_cancelled_before_it_starts_never_runs(services, tmp_path, fake_backend):
    run_id = await submit(services, tmp_path)
    assert await service.cancel_run(services.db, run_id) is RunState.CANCELLED
    assert await drain(services) == 1
    assert fake_backend.requests == []
    assert await _state(services, run_id) is RunState.CANCELLED


async def test_cancel_during_profiling_stops_before_the_guide(
    services, tmp_path, fake_backend, monkeypatch
):
    real = execute.run_profile

    async def cancelled_meanwhile(*args, **kwargs):
        await service.cancel_run(services.db, run_id)
        return await real(*args, **kwargs)

    monkeypatch.setattr(execute, "run_profile", cancelled_meanwhile)
    run_id = await submit(services, tmp_path)
    await drain(services)
    assert await _state(services, run_id) is RunState.CANCELLED
    assert "guide" not in tasks_sent(fake_backend)
    async with services.db.session() as s:
        assert (await runs.get_stage(s, run_id, "profile")).status == runs.COMPLETE  # kept
        assert await runs.get_stage(s, run_id, "guide") is None


async def test_config_change_is_recorded(db, fake_backend, tmp_path):
    submitter = make_services(db, fake_backend, tmp_path)
    run_id = await submit(submitter, tmp_path)
    worker = make_services(
        db, fake_backend, tmp_path, retry={"max_attempts": 2, "base_delay_s": 0, "max_delay_s": 0}
    )
    worker.queue = submitter.queue
    await drain(worker)
    report = await service.build_report(db, run_id)
    (error,) = [e for e in report.errors if e.stage == "config"]
    assert "differs from the one this run was submitted with" in error.message
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    async with db.session() as s:
        assert report.config.hash == (await runs.get_run(s, run_id)).config_hash
    assert config_hash(submitter.cfg) != config_hash(worker.cfg)
    assert config_hash(submitter.cfg) in error.message and config_hash(worker.cfg) in error.message


def _tiers_services(db, fake_backend, tmp_path, tiers):
    svc = make_services(db, fake_backend, tmp_path, scoring={"language_tiers_file": tiers})
    svc.tiers = LanguageTiers.load(tiers)
    return svc


async def _config_errors(db, run_id):
    report = await service.build_report(db, run_id)
    return [e for e in report.errors if e.stage == "config"]


async def test_an_edited_tiers_file_is_a_config_change(db, fake_backend, tmp_path):
    tiers = tmp_path / "tiers.yaml"
    tiers.write_text("default: C\n", encoding="utf-8")
    submitter = _tiers_services(db, fake_backend, tmp_path, tiers)
    run_id = await submit(submitter, tmp_path)
    tiers.write_text("default: B\n", encoding="utf-8")  # same path, new content, then applied
    worker = _tiers_services(db, fake_backend, tmp_path, tiers)
    worker.queue = submitter.queue
    await drain(worker)
    assert len(await _config_errors(db, run_id)) == 1


async def test_a_tiers_edit_the_worker_has_not_applied_is_a_config_change(
    db, fake_backend, tmp_path
):
    tiers = tmp_path / "tiers.yaml"
    tiers.write_text("default: C\n", encoding="utf-8")
    worker = _tiers_services(db, fake_backend, tmp_path, tiers)  # scores with tier C
    tiers.write_text("default: B\n", encoding="utf-8")
    run_id = await submit(worker, tmp_path)  # submitted under the edited file
    await drain(worker)
    (error,) = await _config_errors(db, run_id)
    assert worker.cfg_hash in error.message


async def test_an_unchanged_tiers_file_is_not_a_config_change(db, fake_backend, tmp_path):
    tiers = tmp_path / "tiers.yaml"
    tiers.write_text("default: C\n", encoding="utf-8")
    submitter = _tiers_services(db, fake_backend, tmp_path, tiers)
    run_id = await submit(submitter, tmp_path)
    worker = _tiers_services(db, fake_backend, tmp_path, tiers)
    worker.queue = submitter.queue
    await drain(worker)
    assert await _config_errors(db, run_id) == []


async def test_an_unexpected_chunk_error_fails_the_chunk_then_retry_recovers(
    services, tmp_path, monkeypatch
):
    run_id = await submit(services, tmp_path)
    real = execute.process_chunk
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bug")
        return await real(*args, **kwargs)

    monkeypatch.setattr(execute, "process_chunk", flaky)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE_WITH_ERRORS
    report = await service.build_report(services.db, run_id)
    assert [e.message for e in report.errors] == ["internal error: RuntimeError: bug"]
    assert await service.retry_run(services.db, services.queue, run_id) == "retrying 1 chunk(s)"
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_a_chunk_retry_of_a_finished_run_cannot_be_cancelled(services, tmp_path, monkeypatch):
    run_id = await submit(services, tmp_path)
    real = execute.process_chunk
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bug")
        return await real(*args, **kwargs)

    monkeypatch.setattr(execute, "process_chunk", flaky)
    await drain(services)
    async with services.db.session() as s:
        first_finish = (await runs.get_run(s, run_id)).finished_at
    assert await service.retry_run(services.db, services.queue, run_id) == "retrying 1 chunk(s)"
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
    assert run.state == RunState.TRANSLATING and run.finished_at == first_finish
    with pytest.raises(
        service.InvalidRunStateError,
        match="reworking a finished translation and cannot be cancelled; it finishes on its own",
    ):
        await service.cancel_run(services.db, run_id)
    await drain(services)
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
    assert run.state == RunState.COMPLETE and run.cancel_requested is False
    assert run.finished_at > first_finish  # finish_run stamps the new end
    assert (await service.build_report(services.db, run_id)).status is RunStatus.COMPLETE


async def test_database_errors_propagate_for_queue_retry(services, tmp_path, monkeypatch):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))

    async def db_down(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(execute, "process_chunk", db_down)
    with pytest.raises(OperationalError):
        await execute.process_chunk_job(services, run_id, "c1")


async def test_database_errors_in_run_stages_and_the_sweep_propagate(
    db, fake_backend, tmp_path, monkeypatch
):
    svc = make_services(db, fake_backend, tmp_path, profiles={"sweep": {"short": True}})

    def db_down(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    run_id = await submit(svc, tmp_path)
    with monkeypatch.context() as m:
        m.setattr(execute, "segment_document", db_down)
        with pytest.raises(OperationalError):
            await execute.start_run(svc, run_id)
    await execute.start_run(svc, run_id)  # the queue's retry
    with monkeypatch.context() as m:
        m.setattr(execute, "run_sweep", db_down)
        with pytest.raises(OperationalError):
            await drain(svc)
    await execute.finish_run(svc, run_id)
    assert await _state(svc, run_id) is RunState.COMPLETE


async def test_jobs_for_a_deleted_project_are_skipped(services, tmp_path):
    await submit(services, tmp_path)
    await service.delete_project(services.db, services.storage, "novel")
    assert await drain(services) == 1  # start_run ran and found nothing


async def test_unknown_job_name(services):
    with pytest.raises(ValueError, match="unknown job"):
        await execute.run_job(
            services, "coglossa.nope", {"run_id": "00000000-0000-0000-0000-000000000000"}
        )


def _pop(svc):
    job = svc.queue.pop()
    return job.task, job.kwargs


async def test_an_unexpected_chunk_error_keeps_its_passages_in_the_report(
    services, tmp_path, monkeypatch
):
    run_id = await submit(services, tmp_path)

    async def broken(*args, **kwargs):
        raise RuntimeError("reconciler exploded")

    monkeypatch.setattr(execute, "process_chunk", broken)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE_WITH_ERRORS
    async with services.db.session() as s:
        score = await runs.get_stage(s, run_id, "score", "c1")
        (chunk,) = await runs.load_chunks(s, run_id)
        stored = await outputs.load_spans(s, run_id)
    assert score.status == runs.FAILED
    assert score.error == "internal error: RuntimeError: reconciler exploded"
    assert [(sp.start, sp.end) for sp in stored.spans] == [(i, i) for i in chunk.segment_ids]
    for span in stored.spans:
        assert span.text == "" and [f.code for f in span.flags] == [FlagCode.ENGINE_ERROR]
        assert span.flags[0].evidence["error"] == score.error
    report = await service.build_report(services.db, run_id)
    assert [e.message for e in report.errors] == [score.error]


async def test_a_chunk_that_fails_before_its_input_is_built_saves_no_passages(
    services, tmp_path, monkeypatch
):
    run_id = await submit(services, tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("bad chunk table")

    monkeypatch.setattr(execute, "build_chunk_inputs", broken)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE_WITH_ERRORS
    async with services.db.session() as s:
        score = await runs.get_stage(s, run_id, "score", "c1")
        assert (await outputs.load_spans(s, run_id)).spans == []
    assert score.status == runs.FAILED
    assert score.error == "internal error: RuntimeError: bad chunk table"


async def test_an_unexpected_sweep_error_completes_with_errors(
    db, fake_backend, tmp_path, monkeypatch
):
    svc = make_services(db, fake_backend, tmp_path, profiles={"sweep": {"short": True}})

    async def broken(*args, **kwargs):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(execute, "run_sweep", broken)
    run_id = await submit(svc, tmp_path)
    await drain(svc)
    assert await _state(svc, run_id) is RunState.COMPLETE_WITH_ERRORS
    report = await service.build_report(db, run_id)
    assert [(e.stage, e.message) for e in report.errors] == [
        ("sweep", "internal error: RuntimeError: sweep exploded")
    ]
    assert [s.text for s in report.spans] == ["[en] Le chat dort.", "[en] Il pleut."]
    async with db.session() as s:
        sweep = await runs.get_stage(s, run_id, "sweep")
    assert sweep.status == runs.COMPLETE and sweep.output["revised"] == 0
    monkeypatch.undo()
    assert await service.retry_run(db, svc.queue, run_id) == "re-running the sweep"
    await drain(svc)
    assert await _state(svc, run_id) is RunState.COMPLETE


async def test_a_late_duplicate_delivery_does_not_overwrite_the_saved_chunk(
    services, tmp_path, monkeypatch, fake_backend
):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))  # start_run → queues c1
    real = execute.process_chunk
    first: dict = {}

    async def overtaken(*args, **kwargs):
        outcome = await real(*args, **kwargs)
        if not first:  # a re-delivered copy of this job finishes the chunk first
            first["outcome"] = outcome
            await execute.process_chunk_job(services, run_id, "c1")
            async with services.db.session() as s:
                first["rows"] = (await outputs.load_spans(s, run_id)).row_ids
        return outcome

    monkeypatch.setattr(execute, "process_chunk", overtaken)
    await execute.process_chunk_job(services, run_id, "c1")
    async with services.db.session() as s:
        assert (await outputs.load_spans(s, run_id)).row_ids == first["rows"]
    assert [j.task for j in services.queue.attempts].count(service.TASK_FINISH_RUN) == 1
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE
    report = await service.build_report(services.db, run_id)
    assert report.usage.calls == len(fake_backend.requests)  # the discarded copy's calls too


async def test_no_database_connection_is_held_during_a_model_call(
    services, tmp_path, fake_backend, monkeypatch
):
    held: list[tuple[str, int]] = []
    real_chat = fake_backend.chat

    async def watched(request):
        response = await real_chat(request)
        held.append((tasks_sent(fake_backend)[-1], services.db.engine.pool.checkedout()))
        return response

    monkeypatch.setattr(fake_backend, "chat", watched)
    run_id = await submit(services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM)
    await drain(services)
    guide = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    await service.approve_guide(services.db, services.queue, run_id, guide)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE
    assert {"profile", "guide", "translate", "sweep"} <= {task for task, _ in held}
    assert [h for h in held if h[1]] == []


@pytest.mark.parametrize("multi_chunk", [False, True], ids=["run-sweeping", "run-translating"])
async def test_a_late_copys_stage_hooks_do_not_reopen_a_finished_chunk(
    db, fake_backend, tmp_path, monkeypatch, multi_chunk
):
    svc = make_services(
        db, fake_backend, tmp_path, chunking={"target_words": 100, "max_words": 200}
    )
    run_id = await submit(
        svc, tmp_path, text=LONG_TEXT if multi_chunk else "Le chat dort. Il pleut."
    )
    await execute.run_job(svc, *_pop(svc))  # start_run → queues the chunk jobs
    svc.queue.jobs[:] = [j for j in svc.queue.jobs if j.kwargs["chunk_id"] != "c1"]
    real = execute.process_chunk
    seen: dict = {}

    async def overtaken(*args, **kwargs):
        if not seen:  # the winning copy runs to completion before this copy's hooks fire
            seen["winner"] = True
            await execute.process_chunk_job(svc, run_id, "c1")
            async with svc.db.session() as s:
                seen["rows"] = (await outputs.load_spans(s, run_id)).row_ids
                seen["state"] = RunState((await runs.get_run(s, run_id)).state)
        return await real(*args, **kwargs)

    monkeypatch.setattr(execute, "process_chunk", overtaken)
    await execute.process_chunk_job(svc, run_id, "c1")  # the late copy
    assert seen["state"] is (RunState.TRANSLATING if multi_chunk else RunState.SWEEPING)
    async with svc.db.session() as s:
        score = await runs.get_stage(s, run_id, "score", "c1")
        reconcile = await runs.get_stage(s, run_id, "reconcile", "c1")
        assert (await outputs.load_spans(s, run_id)).row_ids == seen["rows"]
    assert (score.status, score.attempt, reconcile.attempt) == (runs.COMPLETE, 1, 1)
    assert score.output is not None and score.output["chunk_id"] == "c1"
    await drain(svc)
    assert await _state(svc, run_id) is RunState.COMPLETE
    async with svc.db.session() as s:
        assert await runs.finished_chunk_count(s, run_id) == len(await runs.load_chunks(s, run_id))


async def test_a_late_copy_cannot_hide_a_failed_chunk(services, tmp_path, monkeypatch):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))
    services.queue.jobs.clear()
    real = execute.process_chunk
    calls = {"n": 0}

    async def winner_fails(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            await execute.process_chunk_job(services, run_id, "c1")  # the winner, which fails
            return await real(*args, **kwargs)  # this late copy's hooks fire afterwards
        raise RuntimeError("bug")

    monkeypatch.setattr(execute, "process_chunk", winner_fails)
    await execute.process_chunk_job(services, run_id, "c1")
    await drain(services)
    async with services.db.session() as s:
        score = await runs.get_stage(s, run_id, "score", "c1")
        assert await runs.total_error_count(s, run_id) == 1
        assert await runs.chunks_with_errors(s, run_id) == ["c1"]
    assert (score.status, score.error) == (runs.FAILED, "internal error: RuntimeError: bug")
    assert await _state(services, run_id) is RunState.COMPLETE_WITH_ERRORS


def _defer_fails_once(svc, monkeypatch, task):
    real = svc.queue.defer
    failed: list[str] = []

    async def flaky(name, **kwargs):
        if name == task and not failed:
            failed.append(name)
            raise ConnectionError("queue unreachable")
        await real(name, **kwargs)

    monkeypatch.setattr(svc.queue, "defer", flaky)
    return failed


async def test_a_lost_finish_job_is_queued_again_by_a_redelivered_chunk(
    services, tmp_path, monkeypatch
):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))
    job = services.queue.pop()
    failed = _defer_fails_once(services, monkeypatch, service.TASK_FINISH_RUN)
    with pytest.raises(ConnectionError):
        await execute.run_job(services, job.task, job.kwargs)  # sweeping committed, finish lost
    assert failed and await _state(services, run_id) is RunState.SWEEPING
    assert services.queue.jobs == []
    await execute.run_job(services, job.task, job.kwargs)  # the queue retries the chunk job
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_lost_chunk_jobs_are_queued_again_by_a_redelivered_start(
    services, tmp_path, monkeypatch
):
    run_id = await submit(services, tmp_path)
    job = services.queue.pop()
    failed = _defer_fails_once(services, monkeypatch, service.TASK_PROCESS_CHUNK)
    with pytest.raises(ConnectionError):
        await execute.run_job(services, job.task, job.kwargs)  # translating committed, c1 lost
    assert failed and await _state(services, run_id) is RunState.TRANSLATING
    assert services.queue.jobs == []
    await execute.run_job(services, job.task, job.kwargs)  # the queue retries start_run
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_a_redelivered_translate_job_requeues_a_lost_finish(services, tmp_path):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))
    await execute.run_job(services, *_pop(services))  # c1 → sweeping, finish queued
    services.queue.jobs.clear()  # the finish job was lost
    await execute.translate_run(services, run_id)
    assert [j.task for j in services.queue.jobs] == [service.TASK_FINISH_RUN]
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_a_duplicate_finish_job_applies_the_sweep_once(services, tmp_path, monkeypatch):
    run_id = await submit(services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM)
    await drain(services)
    guide = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    await service.approve_guide(services.db, services.queue, run_id, guide)
    real = execute.run_sweep
    nested: list[bool] = []

    async def overtaken(*args, **kwargs):
        result = await real(*args, **kwargs)
        if not nested:  # a duplicate finish job saves its sweep before this one does
            nested.append(True)
            await execute.finish_run(services, run_id)
        return result

    monkeypatch.setattr(execute, "run_sweep", overtaken)
    await drain(services)
    assert nested and await _state(services, run_id) is RunState.COMPLETE
    report = await service.build_report(services.db, run_id)
    assert len(report.revisions) == 1
    assert report.usage.calls == len(services.backends["fake"].requests)


# ---------------------------------------------------------------- failures while saving


async def test_a_formatted_file_keeps_its_formatting_through_the_worker(services, tmp_path):
    """CGL-108: marks are stored with the blocks, markers reach the models, and the passages'
    formatting is stored and comes back in the report and the clean Word download."""
    html = "<p>Le <b>chat</b> dort. Il <a href='https://example.org/'>pleut</a>.</p>"
    run_id = await submit(services, tmp_path, text=html, name="page.html")
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE
    report = await service.build_report(services.db, run_id)
    assert [m.kind.value for m in report.blocks[0].provenance.marks] == ["bold", "link"]
    assert [s.text for s in report.spans] == ["[en] Le chat dort.", "[en] Il pleut."]
    first, second = report.spans
    assert first.formatting is not None and second.formatting is not None
    assert [first.text[m.start : m.end] for m in first.formatting.marks] == ["chat"]
    document = docx.Document(io.BytesIO(clean_docx(report)))
    bold = [r.text for p in document.paragraphs for r in p.runs if r.bold]
    assert bold == ["chat"]


async def test_code_and_translate_no_pass_through_the_worker_unseen(services, tmp_path):
    """CGL-111: text left as written is stored with its blocks, never reaches a model in any
    stage (profile, guide, chunks, sweep), and comes back as written, marked, in the report, the
    review screen and the clean Word download."""
    html = (
        "<p>Le chat dort.</p><pre>  x = 1\n  y = 2</pre><p translate='no'>ACME Box</p>"
        "<p>Il pleut.</p>"
    )
    run_id = await submit(services, tmp_path, text=html, name="page.html")
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE
    sent = "\n".join(m.content for r in services.backends["fake"].requests for m in r.messages)
    assert "x = 1" not in sent and "ACME" not in sent and "Le chat dort." in sent
    report = await service.build_report(services.db, run_id)
    assert [b.provenance.keep for b in report.blocks] == [None, "code", "no_translate", None]
    assert [(s.text, s.kept) for s in report.spans] == [
        ("[en] Le chat dort.", None),
        ("  x = 1\n  y = 2", KeepReason.CODE),
        ("ACME Box", KeepReason.NO_TRANSLATE),
        ("[en] Il pleut.", None),
    ]
    page = await review.span_page(services.db, run_id)
    assert [s.kept for s in page.spans] == [None, KeepReason.CODE, KeepReason.NO_TRANSLATE, None]
    document = docx.Document(io.BytesIO(clean_docx(report)))
    assert "  x = 1\n  y = 2" in [p.text for p in document.paragraphs]


async def test_a_document_with_nul_characters_completes(services, tmp_path):
    run_id = await submit(services, tmp_path, text="Le chat\x00 dort. Il pleut.")
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE
    report = await service.build_report(services.db, run_id)
    assert [b.text for b in report.blocks] == ["Le chat dort. Il pleut."]
    assert all("\x00" not in s.text for s in report.segments)


async def test_a_model_reply_with_nul_characters_completes(
    services, tmp_path, fake_backend, monkeypatch
):
    real_chat = fake_backend.chat

    async def nul_reconcile(request):
        response = await real_chat(request)
        if tasks_sent(fake_backend)[-1] == "reconcile":
            payload = json.loads(response.content)
            payload["spans"][0]["text"] = "[en] Le chat\x00 dort."
            response = response.model_copy(update={"content": json.dumps(payload)})
        return response

    monkeypatch.setattr(fake_backend, "chat", nul_reconcile)
    run_id = await submit(services, tmp_path)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE
    report = await service.build_report(services.db, run_id)
    assert [s.text for s in report.spans] == ["[en] Le chat dort.", "[en] Il pleut."]


async def test_an_unexpected_error_saving_a_run_stage_fails_the_run(
    services, tmp_path, monkeypatch
):
    async def broken(*args, **kwargs):
        raise RuntimeError("cannot save chunks")

    monkeypatch.setattr(runs, "save_chunks", broken)
    run_id = await submit(services, tmp_path)
    await drain(services)
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
        ingest = await runs.get_stage(s, run_id, "ingest")
    assert run.state == RunState.FAILED
    assert run.error == "internal error: RuntimeError: cannot save chunks"
    assert (ingest.status, ingest.error) == (runs.FAILED, run.error)
    monkeypatch.undo()
    await service.retry_run(services.db, services.queue, run_id)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_an_unexpected_error_saving_a_chunk_fails_the_chunk(
    services, tmp_path, monkeypatch, fake_backend
):
    real = outputs.save_chunk_outcome
    attempts: list[str] = []

    async def fails_once(s, run_id, ordinal, outcome):
        attempts.append(outcome.spans[0].text)
        if len(attempts) == 1:
            raise RuntimeError("cannot save outcome")
        await real(s, run_id, ordinal, outcome)

    monkeypatch.setattr(outputs, "save_chunk_outcome", fails_once)
    run_id = await submit(services, tmp_path)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE_WITH_ERRORS
    async with services.db.session() as s:
        score = await runs.get_stage(s, run_id, "score", "c1")
        stored = await outputs.load_spans(s, run_id)
    assert (score.status, score.error) == (
        runs.FAILED,
        "internal error: RuntimeError: cannot save outcome",
    )
    assert attempts == ["[en] Le chat dort.", ""]  # then the engine_error passages were saved
    assert [f.code for sp in stored.spans for f in sp.flags] == [FlagCode.ENGINE_ERROR] * 2
    report = await service.build_report(services.db, run_id)
    assert report.usage.calls == len(fake_backend.requests)  # the call log was kept


async def test_a_chunk_whose_failure_cannot_be_saved_in_full_still_fails(
    services, tmp_path, monkeypatch
):
    async def broken(*args, **kwargs):
        raise RuntimeError("cannot save outcome")

    monkeypatch.setattr(outputs, "save_chunk_outcome", broken)
    run_id = await submit(services, tmp_path)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE_WITH_ERRORS
    async with services.db.session() as s:
        score = await runs.get_stage(s, run_id, "score", "c1")
        assert (await outputs.load_spans(s, run_id)).spans == []
    assert score.status == runs.FAILED
    monkeypatch.undo()
    assert await service.retry_run(services.db, services.queue, run_id) == "retrying 1 chunk(s)"
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_a_stage_failure_whose_call_log_cannot_be_saved_still_fails_the_run(
    services, tmp_path, monkeypatch
):
    real = runs.save_llm_calls

    async def no_profile_calls(s, run_id, records, **kwargs):
        if kwargs.get("stage") == "profile" and records:
            raise RuntimeError("cannot save calls")
        await real(s, run_id, records, **kwargs)

    monkeypatch.setattr(runs, "save_llm_calls", no_profile_calls)
    run_id = await submit(services, tmp_path)
    await drain(services)
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
    assert run.state == RunState.FAILED
    assert run.error == "internal error: RuntimeError: cannot save calls"


async def test_an_unexpected_error_saving_the_sweep_completes_with_errors(
    services, tmp_path, monkeypatch
):
    async def broken(*args, **kwargs):
        raise RuntimeError("cannot apply sweep")

    run_id = await submit(services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM)
    await drain(services)
    guide = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    await service.approve_guide(services.db, services.queue, run_id, guide)
    monkeypatch.setattr(outputs, "apply_sweep", broken)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE_WITH_ERRORS
    report = await service.build_report(services.db, run_id)
    assert [(e.stage, e.message) for e in report.errors] == [
        ("sweep", "internal error: RuntimeError: cannot apply sweep")
    ]
    assert report.revisions == [] and "cat" not in report.spans[0].text
    assert report.usage.calls == len(services.backends["fake"].requests)
    monkeypatch.undo()
    assert await service.retry_run(services.db, services.queue, run_id) == "re-running the sweep"
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE


# ---------------------------------------------------------------- the project guide


def _capture_priors(monkeypatch) -> list:
    real = execute.run_guide
    priors: list = []

    async def capturing(*args, **kwargs):
        priors.append(kwargs["prior"])
        return await real(*args, **kwargs)

    monkeypatch.setattr(execute, "run_guide", capturing)
    return priors


async def test_documents_in_a_project_share_the_guide_without_a_checkpoint(
    services, tmp_path, monkeypatch
):
    priors = _capture_priors(monkeypatch)
    first = await submit(services, tmp_path, name="one.txt")
    await drain(services)
    assert await _state(services, first) is RunState.COMPLETE
    second = await submit(services, tmp_path, text="Il neige. Le chien court.", name="two.txt")
    await drain(services)
    assert await _state(services, second) is RunState.COMPLETE
    first_guide = await service.run_guide(services.db, first)
    assert priors == [None, first_guide]


def _guide_reply(rendering: str) -> str:
    return json.dumps(
        {
            "summary": "s",
            "style_guide": "g",
            "glossary": [{"term": "chat", "rendering": rendering}],
            "entities": [],
            "cultural_references": [],
        }
    )


async def test_files_translated_together_agree_on_names(
    services, tmp_path, fake_backend, monkeypatch
):
    """Two files of one set build their guides at the same moment and pick different
    renderings of "chat"; both translate with the one saved first (CGL-23)."""
    fake_backend.script("guide", _guide_reply("cat"), _guide_reply("kitty"))
    one = await submit(services, tmp_path, name="one.txt")
    two = await submit(services, tmp_path, text="Le chat mange. Il neige.", name="two.txt")
    starts = [services.queue.pop(), services.queue.pop()]
    real = execute.run_guide
    both_built = asyncio.Barrier(2)

    async def together(*args, **kwargs):
        built = await real(*args, **kwargs)
        await both_built.wait()  # neither saves before both have built from no set guide
        return built

    monkeypatch.setattr(execute, "run_guide", together)
    async with asyncio.timeout(30):  # a stage that fails before the barrier must not hang
        await asyncio.gather(*(execute.run_job(services, j.task, j.kwargs) for j in starts))
    monkeypatch.undo()
    await drain(services)
    assert await _state(services, one) is RunState.COMPLETE
    assert await _state(services, two) is RunState.COMPLETE
    guides = [await service.run_guide(services.db, r) for r in (one, two)]
    renderings = {g.rendering for guide in guides for g in guide.glossary if g.term == "chat"}
    assert len(renderings) == 1
    sent = [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1]) for r in fake_backend.requests
    ]
    used = {
        g["rendering"]
        for p in sent
        if p["task"] == "translate"
        for g in p["guide"]["glossary"]
        if g["term"] == "chat"
    }
    assert used == renderings


async def test_a_newer_draft_is_the_project_guide_and_keeps_the_approved_entries(
    services, tmp_path, monkeypatch
):
    approved = Guide(
        summary="approved",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    first = await submit(services, tmp_path, name="one.txt", checkpoint=True)
    await drain(services)
    await service.approve_guide(services.db, services.queue, first, approved)
    await drain(services)
    second = await submit(services, tmp_path, name="two.txt")  # its draft is newer
    await drain(services)
    priors = _capture_priors(monkeypatch)
    await submit(services, tmp_path, name="three.txt")
    await drain(services)
    assert priors == [await service.run_guide(services.db, second)]
    assert priors[0].glossary == approved.glossary


async def test_a_cancelled_runs_draft_is_not_shared(services, tmp_path, monkeypatch):
    first = await submit(services, tmp_path, name="one.txt")
    await drain(services)
    cancelled = await submit(
        services,
        tmp_path,
        text="Il neige beaucoup ce matin. Le chien court.",
        name="two.txt",
        checkpoint=True,
    )
    await drain(services)
    await service.cancel_run(services.db, cancelled)  # its draft guide was saved
    assert await _state(services, cancelled) is RunState.CANCELLED
    priors = _capture_priors(monkeypatch)
    await submit(services, tmp_path, name="three.txt")
    await drain(services)
    assert priors == [await service.run_guide(services.db, first)]
    assert priors != [await service.run_guide(services.db, cancelled)]


async def test_database_errors_while_saving_propagate_for_queue_retry(
    services, tmp_path, monkeypatch
):
    def db_down():
        return OperationalError("INSERT", {}, Exception("connection refused"))

    async def always_down(*args, **kwargs):
        raise db_down()

    errors = iter([RuntimeError("bad outcome"), db_down()])

    async def bug_then_down(*args, **kwargs):
        raise next(errors)  # the failure record then hits the outage

    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))  # start_run → queues c1
    for broken in (always_down, bug_then_down):
        with monkeypatch.context() as m:
            m.setattr(outputs, "save_chunk_outcome", broken)
            with pytest.raises(OperationalError):
                await execute.process_chunk_job(services, run_id, "c1")
    await drain(services)  # the queue's retry
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_database_errors_while_saving_the_sweep_propagate(services, tmp_path, monkeypatch):
    run_id = await submit(services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM)
    await drain(services)
    guide = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    await service.approve_guide(services.db, services.queue, run_id, guide)

    async def down(*args, **kwargs):
        raise OperationalError("INSERT", {}, Exception("connection refused"))

    with monkeypatch.context() as m:
        m.setattr(outputs, "apply_sweep", down)
        with pytest.raises(OperationalError):
            await drain(services)
    await execute.finish_run(services, run_id)  # the queue's retry
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_the_sweep_switch_comes_from_the_runs_config_snapshot(
    services, db, fake_backend, tmp_path
):
    run_id = await submit(services, tmp_path)  # the snapshot keeps the sweep off for short
    live = make_services(db, fake_backend, tmp_path, profiles={"sweep": {"short": True}})
    live.queue, live.scorer_health = services.queue, ScorerHealth(device="cpu")
    await drain(live)
    # the only error is the notice that the worker's configuration differs from the run's
    report = await service.build_report(db, run_id)
    assert [e.stage for e in report.errors] == ["config"]
    async with db.session() as s:
        sweep = await runs.get_stage(s, run_id, "sweep")
    assert sweep.output == {"errors": [], "skipped": True}


async def test_a_resweep_keeps_earlier_revisions_and_uses_the_latest_approved_guide(
    services, tmp_path
):
    from sqlalchemy import select

    from coglossa import catalog
    from coglossa.store.models import RevisionRow, SpanRow

    run_id = await submit(services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM)
    await drain(services)
    first = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    await service.approve_guide(services.db, services.queue, run_id, first)
    await drain(services)
    async with services.db.session() as s, s.begin():
        span = await s.scalar(select(SpanRow).where(SpanRow.run_id == run_id).limit(1))
        s.add(RevisionRow(span_id=span.id, source="user", before="cat", after="the cat", reason=""))
        run = await runs.get_run(s, run_id)
        finished_before = run.finished_at
        project_id = (await runs.get_document(s, run.document_id)).project_id
    before = await service.build_report(services.db, run_id)
    assert [r.source for r in before.revisions] == [RevisionSource.SWEEP, RevisionSource.USER]
    second = first.model_copy(update={"summary": "second"})
    saved = await catalog.save_project_guide(services.db, project_id, second, base_version=2)

    assert await service.rerun_sweep(services.db, services.queue, run_id) == (
        f"re-running the sweep with the project guide, version {saved.info.version}"
    )
    await drain(services)

    after = await service.build_report(services.db, run_id)
    assert after.revisions[: len(before.revisions)] == before.revisions  # history only grows
    assert await _state(services, run_id) is RunState.COMPLETE
    assert after.guide == second
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
    assert run.finished_at is not None and run.finished_at > finished_before


async def test_a_rule_added_while_a_file_is_swept_applies_to_it(services, tmp_path, monkeypatch):
    """CGL-28: the sweep reads the set guide before its model calls. A rule a person adds during
    them still reaches the file: the recheck refuses a file that is not complete, so the run
    sweeps again with the rule before it completes."""
    run_id = await submit(services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM)
    await drain(services)
    await service.approve_guide(
        services.db, services.queue, run_id, Guide(summary="s", style_guide="g")
    )
    project_id = await _project_of(services, run_id)
    rule = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    real, added = execute.run_sweep, []

    async def sweep_while_a_rule_is_added(*args):
        if not added:
            async with services.db.session() as s:
                base = (await runs.prior_guide(s, project_id, "en")).version
            added.append(
                await catalog.save_project_guide(services.db, project_id, rule, base_version=base)
            )
        return await real(*args)

    monkeypatch.setattr(execute, "run_sweep", sweep_while_a_rule_is_added)
    await drain(services)
    assert added and await _state(services, run_id) is RunState.COMPLETE
    report = await service.build_report(services.db, run_id)
    (rev,) = report.revisions
    assert rev.source is RevisionSource.SWEEP and "cat" in report.spans[0].text


async def _start_swept_run(services, tmp_path):
    run_id = await submit(services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM)
    await drain(services)
    await service.approve_guide(
        services.db, services.queue, run_id, Guide(summary="s", style_guide="g")
    )
    return run_id, await _project_of(services, run_id)


async def _save_locked(services, project_id, glossary):
    async with services.db.session() as s:
        base = (await runs.prior_guide(s, project_id, "en")).version
    guide = Guide(summary="s", style_guide="g", glossary=glossary)
    await catalog.save_project_guide(services.db, project_id, guide, base_version=base)


async def test_a_guide_that_keeps_changing_stops_the_sweep_after_two_extra_passes(
    services, tmp_path, monkeypatch, caplog
):
    """CGL-81: a rule saved during every sweep must not loop for ever: the run finishes after two
    extra passes and says a later change was not applied."""
    run_id, project_id = await _start_swept_run(services, tmp_path)
    real, calls = execute.run_sweep, []

    async def sweep_while_a_new_rule_is_saved(*args):
        calls.append(1)
        rules = [
            GlossaryEntry(term=f"mot{n}", rendering=f"word{n}", locked=True)
            for n in range(len(calls))
        ]
        await _save_locked(services, project_id, rules)
        return await real(*args)

    monkeypatch.setattr(execute, "run_sweep", sweep_while_a_new_rule_is_saved)
    with caplog.at_level("INFO"):
        await drain(services)
    assert len(calls) == 3
    assert await _state(services, run_id) is RunState.COMPLETE_WITH_ERRORS
    report = await service.build_report(services.db, run_id)
    (note,) = [e for e in report.errors if e.stage == "sweep"]
    assert note.message == GUIDE_CHANGED_AGAIN
    detail = await catalog.run_detail(services.db, run_id)
    assert [(p.stage, p.text) for p in detail.problems] == [("sweep", GUIDE_CHANGED_AGAIN)]
    assert detail.progress.retryable  # the app offers the re-sweep that applies the change
    assert any("3 sweep passes" in r.getMessage() for r in caplog.records)


def test_the_sweep_rules_ignore_order_but_not_content():
    def guide(*glossary):
        return Guide(summary="s", style_guide="g", glossary=list(glossary))

    a = GlossaryEntry(term="Chat", rendering="cat", locked=True)
    b = GlossaryEntry(term="chien", rendering="dog", locked=True)
    loose = GlossaryEntry(term="loup", rendering="wolf")
    rules = execute._rules
    assert rules(guide(a, b, loose)) == rules(guide(b, a))
    assert rules(guide(a, b)) != rules(guide(a, b.model_copy(update={"rendering": "hound"})))
    assert rules(guide(a, b)) != rules(guide(a, b.model_copy(update={"locked": False})))
    # two entries with one key: a change to the first is seen too
    twin = a.model_copy(update={"term": "chat "})
    assert rules(guide(a, twin)) != rules(guide(a.model_copy(update={"rendering": "x"}), twin))


async def test_a_save_that_only_reorders_locked_entries_causes_no_second_pass(
    services, tmp_path, monkeypatch
):
    run_id, project_id = await _start_swept_run(services, tmp_path)
    one = GlossaryEntry(term="chat", rendering="cat", locked=True)
    two = GlossaryEntry(term="chien", rendering="dog", locked=True)
    await _save_locked(services, project_id, [one, two])
    real, calls = execute.run_sweep, []

    async def sweep_while_the_rules_are_reordered(*args):
        calls.append(1)
        await _save_locked(services, project_id, [two, one])
        return await real(*args)

    monkeypatch.setattr(execute, "run_sweep", sweep_while_the_rules_are_reordered)
    await drain(services)
    assert len(calls) == 1
    assert await _state(services, run_id) is RunState.COMPLETE


async def _project_of(svc, run_id):
    async with svc.db.session() as s:
        return (await runs.get_document(s, (await runs.get_run(s, run_id)).document_id)).project_id


CAT = GlossaryEntry(term="chat", rendering="cat", locked=True)


async def test_a_rule_added_while_a_file_translates_reaches_its_sweep(services, tmp_path):
    run_id = await submit(
        services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM, checkpoint=False
    )
    await execute.run_job(services, *_pop(services))  # start_run: guide built, chunks queued
    project_id = await _project_of(services, run_id)
    view = await catalog.project_guide(services.db, project_id)  # the run's draft, v1
    await catalog.save_project_guide(
        services.db,
        project_id,
        view.guide.model_copy(update={"glossary": [CAT]}),
        base_version=view.info.version,
    )
    await drain(services)
    report = await service.build_report(services.db, run_id)
    assert await _state(services, run_id) is RunState.COMPLETE
    assert [r.source for r in report.revisions] == [RevisionSource.SWEEP]
    assert report.guide.glossary == []  # the run keeps its own guide; the sweep used the set's


async def test_a_rule_removed_while_a_file_translates_is_not_enforced(services, tmp_path):
    project_guide = Guide(summary="s", style_guide="g", glossary=[CAT])
    first = await submit(services, tmp_path, name="one.txt", checkpoint=True)
    await drain(services)
    await service.approve_guide(services.db, services.queue, first, project_guide)
    await drain(services)
    run_id = await submit(
        services,
        tmp_path,
        text="Le chat court. Il neige.",
        name="two.txt",
        pipeline_profile=PipelineProfile.LONG_FORM,
        checkpoint=False,
    )
    await execute.run_job(services, *_pop(services))  # its guide carries the locked "chat"
    assert (await service.run_guide(services.db, run_id)).glossary == [CAT]
    project_id = await _project_of(services, run_id)
    view = await catalog.project_guide(services.db, project_id)
    unlocked = CAT.model_copy(update={"locked": False})
    await catalog.save_project_guide(
        services.db,
        project_id,
        view.guide.model_copy(update={"glossary": [unlocked]}),
        base_version=view.info.version,
    )
    await drain(services)
    report = await service.build_report(services.db, run_id)
    assert await _state(services, run_id) is RunState.COMPLETE
    assert report.revisions == []


async def _set_guide_saved_during_the_next_build(services, monkeypatch, project_id, update):
    """While the next guide stage's model works, a person saves the set guide with `update`."""
    real = execute.run_guide

    async def saved_meanwhile(*args, **kwargs):
        built = await real(*args, **kwargs)
        view = await catalog.project_guide(services.db, project_id)
        await catalog.save_project_guide(
            services.db,
            project_id,
            view.guide.model_copy(update=update),
            base_version=view.info.version,
        )
        return built

    monkeypatch.setattr(execute, "run_guide", saved_meanwhile)


async def _set_with_locked_cat(services, tmp_path):
    first = await submit(services, tmp_path, name="one.txt", checkpoint=True)
    await drain(services)
    await service.approve_guide(
        services.db, services.queue, first, Guide(summary="s", style_guide="g", glossary=[CAT])
    )
    await drain(services)
    return await _project_of(services, first)


async def test_a_lock_removed_while_a_guide_is_built_stays_removed(services, tmp_path, monkeypatch):
    project_id = await _set_with_locked_cat(services, tmp_path)
    await _set_guide_saved_during_the_next_build(
        services, monkeypatch, project_id, {"glossary": []}
    )
    second = await submit(services, tmp_path, text="Le chat court. Il neige.", name="two.txt")
    await drain(services)
    assert (await catalog.project_guide(services.db, project_id)).guide.glossary == []
    assert (await service.run_guide(services.db, second)).glossary == []


async def test_a_lock_removed_while_a_file_translates_is_not_enforced(services, tmp_path):
    project_id = await _set_with_locked_cat(services, tmp_path)
    run_id = await submit(
        services,
        tmp_path,
        text="Le chat court. Il neige.",
        name="two.txt",
        pipeline_profile=PipelineProfile.LONG_FORM,
        checkpoint=False,
    )
    await execute.run_job(services, *_pop(services))  # its own guide carries the locked "chat"
    view = await catalog.project_guide(services.db, project_id)
    await catalog.save_project_guide(
        services.db,
        project_id,
        view.guide.model_copy(update={"glossary": []}),
        base_version=view.info.version,
    )
    await drain(services)
    assert (await service.build_report(services.db, run_id)).revisions == []


async def test_a_style_guide_saved_while_a_guide_is_built_is_kept(services, tmp_path, monkeypatch):
    first = await submit(services, tmp_path, name="one.txt")
    await drain(services)
    project_id = await _project_of(services, first)
    await _set_guide_saved_during_the_next_build(
        services, monkeypatch, project_id, {"style_guide": "British spelling."}
    )
    second = await submit(services, tmp_path, text="Il neige.", name="two.txt")
    await drain(services)
    assert (await service.run_guide(services.db, second)).style_guide == "British spelling."
    view = await catalog.project_guide(services.db, project_id)
    assert view.guide.style_guide == "British spelling."


async def test_each_language_of_a_set_has_its_own_guide(services, tmp_path, fake_backend):
    """Two runs of one file in two languages: each guide is built, merged and swept within its
    own language (CGL-24)."""
    fake_backend.script("guide", _guide_reply("cat"), _guide_reply("gato"))
    english = await submit(services, tmp_path, name="one.txt")
    await drain(services)
    async with services.db.session() as s:
        doc_id = (await runs.get_run(s, english)).document_id
    spanish = await service.submit_existing(
        services.db,
        services.queue,
        services.cfg,
        services.prompts,
        doc_id,
        service.RunOptions(target_lang="es", source_lang="fr"),
    )
    await drain(services)
    assert await _state(services, spanish) is RunState.COMPLETE
    project_id = await _project_of(services, english)
    en = await catalog.project_guide(services.db, project_id, target_lang="en")
    es = await catalog.project_guide(services.db, project_id, target_lang="es")
    assert [g.rendering for g in en.guide.glossary] == ["cat"]
    assert [g.rendering for g in es.guide.glossary] == ["gato"]  # not merged onto "cat"
    assert (en.info.target_lang, es.info.target_lang) == ("en", "es")
    assert es.info.version == 1


async def test_a_lock_in_one_language_is_not_enforced_in_another(services, tmp_path):
    project_id = await _set_with_locked_cat(services, tmp_path)  # English, "chat" locked
    async with services.db.session() as s:
        doc_id = (
            await s.scalars(select(Document.id).where(Document.project_id == project_id))
        ).first()
    spanish = await service.submit_existing(
        services.db,
        services.queue,
        services.cfg,
        services.prompts,
        doc_id,
        service.RunOptions(
            target_lang="es",
            source_lang="fr",
            pipeline_profile=PipelineProfile.LONG_FORM,
            checkpoint=False,
        ),
    )
    await drain(services)
    assert await _state(services, spanish) is RunState.COMPLETE
    assert (await service.build_report(services.db, spanish)).revisions == []
    renderings = [g.rendering for g in (await service.run_guide(services.db, spanish)).glossary]
    assert "cat" not in renderings


async def test_a_checkpointed_draft_is_never_the_set_guide(services, tmp_path):
    """The draft is saved and the run marked as waiting in one transaction, so no moment exists
    in which another file could build on, or merge onto, an unapproved draft."""
    run_id = await submit(services, tmp_path, checkpoint=True)
    services.queue.pop()  # its start job: the stages run here one by one
    await execute._stage(services, run_id, "ingest", RunState.INGESTING, execute._ingest)
    await execute._stage(services, run_id, "profile", RunState.PROFILING, execute._profile)
    await execute._stage(services, run_id, "guide", RunState.BUILDING_GUIDE, execute._guide)
    assert await _state(services, run_id) is RunState.AWAITING_GUIDE_APPROVAL
    async with services.db.session() as s:
        assert await runs.prior_guide(s, await _project_of(services, run_id), "en") is None


async def test_a_draft_keeps_what_a_person_saved_while_it_was_built(
    services, tmp_path, monkeypatch
):
    """The guide stage reads the project guide (v2), a person saves v3 with a locked entry while
    the model works, and the draft is merged onto v3 when it is saved (v4): approving it as is
    keeps the person's entry (CGL-23)."""
    first = await submit(services, tmp_path, name="one.txt", checkpoint=True)
    await drain(services)
    assert await service.approve_guide(services.db, services.queue, first) == 2
    await drain(services)
    async with services.db.session() as s:
        project_id = (
            await runs.get_document(s, (await runs.get_run(s, first)).document_id)
        ).project_id
    locked = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    real = execute.run_guide

    async def saved_meanwhile(*args, **kwargs):
        built = await real(*args, **kwargs)
        await catalog.save_project_guide(services.db, project_id, locked, base_version=2)
        return built

    monkeypatch.setattr(execute, "run_guide", saved_meanwhile)
    second = await submit(services, tmp_path, name="two.txt", checkpoint=True)
    await drain(services)
    assert await _state(services, second) is RunState.AWAITING_GUIDE_APPROVAL
    async with services.db.session() as s:
        draft = await service.run_guide_row(s, second)
    assert (draft.version, draft.built_from_version) == (4, 3)
    assert await service.approve_guide(services.db, services.queue, second) == 5
    assert (await service.run_guide(services.db, second)).glossary == locked.glossary


async def test_approval_refuses_a_draft_older_than_an_approved_guide(services, tmp_path):
    first = await submit(services, tmp_path, name="one.txt", checkpoint=True)
    await drain(services)
    assert await service.approve_guide(services.db, services.queue, first) == 2
    await drain(services)
    second = await submit(services, tmp_path, name="two.txt", checkpoint=True)
    await drain(services)
    async with services.db.session() as s:
        draft = await service.run_guide_row(s, second)
        project_id = (
            await runs.get_document(s, (await runs.get_run(s, second)).document_id)
        ).project_id
    assert (draft.version, draft.built_from_version) == (3, 2)
    locked = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    # a waiting draft is not the project guide, so the panel's base is still v2
    saved = await catalog.save_project_guide(services.db, project_id, locked, base_version=2)
    assert saved.info.version == 4
    with pytest.raises(service.ConflictError) as as_is:
        await service.approve_guide(services.db, services.queue, second)
    assert str(as_is.value) == (
        "the project guide changed since this draft (now version 4); reload it and approve the "
        "current guide"
    )
    assert await _state(services, second) is RunState.AWAITING_GUIDE_APPROVAL
    assert (
        await service.approve_guide(services.db, services.queue, second, locked, base_version=4)
        == 5
    )
    assert await service.run_guide(services.db, second) == locked


async def test_what_a_file_could_not_hold_is_said_on_every_run_of_it(services, tmp_path):
    """CGL-87: a chart the ingestor cannot read is named on the run and in its report, and a
    later run of the same file, which reuses its stored blocks, says so too."""
    d = docx.Document()
    add_chart(d.add_paragraph("Le chat dort."))
    path = tmp_path / "chart.docx"
    d.save(path)
    options = service.SubmitOptions(project="novel", target_lang="en", source_lang="fr")
    first = await service.submit_run(
        services.db, services.storage, services.queue, services.cfg, services.prompts, path, options
    )
    await drain(services)
    notice = UNREAD_PREFIX + "1 chart."
    async with services.db.session() as s:
        doc_id = (await runs.get_run(s, first)).document_id
    again = await service.submit_existing(
        services.db,
        services.queue,
        services.cfg,
        services.prompts,
        doc_id,
        service.RunOptions(target_lang="es", source_lang="fr"),
    )
    await drain(services)
    for run_id in (first, again):
        # a notice, not an error: the run is complete, and translating again would not help
        assert await _state(services, run_id) is RunState.COMPLETE
        report = await service.build_report(services.db, run_id)
        assert report.errors == []
        unread = [n for n in report.notices if n.code is FlagCode.UNREAD_CONTENT]
        assert [n.explanation for n in unread] == [notice]
        detail = await catalog.run_detail(services.db, run_id)
        assert detail.problems == []
        unread = [n for n in detail.notices if n.code is FlagCode.UNREAD_CONTENT]
        assert [n.explanation for n in unread] == [notice]


async def test_a_recheck_clears_a_terminology_flag_an_accepted_form_now_upholds(services, tmp_path):
    """CGL-109: a form accepted on one passage clears the same flag on the others when the set is
    rechecked, without rewriting their text."""
    from coglossa.store.models import FlagRow, SpanRow

    run_id, project_id = await _start_swept_run(services, tmp_path)
    await drain(services)
    async with services.db.session() as s, s.begin():
        spans = list(await s.scalars(select(SpanRow).where(SpanRow.run_id == run_id)))
        violation = {"term": "chat", "expected": "feline", "kind": "glossary", "locked": True}
        flags = [
            FlagRow(
                span_id=span.id,
                code=FlagCode.TERMINOLOGY_VIOLATION.value,
                explanation="'chat' should be rendered as 'feline'.",
                evidence={"violations": [violation]},
            )
            for span in spans
        ]
        s.add_all(flags)
    before = await service.build_report(services.db, run_id)
    # the synthetic translation keeps the source words, so "chat" is the form it uses
    feline = GlossaryEntry(term="chat", rendering="feline", forms=["chat"], locked=True)
    await _save_locked(services, project_id, [feline])
    await service.rerun_sweep(services.db, services.queue, run_id)
    await drain(services)
    after = await service.build_report(services.db, run_id)
    assert after.revisions == before.revisions  # nothing rewritten
    async with services.db.session() as s:
        ids = [f.id for f in flags]
        statuses = [f.status for f in await s.scalars(select(FlagRow).where(FlagRow.id.in_(ids)))]
    assert statuses == ["resolved"] * len(spans)
    assert any("chat" in sp.text for sp in after.spans)  # the entry applied, and a form held
