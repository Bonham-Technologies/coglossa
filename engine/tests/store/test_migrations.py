import logging
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from procrastinate.schema import SchemaManager
from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from coglossa.store.db import to_libpq_url, to_sqlalchemy_url
from coglossa.store.migrate import downgrade, migration_state, upgrade
from coglossa.store.models import (
    Base,
    Document,
    FlagRow,
    Project,
    Run,
    SpanRow,
    SpanScoreRow,
    StageResult,
)
from coglossa.testing.postgres import create_test_database, drop_test_database

OUR_TABLES = {
    "project",
    "guide_version",
    "document",
    "block",
    "segment",
    "run",
    "chunk",
    "stage_result",
    "llm_call",
    "candidate",
    "span",
    "span_score",
    "flag",
    "revision",
    "review_event",
}


def _tables(url):
    engine = create_engine(to_sqlalchemy_url(url), poolclass=NullPool)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


# procrastinate's objects as Postgres sees them, each kind a sorted list of rows: the
# definitions come from pg_get_*def so two schemas built by different SQL compare equal
# exactly when they would behave the same.
_PROCRASTINATE_CATALOG = {
    "functions": """
        SELECT p.oid::regprocedure::text, pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate%'
    """,
    "columns": """
        SELECT c.relname, c.relkind::text, a.attname, format_type(a.atttypid, a.atttypmod),
               a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND c.relname LIKE 'procrastinate%'
          AND c.relkind IN ('r', 'v', 'c')
    """,
    "enums": """
        SELECT t.typname, array_agg(e.enumlabel::text ORDER BY e.enumsortorder)
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE n.nspname = 'public' AND t.typname LIKE 'procrastinate%'
        GROUP BY t.typname
    """,
    "sequences": """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname LIKE 'procrastinate%' AND c.relkind = 'S'
    """,
    "constraints": """
        SELECT cl.relname, co.conname, pg_get_constraintdef(co.oid)
        FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid
        JOIN pg_namespace n ON n.oid = cl.relnamespace
        WHERE n.nspname = 'public' AND cl.relname LIKE 'procrastinate%'
    """,
    "indexes": """
        SELECT tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname = 'public' AND tablename LIKE 'procrastinate%'
    """,
    "triggers": """
        SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid)
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname LIKE 'procrastinate%' AND NOT t.tgisinternal
    """,
}


def _procrastinate_catalog(url) -> dict[str, list[tuple]]:
    with psycopg.connect(to_libpq_url(url)) as conn:
        return {
            kind: sorted(tuple(row) for row in conn.execute(query).fetchall())
            for kind, query in _PROCRASTINATE_CATALOG.items()
        }


def _assert_same_catalog(actual, expected, message):
    for kind in _PROCRASTINATE_CATALOG:
        missing = [row for row in expected[kind] if row not in actual[kind]]
        extra = [row for row in actual[kind] if row not in expected[kind]]
        assert not missing and not extra, f"{message}: {kind} differ\n{missing=}\n{extra=}"


@pytest.fixture(scope="module")
def installed_procrastinate_catalog(pg_admin):
    """procrastinate's objects in a database built from the installed package's schema.sql."""
    url = create_test_database(pg_admin)
    try:
        with psycopg.connect(to_libpq_url(url)) as conn:
            conn.execute(SchemaManager.get_schema())
        return _procrastinate_catalog(url)
    finally:
        drop_test_database(pg_admin, url)


def test_vendored_procrastinate_schema_matches_the_installed_package(
    pg_url, installed_procrastinate_catalog
):
    """Revision 0001 installs procrastinate 3.10.0's schema; later revisions apply the
    package's migrations since. Together they must build what the installed procrastinate
    expects, so a procrastinate upgrade that brings a new migration fails here until a
    revision applies it."""
    assert installed_procrastinate_catalog["functions"]  # the snapshot found the objects
    _assert_same_catalog(
        _procrastinate_catalog(pg_url),
        installed_procrastinate_catalog,
        "the installed procrastinate's schema differs from the one the migrations build: "
        "add a new Alembic revision that applies procrastinate's migration SQL",
    )


