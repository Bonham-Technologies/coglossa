import json
import re
import uuid

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy import text as sa_text
from sqlalchemy.pool import NullPool

from coglossa.cli import main
from coglossa.store.db import to_sqlalchemy_url
from coglossa.store.migrate import upgrade
from coglossa.testing.fake_openai import FakeOpenAIServer
from coglossa.testing.postgres import create_test_database, drop_test_database


@pytest.fixture
def server():
    with FakeOpenAIServer() as srv:
        yield srv


@pytest.fixture
def setup(db, tmp_path, server, monkeypatch, pg_url):
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    cfg = {
        "backends": {"local": {"base_url": server.base_url, "max_concurrency": 4}},
        "models": {"fake-model": {"backend": "local", "context_window": 32768}},
        "roles": {
            "literal_translator": {"model": "fake-model"},
            "contextual_translator": {"model": "fake-model"},
            "reviewer": {"model": "fake-model"},
            "profiler_guide": {"model": "fake-model"},
        },
        "retry": {"max_attempts": 2, "base_delay_s": 0.01, "max_delay_s": 0.02},
        "storage": {"path": str(tmp_path / "files")},
    }
    config = tmp_path / "coglossa.yaml"
    config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    doc = tmp_path / "story.txt"
    doc.write_text("Le chat dort. Il pleut.", encoding="utf-8")
    return config, doc


def _cli(capsys, *argv) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def _run_id(out: str) -> str:
    return re.search(r"run ([0-9a-f-]{36}) queued", out).group(1)


def test_db_upgrade_is_idempotent(setup, capsys):
    config, _ = setup
    code, out, _ = _cli(capsys, "db", "upgrade", "--config", str(config))
    assert code == 0 and "at revision 0012" in out and "coglossa:coglossa@" not in out


def test_submit_work_status_report(setup, capsys, tmp_path):
    config, doc = setup
    code, out, _ = _cli(
        capsys,
        "submit",
        str(doc),
        "--target-lang",
        "en",
        "--source-lang",
        "fr",
        "--config",
        str(config),
    )
    assert code == 0
    run_id = _run_id(out)
    assert "coglossa worker" in out and f"coglossa status {run_id}" in out
    code, out, _ = _cli(capsys, "status", run_id, "--config", str(config))
    assert code == 0 and f"run {run_id}: created" in out
    code, _, _ = _cli(capsys, "worker", "--burst", "--config", str(config))
    assert code == 0
    code, out, _ = _cli(capsys, "status", run_id, "--config", str(config))
    assert "complete" in out and "score 1/1" in out and f"coglossa report {run_id}" in out
    code, out, _ = _cli(
        capsys, "report", run_id, "--out", str(tmp_path / "out"), "--config", str(config)
    )
    assert code == 0 and "complete: 2 spans" in out
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["run_id"] == run_id
    code, _, err = _cli(capsys, "retry", run_id, "--config", str(config))
    assert code == 1 and "nothing to retry" in err
    code, _, err = _cli(capsys, "cancel", run_id, "--config", str(config))
    assert code == 1 and "already complete" in err


def test_checkpoint_review_and_approval(setup, capsys, tmp_path):
    config, doc = setup
    _, out, _ = _cli(
        capsys,
        "submit",
        str(doc),
        "--target-lang",
        "en",
        "--profile",
        "long-form",
        "--config",
        str(config),
    )
    run_id = _run_id(out)
    _cli(capsys, "worker", "--burst", "--config", str(config))
    code, out, _ = _cli(capsys, "status", run_id, "--config", str(config))
    assert "awaiting_guide_approval" in out and f"coglossa guide {run_id}" in out
    guide_path = tmp_path / "guide.json"
    code, _, _ = _cli(capsys, "guide", run_id, "--out", str(guide_path), "--config", str(config))
    assert code == 0
    guide = json.loads(guide_path.read_text(encoding="utf-8"))
    guide["glossary"].append({"term": "chat", "rendering": "cat", "notes": "", "locked": True})
    guide_path.write_text(json.dumps(guide), encoding="utf-8")
    code, out, _ = _cli(
        capsys, "approve-guide", run_id, "--guide", str(guide_path), "--config", str(config)
    )
    assert code == 0 and "guide version 2 approved" in out
    _cli(capsys, "worker", "--burst", "--config", str(config))
    code, out, _ = _cli(
        capsys, "report", run_id, "--out", str(tmp_path / "o2"), "--config", str(config)
    )
    assert code == 0 and "1 revised by the sweep" in out


