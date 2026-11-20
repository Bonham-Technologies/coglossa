import logging
import re
import uuid
from pathlib import Path

import psycopg
import pytest
from coglossa.config import CoglossaConfig, ConfigError, load_config
from coglossa.ingest.base import IngestError
from coglossa.service import ConflictError, InvalidRequestError, InvalidRunStateError
from coglossa.storage import StorageError
from coglossa.store.runs import NotFoundError
from coglossa.testing.queue import RecordingQueue
from coglossa.testing.seed import SEED_CONFIG
from fastapi.routing import iter_route_contexts
from sqlalchemy.exc import OperationalError

from coglossa_api.app import create_app
from coglossa_api.errors import UploadTooLargeError
from coglossa_api.headers import CSP
from tests.conftest import _recording, serve

DOWN = (
    "The service can't reach its database right now. Try again shortly; "
    "if it keeps happening, tell your administrator."
)
ERRORS = {
    "missing": (NotFoundError("run 1 not found"), 404, "run 1 not found"),
    "conflict": (ConflictError("taken"), 409, "taken"),
    "state": (InvalidRunStateError("run 1 is complete"), 409, "run 1 is complete"),
    "big": (UploadTooLargeError("too big"), 413, "too big"),
    "invalid": (InvalidRequestError("bad"), 422, "bad"),
    "ingest": (IngestError("a.pdf needs OCR"), 422, "a.pdf needs OCR"),
    # CGL-82: internals (paths, keys, variable names) stay in the log, not the response
    "config": (
        ConfigError("coglossa.yaml:\n  - roles broken"),
        500,
        "The server isn't set up correctly. Ask an administrator to check the server log.",
    ),
    "storage": (
        StorageError("cannot store x: disk full"),
        500,
        "The server couldn't read or write a stored file. Ask an administrator to check the "
        "server log.",
    ),
    "db": (
        OperationalError("SELECT 1", {}, Exception("connection refused\ndetails")),
        503,
        DOWN,
    ),
    "bug": (RuntimeError("secret internals"), 500, "internal error"),
}
BUSY = "the database was busy with a conflicting change; try again"
for _name, _error in [
    ("deadlock", psycopg.errors.DeadlockDetected("deadlock detected")),
    ("serialization", psycopg.errors.SerializationFailure("could not serialize")),
    ("lock", psycopg.errors.LockNotAvailable("could not obtain lock")),
]:
    ERRORS[_name] = (OperationalError("UPDATE run", {}, _error), 409, BUSY)
ERRORS["other-sqlstate"] = (
    OperationalError("SELECT 1", {}, psycopg.errors.AdminShutdown("terminating connection")),
    503,
    DOWN,
)


async def test_health_needs_no_user(make_api):
    async with make_api(auth={"mode": "trusted_header"}) as api:
        response = await api.client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


async def test_head_health_needs_no_user(make_api):
    async with make_api(auth={"mode": "trusted_header"}) as api:
        response = await api.client.head("/api/health")
        other = await api.client.head("/api/openapi.json")
    assert response.status_code == 200 and response.content == b""
    assert other.status_code == 405


async def test_me_without_auth(api):
    assert (await api.client.get("/api/me")).json() == {
        "user": None,
        "admin": True,
        "teams": [],
        "max_upload_mb": 200,
        "logout_url": None,
    }


async def test_trusted_header_auth(make_api):
    async with make_api(auth={"mode": "trusted_header", "header": "X-Forwarded-User"}) as api:
        missing = await api.client.get("/api/me")
        blank = await api.client.get("/api/me", headers={"X-Forwarded-User": "  "})
        named = await api.client.get("/api/me", headers={"X-Forwarded-User": " ana "})
    assert missing.status_code == blank.status_code == 401
    assert missing.json() == {
        "detail": "missing X-Forwarded-User header: sign in through the proxy in front of Coglossa"
    }
    assert named.json() == {
        "user": "ana",
        "admin": False,
        "teams": [],
        "max_upload_mb": 200,
        "logout_url": None,
    }


def _protected_routes() -> list[tuple[str, str]]:
    """Every /api route of the app, found by walking its routers (not its OpenAPI document,
    which leaves out routes hidden from the schema), except health."""
    app = create_app(CoglossaConfig.model_validate(SEED_CONFIG), Path("coglossa.yaml"))
    routes = {
        (method, route.path)
        for route in iter_route_contexts(app.routes)
        if route.path.startswith("/api/")
        for method in route.methods or ()
        if method != "HEAD"
    }
    return sorted(routes - {("GET", "/api/health")})


def _fill(path: str) -> str:
    return re.sub(
        r"\{(\w+)\}", lambda m: "7" if m.group(1) == "flag_id" else str(uuid.uuid4()), path
    )