def test_upgrade_creates_our_tables_and_the_job_queue(pg_url):
    tables = _tables(pg_url)
    assert tables >= OUR_TABLES
    assert {"procrastinate_jobs", "procrastinate_workers"} <= tables
    assert set(Base.metadata.tables) == OUR_TABLES


def test_migration_state_is_current(pg_url):
    state = migration_state(pg_url)
    assert state.current == state.head == "0011"
    assert state.up_to_date


def test_models_match_the_migrations(pg_url):
    engine = create_engine(to_sqlalchemy_url(pg_url), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn,
                opts={
                    "compare_type": True,
                    "include_name": lambda name, type_, _: (
                        type_ != "table" or name in Base.metadata.tables
                    ),
                },
            )
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def test_downgrade_then_upgrade_again(pg_admin):
    url = create_test_database(pg_admin)
    try:
        empty = migration_state(url)
        assert empty.current is None and not empty.up_to_date
        upgrade(url)
        downgrade(url)
        assert not (_tables(url) & (OUR_TABLES | {"procrastinate_jobs"}))
        upgrade(url)
        assert migration_state(url).up_to_date
    finally:
        drop_test_database(pg_admin, url)


@pytest.mark.parametrize(
    ("previous", "revision"),
    [("0007", "0008"), ("0008", "0009"), ("0009", "0010"), ("0010", "0011")],
)
def test_procrastinate_revisions_round_trip(
    pg_admin, installed_procrastinate_catalog, previous, revision
):
    """CGL-106: 0008 applies procrastinate 3.11.0's migration; 0009 and 0010 3.12.0's
    pre-migrations and 0011 its post-migrations. Each downgrade puts back exactly the
    job-queue objects of the revision before it, and upgrading again builds the installed
    procrastinate's schema."""
    url = create_test_database(pg_admin)
    try:
        upgrade(url, previous)
        before = _procrastinate_catalog(url)
        upgrade(url, revision)
        assert _procrastinate_catalog(url) != before
        downgrade(url, previous)
        assert migration_state(url).current == previous
        _assert_same_catalog(
            _procrastinate_catalog(url), before, f"{revision}'s downgrade left a change"
        )
        upgrade(url)
        assert migration_state(url).up_to_date
        _assert_same_catalog(
            _procrastinate_catalog(url),
            installed_procrastinate_catalog,
            "upgrading again does not build the installed procrastinate's schema",
        )
    finally:
        drop_test_database(pg_admin, url)


# What procrastinate 3.10 and 3.11 call to queue a job and a periodic job.
_DEFER_3_11 = """
SELECT procrastinate_defer_jobs_v1(ARRAY[
    ROW('coglossa', 'old.task', 0, 'lock-1', 'lock-1', '{}'::jsonb, NULL)
]::procrastinate_job_to_defer_v1[])
"""
_DEFER_PERIODIC_3_11 = """
SELECT procrastinate_defer_periodic_job_v2(
    'coglossa', NULL, NULL, 'old.periodic', 0, 'periodic-1', 1, '{}'::jsonb
)
"""


def test_old_workers_can_still_queue_jobs_until_the_post_migrations(pg_admin):
    """CGL-106: after 3.12.0's pre-migrations (0009, 0010) a 3.11 worker or API still queues
    jobs; 0011, the post-migrations, removes what it calls, so it must run after they stop."""
    url = create_test_database(pg_admin)
    try:
        upgrade(url, "0010")
        with psycopg.connect(to_libpq_url(url), autocommit=True) as conn:
            conn.execute(_DEFER_3_11)
            conn.execute(_DEFER_PERIODIC_3_11)
            rows = conn.execute(
                "SELECT task_name, lock_mode::text FROM procrastinate_jobs ORDER BY id"
            ).fetchall()
            assert rows == [("old.task", "ordered"), ("old.periodic", "ordered")]
        upgrade(url, "0011")
        with psycopg.connect(to_libpq_url(url), autocommit=True) as conn:
            with pytest.raises(psycopg.errors.UndefinedObject):  # the composite type is gone
                conn.execute(_DEFER_3_11)
            with pytest.raises(psycopg.errors.UndefinedFunction):
                conn.execute(_DEFER_PERIODIC_3_11)
    finally:
        drop_test_database(pg_admin, url)


