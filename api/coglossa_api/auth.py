"""Spec §3 `auth` and CGL-26: who is asking, and what they may see. `none`: everyone is an
admin. `trusted_header`: a proxy authenticates people and passes the name in `auth.header`
(and, when `auth.groups_header` is set, their groups); the people file adds teams and admins.
The name goes into the feedback log."""

import contextlib
import uuid
from typing import Annotated

from coglossa.access import EVERYONE, Viewer, check_visible, viewer_for
from coglossa.people import nfc, strip_name
from fastapi import Depends, HTTPException, Request

from coglossa_api.context import get_context

_SCOPED = ("run_id", "document_id", "project_id")


def identity_header(request: Request, name: str) -> str | None:
    """A header the proxy sets, as text (CGL-31). Starlette decodes header bytes as latin-1;
    proxies send names in UTF-8, so the bytes are read as UTF-8 when they are valid UTF-8 and
    kept as latin-1 otherwise. NFC, so a name compares and is stored one way."""
    value = request.headers.get(name)
    if value is None:
        return None
    with contextlib.suppress(UnicodeDecodeError):  # not UTF-8: the latin-1 reading stands
        value = value.encode("latin-1").decode("utf-8")
    return nfc(value)


def current_viewer(request: Request) -> Viewer:
    ctx = get_context(request)
    auth = ctx.cfg.auth
    if auth.mode == "none":
        return EVERYONE
    user = strip_name(identity_header(request, auth.header) or "")
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"missing {auth.header} header: sign in through the proxy in front of Coglossa",
        )
    sent = identity_header(request, auth.groups_header) if auth.groups_header else None
    groups = (sent or "").split(",")
    return viewer_for(user, groups, ctx.people, auth.admin_group, teams=auth.team_filter())


ViewerDep = Annotated[Viewer, Depends(current_viewer)]


def current_user(viewer: ViewerDep) -> str | None:
    return viewer.name


User = Annotated[str | None, Depends(current_user)]


async def guard_ids(request: Request, viewer: ViewerDep) -> None:
    """Every route naming a run, document or project: what the viewer cannot see answers as a
    missing id does. A router dependency, so no route can forget it. FastAPI reads and parses
    the body first (a body that is not JSON answers 422 whatever the id), then this runs before
    the body is checked against the route's model, so a bad body never tells an outsider that
    the set exists."""
    if viewer.admin:
        return
    ids: dict[str, uuid.UUID] = {}
    for key in _SCOPED:
        value = request.path_params.get(key)
        if value is None:
            continue
        try:
            ids[key] = uuid.UUID(str(value))
        except ValueError:
            return  # the route answers 422 for a malformed id
    if ids:
        async with get_context(request).db.session() as s:
            await check_visible(s, viewer, **ids)


def require_admin(viewer: ViewerDep) -> None:
    if not viewer.admin:
        raise HTTPException(status_code=403, detail="only an administrator can see or change this")
