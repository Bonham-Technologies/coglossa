"""Serving the web app (spec §2.1): the API hands out the SPA's built files, so a site runs one
web endpoint. Every path outside /api returns index.html and the app routes on the client;
built assets carry content hashes, so browsers may keep them for a year. The files hold no
data, so they are served without the trusted header (the proxy in front adds it anyway)."""

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

IMMUTABLE = "public, max-age=31536000, immutable"


class _Assets(StaticFiles):
    async def get_response(self, path: str, scope: Scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = IMMUTABLE
        return response


class _WebRoute(APIRoute):
    """HEAD never matches under /api, so a HEAD on an API route still gets that route's own
    answer (405 where it is GET-only) instead of the app's index."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] == "http" and scope["method"] == "HEAD":
            path = scope["path"].lstrip("/")
            if path == "api" or path.startswith("api/"):
                return Match.NONE, {}
        return super().matches(scope)


def missing_index(web_dir: Path) -> str | None:
    if (web_dir / "index.html").is_file():
        return None
    return (
        f"api.web_dir {web_dir} has no index.html; build the web app (cd web && pnpm build) "
        "or remove api.web_dir"
    )


def mount_web(app: FastAPI, web_dir: Path) -> None:
    """Mount last, after every /api router: the catch-all must not shadow them."""
    root = web_dir.resolve()
    index = root / "index.html"
    if (root / "assets").is_dir():
        app.mount("/assets", _Assets(directory=root / "assets"), name="assets")

    router = APIRouter(route_class=_WebRoute)

    @router.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def web_app(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        try:
            candidate = (root / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
        except (OSError, ValueError):  # a null byte or an over-long name is not a file
            pass
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    app.include_router(router)