_LOCK_INDEX = "procrastinate_jobs_todo_doing_lock_idx_v1"


@pytest.mark.parametrize(
    "leftover",
    [
        # an interrupted concurrent build leaves an invalid index (marking it so needs a superuser)
        [
            f"CREATE INDEX {_LOCK_INDEX} ON procrastinate_jobs (id)",
            f"UPDATE pg_index SET indisvalid = false WHERE indexrelid = '{_LOCK_INDEX}'::regclass",
        ],
        # a build that finished before the revision was recorded leaves the index itself
        [
            f"CREATE INDEX {_LOCK_INDEX} ON procrastinate_jobs (lock)"
            " WHERE status = ANY (ARRAY['todo'::procrastinate_job_status,"
            " 'doing'::procrastinate_job_status])"
        ],
    ],
    ids=["invalid", "valid"],
)
def test_0010_runs_again_after_an_interrupted_index_build(
    pg_admin, installed_procrastinate_catalog, leftover
):
    """CGL-106: running CREATE INDEX CONCURRENTLY again fails on the name of the index an
    earlier attempt left: 0010 drops an invalid one and builds it again, and keeps a valid one."""
    url = create_test_database(pg_admin)
    try:
        upgrade(url, "0009")
        with psycopg.connect(to_libpq_url(url), autocommit=True) as conn:
            for statement in leftover:
                conn.execute(statement)
        upgrade(url)
        assert migration_state(url).up_to_date
        _assert_same_catalog(
            _procrastinate_catalog(url),
            installed_procrastinate_catalog,
            "0010 did not leave the index procrastinate builds",
        )
        with psycopg.connect(to_libpq_url(url)) as conn:
            valid = conn.execute(
                f"SELECT indisvalid FROM pg_index WHERE indexrelid = '{_LOCK_INDEX}'::regclass"
            ).fetchone()
        assert valid == (True,)
    finally:
        drop_test_database(pg_admin, url)


async def test_deleting_a_project_cascades(db):
    async with db.session() as s, s.begin():
        project = Project(name="p")
        s.add(project)
        await s.flush()
        doc = Document(
            project_id=project.id, filename="a.txt", format="txt", sha256="x", storage_key="k"
        )
        s.add(doc)
        await s.flush()
        run = Run(
            document_id=doc.id,
            target_lang="en",
            config_hash="h",
            config_snapshot={},
            prompt_versions={},
        )
        s.add(run)
        await s.flush()
        span = SpanRow(
            id=uuid.uuid4(),
            run_id=run.id,
            chunk_id="c1",
            chunk_ordinal=0,
            ordinal=0,
            seg_start="s1",
            seg_end="s1",
            agreement="agree",
            chosen_from="literal",
            rationale="",
            issues=[],
            proposed_terms=[],
        )
        s.add(span)
        await s.flush()
        s.add(SpanScoreRow(span_id=span.id, signals={}, reliability="A"))
        s.add(FlagRow(span_id=span.id, code="low_meaning", explanation="x", evidence={}))
        s.add(StageResult(run_id=run.id, stage="ingest", status="complete"))
    async with db.session() as s, s.begin():
        await s.delete(await s.get(Project, project.id))
    async with db.session() as s:
        for model in (Document, Run, SpanRow, SpanScoreRow, FlagRow, StageResult):
            assert (await s.scalars(select(model))).all() == []


_NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _insert(conn, table_name, /, **values):
    table = Table(table_name, MetaData(), autoload_with=conn)
    conn.execute(table.insert().values(**values))


def _project(conn, name, target_lang, **extra):
    pid = uuid.uuid4()
    _insert(conn, "project", id=pid, name=name, target_lang=target_lang, created_at=_NOW, **extra)
    return pid


