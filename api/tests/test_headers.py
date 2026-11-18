"""CGL-35: every response carries the security headers, so a site's proxy need not add them."""

from coglossa.testing.seed import seed_run

from coglossa_api.headers import CSP, EXPORT_CSP

INDEX = "<!doctype html><title>Coglossa</title><div id=root></div>"


def assert_secured(response, csp: str = CSP) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers.get_list("content-security-policy") == [csp]


def test_the_app_policy_allows_only_its_own_origin():
    directives = dict(d.strip().split(" ", 1) for d in CSP.split(";"))
    assert directives["default-src"] == "'self'"
    for name in ("script-src", "style-src", "connect-src"):
        assert directives[name] == "'self'", name
    # Vite inlines small fonts and images as data: URLs; neither can run code
    assert directives["font-src"] == "'self' data:"
    assert directives["img-src"] == "'self' data:"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["object-src"] == "'none'"
    assert "unsafe" not in CSP


async def test_the_web_app_api_and_errors_carry_the_headers(make_api, tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app-1a2b.js").write_text("console.log(1)", encoding="utf-8")
    async with make_api(api={"web_dir": str(root)}) as api:
        for path in ("/", "/projects/abc", "/assets/app-1a2b.js", "/api/health", "/api/projects"):
            response = await api.client.get(path)
            assert response.status_code == 200, path
            assert_secured(response)
        for path in ("/api/nope", "/assets/missing.js"):
            response = await api.client.get(path)
            assert response.status_code == 404, path
            assert_secured(response)
        refused = await api.client.post("/projects/abc")
        assert refused.status_code == 405
        assert_secured(refused)


async def test_the_html_export_gets_a_policy_that_runs_nothing(api, db):
    seeded = await seed_run(db)
    page = await api.client.get(f"/api/runs/{seeded.run_id}/exports/html")
    assert page.status_code == 200
    assert "<style>" in page.text  # the export's own inline styles, which the policy allows
    assert_secured(page, EXPORT_CSP)
    directives = dict(d.strip().split(" ", 1) for d in EXPORT_CSP.split(";") if " " in d.strip())
    assert directives["default-src"] == "'none'"
    assert directives["style-src"] == "'unsafe-inline'"
    assert "sandbox" in [d.strip() for d in EXPORT_CSP.split(";")]
    report = await api.client.get(f"/api/runs/{seeded.run_id}/exports/json")
    assert_secured(report)