def test_guide_prints_json_to_stdout(setup, capsys):
    config, doc = setup
    _, out, _ = _cli(
        capsys, "submit", str(doc), "--target-lang", "en", "--checkpoint", "--config", str(config)
    )
    run_id = _run_id(out)
    _cli(capsys, "worker", "--burst", "--config", str(config))
    code, out, _ = _cli(capsys, "guide", run_id, "--config", str(config))
    assert code == 0 and json.loads(out)["summary"]


def test_cancel_and_retry_a_queued_run(setup, capsys):
    config, doc = setup
    _, out, _ = _cli(capsys, "submit", str(doc), "--target-lang", "en", "--config", str(config))
    run_id = _run_id(out)
    code, out, _ = _cli(capsys, "cancel", run_id, "--config", str(config))
    assert code == 0 and "cancelled" in out
    code, _, err = _cli(capsys, "retry", run_id, "--config", str(config))
    assert code == 1 and "submit a new run" in err


def test_status_does_not_suggest_a_retry_that_cannot_help(setup, capsys, tmp_path):
    config, doc = setup
    _, out, _ = _cli(capsys, "submit", str(doc), "--target-lang", "en", "--config", str(config))
    run_id = _run_id(out)
    changed = yaml.safe_load(config.read_text(encoding="utf-8"))
    changed["retry"]["max_attempts"] = 3  # the operator changed the config after submitting
    worker_config = tmp_path / "changed.yaml"
    worker_config.write_text(yaml.safe_dump(changed), encoding="utf-8")
    _cli(capsys, "worker", "--burst", "--config", str(worker_config))
    code, out, _ = _cli(capsys, "status", run_id, "--config", str(config))
    assert code == 0 and "complete_with_errors" in out
    assert f"next: coglossa report {run_id}\n" in out and "retry" not in out
    code, _, err = _cli(capsys, "retry", run_id, "--config", str(config))
    assert code == 1 and "(config) come from earlier stages" in err


def test_cancel_of_running_work_says_when_it_stops(setup, capsys, pg_url):
    import asyncio

    from coglossa.schema import RunState
    from coglossa.store import runs
    from coglossa.store.db import Database

    config, doc = setup
    _, out, _ = _cli(capsys, "submit", str(doc), "--target-lang", "en", "--config", str(config))
    run_id = _run_id(out)

    async def translating() -> None:
        import uuid

        db = Database(pg_url)
        async with db.session() as s, s.begin():
            (await runs.get_run(s, uuid.UUID(run_id), lock=True)).state = RunState.TRANSLATING
        await db.dispose()

    asyncio.run(translating())
    code, out, _ = _cli(capsys, "cancel", run_id, "--config", str(config))
    assert code == 0 and "stops after the current chunk or stage" in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["status", "not-a-uuid"], "'not-a-uuid' is not a run id"),
        (["status", "00000000-0000-0000-0000-000000000000"], "not found"),
        (["approve-guide", "00000000-0000-0000-0000-000000000000"], "not found"),
    ],
)
def test_errors_are_one_line(setup, capsys, argv, message):
    config, _ = setup
    code, _, err = _cli(capsys, *argv, "--config", str(config))
    assert code == 1 and message in err and "Traceback" not in err