def _run_of(conn, project_id, target_lang, minutes):
    doc = uuid.uuid4()
    _insert(
        conn,
        "document",
        id=doc,
        project_id=project_id,
        filename="a.txt",
        format="txt",
        sha256="x",
        storage_key=f"k/{doc}",
        created_at=_NOW,
    )
    rid = uuid.uuid4()
    _insert(
        conn,
        "run",
        id=rid,
        document_id=doc,
        state="complete",
        target_lang=target_lang,
        use_project_guide=False,
        cancel_requested=False,
        config_hash="h",
        config_snapshot={},
        prompt_versions={},
        created_at=_NOW + timedelta(minutes=minutes),
    )
    return rid


def _version(conn, project_id, version, source_run_id=None, **extra):
    vid = uuid.uuid4()
    _insert(
        conn,
        "guide_version",
        id=vid,
        project_id=project_id,
        version=version,
        status="draft",
        author="engine",
        source_run_id=source_run_id,
        content={},
        created_at=_NOW,
        **extra,
    )
    return vid


def test_0004_gives_every_guide_version_a_language(pg_admin):
    url = create_test_database(pg_admin)
    engine = create_engine(to_sqlalchemy_url(url), poolclass=NullPool)
    try:
        upgrade(url, "0003")
        with engine.begin() as conn:
            french = _project(conn, "french", "fr")
            from_run = _version(conn, french, 1, _run_of(conn, french, "es", 0))
            from_default = _version(conn, french, 2)
            cli = _project(conn, "cli", None)
            _run_of(conn, cli, "it", 5)
            _run_of(conn, cli, "de", 1)  # the earliest run
            from_earliest = _version(conn, cli, 1)
            empty = _project(conn, "empty", None)
            unknown = _version(conn, empty, 1)
            mixed = _project(conn, "mixed", "en-GB")
            british = _run_of(conn, mixed, "en_gb", 0)
            brazil_first = _run_of(conn, mixed, "pt_BR", 1)
            brazil_later = _run_of(conn, mixed, "PT-br", 2)
            from_later = _version(conn, mixed, 1, brazil_later)
            from_british = _version(conn, mixed, 2, british)
        upgrade(url)
        with engine.begin() as conn:
            langs = dict(conn.execute(sa_text("SELECT id, target_lang FROM guide_version")).all())
            assert langs == {
                from_run: "es",
                from_default: "fr",
                from_earliest: "de",
                unknown: "und",
                from_later: "pt_BR",  # one spelling per language: the earliest run's
                from_british: "en-GB",  # the project's default comes first
            }
            run_langs = dict(conn.execute(sa_text("SELECT id, target_lang FROM run")).all())
            assert [run_langs[r] for r in (british, brazil_first, brazil_later)] == [
                "en-GB",
                "pt_BR",
                "pt_BR",
            ]
            _version(conn, french, 1, target_lang="fr")  # numbers count per language now
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _version(conn, french, 1, target_lang="es")
    finally:
        engine.dispose()
        drop_test_database(pg_admin, url)


def _guide_columns(url):
    engine = create_engine(to_sqlalchemy_url(url), poolclass=NullPool)
    try:
        return {c["name"] for c in inspect(engine).get_columns("guide_version")}
    finally:
        engine.dispose()


