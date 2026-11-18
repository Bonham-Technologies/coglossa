import logging
import uuid

import pytest

from coglossa import service
from coglossa.ingest.base import IngestError, UnsupportedFormatError
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import PipelineProfile, RunState
from coglossa.storage import LocalStorage, StorageError
from coglossa.store import runs
from coglossa.store.runs import NotFoundError
from coglossa.testing.queue import RecordingQueue
from tests.conftest import build_config


@pytest.fixture
def env(db, tmp_path):
    return (
        db,
        LocalStorage(tmp_path / "files"),
        RecordingQueue(),
        build_config(),
        PromptLibrary(None),
    )


async def _submit(env, filename="story.txt", data=b"Il pleut.", project="novel", **opts):
    db, storage, queue, cfg, prompts = env
    options = service.RunOptions(target_lang="en", **opts)
    return await service.submit_bytes(
        db, storage, queue, cfg, prompts, filename, data, options, project=project
    )


async def _document(db, run_id):
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        return run, await runs.get_document(s, run.document_id)


@pytest.mark.parametrize(
    ("filename", "shown", "key_name"),
    [
        ("رواية.txt", "رواية.txt", "_____.txt"),
        ("café notes.docx", "café notes.docx", "caf__notes.docx"),
        ("../../etc/passwd.txt", "passwd.txt", "passwd.txt"),
    ],
)
async def test_submit_bytes_keeps_the_visible_name_and_a_safe_key(env, filename, shown, key_name):
    db, storage, queue, *_ = env
    run_id = await _submit(env, filename=filename, source_lang="fr")
    run, doc = await _document(db, run_id)
    assert doc.filename == shown
    assert doc.storage_key == f"projects/{doc.project_id}/documents/{doc.id}/{key_name}"
    assert storage.get(doc.storage_key) == b"Il pleut."
    assert (run.target_lang, run.source_lang, run.state) == ("en", "fr", RunState.CREATED)
    assert [(j.task, j.kwargs) for j in queue.jobs] == [
        (service.TASK_START_RUN, {"run_id": str(run_id)})
    ]


async def test_submit_bytes_into_an_existing_project_by_id(env):
    db, *_ = env
    first = await _submit(env, project="novel")
    _, doc = await _document(db, first)
    second = await _submit(env, project=doc.project_id, pipeline_profile=PipelineProfile.SHORT)
    run, doc2 = await _document(db, second)
    assert doc2.project_id == doc.project_id
    assert run.pipeline_profile == "short"
    with pytest.raises(NotFoundError, match="project .* not found"):
        await _submit(env, project=uuid.uuid4())


@pytest.mark.parametrize(
    ("filename", "data", "error", "message"),
    [
        ("", b"x", service.InvalidRequestError, "the upload has no file name"),
        ("/", b"x", service.InvalidRequestError, "the upload has no file name"),
        ("a" * 252 + ".txt", b"x", service.InvalidRequestError, "longer than 255 characters"),
        ("notes.rtf", b"x", UnsupportedFormatError, "unsupported file type '.rtf'"),
        ("README", b"x", UnsupportedFormatError, "unsupported file type 'README'"),
        ("empty.txt", b"", IngestError, "empty.txt is empty"),
    ],
)
async def test_submit_bytes_refuses_bad_uploads(env, filename, data, error, message):
    db, storage, queue, *_ = env
    with pytest.raises(error, match=message):
        await _submit(env, filename=filename, data=data)
    assert queue.jobs == []
    assert not (storage.root / "projects").exists()
    async with db.session() as s:
        with pytest.raises(NotFoundError):
            await runs.find_project(s, "novel")


async def test_a_failed_submit_leaves_no_stored_file(env, monkeypatch):
    db, storage, queue, *_ = env

    async def broken(*args, **kwargs):
        raise RuntimeError("database said no")

    monkeypatch.setattr(runs, "create_run", broken)
    with pytest.raises(RuntimeError, match="database said no"):
        await _submit(env)
    assert list((storage.root / "projects").rglob("*.txt")) == []
    assert queue.jobs == []


