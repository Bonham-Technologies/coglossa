"""Exceptions to HTTP responses. The body is always {"detail": "<one line>"}, plus "code" for
the conflicts a client handles specially (ConflictError.code) and, for "passage_changed", the
passage's "current_text": never a
traceback, a database URL or a password. Unexpected errors are logged with their traceback
and answered with "internal error"."""

import logging
from collections.abc import Awaitable, Callable

from coglossa.config import ConfigError
from coglossa.export.office import ExportError
from coglossa.ingest.base import IngestError
from coglossa.service import ConflictError, InvalidRequestError
from coglossa.storage import StorageError
from coglossa.store.db import error_reason
from coglossa.store.runs import NotFoundError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError

from coglossa_api.headers import CSP, SECURITY_HEADERS

log = logging.getLogger("coglossa.api")
Handler = Callable[[Request, Exception], Awaitable[JSONResponse]]


class UploadTooLargeError(Exception):
    """The upload is larger than api.max_upload_mb (HTTP 413)."""


_STATUS: tuple[tuple[type[Exception], int], ...] = (
    (NotFoundError, 404),
    (ConflictError, 409),  # includes InvalidRunStateError
    (UploadTooLargeError, 413),
    (InvalidRequestError, 422),
    (IngestError, 422),
    (ExportError, 422),  # CGL-110: a PowerPoint or Excel file that can't be written back
)


def _response(
    status: int, message: str, code: str | None = None, current_text: str | None = None
) -> JSONResponse:
    content = {"detail": " ".join(message.split())}
    if code is not None:
        content["code"] = code
    if current_text is not None:
        content["current_text"] = current_text
    return JSONResponse(status_code=status, content=content)


def _handler(status: int) -> Handler:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        return _response(
            status, str(exc), getattr(exc, "code", None), getattr(exc, "current_text", None)
        )

    return handle


async def _config_error(request: Request, exc: Exception) -> JSONResponse:
    log.error("configuration error on %s %s: %s", request.method, request.url.path, exc)
    return _response(
        500, "The server isn't set up correctly. Ask an administrator to check the server log."
    )


async def _storage_error(request: Request, exc: Exception) -> JSONResponse:
    # The message names stored paths, keys and buckets: log it, never send it.
    log.error("storage error on %s %s: %s", request.method, request.url.path, exc)
    return _response(
        500,
        "The server couldn't read or write a stored file. "
        "Ask an administrator to check the server log.",
    )


# Deadlock, serialization failure, lock not available: concurrent changes, not an outage.
_BUSY_SQLSTATES = frozenset({"40P01", "40001", "55P03"})


async def _database_down(request: Request, exc: Exception) -> JSONResponse:
    if getattr(getattr(exc, "orig", None), "sqlstate", None) in _BUSY_SQLSTATES:
        log.warning("database conflict: %s", error_reason(exc))
        return _response(409, "the database was busy with a conflicting change; try again")
    reason = error_reason(exc)
    log.warning("database unavailable: %s", reason)
    # The reason names the database host, port or user: log it, never send it.
    return _response(
        503,
        "The service can't reach its database right now. Try again shortly; "
        "if it keeps happening, tell your administrator.",
    )


async def _internal(request: Request, exc: Exception) -> JSONResponse:
    log.error("internal error on %s %s", request.method, request.url.path, exc_info=exc)
    response = _response(500, "internal error")
    # Starlette sends this one from outside every middleware, SecurityHeadersMiddleware too
    response.headers.update({**SECURITY_HEADERS, "Content-Security-Policy": CSP})
    return response


def install_error_handlers(app: FastAPI) -> None:
    for exc_type, status in _STATUS:
        app.add_exception_handler(exc_type, _handler(status))
    app.add_exception_handler(ConfigError, _config_error)
    app.add_exception_handler(StorageError, _storage_error)
    # Mirrors coglossa.worker.execute.DB_ERRORS (not imported: the API does not import the worker).
    app.add_exception_handler(OperationalError, _database_down)
    app.add_exception_handler(InterfaceError, _database_down)
    app.add_exception_handler(Exception, _internal)
