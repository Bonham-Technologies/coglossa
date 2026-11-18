"""The REST API (spec §2.1, §8): a thin HTTP layer over the control plane (coglossa.service,
coglossa.catalog, coglossa.review). It never calls a model; doctor, which checks the
backends, is the one exception."""

import contextlib
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import coglossa
from coglossa.config import CoglossaConfig
from coglossa.people import load_people
from coglossa.prompts.library import PromptLibrary
from coglossa.service import JobQueue
from coglossa.storage import build_storage
from coglossa.store.db import Database, database_url
from coglossa.worker.app import ProcrastinateQueue, open_app
from coglossa.worker.kick import KickingQueue, build_kicker
from fastapi import APIRouter, Depends, FastAPI, Request, Response

from coglossa_api.auth import current_viewer, guard_ids
from coglossa_api.context import ApiContext
from coglossa_api.errors import install_error_handlers
from coglossa_api.headers import SecurityHeadersMiddleware
from coglossa_api.proxy import ProxySecretMiddleware
from coglossa_api.routes import ROUTERS
from coglossa_api.web import mount_web

QueueOpener = Callable[[str], AbstractAsyncContextManager[JobQueue]]


@contextlib.asynccontextmanager
async def procrastinate_queue(url: str) -> AsyncIterator[JobQueue]:
    """The job queue. open_app swaps the connector of procrastinate's shared app, so a process
    opens it once, here for the app's lifetime; any other opening (a worker in the same
    process, in tests) nests inside this one and is closed first."""
    async with open_app(url) as opened:
        yield ProcrastinateQueue(opened)


health = APIRouter(prefix="/api", tags=["system"])
documents = APIRouter()


@documents.get("/openapi.json", include_in_schema=False)
async def get_openapi(request: Request) -> dict[str, Any]:
    return request.app.openapi()


@health.get("/health")
async def get_health() -> dict[str, str]:
    """Liveness: answers without the database and without a signed-in user."""
    return {"status": "ok", "version": coglossa.__version__}


@health.head("/health", include_in_schema=False)
async def head_health() -> Response:
    """The same probe for load balancers and monitors that send HEAD."""
    return Response()


def create_app(
    cfg: CoglossaConfig, config_path: Path, *, open_queue: QueueOpener = procrastinate_queue
) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(database_url(cfg))
        try:
            async with open_queue(db.url) as queue:
                kicker = build_kicker(cfg.worker)
                if kicker is not None:
                    queue = KickingQueue(queue, kicker)
                app.state.ctx = ApiContext(
                    cfg=cfg,
                    config_path=config_path,
                    db=db,
                    storage=build_storage(cfg.storage),
                    prompts=PromptLibrary(cfg.prompts_dir),
                    queue=queue,
                    people=load_people(cfg.auth.people_file),
                )
                try:
                    yield
                finally:
                    if kicker is not None:
                        await kicker.aclose()
        finally:
            await db.dispose()

    app = FastAPI(
        title="Coglossa",
        version=coglossa.__version__,
        summary="Multi-agent translation with verifiable, scored output.",
        lifespan=lifespan,
        openapi_url=None,  # served below, behind auth
        docs_url=None,  # Swagger UI loads its assets from a CDN: blank on an air-gapped site
        redoc_url=None,
    )
    install_error_handlers(app)
    if cfg.auth.proxy_secret_env:
        # validate_config refused an unset variable, so the value is there
        app.add_middleware(ProxySecretMiddleware, secret=os.environ[cfg.auth.proxy_secret_env])
    # Added last, so it is outermost: the proxy check's refusals carry the headers too (CGL-35)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(health)
    # Who is asking, then (CGL-26) whether they may see the run, document or project named in
    # the path: one check for every route, so none can forget it.
    protected = APIRouter(prefix="/api", dependencies=[Depends(current_viewer), Depends(guard_ids)])
    for router in [documents, *ROUTERS]:
        protected.include_router(router)
    app.include_router(protected)
    if cfg.api.web_dir is not None:
        mount_web(app, cfg.api.web_dir)
    return app