async def test_cleanup_errors_do_not_hide_the_original_error(env, monkeypatch):
    db, storage, *_ = env

    async def broken(*args, **kwargs):
        raise RuntimeError("database said no")

    def cannot_delete(prefix):
        raise StorageError("disk gone")

    monkeypatch.setattr(runs, "create_run", broken)
    monkeypatch.setattr(storage, "delete_prefix", cannot_delete)
    with pytest.raises(RuntimeError, match="database said no"):
        await _submit(env)


async def test_submit_run_from_a_path_still_works(env, tmp_path):
    db, storage, queue, cfg, prompts = env
    path = tmp_path / "chapter.md"
    path.write_text("# Titre\n\nTexte.", encoding="utf-8")
    opts = service.SubmitOptions(project="book", target_lang="de")
    run_id = await service.submit_run(db, storage, queue, cfg, prompts, path, opts)
    run, doc = await _document(db, run_id)
    assert (doc.filename, doc.format, run.target_lang) == ("chapter.md", "md", "de")


async def test_submit_existing_creates_a_new_run_for_the_document(env):
    db, storage, queue, cfg, prompts = env
    first = await _submit(env)
    _, doc = await _document(db, first)
    second = await service.submit_existing(
        db, queue, cfg, prompts, doc.id, service.RunOptions(target_lang="de", checkpoint=False)
    )
    run, doc2 = await _document(db, second)
    assert second != first
    assert doc2.id == doc.id
    assert (run.target_lang, run.checkpoint) == ("de", False)
    assert [j.kwargs["run_id"] for j in queue.jobs] == [str(first), str(second)]
    with pytest.raises(NotFoundError, match="document .* not found"):
        await service.submit_existing(
            db, queue, cfg, prompts, uuid.uuid4(), service.RunOptions(target_lang="de")
        )


async def test_delete_project_by_id(env):
    db, storage, *_ = env
    run_id = await _submit(env)
    _, doc = await _document(db, run_id)
    await service.delete_project_id(db, storage, doc.project_id)
    assert not (storage.root / "projects" / str(doc.project_id)).exists()
    with pytest.raises(NotFoundError):
        await service.delete_project_id(db, storage, doc.project_id)


async def test_a_file_cleanup_failure_after_the_delete_is_logged_not_raised(
    env, monkeypatch, caplog
):
    """CGL-84: the rows are gone once the delete commits; a storage error after that must not
    report the delete as failed. It is logged with the prefix, and doctor lists the leftover."""
    db, storage, *_ = env
    run_id = await _submit(env)
    _, doc = await _document(db, run_id)

    def cannot_delete(prefix):
        raise StorageError(f"cannot delete {prefix}: Permission denied")

    monkeypatch.setattr(storage, "delete_prefix", cannot_delete)
    with caplog.at_level(logging.ERROR, logger="coglossa"):
        await service.delete_project_id(db, storage, doc.project_id)
    prefix = f"projects/{doc.project_id}/"
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert prefix in caplog.text and "Permission denied" in caplog.text
    with pytest.raises(NotFoundError):
        await service.delete_project_id(db, storage, doc.project_id)
    assert (storage.root / "projects" / str(doc.project_id)).exists()


def test_conflicts_and_invalid_requests_are_distinct():
    assert issubclass(service.InvalidRunStateError, service.ConflictError)
    assert issubclass(service.InvalidRequestError, ValueError)
    assert not issubclass(service.InvalidRequestError, service.ConflictError)


async def test_storage_calls_run_off_the_event_loop(env, monkeypatch):
    import threading

    db, storage, *_ = env
    loop_thread = threading.current_thread()
    threads: dict[str, list[threading.Thread]] = {"put": [], "delete_prefix": []}
    for name in threads:
        real = getattr(storage, name)

        def spy(*args, _name=name, _real=real):
            threads[_name].append(threading.current_thread())
            return _real(*args)

        monkeypatch.setattr(storage, name, spy)
    await _submit(env)

    async def broken(*args, **kwargs):
        raise RuntimeError("database said no")

    with monkeypatch.context() as m:  # the clean-up after a failed submit
        m.setattr(runs, "create_run", broken)
        with pytest.raises(RuntimeError):
            await _submit(env)
    await service.delete_project(db, storage, "novel")
    assert len(threads["put"]) == 2 and len(threads["delete_prefix"]) == 2
    assert all(t is not loop_thread for ts in threads.values() for t in ts)
