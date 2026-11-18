import httpx
import pytest

import smoke

SITE = "https://site.test"
API = "https://api.test"
MODAL_AUTH = {"Modal-Key": "wk-1", "Modal-Secret": "ws-1"}


SEEN = []
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


def make_transport(
    login_location="/", api_me_status=401, health_status=200, me_user="ci", no_token_status=401,
    modal_auth_on=True, untagged=(), robots_txt=ROBOTS_TXT,
):
    """`untagged`: (method, path) pairs on the front door answered without X-Robots-Tag."""

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        SEEN.append((request.method, host, path))
        if host == "site.test":
            response = front_door(request, path)
            if (request.method, path) not in untagged:
                response.headers["x-robots-tag"] = "noindex, nofollow"
            return response
        if host == "api.test":  # Modal proxy auth, in front of the API
            given = (request.headers.get("modal-key"), request.headers.get("modal-secret"))
            if modal_auth_on and given != ("wk-1", "ws-1"):
                return httpx.Response(no_token_status)
        if host == "api.test" and path == "/api/me":
            return httpx.Response(api_me_status)
        if host == "api.test" and path == "/api/health":
            return httpx.Response(health_status)
        return httpx.Response(404)

    def front_door(request: httpx.Request, path: str) -> httpx.Response:
        if path == "/login" and request.method == "POST":
            assert "origin" not in request.headers
            headers = {"location": login_location}
            if login_location == "/":
                headers["set-cookie"] = "cg_session=abc; Path=/; HttpOnly"
            return httpx.Response(303, headers=headers)
        if path == "/login":
            return httpx.Response(200, text="<!doctype html><title>Sign in</title>")
        if path == "/robots.txt":
            return httpx.Response(200, text=robots_txt)
        if path == "/api/me":
            if "cg_session=abc" in request.headers.get("cookie", ""):
                return httpx.Response(200, json={"user": me_user, "logout_url": "/logout"})
            return httpx.Response(401)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def run(transport):
    return smoke.run_checks(SITE, API, "ci", "pw", MODAL_AUTH, transport=transport)


@pytest.fixture
def smoke_env(monkeypatch):
    monkeypatch.setenv("COGLOSSA_SMOKE_PASSWORD", "pw")
    monkeypatch.setenv("COGLOSSA_MODAL_KEY", "wk-1")
    monkeypatch.setenv("COGLOSSA_MODAL_SECRET", "ws-1")


def test_all_checks_pass(capsys):
    results = run(make_transport())
    assert len(results) == 6 and all(ok for _, ok, _ in results)


def test_modal_may_refuse_with_403(capsys):
    results = run(make_transport(no_token_status=403))
    assert results[2] == ("the API refuses requests without the Modal token", True, "HTTP 403")


def test_an_api_open_without_the_modal_token_fails():
    results = run(make_transport(no_token_status=200))
    assert not results[2][1] and "without the Modal token (HTTP 200)" in results[2][2]
    assert all(ok for _, ok, _ in results[3:])  # the token is sent where it is needed


def test_check_3_fails_when_modal_proxy_auth_is_off():
    # Without Modal's proxy auth the API still answers a tokenless /api/me with 401 (its own
    # proxy-secret check), which must not pass for Modal's refusal.
    results = run(make_transport(modal_auth_on=False))
    assert not results[2][1] and "without the Modal token (HTTP 200)" in results[2][2]


def test_main_exits_zero_with_six_ok_lines(capsys, monkeypatch, smoke_env):
    monkeypatch.setattr(smoke, "_transport", make_transport())
    code = smoke.main(["--site", SITE, "--api-origin", API, "--user", "ci"])
    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert len([line for line in out if line.startswith("ok")]) == 6


def test_api_answering_without_secret_fails(capsys, monkeypatch, smoke_env):
    monkeypatch.setattr(smoke, "_transport", make_transport(api_me_status=200))
    code = smoke.main(["--site", SITE, "--api-origin", API, "--user", "ci"])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in out and "the API answers without the proxy secret" in out


def test_failed_login_fails_check_one(capsys, monkeypatch, smoke_env):
    monkeypatch.setattr(smoke, "_transport", make_transport(login_location="/login?failed=1"))
    code = smoke.main(["--site", SITE, "--api-origin", API, "--user", "ci"])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in out and "sign in" in out


def test_wrong_user_fails():
    results = run(make_transport(me_user="someone"))
    assert not results[1][1]


def test_missing_password_is_an_error(monkeypatch, smoke_env):
    monkeypatch.delenv("COGLOSSA_SMOKE_PASSWORD", raising=False)
    with pytest.raises(SystemExit, match="^COGLOSSA_SMOKE_PASSWORD is not set$"):
        smoke.main(["--site", SITE, "--api-origin", API, "--user", "ci"])


def test_a_missing_modal_token_is_an_error(monkeypatch, smoke_env):
    monkeypatch.delenv("COGLOSSA_MODAL_KEY")
    monkeypatch.delenv("COGLOSSA_MODAL_SECRET")
    with pytest.raises(SystemExit, match="^COGLOSSA_MODAL_KEY, COGLOSSA_MODAL_SECRET are not set$"):
        smoke.main(["--site", SITE, "--api-origin", API, "--user", "ci"])


def test_login_redirect_is_not_followed():
    SEEN.clear()
    run(make_transport())
    assert ("GET", "site.test", "/") not in SEEN


# CGL-85: the preview keeps out of search on every kind of response the front door sends.
ROBOTS_CHECK = "the preview keeps out of search"


def test_the_preview_keeps_out_of_search():
    SEEN.clear()
    results = run(make_transport())
    expected = "robots.txt disallows everything; X-Robots-Tag on all"
    assert results[5] == (ROBOTS_CHECK, True, expected)
    assert ("GET", "site.test", "/robots.txt") in SEEN
    assert ("GET", "site.test", "/login") in SEEN


@pytest.mark.parametrize(
    ("untagged", "named"),
    [
        (("GET", "/robots.txt"), "robots.txt"),
        (("GET", "/login"), "the sign-in page"),
        (("POST", "/login"), "the sign-in redirect"),
        (("GET", "/api/me"), "a forwarded API call"),
    ],
)
def test_a_response_without_x_robots_tag_fails(untagged, named):
    results = run(make_transport(untagged=(untagged,)))
    name, ok, detail = results[5]
    assert name == ROBOTS_CHECK and not ok
    assert detail == f"no X-Robots-Tag: noindex on {named}"
    assert all(ok for _, ok, _ in results[:5])


@pytest.mark.parametrize(
    "robots_txt", ["User-agent: *\nAllow: /\n", "", "User-agent: *\nDisallow: /private\n"]
)
def test_a_robots_txt_that_lets_crawlers_in_fails(robots_txt):
    results = run(make_transport(robots_txt=robots_txt))
    assert results[5] == (ROBOTS_CHECK, False, "robots.txt does not disallow everything")


def test_the_robots_check_names_a_response_it_never_got():
    # Check 1's request fails: check 6 still runs and names the response it did not get.
    inner = make_transport()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise httpx.ConnectError("down")
        return inner.handle_request(request)

    results = run(httpx.MockTransport(handler))
    assert not results[0][1]
    assert results[5] == (ROBOTS_CHECK, False, "no X-Robots-Tag: noindex on the sign-in redirect")
