import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import yaml
from coglossa.config import CoglossaConfig, load_config
from coglossa.testing.queue import RecordingQueue
from coglossa.testing.seed import SEED_CONFIG
from fastapi import FastAPI

from coglossa_api.app import create_app

pytest_plugins = ["coglossa.testing.pytest_db"]


@pytest.fixture
def write_config(tmp_path, pg_url, monkeypatch) -> Callable[..., Path]:
    """Writes coglossa.yaml (the seeded run's models, storage under tmp_path, a fast progress
    stream) and points COGLOSSA_DATABASE_URL at the test database."""
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)

    def write(**overrides) -> Path:
        data = {
            **SEED_CONFIG,
            "storage": {"path": str(tmp_path / "files")},
            "api": {"progress_poll_s": 0.02},
            **overrides,
        }
        path = tmp_path / "coglossa.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return path

    return write


@contextlib.asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Runs the app's lifespan and talks to it in-process. raise_app_exceptions=False: a 500
    reaches the test as a response, as it would reach a browser."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://coglossa.test"
        ) as client:
            yield client


@dataclass
class Api:
    client: httpx.AsyncClient
    queue: RecordingQueue
    cfg: CoglossaConfig
    storage_root: Path


def _recording(queue: RecordingQueue):
    @contextlib.asynccontextmanager
    async def opener(url: str) -> AsyncIterator[RecordingQueue]:
        yield queue

    return opener


@pytest.fixture
def make_api(db, write_config):
    @contextlib.asynccontextmanager
    async def make(**overrides) -> AsyncIterator[Api]:
        path = write_config(**overrides)
        cfg, _ = load_config(path)
        queue = RecordingQueue()
        app = create_app(cfg, path, open_queue=_recording(queue))
        async with serve(app) as client:
            yield Api(client=client, queue=queue, cfg=cfg, storage_root=cfg.storage.path)

    return make


@pytest.fixture
async def api(make_api) -> AsyncIterator[Api]:
    async with make_api() as opened:
        yield opened
