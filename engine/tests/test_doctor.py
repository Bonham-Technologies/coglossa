import json
import uuid

import boto3
import pytest
import yaml
from moto import mock_aws
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from coglossa.cli import main
from coglossa.doctor import run_doctor
from coglossa.llm.types import OutputTruncatedError, PermanentBackendError
from coglossa.schema import CheckStatus
from coglossa.storage import LocalStorage, StorageError
from coglossa.store import runs
from coglossa.testing.fake_backend import FakeBackend
from coglossa.testing.fake_openai import FakeOpenAIServer
from coglossa.testing.fake_scorer import FakeScorerServer
from coglossa.testing.postgres import create_test_database, drop_test_database


def _config(tmp_path, server_url, **extra):
    cfg = {
        "backends": {"local": {"base_url": server_url, "max_concurrency": 2, "timeout_s": 5}},
        "models": {"fake-model": {"backend": "local", "context_window": 4096}},
        "roles": {
            "literal_translator": {"model": "fake-model"},
            "contextual_translator": {"model": "fake-model"},
            "reviewer": {"model": "fake-model"},
            "profiler_guide": {"model": "fake-model"},
        },
        "storage": {"path": str(tmp_path / "files")},
        **extra,
    }
    path = tmp_path / "coglossa.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def _by_name(report):
    return {c.name: c for c in report.checks}


@pytest.fixture
def server():
    with FakeOpenAIServer() as srv:
        yield srv


async def test_a_healthy_installation(tmp_path, server, monkeypatch, pg_url):
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    with FakeScorerServer() as scorer:
        path = _config(tmp_path, server.base_url, scorer={"url": scorer.base_url})
        report = await run_doctor(path)
    checks = _by_name(report)
    assert report.ok
    for name in (
        "config",
        "prompts",
        "tiers",
        "backend local",
        "model fake-model",
        "scorer",
        "database",
        "storage",
        "leftover files",
    ):
        assert checks[name].status is CheckStatus.PASS, (name, checks[name])
    assert "fake-model" in checks["backend local"].message
    assert "0010" in checks["database"].message
    assert f":{make_url(pg_url).password}@" not in checks["database"].message  # redacted


async def test_model_not_served(tmp_path, server, monkeypatch):
    monkeypatch.delenv("COGLOSSA_DATABASE_URL", raising=False)
    path = _config(
        tmp_path,
        server.base_url,
        models={"qwen": {"backend": "local", "context_window": 4096}},
        roles={
            r: {"model": "qwen"}
            for r in ("literal_translator", "contextual_translator", "reviewer", "profiler_guide")
        },
    )
    report = await run_doctor(path)
    model = _by_name(report)["model qwen"]
    assert model.status is CheckStatus.FAIL
    assert model.fix == "set models.qwen.name to one of: fake-model"
    assert not report.ok
    assert _by_name(report)["database"].status is CheckStatus.WARN


async def test_unreachable_backend(tmp_path):
    report = await run_doctor(_config(tmp_path, "http://127.0.0.1:9/v1"), probe_context=False)
    backend = _by_name(report)["backend local"]
    assert backend.status is CheckStatus.FAIL and "base_url" in backend.fix
    assert "model fake-model" not in _by_name(report)


async def test_context_window_larger_than_the_server_accepts(tmp_path):
    with FakeOpenAIServer(context_window=1000) as srv:
        report = await run_doctor(_config(tmp_path, srv.base_url))
    model = _by_name(report)["model fake-model"]
    assert model.status is CheckStatus.FAIL
    assert (
        "about 3686 tokens" in model.message
        and model.fix == "lower models.fake-model.context_window"
    )


async def test_no_probe_skips_the_context_check(tmp_path):
    with FakeOpenAIServer(context_window=1000) as srv:
        report = await run_doctor(_config(tmp_path, srv.base_url), probe_context=False)
    assert _by_name(report)["model fake-model"].status is CheckStatus.PASS


async def test_structured_output_not_honoured(tmp_path):
    backend = FakeBackend(name="local")
    backend.script("ping", "OK", "plain text, not json")
    report = await run_doctor(
        _config(tmp_path, "http://unused/v1"), backends_factory=lambda cfg: {"local": backend}
    )
    model = _by_name(report)["model fake-model"]
    assert model.status is CheckStatus.FAIL
    assert model.fix == "set models.fake-model.structured_output to json_object or none"