def test_0004_downgrades_while_each_set_has_one_guide_language(pg_admin):
    """CGL-30: back to 0003 the language column goes and the guide's numbers count per set
    again. A set with two languages' guides sharing a number cannot go back: the downgrade
    is refused whole and the database stays as it was."""
    url = create_test_database(pg_admin)
    engine = create_engine(to_sqlalchemy_url(url), poolclass=NullPool)
    try:
        upgrade(url, "0004")
        with engine.begin() as conn:
            french = _project(conn, "french", "fr")
            kept = {_version(conn, french, n, target_lang="fr") for n in (1, 2)}
            german = _project(conn, "german", "de")
            kept.add(_version(conn, german, 1, target_lang="de"))
        downgrade(url, "0003")
        assert migration_state(url).current == "0003"
        assert "target_lang" not in _guide_columns(url)
        with engine.begin() as conn:
            assert set(conn.execute(sa_text("SELECT id FROM guide_version")).scalars()) == kept
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _version(conn, french, 2)  # one number per set again
        upgrade(url, "0004")
        with engine.begin() as conn:
            _version(conn, french, 1, target_lang="es")  # a second language shares number 1
        with pytest.raises(IntegrityError):
            downgrade(url, "0003")
        assert migration_state(url).current == "0004"
        assert "target_lang" in _guide_columns(url)
        upgrade(url)
        assert migration_state(url).up_to_date
    finally:
        engine.dispose()
        drop_test_database(pg_admin, url)


def test_0005_leaves_existing_sets_without_a_team(pg_admin):
    url = create_test_database(pg_admin)
    engine = create_engine(to_sqlalchemy_url(url), poolclass=NullPool)
    try:
        upgrade(url, "0004")
        with engine.begin() as conn:
            pid = _project(conn, "old", "en")
        upgrade(url)
        with engine.begin() as conn:
            row = conn.execute(
                sa_text("SELECT team, created_by FROM project WHERE id = :id"), {"id": pid}
            ).one()
        assert tuple(row) == (None, None)
    finally:
        engine.dispose()
        drop_test_database(pg_admin, url)


def _misread(name):
    """A name as Coglossa read it before CGL-31: the proxy's UTF-8 bytes taken as latin-1."""
    return name.encode("utf-8").decode("latin-1")


def _owners(engine):
    with engine.begin() as conn:
        return dict(conn.execute(sa_text("SELECT id, created_by FROM project")).all())


def test_0006_repairs_names_misread_as_latin1(pg_admin, caplog):
    """CGL-75: a set saved under a name read as latin-1 before CGL-31 goes back to the name
    the proxy sent, in NFC, so its maker sees it again. Every other name is left as it is."""
    url = create_test_database(pg_admin)
    engine = create_engine(to_sqlalchemy_url(url), poolclass=NullPool)
    decomposed = unicodedata.normalize("NFD", "Zoë")
    owners = {
        "José": _misread("José"),
        "Łukasz": _misread("Łukasz"),  # its UTF-8 bytes read as a C1 control character
        "Zoë": _misread(decomposed),  # sent decomposed: stored in NFC
        "ana": "ana",
        "latin-1 José": "José",  # sent in latin-1: b"Jos\xe9" is not UTF-8
        # str.strip() before CGL-31 cut off the misread à's last byte, U+00A0: not UTF-8 now
        "Pietà": _misread("Pietà").strip(),
        "李": "李",  # outside latin-1: saved after CGL-31
        "none": None,
    }
    try:
        upgrade(url, "0005")
        with engine.begin() as conn:
            ids = {
                key: _project(conn, key, "en", created_by=stored) for key, stored in owners.items()
            }
        with caplog.at_level(logging.INFO, logger="coglossa.migrations"):
            upgrade(url, "0006")
        assert "repaired 3 set owner names" in caplog.text
        expected = {
            ids["José"]: "José",
            ids["Łukasz"]: "Łukasz",
            ids["Zoë"]: unicodedata.normalize("NFC", "Zoë"),
            ids["ana"]: "ana",
            ids["latin-1 José"]: "José",
            ids["Pietà"]: "PietÃ",
            ids["李"]: "李",
            ids["none"]: None,
        }
        assert _owners(engine) == expected
        assert unicodedata.is_normalized("NFC", _owners(engine)[ids["Zoë"]])

        # the downgrade changes nothing, and a second upgrade finds nothing left to repair
        downgrade(url, "0005")
        assert _owners(engine) == expected
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="coglossa.migrations"):
            upgrade(url)
        assert "repaired 0 set owner names" in caplog.text
        assert _owners(engine) == expected
    finally:
        engine.dispose()
        drop_test_database(pg_admin, url)