def _count(pg_url, table):
    import psycopg

    from coglossa.store.db import to_libpq_url

    with psycopg.connect(to_libpq_url(pg_url)) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_delete_project_needs_yes_and_removes_everything(setup, capsys, tmp_path, pg_url):
    config, doc = setup
    _cli(
        capsys,
        "submit",
        str(doc),
        "--target-lang",
        "en",
        "--project",
        "novel",
        "--config",
        str(config),
    )
    files = tmp_path / "files"
    stored = [f for f in files.rglob("*") if f.is_file()]
    assert stored and _count(pg_url, "project") == 1
    assert _count(pg_url, "document") == 1 and _count(pg_url, "run") == 1

    code, _, err = _cli(capsys, "delete-project", "novel", "--config", str(config))
    assert code == 1 and "pass --yes" in err
    assert _count(pg_url, "project") == 1 and _count(pg_url, "document") == 1
    assert _count(pg_url, "run") == 1 and all(f.exists() for f in stored)

    code, out, _ = _cli(capsys, "delete-project", "novel", "--yes", "--config", str(config))
    assert code == 0 and "project novel deleted" in out
    assert _count(pg_url, "project") == 0 and _count(pg_url, "document") == 0
    assert _count(pg_url, "run") == 0 and not any(f.exists() for f in stored)


def test_missing_database_configuration(setup, capsys, monkeypatch):
    config, doc = setup
    monkeypatch.delenv("COGLOSSA_DATABASE_URL")
    code, _, err = _cli(capsys, "submit", str(doc), "--target-lang", "en", "--config", str(config))
    assert code == 1 and "set COGLOSSA_DATABASE_URL" in err


def test_unreachable_database(setup, capsys, monkeypatch):
    config, doc = setup
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", "postgresql://u:secret@127.0.0.1:9/x")
    code, _, err = _cli(
        capsys, "status", "00000000-0000-0000-0000-000000000000", "--config", str(config)
    )
    assert code == 1 and "cannot connect to the database at" in err and "secret" not in err
    assert len(err.strip().splitlines()) == 1


def test_db_upgrade_says_how_many_set_owners_it_repaired(setup, capsys, monkeypatch, pg_admin):
    """CGL-75: the operator sees what revision 0006 changed."""
    config, _ = setup
    url = create_test_database(pg_admin)
    engine = create_engine(to_sqlalchemy_url(url), poolclass=NullPool)
    try:
        upgrade(url, "0005")
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO project (id, name, created_at, created_by) "
                    "VALUES (:id, 'p', now(), :by)"
                ),
                {"id": uuid.uuid4(), "by": "José".encode().decode("latin-1")},
            )
        monkeypatch.setenv("COGLOSSA_DATABASE_URL", url)
        code, out, _ = _cli(capsys, "db", "upgrade", "--config", str(config))
        assert code == 0 and "repaired 1 set owner names saved misread as latin-1" in out
        code, out, _ = _cli(capsys, "db", "upgrade", "--config", str(config))
        assert code == 0 and "repaired" not in out  # nothing left to run
    finally:
        engine.dispose()
        drop_test_database(pg_admin, url)


def test_db_upgrade_unreachable_database(setup, capsys, monkeypatch):
    config, _ = setup
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", "postgresql://u:secret@127.0.0.1:9/x")
    code, _, err = _cli(capsys, "db", "upgrade", "--config", str(config))
    assert code == 1 and "error: cannot connect to the database at postgresql://u:***@" in err
    assert "secret" not in err and len(err.strip().splitlines()) == 1


def test_unsupported_input(setup, capsys, tmp_path):
    config, _ = setup
    bad = tmp_path / "x.png"
    bad.write_bytes(b"\x89PNG")
    code, _, err = _cli(capsys, "submit", str(bad), "--target-lang", "en", "--config", str(config))
    assert code == 1 and "unsupported file type" in err


def test_internal_errors_have_no_traceback(setup, capsys, monkeypatch):
    import coglossa.cli_ops as ops

    def boom(args):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(ops.COMMANDS, "status", boom)
    code, _, err = _cli(capsys, "status", "x", "--config", str(setup[0]))
    assert code == 1 and "internal error: RuntimeError: kaboom" in err