async def test_failed_completion_and_inconclusive_probe(tmp_path):
    backend = FakeBackend(name="local")
    backend.script("ping", PermanentBackendError("HTTP 404: no such model"))
    report = await run_doctor(
        _config(tmp_path, "http://unused/v1"), backends_factory=lambda cfg: {"local": backend}
    )
    assert "test completion failed" in _by_name(report)["model fake-model"].message
    backend = FakeBackend(name="local")
    backend.script("probe", PermanentBackendError("HTTP 400: odd"))
    report = await run_doctor(
        _config(tmp_path, "http://unused/v1"), backends_factory=lambda cfg: {"local": backend}
    )
    assert _by_name(report)["model fake-model"].status is CheckStatus.WARN


async def test_scorer_states(tmp_path, server):
    report = await run_doctor(_config(tmp_path, server.base_url), probe_context=False)
    assert _by_name(report)["scorer"].status is CheckStatus.WARN
    path = _config(
        tmp_path, server.base_url, scorer={"url": "http://127.0.0.1:9", "connect_timeout_s": 0.2}
    )
    report = await run_doctor(path, probe_context=False)
    assert _by_name(report)["scorer"].status is CheckStatus.FAIL
    with FakeScorerServer(qe_model=None) as scorer:
        path = _config(tmp_path, server.base_url, scorer={"url": scorer.base_url})
        report = await run_doctor(path, probe_context=False)
    assert _by_name(report)["scorer"].status is CheckStatus.WARN


async def test_database_states(tmp_path, server, monkeypatch, pg_admin):
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", "postgresql://u:secret@127.0.0.1:9/x")
    report = await run_doctor(_config(tmp_path, server.base_url), probe_context=False)
    db_check = _by_name(report)["database"]
    assert db_check.status is CheckStatus.FAIL and "secret" not in db_check.message
    url = create_test_database(pg_admin)
    try:
        monkeypatch.setenv("COGLOSSA_DATABASE_URL", url)
        report = await run_doctor(_config(tmp_path, server.base_url), probe_context=False)
        assert _by_name(report)["database"].fix == "run coglossa db upgrade"
    finally:
        drop_test_database(pg_admin, url)


async def test_storage_not_writable(tmp_path, server):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    path = _config(tmp_path, server.base_url, storage={"path": str(blocker / "sub")})
    report = await run_doctor(path, probe_context=False)
    assert _by_name(report)["storage"].status is CheckStatus.FAIL


async def test_leftover_files_are_listed_and_kept(tmp_path, server, monkeypatch, db, pg_url):
    """CGL-84: a folder under projects/ that no set owns, left when a delete could not remove
    its files, is reported; doctor never deletes it."""
    async with db.session() as s, s.begin():
        kept = await runs.get_or_create_project(s, "kept")
    orphan = uuid.uuid4()
    files = tmp_path / "files" / "projects"
    for owner in (kept.id, orphan):
        (files / str(owner) / "documents" / "d").mkdir(parents=True)
        (files / str(owner) / "documents" / "d" / "story.txt").write_text("x")
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    report = await run_doctor(_config(tmp_path, server.base_url), probe_context=False)
    check = _by_name(report)["leftover files"]
    assert check.status is CheckStatus.WARN and report.ok
    assert f"projects/{orphan}/" in check.message and str(kept.id) not in check.message
    assert "never deletes" in check.fix
    assert (files / str(orphan) / "documents" / "d" / "story.txt").exists()


async def test_leftover_files_need_the_database_and_storage(tmp_path, server, monkeypatch):
    monkeypatch.delenv("COGLOSSA_DATABASE_URL", raising=False)
    report = await run_doctor(_config(tmp_path, server.base_url), probe_context=False)
    assert "leftover files" not in _by_name(report)


async def test_leftover_files_listing_failure_warns(tmp_path, server, monkeypatch, pg_url):
    def cannot_list(self, prefix):
        raise StorageError(f"cannot list {prefix}: Permission denied")

    monkeypatch.setattr(LocalStorage, "list_prefixes", cannot_list)
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    report = await run_doctor(_config(tmp_path, server.base_url), probe_context=False)
    check = _by_name(report)["leftover files"]
    assert check.status is CheckStatus.WARN and "Permission denied" in check.message


