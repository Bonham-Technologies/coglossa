"""CGL-35: the security headers on every response, the web app's files included, so a site's
proxy does not have to add them (operator guide, "Security headers"). A pure ASGI middleware,
like ProxySecretMiddleware, so the progress stream passes through unbuffered."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The web app loads nothing from elsewhere: its scripts, styles and fonts are built files
# (theme-init.js is a file, not inline, for this policy), and it talks only to this API. Vite
# inlines small fonts and images as data: URLs, which cannot run code.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "font-src 'self' data:",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

# The HTML export is a download with its own inline styles. If someone opens it from this
# origin, it may style itself and nothing more: no scripts, no requests, no forms.
EXPORT_CSP = "; ".join(
    [
        "default-src 'none'",
        "style-src 'unsafe-inline'",
        "img-src data:",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "sandbox",
    ]
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware:
    """Adds the headers to every HTTP response. A route that sets its own
    Content-Security-Policy (the HTML export) keeps it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                if "content-security-policy" not in headers:
                    headers["Content-Security-Policy"] = CSP
            await send(message)

        await self.app(scope, receive, send_secured)