PROTECTED = _protected_routes()


def test_every_route_is_listed():
    assert len(PROTECTED) > 30 and ("GET", "/api/openapi.json") in PROTECTED


@pytest.mark.parametrize(("method", "path"), PROTECTED)
async def test_every_route_but_health_needs_the_trusted_header(make_api, method, path):
    async with make_api(auth={"mode": "trusted_header"}) as api:
        response = await api.client.request(method, _fill(path))
    assert response.status_code == 401, response.text
    assert response.json()["detail"].startswith("missing X-Remote-User header")


async def test_exceptions_map_to_one_line_responses(db, write_config, caplog):
    path = write_config()
    cfg, _ = load_config(path)
    app = create_app(cfg, path, open_queue=_recording(RecordingQueue()))

    async def boom(kind: str) -> None:
        raise ERRORS[kind][0]

    app.add_api_route("/api/boom/{kind}", boom)
    async with serve(app) as client:
        for kind, (_, status, detail) in ERRORS.items():
            with caplog.at_level(logging.WARNING, logger="coglossa.api"):
                response = await client.get(f"/api/boom/{kind}")
            assert (response.status_code, response.json()) == (status, {"detail": detail}), kind
            # CGL-35: an unhandled error's 500 comes from outside every middleware
            assert response.headers["x-frame-options"] == "DENY", kind
            assert response.headers.get_list("content-security-policy") == [CSP], kind
            assert response.headers["cross-origin-opener-policy"] == "same-origin", kind  # CGL-70

        # CGL-97: the driver's reason is logged, never sent
        for kind, reason in [
            ("db", "connection refused"),
            ("other-sqlstate", "terminating connection"),
        ]:
            with caplog.at_level(logging.WARNING, logger="coglossa.api"):
                down = await client.get(f"/api/boom/{kind}")
            assert reason not in down.text, kind
            assert any(
                r.levelno == logging.WARNING and f"database unavailable: {reason}" in r.getMessage()
                for r in caplog.records
            ), kind
    assert "secret internals" not in response.text
    assert any(r.levelno == logging.ERROR and r.exc_info for r in caplog.records)


@pytest.mark.parametrize(
    "error",
    [
        StorageError("cannot read projects/p1/src.txt: Permission denied"),
        ConfigError("api_key_env COGLOSSA_SECRET_KEY is not set"),
    ],
    ids=["storage", "config"],
)
async def test_storage_and_config_errors_keep_internals_in_the_log(db, write_config, caplog, error):
    path = write_config()
    cfg, _ = load_config(path)
    app = create_app(cfg, path, open_queue=_recording(RecordingQueue()))

    async def boom() -> None:
        raise error

    app.add_api_route("/api/boom", boom)
    async with serve(app) as client:
        with caplog.at_level(logging.ERROR, logger="coglossa.api"):
            response = await client.get("/api/boom")
    assert response.status_code == 500
    assert str(error) not in response.text
    assert not any(w in response.text for w in ("projects/", "COGLOSSA_SECRET_KEY", "Permission"))
    assert any(r.levelno == logging.ERROR and str(error) in r.getMessage() for r in caplog.records)


async def test_unknown_routes_are_404(api):
    assert (await api.client.get("/api/nothing-here")).status_code == 404


async def test_openapi_document_needs_the_user(make_api):
    async with make_api(auth={"mode": "trusted_header"}) as api:
        anonymous = await api.client.get("/api/openapi.json")
        signed_in = await api.client.get("/api/openapi.json", headers={"X-Remote-User": "ana"})
    assert anonymous.status_code == 401
    assert signed_in.status_code == 200
    assert {"openapi", "paths"} <= signed_in.json().keys()
    assert "/api/me" in signed_in.json()["paths"]


async def test_no_docs_pages(api):
    for path in ("/docs", "/api/docs", "/openapi.json", "/redoc", "/api/redoc"):
        assert (await api.client.get(path)).status_code == 404, path


async def test_the_queue_kicks_a_worker_only_when_configured(db, write_config, monkeypatch):
    from coglossa.worker.kick import KickingQueue

    async def queue_of(**overrides):
        path = write_config(**overrides)
        cfg, _ = load_config(path)
        app = create_app(cfg, path, open_queue=_recording(RecordingQueue()))
        async with serve(app):
            return app.state.ctx.queue

    monkeypatch.setenv("KICK_TOKEN", "t")
    kicking = await queue_of(worker={"kick_url": "https://k/", "kick_token_env": "KICK_TOKEN"})
    assert isinstance(kicking, KickingQueue) and kicking.kicker.token == "t"
    assert not isinstance(await queue_of(), KickingQueue)
