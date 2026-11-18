from urllib.parse import urlsplit

from coglossa.config import config_hash
from coglossa.schema import CheckStatus, DoctorCheck, DoctorReport
from coglossa.scoring.tiers import LanguageTiers

from coglossa_api.routes import system as system_module


async def test_doctor_passes_the_probe_flag(api, monkeypatch):
    seen = []

    async def fake_doctor(path, *, probe_context):
        seen.append((path, probe_context))
        return DoctorReport.of([DoctorCheck(name="config", status=CheckStatus.PASS, message="ok")])

    monkeypatch.setattr(system_module, "run_doctor", fake_doctor)
    report = (await api.client.get("/api/system/doctor")).json()
    assert report == {
        "checks": [{"name": "config", "status": "pass", "message": "ok", "fix": ""}],
        "ok": True,
    }
    await api.client.get("/api/system/doctor", params={"probe": "true"})
    assert [p for _, p in seen] == [False, True]
    assert seen[0][0].name == "coglossa.yaml"


async def test_doctor_for_real(api):
    report = (await api.client.get("/api/system/doctor")).json()
    statuses = {c["name"]: c["status"] for c in report["checks"]}
    assert statuses["database"] == "pass"
    assert statuses["storage"] == "pass"


async def test_doctor_does_not_reveal_the_database_password(api, pg_url):
    parts = urlsplit(pg_url)
    assert parts.password
    response = await api.client.get("/api/system/doctor")
    # the test password equals the user name, so look for the credentials, not the bare word
    assert f"{parts.username}:{parts.password}@" not in response.text
    assert pg_url not in response.text


async def test_config_view(api):
    view = (await api.client.get("/api/system/config")).json()
    assert view["path"].endswith("coglossa.yaml")
    assert view["hash"] == config_hash(api.cfg)
    assert view["config"]["roles"]["reviewer"]["model"] == "model-a"
    assert view["config"]["auth"] == {
        "mode": "none",
        "header": "X-Remote-User",
        "groups_header": None,
        "admin_group": "coglossa-admins",
        "team_groups_prefix": None,
        "team_groups": None,
        "people_file": None,
        "proxy_secret_env": None,
        "logout_url": None,
    }


async def test_tier_table(api):
    tiers = LanguageTiers.load(None)
    table = (await api.client.get("/api/system/tiers")).json()
    assert table["default"] == tiers.default.value
    assert table["languages"] == {k: v.value for k, v in sorted(tiers.languages.items())}
    assert table["pairs"] == {f"{s}>{t}": v.value for (s, t), v in sorted(tiers.pairs.items())}


async def test_me_offers_the_proxy_sign_out_address(make_api):
    auth = {"mode": "trusted_header", "logout_url": "/logout"}
    async with make_api(auth=auth) as api:
        me = await api.client.get("/api/me", headers={"X-Remote-User": "ana"})
    assert me.json() == {
        "user": "ana",
        "admin": False,
        "teams": [],
        "max_upload_mb": 200,
        "logout_url": "/logout",
    }
