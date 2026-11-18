from pathlib import Path

import pytest

from coglossa_api.web import missing_index

INDEX = "<!doctype html><title>Coglossa</title><div id=root></div>"


@pytest.fixture
def web_dir(tmp_path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app-1a2b.js").write_text("console.log(1)", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / "theme-init.js").write_text(
        "document.documentElement.dataset.theme='dark'", encoding="utf-8"
    )
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    return root


async def test_the_app_and_its_routes_return_index(make_api, web_dir):
    async with make_api(api={"web_dir": str(web_dir)}) as api:
        for path in ("/", "/projects/abc", "/runs/1/review?flagged=true"):
            response = await api.client.get(path)
            assert response.status_code == 200, path
            assert response.text == INDEX
            assert response.headers["content-type"].startswith("text/html")
            assert response.headers["cache-control"] == "no-cache"


async def test_assets_are_cached_and_top_level_files_served(make_api, web_dir):
    async with make_api(api={"web_dir": str(web_dir)}) as api:
        asset = await api.client.get("/assets/app-1a2b.js")
        assert asset.status_code == 200 and asset.text == "console.log(1)"
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert (await api.client.get("/assets/missing.js")).status_code == 404
        icon = await api.client.get("/favicon.svg")
        assert icon.status_code == 200 and icon.text == "<svg/>"


async def test_api_paths_stay_json_and_nothing_escapes(make_api, web_dir):
    async with make_api(api={"web_dir": str(web_dir)}) as api:
        unknown = await api.client.get("/api/nope")
        assert unknown.status_code == 404 and unknown.json() == {"detail": "Not Found"}
        assert (await api.client.get("/api/health")).json()["status"] == "ok"
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
            response = await api.client.get(path)
            assert response.status_code in (200, 404), path
            assert response.text != "no", path
        assert (await api.client.post("/projects/abc")).status_code == 405


async def test_unusable_paths_fall_back_to_index(make_api, web_dir):
    async with make_api(api={"web_dir": str(web_dir)}) as api:
        for path in ("/a%00b", "/" + "a" * 5000):
            response = await api.client.get(path)
            assert response.status_code == 200, path
            assert response.text == INDEX, path


async def test_a_build_without_assets_still_serves_index(make_api, tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    async with make_api(api={"web_dir": str(root)}) as api:
        assert (await api.client.get("/")).text == INDEX


async def test_the_web_app_needs_no_user(make_api, web_dir):
    async with make_api(api={"web_dir": str(web_dir)}, auth={"mode": "trusted_header"}) as api:
        assert (await api.client.get("/")).status_code == 200
        assert (await api.client.get("/api/me")).status_code == 401


async def test_without_web_dir_there_is_no_web_app(make_api):
    async with make_api() as api:
        assert (await api.client.get("/")).status_code == 404


def test_missing_index(tmp_path):
    assert missing_index(tmp_path / "nowhere") == (
        f"api.web_dir {tmp_path / 'nowhere'} has no index.html; build the web app "
        "(cd web && pnpm build) or remove api.web_dir"
    )
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    assert missing_index(tmp_path) is None


async def test_head_matches_get_for_the_app_and_assets(make_api, web_dir):
    async with make_api(api={"web_dir": str(web_dir)}) as api:
        for path in ("/", "/projects/abc", "/assets/app-1a2b.js", "/favicon.svg"):
            got = await api.client.get(path)
            head = await api.client.head(path)
            assert head.status_code == 200, path
            assert head.content == b"", path
            for name in ("content-type", "cache-control", "content-length"):
                assert head.headers.get(name) == got.headers.get(name), (path, name)


async def test_head_under_api_is_not_the_app(make_api, web_dir):
    async with make_api(api={"web_dir": str(web_dir)}) as api:
        assert (await api.client.head("/api/nope")).status_code == 404
        assert (await api.client.head("/api/openapi.json")).status_code == 405
        assert (await api.client.head("/api/health")).status_code == 200


async def test_unhashed_files_are_revalidated_and_assets_stay_immutable(make_api, web_dir):
    async with make_api(api={"web_dir": str(web_dir)}) as api:
        for path in ("/theme-init.js", "/favicon.svg"):
            for send in (api.client.get, api.client.head):
                response = await send(path)
                assert response.status_code == 200, path
                assert response.headers["cache-control"] == "no-cache", path
        asset = await api.client.get("/assets/app-1a2b.js")
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
