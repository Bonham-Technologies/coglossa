import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from procrastinate.schema import SchemaManager
from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from coglossa.store.db import to_sqlalchemy_url
from coglossa.store.migrate import MIGRATIONS_DIR, downgrade, migration_state, upgrade
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


def test_vendored_procrastinate_schema_matches_the_installed_package():
    vendored = MIGRATIONS_DIR / "versions" / "0001_procrastinate_3_10_0.sql"
    assert vendored.read_text(encoding="utf-8") == SchemaManager.get_schema(), (
        "the installed procrastinate's schema differs from the one revision 0001 installs: "
        "add a new Alembic revision that applies procrastinate's migration SQL"
    )


def test_upgrade_creates_our_tables_and_the_job_queue(pg_url):
    tables = _tables(pg_url)
    assert tables >= OUR_TABLES
    assert {"procrastinate_jobs", "procrastinate_workers"} <= tables
    assert set(Base.metadata.tables) == OUR_TABLES


def test_migration_state_is_current(pg_url):
    state = migration_state(pg_url)
    assert state.current == state.head == "0005"
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


def _project(conn, name, target_lang):
    pid = uuid.uuid4()
    _insert(conn, "project", id=pid, name=name, target_lang=target_lang, created_at=_NOW)
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
