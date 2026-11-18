import uuid

import pytest

from coglossa import service
from coglossa.prompts.library import PromptLibrary
from coglossa.scoring.tiers import LanguageTiers
from coglossa.storage import LocalStorage
from coglossa.testing.queue import RecordingQueue
from coglossa.worker.execute import WorkerServices, run_job
from tests.conftest import build_config


async def _no_sleep(_: float) -> None:
    return None


def make_services(db, fake_backend, tmp_path, **cfg_overrides) -> WorkerServices:
    cfg = build_config(**cfg_overrides)
    return WorkerServices(
        cfg=cfg,
        db=db,
        storage=LocalStorage(tmp_path / "files"),
        queue=RecordingQueue(),
        backends={"fake": fake_backend},
        prompts=PromptLibrary(cfg.prompts_dir),
        tiers=LanguageTiers.load(None),
        sleep=_no_sleep,
    )


@pytest.fixture
def services(db, fake_backend, tmp_path):
    return make_services(db, fake_backend, tmp_path)


async def submit(svc, tmp_path, text="Le chat dort. Il pleut.", name="story.txt", **opts):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    options = service.SubmitOptions(project="novel", target_lang="en", source_lang="fr", **opts)
    return await service.submit_run(
        svc.db, svc.storage, svc.queue, svc.cfg, svc.prompts, path, options
    )


async def drain(svc, *, limit: int = 500) -> int:
    """Run queued jobs in order until the queue is empty (a deterministic in-test worker)."""
    count = 0
    while (job := svc.queue.pop()) is not None:
        await run_job(svc, job.task, job.kwargs)
        count += 1
        assert count < limit, "job loop did not settle"
    return count


def tasks_sent(fake_backend) -> list[str]:
    import json

    return [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["task"]
        for r in fake_backend.requests
    ]


def uid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
