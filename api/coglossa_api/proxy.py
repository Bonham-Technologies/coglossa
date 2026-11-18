"""Spec (hosted preview) §4.2: refuse requests that did not come through the authenticating
proxy. A pure ASGI middleware, not BaseHTTPMiddleware, so streamed responses (the progress
events) pass through unbuffered."""

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

PROXY_SECRET_HEADER = "X-Coglossa-Proxy-Secret"
OPEN_PATHS = frozenset({"/api/health"})  # liveness checks never carry the secret


class ProxySecretMiddleware:
    def __init__(self, app: ASGIApp, secret: str) -> None:
        self.app = app
        self._secret = secret.encode()
        self._header = PROXY_SECRET_HEADER.lower().encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        given = next((v for k, v in scope["headers"] if k == self._header), b"")
        if hmac.compare_digest(given, self._secret):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"detail": "this address only answers requests from the Coglossa sign-in proxy"},
            status_code=401,
        )
        await response(scope, receive, send)
