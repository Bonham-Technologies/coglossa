import asyncio
import json

import pytest
from coglossa import catalog
from coglossa.testing.seed import seed_run

from coglossa_api.proxy import PROXY_SECRET_HEADER, ProxySecretMiddleware

SECRET = "s3cret-" * 5
AUTH = {"mode": "trusted_header", "proxy_secret_env": "COGLOSSA_PROXY_SECRET"}
SIGNED_IN = {"X-Remote-User": "ana"}
WITH_SECRET = {**SIGNED_IN, PROXY_SECRET_HEADER: SECRET}


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("COGLOSSA_PROXY_SECRET", SECRET)


async def test_requests_without_the_secret_are_refused(secret_env, make_api, tmp_path):
    web_dir = tmp_path / "dist"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<title>Coglossa</title>", encoding="utf-8")
    async with make_api(auth=AUTH, api={"web_dir": str(web_dir), "progress_poll_s": 0.02}) as api:
        client = api.client
        missing = await client.get("/api/me", headers=SIGNED_IN)
        assert missing.status_code == 401
        assert "proxy" in missing.json()["detail"]
        assert missing.headers["x-frame-options"] == "DENY"  # CGL-35: refusals are secured too
        wrong = await client.get("/api/me", headers={**SIGNED_IN, PROXY_SECRET_HEADER: "nope"})
        assert wrong.status_code == 401
        ok = await client.get("/api/me", headers=WITH_SECRET)
        assert ok.status_code == 200
        assert ok.json()["user"] == "ana"
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.head("/api/health")).status_code == 200  # CGL-43
        assert (await client.get("/")).status_code == 401
        assert (await client.head("/")).status_code == 401
        assert (await client.get("/", headers={PROXY_SECRET_HEADER: SECRET})).status_code == 200


async def test_the_progress_stream_still_streams(secret_env, make_api, db):
    seeded = await seed_run(db)
    await catalog.set_team(db, seeded.project_id, "desk")  # the signed-in user's team (CGL-26)
    async with make_api(auth={**AUTH, "groups_header": "X-Remote-Groups"}) as api:
        refused = await api.client.get(f"/api/runs/{seeded.run_id}/events", headers=SIGNED_IN)
        assert refused.status_code == 401
        async with asyncio.timeout(5):
            response = await api.client.get(
                f"/api/runs/{seeded.run_id}/events",
                headers={**WITH_SECRET, "X-Remote-Groups": "desk"},
            )
    assert response.headers["content-type"].startswith("text/event-stream")
    data = next(line for line in response.text.splitlines() if line.startswith("data: "))
    assert json.loads(data[len("data: ") :])["state"] == "complete"


async def test_the_middleware_passes_chunks_on_as_they_come():
    """httpx's in-process transport collects whole bodies, so check the streaming at the ASGI
    level: the first chunk must reach the client while the app is still producing the rest."""
    release = asyncio.Event()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        await release.wait()
        await send({"type": "http.response.body", "body": b"last", "more_body": False})

    sent = []
    first = asyncio.Event()

    async def send(message):
        sent.append(message)
        if message.get("body") == b"first":
            first.set()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "path": "/api/runs/x/events",
        "headers": [(PROXY_SECRET_HEADER.lower().encode(), SECRET.encode())],
    }
    task = asyncio.create_task(ProxySecretMiddleware(app, SECRET)(scope, receive, send))
    async with asyncio.timeout(5):
        await first.wait()
    assert not task.done()  # still streaming
    release.set()
    await task
    assert [m.get("body") for m in sent[1:]] == [b"first", b"last"]


async def test_other_protocols_pass_through():
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    await ProxySecretMiddleware(app, SECRET)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]


async def test_without_the_setting_nothing_changes(api):
    response = await api.client.get("/api/me")
    assert response.status_code == 200
    assert response.json() == {
        "user": None,
        "admin": True,
        "teams": [],
        "max_upload_mb": 200,
        "logout_url": None,
    }