async def test_leftover_files_database_failure_warns_in_one_line(
    tmp_path, server, monkeypatch, pg_url
):
    """A database error during the look shows the driver's one-line reason, not SQLAlchemy's
    multi-line text with the SQL and a docs link."""

    def cannot_list(self, prefix):
        raise OperationalError(
            "SELECT projects.id FROM projects", {}, Exception("server closed\nmore")
        )

    monkeypatch.setattr(LocalStorage, "list_prefixes", cannot_list)
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    report = await run_doctor(_config(tmp_path, server.base_url), probe_context=False)
    check = _by_name(report)["leftover files"]
    assert check.status is CheckStatus.WARN
    assert check.message == "cannot look for leftover files: server closed"


@pytest.fixture
def s3_env(monkeypatch):
    monkeypatch.setenv("COGLOSSA_S3_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("COGLOSSA_S3_SECRET_ACCESS_KEY", "test-secret")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="coglossa-files")
        yield


async def test_s3_storage_writable(tmp_path, server, s3_env):
    path = _config(tmp_path, server.base_url, storage={"kind": "s3", "bucket": "coglossa-files"})
    report = await run_doctor(path, probe_context=False)
    check = _by_name(report)["storage"]
    assert check.status is CheckStatus.PASS
    assert check.message == "s3://coglossa-files is writable"


async def test_s3_storage_missing_bucket(tmp_path, server, s3_env):
    path = _config(tmp_path, server.base_url, storage={"kind": "s3", "bucket": "no-such-bucket"})
    report = await run_doctor(path, probe_context=False)
    check = _by_name(report)["storage"]
    assert check.status is CheckStatus.FAIL
    assert check.fix == "fix storage (bucket, endpoint_url, keys)"


async def test_invalid_config_stops_early(tmp_path):
    path = tmp_path / "coglossa.yaml"
    path.write_text("backends: {}\nmodels: {}\n", encoding="utf-8")
    report = await run_doctor(path)
    assert [c.name for c in report.checks] == ["config"] and not report.ok


async def test_broken_prompt_override(tmp_path, server):
    prompts = tmp_path / "prompts" / "reconcile"
    prompts.mkdir(parents=True)
    (prompts / "v9.j2").write_text("{% if %}", encoding="utf-8")
    path = _config(tmp_path, server.base_url, prompts_dir="prompts")
    report = await run_doctor(path, probe_context=False)
    assert _by_name(report)["prompts"].status is CheckStatus.FAIL


async def test_config_warnings_are_reported(tmp_path, server):
    path = _config(
        tmp_path,
        server.base_url,
        roles={
            "literal_translator": {"model": "fake-model"},
            "contextual_translator": {"model": "fake-model"},
            "reviewer": {"model": "fake-model"},
            "profiler_guide": {"model": "fake-model"},
            "judge": {"model": "fake-model"},
        },
    )
    report = await run_doctor(path, probe_context=False)
    assert any(c.name == "config" and c.status is CheckStatus.WARN for c in report.checks)


def test_cli_text_and_json(tmp_path, server, capsys, monkeypatch):
    monkeypatch.delenv("COGLOSSA_DATABASE_URL", raising=False)
    path = _config(tmp_path, server.base_url)
    assert main(["doctor", "--config", str(path), "--no-probe"]) == 0
    out = capsys.readouterr().out
    assert (
        "PASS  backend local" in out
        and "WARN  database" in out
        and "fix: set COGLOSSA_DATABASE_URL" in out
    )
    assert main(["doctor", "--config", str(path), "--json", "--no-probe"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["checks"][0]["name"] == "config"
    bad = _config(tmp_path, "http://127.0.0.1:9/v1")
    assert main(["doctor", "--config", str(bad), "--no-probe"]) == 1


async def test_an_unexpected_error_fails_one_check_and_the_rest_still_run(
    tmp_path, server, monkeypatch
):
    def boom(*_a, **_k):
        raise RuntimeError("kaput")

    monkeypatch.setattr("coglossa.doctor.LanguageTiers.load", boom)
    monkeypatch.setattr("coglossa.doctor.migration_state", boom)
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", "postgresql://u:p@127.0.0.1:9/x")
    report = await run_doctor(_config(tmp_path, server.base_url), probe_context=False)
    checks = _by_name(report)
    assert checks["tiers"].status is CheckStatus.FAIL
    assert checks["tiers"].message == "internal error: RuntimeError: kaput"
    assert checks["database"].status is CheckStatus.FAIL and "kaput" in checks["database"].message
    assert checks["model fake-model"].status is CheckStatus.PASS
    assert checks["storage"].status is CheckStatus.PASS and not report.ok


async def test_one_broken_model_does_not_hide_the_others(tmp_path):
    class Flaky(FakeBackend):
        async def chat(self, request):
            if request.model == "bad":
                raise RuntimeError("boom")
            return await super().chat(request)

    backend = Flaky(name="local", models=("bad", "fake-model"))
    cfg = _config(
        tmp_path,
        "http://unused/v1",
        models={
            "bad": {"backend": "local", "context_window": 4096},
            "fake-model": {"backend": "local", "context_window": 4096},
        },
    )
    report = await run_doctor(
        cfg, probe_context=False, backends_factory=lambda c: {"local": backend}
    )
    checks = _by_name(report)
    assert checks["model bad"].status is CheckStatus.FAIL and "boom" in checks["model bad"].message
    assert checks["model fake-model"].status is CheckStatus.PASS


async def test_truncated_test_completion_passes(tmp_path):
    backend = FakeBackend(name="local")
    backend.script("ping", OutputTruncatedError("cut at max_tokens"), '{"ok": true}')
    report = await run_doctor(
        _config(tmp_path, "http://unused/v1"),
        probe_context=False,
        backends_factory=lambda cfg: {"local": backend},
    )
    assert _by_name(report)["model fake-model"].status is CheckStatus.PASS


async def test_probes_send_the_models_extra_body(tmp_path):
    backend = FakeBackend(name="local")
    path = _config(tmp_path, "http://unused/v1")
    cfg = yaml.safe_load(path.read_text("utf-8"))
    cfg["models"]["fake-model"]["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    await run_doctor(path, backends_factory=lambda cfg: {"local": backend})
    assert len(backend.requests) >= 3  # test completion, structured output, context probe
    assert all(
        r.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
        for r in backend.requests
    )


async def test_worker_line_only_with_a_kick_url(tmp_path, server):
    plain = _by_name(await run_doctor(_config(tmp_path, server.base_url)))
    assert "worker" not in plain
    path = _config(tmp_path, server.base_url, worker={"kick_url": "https://k/"})
    kicking = _by_name(await run_doctor(path))
    assert kicking["worker"].status is CheckStatus.PASS
    assert kicking["worker"].message == "kicks https://k/ when a job is queued"


NAMED_USERS = {"mode": "trusted_header", "header": "X-Remote-User"}


async def _auth_check(tmp_path, monkeypatch, bind, **auth):
    """CGL-61: the `auth` check, with COGLOSSA_HTTP_BIND (where compose publishes the app) set
    to `bind`, or unset when None."""
    if bind is None:
        monkeypatch.delenv("COGLOSSA_HTTP_BIND", raising=False)
    else:
        monkeypatch.setenv("COGLOSSA_HTTP_BIND", bind)
    path = _config(tmp_path, "http://127.0.0.1:9/v1", auth={**NAMED_USERS, **auth})
    return _by_name(await run_doctor(path, probe_context=False)).get("auth")


@pytest.mark.parametrize("bind", ["0.0.0.0", "10.0.0.5", "", None])
async def test_named_users_on_an_open_port_without_a_proxy_secret_warn(tmp_path, monkeypatch, bind):
    check = await _auth_check(tmp_path, monkeypatch, bind)
    assert check.status is CheckStatus.WARN
    assert "anyone who can reach" in check.message and "any user name" in check.message
    assert "auth.proxy_secret_env" in check.fix
    assert "X-Coglossa-Proxy-Secret" in check.fix
    assert "COGLOSSA_HTTP_BIND=127.0.0.1" in check.fix


@pytest.mark.parametrize("bind", ["127.0.0.1", "127.0.1.1", "::1", "[::1]", "localhost"])
async def test_named_users_on_loopback_pass(tmp_path, monkeypatch, bind):
    check = await _auth_check(tmp_path, monkeypatch, bind)
    assert check.status is CheckStatus.PASS
    assert bind in check.message


async def test_named_users_with_a_proxy_secret_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("COGLOSSA_PROXY_SECRET", "s3cret-for-the-test")
    check = await _auth_check(
        tmp_path, monkeypatch, "0.0.0.0", proxy_secret_env="COGLOSSA_PROXY_SECRET"
    )
    assert check.status is CheckStatus.PASS
    assert "X-Coglossa-Proxy-Secret" in check.message
    assert "s3cret" not in check.message


async def test_no_auth_line_without_named_users(tmp_path, monkeypatch):
    monkeypatch.setenv("COGLOSSA_HTTP_BIND", "0.0.0.0")
    report = await run_doctor(_config(tmp_path, "http://127.0.0.1:9/v1"), probe_context=False)
    assert "auth" not in _by_name(report)


async def _teams_check(tmp_path, people=None, **auth):
    """CGL-74: the `teams` check, with a people file holding `people` when given."""
    if people is not None:
        (tmp_path / "people.yaml").write_text(people, encoding="utf-8")
        auth["people_file"] = "people.yaml"
    path = _config(tmp_path, "http://127.0.0.1:9/v1", auth={**NAMED_USERS, **auth})
    return _by_name(await run_doctor(path, probe_context=False)).get("teams")


async def test_teams_without_a_filter_report_that_every_group_is_a_team(tmp_path):
    check = await _teams_check(tmp_path, groups_header="X-Remote-Groups")
    assert check.status is CheckStatus.PASS
    assert check.message == (
        "every group the proxy sends becomes a team (auth.team_groups_prefix and"
        " auth.team_groups are unset)"
    )
    from_file = await _teams_check(tmp_path, "teams:\n  desk: [jose]\n")
    assert from_file.status is CheckStatus.PASS
    assert from_file.message == "teams come from the people file only (auth.groups_header is unset)"


async def test_teams_report_the_filter(tmp_path):
    check = await _teams_check(
        tmp_path,
        "teams:\n  coglossa-es: [maria]\n  Desk: [jose]\n",
        team_groups_prefix="coglossa-",
        team_groups=["desk", "pacific"],
    )
    assert check.status is CheckStatus.PASS
    assert check.message == (
        "only groups whose names start with 'coglossa-' or are 'desk', 'pacific' become teams"
    )


async def test_teams_warn_about_people_file_teams_the_filter_leaves_out(tmp_path):
    check = await _teams_check(
        tmp_path,
        "teams:\n  coglossa-es: [maria]\n  Desk: [jose]\n  pacific: [tevita]\n",
        team_groups_prefix="coglossa-",
    )
    assert check.status is CheckStatus.WARN
    assert check.message == (
        "only groups whose names start with 'coglossa-' become teams, so the people file's"
        " teams 'Desk', 'pacific' give their members nothing"
    )
    assert "auth.team_groups" in check.fix


async def test_teams_warn_when_the_filter_leaves_out_every_group(tmp_path):
    everything = await _teams_check(
        tmp_path, "teams:\n  Desk: [jose]\n  pacific: [tevita]\n", team_groups_prefix="cog-"
    )
    assert everything.status is CheckStatus.WARN
    assert everything.message == (
        "only groups whose names start with 'cog-' become teams, so every team in the people"
        " file ('Desk', 'pacific') gives its members nothing"
    )
    nothing = await _teams_check(tmp_path, team_groups=[])
    assert nothing.status is CheckStatus.WARN
    assert nothing.message == "auth.team_groups is empty, so no group becomes a team"
    admins = await _teams_check(tmp_path, team_groups=["coglossa-admins"])
    assert admins.status is CheckStatus.WARN
    assert admins.message == (
        "auth.team_groups lists only auth.admin_group, which is never a team, so no group"
        " becomes a team"
    )


async def test_no_teams_line_without_named_users(tmp_path):
    report = await run_doctor(_config(tmp_path, "http://127.0.0.1:9/v1"), probe_context=False)
    assert "teams" not in _by_name(report)
