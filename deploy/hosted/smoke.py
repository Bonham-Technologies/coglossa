"""Smoke test for the hosted preview.

Usage: COGLOSSA_SMOKE_PASSWORD=... COGLOSSA_MODAL_KEY=... COGLOSSA_MODAL_SECRET=... \
           python smoke.py --site URL --api-origin URL --user NAME
Exits 0 when every check passes and prints one line per check.

The API endpoint requires Modal proxy auth: Modal itself refuses a request without the
workspace proxy-auth token (Modal-Key/Modal-Secret) with 401 or 403, before the API runs.
Check 3 asks for the health check without the token, which the API itself would answer
200. Checks 4 and 5 send the token, so they reach the API: check 4 then proves the API's own
proxy-secret check, and check 5 that the health check answers. Check 6 (CGL-85) asks for
robots.txt and the sign-in page and checks that they, the sign-in redirect and the forwarded
call from check 2 all carry X-Robots-Tag: noindex, and that robots.txt disallows everything.
"""

import argparse
import os
import sys

import httpx

# Tests replace this with an httpx.MockTransport.
_transport = None


def _cookie(response: httpx.Response, name: str) -> str | None:
    for value in response.headers.get_list("set-cookie"):
        first = value.split(";", 1)[0]
        key, _, val = first.partition("=")
        if key.strip() == name and val:
            return val
    return None


def run_checks(site, api_origin, user, password, modal_auth, transport=None):
    """Return (name, ok, detail) for each check, in order. `modal_auth` is the Modal-Key and
    Modal-Secret headers."""
    site, api_origin = site.rstrip("/"), api_origin.rstrip("/")
    results: list[tuple[str, bool, str]] = []
    with httpx.Client(transport=transport, follow_redirects=False, timeout=60) as client:
        # 1. Sign in. No Origin header is sent, so the front door's origin check does not apply.
        session = None
        signin = api_me = None  # kept for check 6
        try:
            r = signin = client.post(f"{site}/login", data={"name": user, "password": password})
            session = _cookie(r, "cg_session")
            location = r.headers.get("location", "")
            ok = r.status_code == 303 and location == "/" and session is not None
            detail = f"HTTP {r.status_code}, location {location!r}, cookie {'set' if session else 'missing'}"
        except httpx.HTTPError as exc:
            ok, detail = False, f"request failed: {exc}"
        results.append(("sign in through the front door", ok, detail))

        # 2. The session works through the proxy.
        try:
            r = api_me = client.get(
                f"{site}/api/me", headers={"Cookie": f"cg_session={session or ''}"}
            )
            body = r.json() if r.status_code == 200 else {}
            got = body.get("user") if isinstance(body, dict) else None
            ok = r.status_code == 200 and got == user
            detail = f"HTTP {r.status_code}, user {got!r}"
        except (httpx.HTTPError, ValueError) as exc:
            ok, detail = False, f"request failed: {exc}"
        results.append(("the session reaches the API", ok, detail))

        # 3. Modal refuses callers without the proxy-auth token (401 or 403, by Modal's choice).
        # Health is used because the API answers it 200 without any secret, so a 401 or 403
        # can only come from Modal.
        try:
            r = client.get(f"{api_origin}/api/health")
            ok = r.status_code in (401, 403)
            detail = (
                f"HTTP {r.status_code}"
                if ok
                else f"the API answers without the Modal token (HTTP {r.status_code})"
            )
        except httpx.HTTPError as exc:
            ok, detail = False, f"request failed: {exc}"
        results.append(("the API refuses requests without the Modal token", ok, detail))

        # 4. Past Modal, the API refuses callers without the proxy secret.
        try:
            r = client.get(f"{api_origin}/api/me", headers=modal_auth)
            ok = r.status_code == 401
            detail = (
                "HTTP 401"
                if ok
                else f"the API answers without the proxy secret (HTTP {r.status_code})"
            )
        except httpx.HTTPError as exc:
            ok, detail = False, f"request failed: {exc}"
        results.append(("the API refuses requests without the proxy secret", ok, detail))

        # 5. Health needs no proxy secret.
        try:
            r = client.get(f"{api_origin}/api/health", headers=modal_auth)
            ok, detail = r.status_code == 200, f"HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            ok, detail = False, f"request failed: {exc}"
        results.append(("the API health check answers", ok, detail))

        # 6. The preview is private, so it keeps out of search (CGL-85): robots.txt disallows
        # everything, and X-Robots-Tag is on each kind of response the front door sends: one it
        # answers itself (robots.txt, the sign-in redirect), a file it lets through (the sign-in
        # page) and a forwarded API call. Vercel may treat each kind differently.
        try:
            robots = client.get(f"{site}/robots.txt")
            page = client.get(f"{site}/login")
            seen = [
                ("robots.txt", robots),
                ("the sign-in page", page),
                ("the sign-in redirect", signin),
                ("a forwarded API call", api_me),
            ]
            untagged = [
                name
                for name, resp in seen
                if resp is None or "noindex" not in resp.headers.get("x-robots-tag", "")
            ]
            lines = [line.strip() for line in robots.text.splitlines()]
            if robots.status_code != 200 or not {"User-agent: *", "Disallow: /"} <= set(lines):
                ok, detail = False, "robots.txt does not disallow everything"
            elif untagged:
                ok, detail = False, f"no X-Robots-Tag: noindex on {', '.join(untagged)}"
            else:
                ok, detail = True, "robots.txt disallows everything; X-Robots-Tag on all"
        except httpx.HTTPError as exc:
            ok, detail = False, f"request failed: {exc}"
        results.append(("the preview keeps out of search", ok, detail))
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--api-origin", required=True)
    parser.add_argument("--user", required=True)
    args = parser.parse_args(argv)
    names = ["COGLOSSA_SMOKE_PASSWORD", "COGLOSSA_MODAL_KEY", "COGLOSSA_MODAL_SECRET"]
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        sys.exit(f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set")
    password, key, secret = (os.environ[name] for name in names)
    modal_auth = {"Modal-Key": key, "Modal-Secret": secret}
    results = run_checks(
        args.site, args.api_origin, args.user, password, modal_auth, transport=_transport
    )
    for i, (name, ok, detail) in enumerate(results, 1):
        print(f"{'ok  ' if ok else 'FAIL'} {i}. {name}: {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