def test_ctrl_c(setup, capsys, monkeypatch):
    import coglossa.cli_ops as ops

    def interrupted(args):
        raise KeyboardInterrupt

    monkeypatch.setitem(ops.COMMANDS, "worker", interrupted)
    code, _, _ = _cli(capsys, "worker", "--config", str(setup[0]))
    assert code == 130


def test_unmigrated_database_is_refused(setup, capsys, pg_admin, monkeypatch):
    from coglossa.testing.postgres import create_test_database, drop_test_database

    config, _ = setup
    empty = create_test_database(pg_admin)
    try:
        monkeypatch.setenv("COGLOSSA_DATABASE_URL", empty)
        code, _, err = _cli(
            capsys, "status", "00000000-0000-0000-0000-000000000000", "--config", str(config)
        )
    finally:
        drop_test_database(pg_admin, empty)
    assert code == 1 and "schema is at revision none" in err and "coglossa db upgrade" in err
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1


def test_worker_removes_its_log_handler(setup, capsys):
    import logging

    config, _ = setup
    before = list(logging.getLogger("coglossa").handlers)
    code, _, _ = _cli(capsys, "worker", "--burst", "--config", str(config))
    assert code == 0 and logging.getLogger("coglossa").handlers == before


def test_import_glossary(setup, capsys, tmp_path):
    """CGL-88: preview with --dry-run, then import; conflicts are reported and kept."""
    config, doc = setup
    _cli(
        capsys,
        "submit",
        str(doc),
        "--target-lang",
        "en",
        "--project",
        "novel",
        "--config",
        str(config),
    )
    terms = tmp_path / "terms.csv"
    terms.write_text("term,translation\nchat,cat\npluie,rain\nchat,kitty\n", encoding="utf-8")
    base = ["import-glossary", str(terms), "--project", "novel", "--config", str(config)]
    code, out, _ = _cli(capsys, *base, "--dry-run")
    assert code == 0
    assert "terms.csv (en guide): 2 new, 0 already in the guide" in out
    assert "line 4: chat repeats line 2, which is used" in out
    assert "dry run: nothing was saved" in out
    code, out, _ = _cli(capsys, *base, "--lock-all")
    assert code == 0 and "guide version 1 saved: 2 added, 0 replaced, 2 newly locked" in out
    tbx = tmp_path / "base.tbx"
    tbx.write_bytes(
        b'<martif><termEntry><langSet xml:lang="fr"><term>vent</term></langSet>'
        b'<langSet xml:lang="en"><term>wind</term></langSet></termEntry>'
        b'<termEntry><langSet xml:lang="fr"><term>seul</term></langSet></termEntry></martif>'
    )
    code, out, _ = _cli(capsys, *base[:1], str(tbx), *base[2:], "--source-lang", "fr", "--dry-run")
    assert "base.tbx (en guide): 1 new" in out
    assert "entries left out, without a term in both languages: 1" in out
    terms.write_text("chat,kitty\n", encoding="utf-8")
    code, out, _ = _cli(capsys, *base)
    assert "line 1: chat → kitty (the guide has cat, locked)" in out
    assert "nothing to change: the guide stays at version 1" in out
    assert "1 different translations kept as the guide has them" in out
    code, out, _ = _cli(capsys, *base, "--overwrite-conflicts")
    assert "guide version 2 saved: 0 added, 1 replaced, 0 newly locked" in out


def test_import_glossary_errors(setup, capsys, tmp_path):
    config, _ = setup
    terms = tmp_path / "terms.csv"
    base = ["import-glossary", str(terms), "--config", str(config)]
    code, _, err = _cli(capsys, *base)
    assert code == 1 and "error: cannot read" in err and "terms.csv" in err
    terms.write_text("chat,cat\nchien\n", encoding="utf-8")
    code, _, err = _cli(capsys, *base, "--project", "nope")
    assert code == 1 and "project 'nope' not found" in err


def test_submit_help_says_each_language_has_its_own_guide(capsys):
    # CGL-30: since CGL-24 a set's guide is per language, not one for every document
    with pytest.raises(SystemExit):
        main(["submit", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "share a guide" not in out
    assert "one translation guide per language" in out
