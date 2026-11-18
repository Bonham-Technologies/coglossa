# One Set, Several Languages (CGL-24) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A set (engine *project*) holds its files once and is translated into several languages,
each with its own translation guide; the web app switches between them and starts a new language
for every file without re-uploading.

**Architecture:** `guide_version` gains `target_lang`; every guide read and write is scoped to a
run's language. A set's languages are derived from its runs (its default language first). A new
`POST /projects/{id}/languages` creates one run per file. The web app picks a *current language*
from the address and filters each file's runs by it; language tabs appear only with two or more
languages.

**Tech Stack:** Python 3.12, SQLAlchemy 2 async, Alembic, FastAPI, pytest (Postgres via
`make db-up`); React 19, TanStack Query, React Router 8, Vitest 4 + MSW, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-18-set-languages-design.md`

## Global Constraints

- Each language has its own translation guide; renderings, locks and the style guide never cross languages.
- A set with one language shows no language controls and behaves exactly as today; existing tests keep passing (only signature updates, never weakened assertions).
- Web copy is plain words for non-technical users; no engine jargon ("run", "project", "target") in the UI.
- Colours only from CSS tokens; statuses always carry words and a symbol (`lib/status.ts`).
- Language names come from `languageName()` (`Intl.DisplayNames`); codes are never shown when a name exists.
- Engine and API tests run through `make` (they need Postgres): `make test-engine`, `make test-api`; inside a package `cd engine && uv run pytest tests/<path> -q` after `make db-up`.
- After any model or route change run `make schema` (regenerates `schema/openapi.json` and `web/src/api/schema.d.ts`; CI checks they match).
- Coverage gates: engine and web ≥ 95% (branches included). New branches need tests.
- Language codes compare case-insensitively with `-` and `_` alike wherever "the same language" is decided (`language_key` / `languageKey`).
- Commit subjects conventional (`feat(engine):`, `feat(web):` …) and end with the session footer:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012TBMbjzsQyykrdRBR7Vuv5
  ```

## Review Focus

1. **A run whose code differs only in case or separator from a set language** (`ES` vs `es`, `pt_BR` vs `pt-BR`): the set must list one language and refuse adding it again; tested in Task 2 (`ordered_languages`) and Task 3 (`add_language` conflict on `ES`).
2. **A CLI project with no default language and no runs** asking for its guide: GET answers 404 "no guide yet", PUT is refused with a plain 422, never a 500; tested in Task 1.
3. **Two languages building their guides at the same time** in one set: each draft merges only onto its own language's set guide (CGL-23 merge); tested in Task 1 (worker test with two languages).
4. **The address names a run of another language than `?lang=`**: the run wins, and the file tabs, header and guide follow the run's language; tested in Task 6.
5. **Adding files while the set is mid-way through a second language**: the new file gets a run in every language and the upload's response is the run in the language being viewed; tested in Task 3 (service) and Task 6 (dialog sends `all_languages`).

---

## File Structure

Engine:
- `engine/coglossa/store/migrations/versions/0004_guide_language.py` (create): column, backfill, uniqueness.
- `engine/coglossa/store/models.py`: `GuideVersion.target_lang`, new unique constraint.
- `engine/coglossa/store/runs.py`: `language_key`, `ordered_languages`, `set_languages`; language-scoped `latest_guide`, `prior_guide`, `add_guide_version`.
- `engine/coglossa/worker/execute.py`, `engine/coglossa/service.py`, `engine/coglossa/review.py`: pass `run.target_lang`.
- `engine/coglossa/catalog.py`: guide functions take `target_lang`; `ProjectInfo.languages`; counts per file per language.
- `engine/coglossa/schema/views.py`: `SetLanguage`, `ProjectInfo.languages`, `GuideVersionInfo.target_lang`.
- `engine/coglossa/service.py`: `LanguageExistsError`, `add_language`, `submit_bytes(also=…)`.
- `engine/coglossa/testing/seed.py`: pass the language.

API:
- `api/coglossa_api/models.py`: `AddLanguage`.
- `api/coglossa_api/routes/projects.py`: `POST /projects/{id}/languages`; upload `all_languages`.
- `api/coglossa_api/routes/guides.py`: `target_lang` query parameter.

Web:
- `web/src/set/languages.ts` (create): language helpers (pure).
- `web/src/api/sets.ts`, `web/src/api/guide.ts`, `web/src/api/projects.ts`, `web/src/api/types.ts`, `web/src/upload/queue.ts`: language-aware hooks and requests.
- `web/src/set/LanguageTabs.tsx` (create), `web/src/set/AddLanguageDialog.tsx` (create).
- `web/src/set/SetPage.tsx`, `FileTabs.tsx`, `SetHeader.tsx`, `SummaryBanner.tsx`, `AddFilesDialog.tsx`, `web/src/guide/GuidePanel.tsx`, `web/src/review/AlwaysTranslate.tsx`, `web/src/components/SetDialogs.tsx`, `web/src/translate/LanguagePicker.tsx`.
- `web/src/test/fixtures.ts`: new fields.
- `web/e2e/journey.spec.ts`: one new test.

Docs: `README.md`, `docs/superpowers/specs/2026-10-16-coglossa-ui-ux-design.md`, the CGL-24 spec (§4.3 status code).

---

### Task 1: The guide has a language (engine)

**Files:**
- Create: `engine/coglossa/store/migrations/versions/0004_guide_language.py`
- Modify: `engine/coglossa/store/models.py` (class `GuideVersion`)
- Modify: `engine/coglossa/store/runs.py` (`latest_guide`, `prior_guide`, `add_guide_version`; new `language_key`, `ordered_languages`, `set_languages`)
- Modify: `engine/coglossa/worker/execute.py` (`_guide`, the sweep in `finish_run`)
- Modify: `engine/coglossa/service.py` (`approve_guide`, `rerun_sweep`)
- Modify: `engine/coglossa/review.py` (`add_guide_entry`)
- Modify: `engine/coglossa/catalog.py` (`_version_info`, `guide_versions`, `project_guide`, `save_project_guide`)
- Modify: `engine/coglossa/schema/views.py` (`GuideVersionInfo.target_lang`)
- Modify: `engine/coglossa/testing/seed.py` (`add_guide_version` call)
- Test: `engine/tests/store/test_migrations.py`, `engine/tests/store/test_runs.py`, `engine/tests/worker/test_execute.py`, `engine/tests/test_guides.py`, and every existing test calling the changed functions

**Interfaces:**
- Produces:
  - `runs.language_key(code: str) -> str`
  - `runs.ordered_languages(default: str | None, used: Iterable[str]) -> list[str]`
  - `runs.set_languages(s: AsyncSession, project: Project) -> list[str]`
  - `runs.latest_guide(s, project_id, target_lang: str, *, approved_only: bool) -> GuideVersion | None`
  - `runs.prior_guide(s, project_id, target_lang: str) -> GuideVersion | None`
  - `runs.add_guide_version(s, project_id, guide, *, target_lang: str, status, author, source_run_id=None, built_from_version=None) -> GuideVersion`
  - `catalog.project_guide(db, project_id, version: int | None = None, target_lang: str | None = None) -> GuideView`
  - `catalog.guide_versions(db, project_id, target_lang: str | None = None) -> list[GuideVersionInfo]`
  - `catalog.save_project_guide(db, project_id, guide, *, base_version, target_lang: str | None = None) -> GuideView`
  - `GuideVersionInfo.target_lang: str`

- [ ] **Step 1: Write the failing migration test**

Append to `engine/tests/store/test_migrations.py` (add `import pytest`, `from sqlalchemy import MetaData, Table` and `from sqlalchemy.exc import IntegrityError` to the imports; `datetime`/`UTC` from `datetime`):

```python
def _insert(conn, name, **values):
    table = Table(name, MetaData(), autoload_with=conn)
    conn.execute(table.insert().values(**values))


def _project(conn, name, target_lang):
    pid = uuid.uuid4()
    _insert(conn, "project", id=pid, name=name, target_lang=target_lang, created_at=_NOW)
    return pid


def _run_of(conn, project_id, target_lang, minutes):
    doc = uuid.uuid4()
    _insert(
        conn, "document", id=doc, project_id=project_id, filename="a.txt", format="txt",
        sha256="x", storage_key=f"k/{doc}", created_at=_NOW,
    )
    rid = uuid.uuid4()
    _insert(
        conn, "run", id=rid, document_id=doc, state="complete", target_lang=target_lang,
        use_project_guide=False, cancel_requested=False, config_hash="h", config_snapshot={},
        prompt_versions={}, created_at=_NOW + timedelta(minutes=minutes),
    )
    return rid


def _version(conn, project_id, version, source_run_id=None):
    vid = uuid.uuid4()
    _insert(
        conn, "guide_version", id=vid, project_id=project_id, version=version, status="draft",
        author="engine", source_run_id=source_run_id, content={}, created_at=_NOW,
    )
    return vid


_NOW = datetime(2026, 10, 18, tzinfo=UTC)


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
        upgrade(url)
        with engine.begin() as conn:
            langs = dict(conn.execute(sa_text("SELECT id, target_lang FROM guide_version")).all())
            assert langs == {from_run: "es", from_default: "fr", from_earliest: "de", unknown: "und"}
            _insert(  # numbers count per language now: version 1 in French is new
                conn, "guide_version", id=uuid.uuid4(), project_id=french, version=1,
                status="draft", author="engine", target_lang="fr", content={}, created_at=_NOW,
            )
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _insert(
                conn, "guide_version", id=uuid.uuid4(), project_id=french, version=1,
                status="draft", author="engine", target_lang="es", content={}, created_at=_NOW,
            )
    finally:
        engine.dispose()
        drop_test_database(pg_admin, url)
```

Also change `test_migration_state_is_current` to expect `"0004"`. Imports to add: `from datetime import UTC, datetime, timedelta`, `from sqlalchemy import text as sa_text`.

- [ ] **Step 2: Run it to see it fail**

Run: `make db-up && cd engine && uv run pytest tests/store/test_migrations.py -q`
Expected: FAIL — `test_migration_state_is_current` (head is 0003) and `test_0004_…` (no column `target_lang`, `KeyError`/`ProgrammingError`).

- [ ] **Step 3: Write the migration and the model**

`engine/coglossa/store/migrations/versions/0004_guide_language.py`:

```python
"""each guide version belongs to one target language of its project (CGL-24)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The first rule that gives a language wins: the version's own run, the project's default, the
# project's earliest run. "und" (undetermined) is left only for a guide saved by hand in a CLI
# project with no runs and no default; no run ever reads it.
_BACKFILL = (
    """UPDATE guide_version g SET target_lang = r.target_lang
       FROM run r WHERE r.id = g.source_run_id""",
    """UPDATE guide_version g SET target_lang = p.target_lang
       FROM project p
       WHERE g.target_lang IS NULL AND p.id = g.project_id AND p.target_lang IS NOT NULL""",
    """UPDATE guide_version g SET target_lang = (
         SELECT r.target_lang FROM run r JOIN document d ON d.id = r.document_id
         WHERE d.project_id = g.project_id ORDER BY r.created_at, r.id LIMIT 1)
       WHERE g.target_lang IS NULL""",
    "UPDATE guide_version SET target_lang = 'und' WHERE target_lang IS NULL",
)


def upgrade() -> None:
    op.add_column("guide_version", sa.Column("target_lang", sa.Text(), nullable=True))
    for statement in _BACKFILL:
        op.execute(sa.text(statement))
    op.alter_column("guide_version", "target_lang", nullable=False)
    op.drop_constraint("guide_version_project_id_version_key", "guide_version", type_="unique")
    op.create_unique_constraint(
        "guide_version_project_id_target_lang_version_key",
        "guide_version",
        ["project_id", "target_lang", "version"],
    )


def downgrade() -> None:
    # Works only while no two languages of a project share a version number (before a second
    # language has a guide); otherwise the old constraint cannot be restored.
    op.drop_constraint(
        "guide_version_project_id_target_lang_version_key", "guide_version", type_="unique"
    )
    op.create_unique_constraint(
        "guide_version_project_id_version_key", "guide_version", ["project_id", "version"]
    )
    op.drop_column("guide_version", "target_lang")
```

In `models.py`, `GuideVersion`:

```python
    __table_args__ = (UniqueConstraint("project_id", "target_lang", "version"),)
    ...
    project_id: Mapped[uuid.UUID] = _cascade("project.id", index=True)
    target_lang: Mapped[str]  # the language whose guide this is (CGL-24)
    version: Mapped[int]  # counts per project and language
```

- [ ] **Step 4: Run the migration tests**

Run: `cd engine && uv run pytest tests/store/test_migrations.py -q`
Expected: PASS (including `test_models_match_the_migrations` and `test_downgrade_then_upgrade_again`).

- [ ] **Step 5: Write the failing store tests**

In `engine/tests/store/test_runs.py`: `_run(db, name="p")` gains `target_lang="en"` and passes it to `create_run`; `_draft` gains `target_lang="en"` and passes `target_lang=` to `add_guide_version`; `_project_guide(db, project_id)` calls `runs.prior_guide(s, project_id, "en")`. Update `test_guide_versions_are_numbered_per_project` calls to pass `target_lang="en"` (and `latest_guide(s, project.id, "en", approved_only=…)`). Add:

```python
async def test_guide_versions_count_and_resolve_per_language(db):
    project, _, english = await _run(db)
    _, _, spanish = await _run(db, target_lang="es")
    await _set_state(db, english.id, "complete")
    await _set_state(db, spanish.id, "complete")
    en1 = await _draft(db, project.id, english.id, "english")
    es1 = await _draft(db, project.id, spanish.id, "spanish", target_lang="es")
    en2 = await _draft(db, project.id, english.id, "english again")
    assert (en1.version, es1.version, en2.version) == (1, 1, 2)
    async with db.session() as s:
        assert (await runs.prior_guide(s, project.id, "en")).id == en2.id
        assert (await runs.prior_guide(s, project.id, "es")).id == es1.id
        assert await runs.prior_guide(s, project.id, "de") is None
        assert (await runs.latest_guide(s, project.id, "es", approved_only=False)).id == es1.id


def test_language_keys_and_order():
    assert runs.language_key("pt_BR") == runs.language_key("PT-br") == "pt-br"
    assert runs.ordered_languages("en", ["es", "EN", "fr", "es"]) == ["en", "es", "fr"]
    assert runs.ordered_languages(None, ["de", "it"]) == ["de", "it"]
    assert runs.ordered_languages(None, []) == []


async def test_set_languages_puts_the_default_first_then_first_runs(db):
    project, _, _ = await _run(db, target_lang="es")  # a CLI project: no default language
    await _run(db, target_lang="de")
    await _run(db, target_lang="ES")
    async with db.session() as s:
        row = await runs.get_project(s, project.id)
        assert await runs.set_languages(s, row) == ["es", "de"]
        row.target_lang = "fr"
        assert await runs.set_languages(s, row) == ["fr", "es", "de"]
```

(`_draft` signature becomes `async def _draft(db, project_id, run_id, summary, status=runs.DRAFT, target_lang="en")`.)

- [ ] **Step 6: Run them to see them fail**

Run: `cd engine && uv run pytest tests/store/test_runs.py -q`
Expected: FAIL — `TypeError: add_guide_version() got an unexpected keyword argument 'target_lang'`, `AttributeError: … has no attribute 'language_key'`.

- [ ] **Step 7: Implement the store functions**

In `engine/coglossa/store/runs.py` (import `Document` from models if not imported):

```python
def language_key(code: str) -> str:
    """Language codes compare case-insensitively, with - and _ alike (BCP 47)."""
    return code.casefold().replace("_", "-")


def ordered_languages(default: str | None, used: Iterable[str]) -> list[str]:
    """A set's languages (CGL-24): its default first, then `used` in order, one spelling per
    language."""
    out: list[str] = []
    seen: set[str] = set()
    for code in [*([default] if default else []), *used]:
        if language_key(code) not in seen:
            seen.add(language_key(code))
            out.append(code)
    return out


async def set_languages(s: AsyncSession, project: Project) -> list[str]:
    """The languages a project is translated into: its default, then every other language of
    its runs in the order each language's first run was created."""
    first = func.min(Run.created_at)
    rows = await s.execute(
        select(Run.target_lang, first)
        .join(Document, Run.document_id == Document.id)
        .where(Document.project_id == project.id)
        .group_by(Run.target_lang)
        .order_by(first, Run.target_lang)
    )
    return ordered_languages(project.target_lang, (lang for lang, _ in rows))
```

`latest_guide`, `prior_guide`, `add_guide_version` take the language and filter by it:

```python
async def latest_guide(
    s: AsyncSession, project_id: uuid.UUID, target_lang: str, *, approved_only: bool
) -> GuideVersion | None:
    query = select(GuideVersion).where(
        GuideVersion.project_id == project_id, GuideVersion.target_lang == target_lang
    )
    ...


async def prior_guide(
    s: AsyncSession, project_id: uuid.UUID, target_lang: str
) -> GuideVersion | None:
    """<keep the docstring; add:> Each target language has its own guide (CGL-24)."""
    return await s.scalar(
        select(GuideVersion)
        .outerjoin(Run, Run.id == GuideVersion.source_run_id)
        .where(
            GuideVersion.project_id == project_id,
            GuideVersion.target_lang == target_lang,
            usable_guide_version(),
        )
        .order_by(GuideVersion.version.desc())
        .limit(1)
    )


async def add_guide_version(
    s: AsyncSession,
    project_id: uuid.UUID,
    guide: Guide,
    *,
    target_lang: str,
    status: Literal["draft", "approved"],
    author: Literal["engine", "user"],
    source_run_id: uuid.UUID | None = None,
    built_from_version: int | None = None,
) -> GuideVersion:
    await s.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    current = await s.scalar(
        select(func.max(GuideVersion.version)).where(
            GuideVersion.project_id == project_id, GuideVersion.target_lang == target_lang
        )
    )
    row = GuideVersion(
        project_id=project_id,
        target_lang=target_lang,
        version=(current or 0) + 1,
        ...
    )
```

- [ ] **Step 8: Pass the run's language at every caller**

- `worker/execute.py` `_guide`: `runs.latest_guide(s, doc.project_id, run.target_lang, approved_only=True)`, `runs.prior_guide(s, doc.project_id, run.target_lang)` (twice: the read before the model call and `current` in `save`), `add_guide_version(..., target_lang=run.target_lang, ...)`. In `finish_run`'s sweep: `runs.prior_guide(s, doc.project_id, run.target_lang)`.
- `service.approve_guide`: `prior_guide(s, doc.project_id, run.target_lang)`, `latest_guide(s, doc.project_id, run.target_lang, approved_only=True)`, `add_guide_version(..., target_lang=run.target_lang, ...)`. `rerun_sweep`: `latest_guide(s, doc.project_id, run.target_lang, approved_only=True)`.
- `review.add_guide_entry`: `prior_guide(s, doc.project_id, run.target_lang)`, `add_guide_version(..., target_lang=run.target_lang, ...)`.
- `testing/seed.py`: `add_guide_version(s, row.id, GUIDE, target_lang=target_lang, status=…)`.
- `schema/views.py`: `GuideVersionInfo` gains `target_lang: str` after `version`; `catalog._version_info` sets `target_lang=row.target_lang`.
- `catalog.py` guide functions (the set's first language by default):

```python
async def _guide_language(s: AsyncSession, project_id: uuid.UUID, target_lang: str | None) -> str | None:
    """The language a set-guide request is about: the one asked for, else the set's first."""
    if target_lang is not None:
        return target_lang
    languages = await runs.set_languages(s, await runs.get_project(s, project_id))
    return languages[0] if languages else None


async def guide_versions(
    db: Database, project_id: uuid.UUID, target_lang: str | None = None
) -> list[GuideVersionInfo]:
    async with db.session() as s:
        lang = await _guide_language(s, project_id, target_lang)  # 404 for an unknown project
        rows = await s.scalars(
            select(GuideVersion)
            .where(GuideVersion.project_id == project_id, GuideVersion.target_lang == lang)
            .order_by(GuideVersion.version.desc())
        )
        return [_version_info(r) for r in rows]


async def project_guide(
    db: Database, project_id: uuid.UUID, version: int | None = None, target_lang: str | None = None
) -> GuideView:
    async with db.session() as s:
        lang = await _guide_language(s, project_id, target_lang)
        if version is None:
            row = await runs.prior_guide(s, project_id, lang) if lang is not None else None
            if row is None:
                raise runs.NotFoundError(<the existing message>)
        else:
            row = await s.scalar(
                select(GuideVersion).where(
                    GuideVersion.project_id == project_id,
                    GuideVersion.target_lang == lang,
                    GuideVersion.version == version,
                )
            )
            if row is None:
                raise runs.NotFoundError(f"guide version {version} not found")
        return guide_view(row)


async def save_project_guide(
    db: Database,
    project_id: uuid.UUID,
    guide: Guide,
    *,
    base_version: int | None,
    target_lang: str | None = None,
) -> GuideView:
    guide = clean_guide(guide)
    async with db.session() as s, s.begin():
        project = await runs.lock_project(s, project_id)
        lang = await _guide_language(s, project_id, target_lang)
        if lang is None:
            raise service.InvalidRequestError(
                f"choose a target language: project '{project.name}' has none yet"
            )
        latest = await runs.prior_guide(s, project_id, lang)
        ... (unchanged check)
        row = await runs.add_guide_version(
            s, project_id, guide, target_lang=lang, status=runs.APPROVED, author=runs.USER
        )
        return guide_view(row)
```

(`guide_versions` with `lang is None` matches no rows and returns `[]`.)

- [ ] **Step 9: Update the remaining test call sites**

Run: `cd engine && grep -rn "add_guide_version(\|prior_guide(\|latest_guide(" tests` and give each call the language its run uses (`"en"` in nearly all; seeded runs use `seed_run`'s `target_lang`). Do not change any assertion.

- [ ] **Step 10: Write the failing language-isolation tests**

In `engine/tests/worker/test_execute.py` (the "project guide" section):

```python
async def test_each_language_of_a_set_has_its_own_guide(services, tmp_path, fake_backend):
    """Two runs of one file in two languages: each guide is built, merged and swept within its
    own language (CGL-24)."""
    fake_backend.script("guide", _guide_reply("cat"), _guide_reply("gato"))
    english = await submit(services, tmp_path, name="one.txt")
    await drain(services)
    async with services.db.session() as s:
        doc_id = (await runs.get_run(s, english)).document_id
    spanish = await service.submit_existing(
        services.db, services.queue, services.cfg, services.prompts, doc_id,
        service.RunOptions(target_lang="es", source_lang="fr"),
    )
    await drain(services)
    assert await _state(services, spanish) is RunState.COMPLETE
    project_id = await _project_of(services, english)
    en = await catalog.project_guide(services.db, project_id, target_lang="en")
    es = await catalog.project_guide(services.db, project_id, target_lang="es")
    assert [g.rendering for g in en.guide.glossary] == ["cat"]
    assert [g.rendering for g in es.guide.glossary] == ["gato"]  # not merged onto "cat"
    assert (en.info.target_lang, es.info.target_lang) == ("en", "es")
    assert es.info.version == 1


async def test_a_lock_in_one_language_is_not_enforced_in_another(services, tmp_path):
    project_id = await _set_with_locked_cat(services, tmp_path)  # English, "chat" locked
    async with services.db.session() as s:
        doc_id = (
            await s.scalars(select(Document.id).where(Document.project_id == project_id))
        ).first()
    spanish = await service.submit_existing(
        services.db, services.queue, services.cfg, services.prompts, doc_id,
        service.RunOptions(
            target_lang="es", source_lang="fr", pipeline_profile=PipelineProfile.LONG_FORM,
            checkpoint=False,
        ),
    )
    await drain(services)
    assert (await service.build_report(services.db, spanish)).revisions == []
    assert "cat" not in [g.rendering for g in (await service.run_guide(services.db, spanish)).glossary]
```

(Add `from sqlalchemy import select` and `from coglossa.store.models import Document` to the test imports.)

In `engine/tests/test_guides.py` add:

```python
async def test_a_set_without_any_language_has_no_guide_and_refuses_a_save(db):
    async with db.session() as s, s.begin():
        project = await runs.get_or_create_project(s, "cli")  # no default, no runs
    with pytest.raises(runs.NotFoundError, match="has no guide yet"):
        await catalog.project_guide(db, project.id)
    assert await catalog.guide_versions(db, project.id) == []
    with pytest.raises(service.InvalidRequestError, match="choose a target language"):
        await catalog.save_project_guide(db, project.id, Guide(summary="", style_guide=""), base_version=None)
    saved = await catalog.save_project_guide(
        db, project.id, Guide(summary="", style_guide="x"), base_version=None, target_lang="de"
    )
    assert (saved.info.version, saved.info.target_lang) == (1, "de")
```

(Use the imports the file already has; add `pytest`, `catalog`, `service`, `runs`, `Guide` if missing.)

- [ ] **Step 11: Run them to see them pass (implementation is in place) — then prove they guard**

Run: `cd engine && uv run pytest tests/worker/test_execute.py -k "language" tests/test_guides.py -q`
Expected: PASS. Then temporarily drop `GuideVersion.target_lang == target_lang` from `prior_guide` and re-run: `test_each_language_of_a_set_has_its_own_guide` must FAIL (Spanish shows "cat"). Restore.

- [ ] **Step 12: Full engine suite**

Run: `make test-engine`
Expected: all pass, coverage ≥ 95%.

- [ ] **Step 13: Commit**

```bash
git add engine
git commit -m "feat(engine): each language of a set has its own translation guide (CGL-24)"
```

---

### Task 2: A set reports its languages (catalog)

**Files:**
- Modify: `engine/coglossa/schema/views.py` (`SetLanguage`, `ProjectInfo.languages`)
- Modify: `engine/coglossa/catalog.py` (`_projects_query`, `_set_summaries`, `_project_info`, `list_projects`, `get_project`)
- Test: `engine/tests/test_catalog.py`, `engine/tests/store/test_runs.py` (`_project_guide`)

**Interfaces:**
- Consumes: `runs.ordered_languages`, `runs.usable_guide_version()` (Task 1).
- Produces: `SetLanguage(target_lang: str, guide_version: int | None)`; `ProjectInfo.languages: list[SetLanguage]`; `ProjectInfo.guide_version` = first language's version; `files_*`/`open_flags` count each file's newest run per language.

- [ ] **Step 1: Write the failing tests**

In `engine/tests/store/test_runs.py`, `_project_guide(db, project_id, target_lang="en")` now asserts against the language entry:

```python
async def _project_guide(db, project_id, target_lang="en"):
    """prior_guide's version and the catalog's (its SQL mirror); they must always agree."""
    async with db.session() as s:
        row = await runs.prior_guide(s, project_id, target_lang)
    info = await catalog.get_project(db, project_id)
    listed = {l.target_lang: l.guide_version for l in info.languages}
    assert listed.get(target_lang) == (row.version if row else None)
    return row
```

In `engine/tests/test_catalog.py` add (uses `seed_run`; same `project="demo"` puts both runs in one set):

```python
async def test_a_set_lists_its_languages_with_their_guides_and_counts_each(db):
    english = await seed_run(db, target_lang="en")
    spanish = await seed_run(db, target_lang="es", state=RunState.TRANSLATING)
    async with db.session() as s, s.begin():  # the Spanish run is of the same file
        (await runs.get_run(s, spanish.run_id, lock=True)).document_id = english.document_id
    info = await catalog.get_project(db, english.project_id)
    assert [(l.target_lang, l.guide_version) for l in info.languages] == [("en", 1), ("es", 1)]
    assert info.guide_version == 1
    assert (info.files_ready, info.files_translating) == (1, 1)  # one file, two languages
    listed = {p.id: p for p in await catalog.list_projects(db)}
    assert listed[str(english.project_id)].languages == info.languages


async def test_a_new_set_lists_its_default_language_without_a_guide(db):
    info = await catalog.create_project(db, "Atlas", target_lang="fr")
    assert [(l.target_lang, l.guide_version) for l in info.languages] == [("fr", None)]
```

(Note: `seed_run` adds a guide version for the run's language: version 1 in each language.)

- [ ] **Step 2: Run them to see them fail**

Run: `cd engine && uv run pytest tests/test_catalog.py tests/store/test_runs.py -q`
Expected: FAIL — `AttributeError: 'ProjectInfo' object has no attribute 'languages'`.

- [ ] **Step 3: Implement**

`schema/views.py`:

```python
class SetLanguage(BaseModel):
    """One language a set is translated into (CGL-24)."""

    target_lang: str
    guide_version: int | None  # its set guide's version (runs.prior_guide); None: no guide yet


class ProjectInfo(BaseModel):
    ...
    guide_version: int | None  # the first language's guide version; None: no guide yet
    languages: list[SetLanguage]  # the default language first, then by first run
    ...
    files_translating: int  # files whose newest run in a language is under way, per language
    files_ready: int  # per file and language: the newest run finished with output
    files_failed: int  # per file and language: the newest run failed or was cancelled
    open_flags: int  # open flags on the newest run of each file in each language
```

`catalog.py`:
- `_projects_query()` returns `select(Project, documents, run_count)` (drop the `guide` subquery; its type becomes `Select[tuple[Project, int, int]]`).
- `_set_summaries`: `partition_by=(Run.document_id, Run.target_lang)`.
- New:

```python
async def _set_languages(
    s: AsyncSession, projects: list[Project]
) -> dict[uuid.UUID, list[SetLanguage]]:
    """Two grouped queries for the whole list: the languages of the runs, and each language's
    set guide version (runs.prior_guide's SQL mirror)."""
    ids = [p.id for p in projects]
    if not ids:
        return {}
    first = func.min(Run.created_at)
    used: dict[uuid.UUID, list[str]] = defaultdict(list)
    for project_id, lang, _ in await s.execute(
        select(Document.project_id, Run.target_lang, first)
        .join(Run, Run.document_id == Document.id)
        .where(Document.project_id.in_(ids))
        .group_by(Document.project_id, Run.target_lang)
        .order_by(Document.project_id, first, Run.target_lang)
    ):
        used[project_id].append(lang)
    versions = {
        (project_id, lang): version
        for project_id, lang, version in await s.execute(
            select(GuideVersion.project_id, GuideVersion.target_lang, func.max(GuideVersion.version))
            .outerjoin(Run, Run.id == GuideVersion.source_run_id)
            .where(GuideVersion.project_id.in_(ids), runs.usable_guide_version())
            .group_by(GuideVersion.project_id, GuideVersion.target_lang)
        )
    }
    return {
        p.id: [
            SetLanguage(target_lang=lang, guide_version=versions.get((p.id, lang)))
            for lang in runs.ordered_languages(p.target_lang, used[p.id])
        ]
        for p in projects
    }
```

- `_project_info(project, documents, run_count, languages, summary)` sets `languages=languages` and `guide_version=languages[0].guide_version if languages else None`.
- `list_projects` / `get_project`: after the rows, `languages = await _set_languages(s, [row[0] for row in rows])` and pass `languages[row[0].id]`.

- [ ] **Step 4: Run the tests**

Run: `cd engine && uv run pytest tests/test_catalog.py tests/store/test_runs.py -q`
Expected: PASS.

- [ ] **Step 5: Full engine suite, then commit**

Run: `make test-engine` — Expected: all pass, coverage ≥ 95%.

```bash
git add engine
git commit -m "feat(engine): a set lists its languages and counts files per language (CGL-24)"
```

---

### Task 3: Start a language for every file; uploads into every language (service)

**Files:**
- Modify: `engine/coglossa/service.py` (`LanguageExistsError`, `add_language`, `submit_bytes`)
- Test: `engine/tests/test_service.py`

**Interfaces:**
- Consumes: `runs.set_languages`, `runs.language_key`, `runs.lock_project` (Task 1).
- Produces:
  - `service.LanguageExistsError(ConflictError)` with `code = "language_exists"`
  - `service.add_language(db, queue, cfg, prompts, project_id: uuid.UUID, opts: RunOptions) -> list[uuid.UUID]` (one run per document, oldest document first; `checkpoint=False`, `use_project_guide=False` forced)
  - `service.submit_bytes(..., *, project, also: Sequence[str] = ())` — extra target languages, each a run of the new document; returns the run in `opts.target_lang`

- [ ] **Step 1: Write the failing tests**

In `engine/tests/test_service.py` (follow its existing fixtures for `db`, a recording queue, `cfg`, `prompts`, `storage`; use the helper the file already uses to submit bytes):

```python
async def test_add_language_starts_every_file_once(db, queue, cfg, prompts, storage):
    first = await service.submit_bytes(
        db, storage, queue, cfg, prompts, "a.txt", b"Le chat.", RunOptions(target_lang="en"), project="set"
    )
    await service.submit_bytes(
        db, storage, queue, cfg, prompts, "b.txt", b"Le chien.", RunOptions(target_lang="en"), project="set"
    )
    project_id = (await catalog.run_detail(db, first)).run.project_id
    queue.jobs.clear()
    started = await service.add_language(
        db, queue, cfg, prompts, uuid.UUID(project_id),
        RunOptions(target_lang="es", checkpoint=True, use_project_guide=True),
    )
    assert len(started) == 2
    async with db.session() as s:
        made = [await runs.get_run(s, r) for r in started]
    assert {r.target_lang for r in made} == {"es"}
    assert all(r.checkpoint is False and not r.use_project_guide for r in made)
    assert [j.kwargs["run_id"] for j in queue.jobs] == [str(r) for r in started]
    with pytest.raises(service.LanguageExistsError) as refused:
        await service.add_language(db, queue, cfg, prompts, uuid.UUID(project_id), RunOptions(target_lang="ES"))
    assert refused.value.code == "language_exists"


async def test_add_language_needs_files(db, queue, cfg, prompts):
    info = await catalog.create_project(db, "empty", target_lang="en")
    with pytest.raises(service.InvalidRequestError, match="has no files"):
        await service.add_language(db, queue, cfg, prompts, uuid.UUID(info.id), RunOptions(target_lang="es"))
    assert queue.jobs == []


async def test_an_upload_can_start_in_several_languages(db, queue, cfg, prompts, storage):
    run_id = await service.submit_bytes(
        db, storage, queue, cfg, prompts, "a.txt", b"Le chat.", RunOptions(target_lang="es"),
        project="set", also=["en", "de"],
    )
    async with db.session() as s:
        doc_id = (await runs.get_run(s, run_id)).document_id
        langs = list(await s.scalars(select(Run.target_lang).where(Run.document_id == doc_id)))
    assert (await catalog.run_detail(db, run_id)).run.target_lang == "es"
    assert sorted(langs) == ["de", "en", "es"]
    assert len(queue.jobs) == 3
```

(If `test_service.py` names its fixtures differently, use its names; the assertions stay.)

- [ ] **Step 2: Run them to see them fail**

Run: `cd engine && uv run pytest tests/test_service.py -k "language or several" -q`
Expected: FAIL — `AttributeError: module 'coglossa.service' has no attribute 'add_language'`.

- [ ] **Step 3: Implement**

```python
class LanguageExistsError(ConflictError):
    """The set is already translated into that language (CGL-24)."""

    code = "language_exists"


async def add_language(
    db: Database,
    queue: JobQueue,
    cfg: CoglossaConfig,
    prompts: PromptLibrary,
    project_id: uuid.UUID,
    opts: RunOptions,
) -> list[uuid.UUID]:
    """Every file of a set in another language, with nothing uploaded again (CGL-24). One
    transaction under the project lock: every file gets a run or none does. The runs build the
    new language's own guide and never pause for it."""
    opts = dataclasses.replace(opts, checkpoint=False, use_project_guide=False)
    async with db.session() as s, s.begin():
        project = await runs.lock_project(s, project_id)
        wanted = runs.language_key(opts.target_lang)
        if any(runs.language_key(lang) == wanted for lang in await runs.set_languages(s, project)):
            raise LanguageExistsError(
                f"project '{project.name}' is already translated into {opts.target_lang}"
            )
        documents = list(
            await s.scalars(
                select(Document.id)
                .where(Document.project_id == project_id)
                .order_by(Document.created_at, Document.id)
            )
        )
        if not documents:
            raise InvalidRequestError(f"project '{project.name}' has no files to translate")
        run_ids = [await _new_run(s, doc_id, cfg, prompts, opts) for doc_id in documents]
    for run_id in run_ids:
        await _queue_run_job(queue, TASK_START_RUN, run_id)
    return run_ids
```

`submit_bytes` gains `also: Sequence[str] = ()` (keyword-only, after `project`); inside the transaction after `run_id = await _new_run(...)`:

```python
            others = [
                await _new_run(s, doc.id, cfg, prompts, dataclasses.replace(opts, target_lang=lang))
                for lang in also
            ]
```

and after the commit: `for queued in (run_id, *others): await _queue_run_job(queue, TASK_START_RUN, queued)`. Docstring: add "`also`: more target languages, each a run of the new document (a set with several languages, CGL-24)". Add imports `dataclasses`, `Sequence`, `select`, `Document` if missing. If `RunOptions` is not a dataclass, use its own copy method; check its definition first.

- [ ] **Step 4: Run the tests**

Run: `cd engine && uv run pytest tests/test_service.py -q`
Expected: PASS.

- [ ] **Step 5: Full engine suite, then commit**

Run: `make test-engine` — Expected: all pass, coverage ≥ 95%.

```bash
git add engine
git commit -m "feat(engine): start every file of a set in another language (CGL-24)"
```

---

### Task 4: API — languages endpoint, uploads into every language, guide language

**Files:**
- Modify: `api/coglossa_api/models.py` (`AddLanguage`)
- Modify: `api/coglossa_api/routes/projects.py` (`add_language` route; `upload` gains `all_languages`)
- Modify: `api/coglossa_api/routes/guides.py` (`target_lang` query on the three project-guide routes)
- Regenerate: `schema/openapi.json`, `web/src/api/schema.d.ts` (`make schema`)
- Test: `api/tests/test_projects.py`, `api/tests/test_guides.py`

**Interfaces:**
- Consumes: `service.add_language`, `service.submit_bytes(also=…)`, `catalog` guide functions with `target_lang` (Tasks 1–3), `ProjectInfo.languages` (Task 2).
- Produces (HTTP):
  - `POST /api/projects/{project_id}/languages` body `{"target_lang": "es"}` → 202 `list[RunSummary]`; 409 `{"detail": …, "code": "language_exists"}`; 422 for a set with no files or a bad code; 404 unknown set.
  - `POST /api/projects/{project_id}/runs` form field `all_languages` (bool, default false).
  - `GET /api/projects/{id}/guide?target_lang=`, `GET …/guide/versions?target_lang=`, `PUT …/guide?target_lang=`.

- [ ] **Step 1: Write the failing tests**

`api/tests/test_projects.py`:

```python
async def test_translate_a_set_into_another_language(api, db):
    seeded = await seed_run(db)  # one file, English
    url = f"/api/projects/{seeded.project_id}/languages"
    started = await api.client.post(url, json={"target_lang": "es"})
    assert started.status_code == 202
    assert [(r["document_id"], r["target_lang"]) for r in started.json()] == [
        (str(seeded.document_id), "es")
    ]
    assert [j.task for j in api.queue.jobs] == ["coglossa.start_run"]
    info = (await api.client.get(f"/api/projects/{seeded.project_id}")).json()
    assert [l["target_lang"] for l in info["languages"]] == ["en", "es"]
    again = await api.client.post(url, json={"target_lang": "es"})
    assert again.status_code == 409 and again.json()["code"] == "language_exists"
    bad = await api.client.post(url, json={"target_lang": "not a code"})
    assert bad.status_code == 422


async def test_a_set_without_files_has_no_language_to_add(api):
    created = (await api.client.post("/api/projects", json={"name": "E", "target_lang": "en"})).json()
    refused = await api.client.post(f"/api/projects/{created['id']}/languages", json={"target_lang": "es"})
    assert refused.status_code == 422 and "has no files" in refused.json()["detail"]


async def test_an_upload_into_every_language_of_the_set(api, db):
    seeded = await seed_run(db)
    await api.client.post(f"/api/projects/{seeded.project_id}/languages", json={"target_lang": "es"})
    api.queue.jobs.clear()
    uploaded = await api.client.post(
        f"/api/projects/{seeded.project_id}/runs",
        files={"file": ("b.txt", b"Le chien.", "text/plain")},
        data={"target_lang": "es", "all_languages": "true"},
    )
    assert uploaded.status_code == 202 and uploaded.json()["target_lang"] == "es"
    runs_of_file = (await api.client.get(f"/api/runs?document_id={uploaded.json()['document_id']}")).json()
    assert sorted(r["target_lang"] for r in runs_of_file) == ["en", "es"]
    assert len(api.queue.jobs) == 2
```

`api/tests/test_guides.py`:

```python
async def test_the_set_guide_of_each_language(api, db):
    english = await seed_run(db)
    spanish = await seed_run(db, target_lang="es")
    base = f"/api/projects/{english.project_id}/guide"
    assert (await api.client.get(base)).json()["info"]["target_lang"] == "en"  # the first language
    es = (await api.client.get(base, params={"target_lang": "es"})).json()
    assert (es["info"]["target_lang"], es["info"]["version"]) == ("es", 1)
    saved = await api.client.put(
        base, params={"target_lang": "es"}, json={"guide": LOCKED, "base_version": 1}
    )
    assert saved.status_code == 200 and saved.json()["info"]["version"] == 2
    versions = (await api.client.get(f"{base}/versions", params={"target_lang": "es"})).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert [v["version"] for v in (await api.client.get(f"{base}/versions")).json()] == [1]
    assert spanish.project_id == english.project_id
```

- [ ] **Step 2: Run them to see them fail**

Run: `make db-up && cd api && uv run pytest tests/test_projects.py tests/test_guides.py -q`
Expected: FAIL — 404/405 on `/languages`, missing `target_lang` in guide info is fine already; `target_lang` query ignored → Spanish assertions fail.

- [ ] **Step 3: Implement**

`models.py` (next to `RunRequest`, reusing its language field annotation):

```python
class AddLanguage(BaseModel):
    """Translate every file of a set into another language (CGL-24)."""

    target_lang: str = Field(pattern=LANGUAGE_CODE_PATTERN, max_length=35)
```

`routes/projects.py`:

```python
@router.post(
    "/projects/{project_id}/languages",
    status_code=202,
    responses={
        404: {"model": ErrorBody, "description": "No such project."},
        409: {"model": ErrorBody, "description": "The set is already in that language."},
    },
)
async def add_language(project_id: uuid.UUID, body: AddLanguage, ctx: Ctx) -> list[RunSummary]:
    """Translate every file of the set into another language, with nothing uploaded again
    (CGL-24). Each language has its own translation guide. Refused: a language the set already
    has (409, code `language_exists`), a set with no files (422)."""
    project = await catalog.get_project(ctx.db, project_id)
    opts = run_options(
        project,
        target_lang=body.target_lang,
        source_lang=None,
        pipeline_profile=None,
        checkpoint=False,
        use_project_guide=False,
    )
    run_ids = await service.add_language(ctx.db, ctx.queue, ctx.cfg, ctx.prompts, project_id, opts)
    return [(await catalog.run_detail(ctx.db, run_id)).run for run_id in run_ids]
```

`upload` gains `all_languages: Annotated[bool, Form()] = False` and passes

```python
        also=[
            lang.target_lang
            for lang in project.languages
            if runs.language_key(lang.target_lang) != runs.language_key(opts.target_lang)
        ]
        if all_languages
        else [],
```

to `service.submit_bytes` (import `from coglossa.store import runs`). Docstring: "`all_languages`: also start the file in every other language of the set."

`routes/guides.py`: `LanguageQuery = Annotated[str | None, Query(pattern=LANGUAGE_CODE_PATTERN, max_length=35)]`; each project-guide route takes `target_lang: LanguageQuery = None` and passes `target_lang=target_lang` to the catalog. Docstrings: "`target_lang`: which language's guide; the set's first language when left out."

- [ ] **Step 4: Run the tests and regenerate the schema**

Run: `cd api && uv run pytest -q` — Expected: PASS.
Run: `make schema` — Expected: `schema/openapi.json` and `web/src/api/schema.d.ts` change (new route, fields). Then `make test-api` — Expected: PASS (including `test_openapi.py`).

- [ ] **Step 5: Commit**

```bash
git add api schema web/src/api/schema.d.ts
git commit -m "feat(api): translate a set into another language; guides per language (CGL-24)"
```

---

### Task 5: Web data layer knows the current language

**Files:**
- Create: `web/src/set/languages.ts`, `web/src/set/languages.test.ts`
- Modify: `web/src/api/types.ts`, `web/src/api/sets.ts`, `web/src/api/guide.ts`, `web/src/api/projects.ts`, `web/src/upload/queue.ts`, `web/src/test/fixtures.ts`
- Modify callers so the app compiles with unchanged behaviour: `web/src/set/SetHeader.tsx`, `web/src/set/SummaryBanner.tsx`, `web/src/guide/GuidePanel.tsx`, `web/src/review/AlwaysTranslate.tsx`, `web/src/set/SetPage.tsx`
- Test: `web/src/api/sets.test.ts`, `web/src/api/guide.test.ts`, `web/src/api/projects.test.ts`

**Interfaces:**
- Consumes: generated types (Task 4).
- Produces:
  - `languageKey(code: string): string`; `sameLanguage(a: string, b: string): boolean`
  - `languagesOf(project: ProjectInfo): string[]` (`project.languages` codes; `[project.target_lang]` when empty and set; else `[]`)
  - `runsIn(doc: DocumentSummary, lang: string): RunSummary[]` (newest first); `newestIn(doc, lang): RunSummary | undefined`
  - `newestRuns(doc: DocumentSummary): RunSummary[]` (newest run of each language)
  - `languageStatus(docs: DocumentSummary[], lang: string): Status | null`
  - `listLanguages(codes: string[]): string` ("Spanish, German and French")
  - `useTranslateAgain(projectId).mutate({ documentId, targetLang })`
  - `recheckSet(projectId, targetLang, only?)`, `useRecheckSet(projectId, targetLang)`
  - `guideKeys.project(projectId, lang)`, `guideKeys.versions(projectId, lang)`; `fetchProjectGuide(projectId, lang, version?)`; `useProjectGuide(projectId, lang)`; `useSaveProjectGuide(projectId, lang)`; `guideSaved(queryClient, projectId, lang, view)`
  - `useAddLanguage(projectId)` → `mutate(targetLang)` resolves `RunSummary[]`
  - `UploadOptions.all_languages?: boolean`

- [ ] **Step 1: Write the failing tests**

`web/src/set/languages.test.ts`:

```ts
import { documentSummary, project, runSummary } from "../test/fixtures";
import {
  languageKey, languageStatus, languagesOf, listLanguages, newestIn, newestRuns, runsIn, sameLanguage,
} from "./languages";

const es = (id: string, state: typeof runSummary.state, created: string) => ({
  ...runSummary, id, target_lang: "es", state, created_at: created,
});
const doc = {
  ...documentSummary,
  runs: [es("r-3", "translating", "2026-10-03T00:00:00Z"), es("r-2", "complete", "2026-10-02T00:00:00Z"), runSummary],
};

it("compares language codes the way BCP 47 does", () => {
  expect(languageKey("pt_BR")).toBe("pt-br");
  expect(sameLanguage("ES", "es")).toBe(true);
  expect(sameLanguage("es", "en")).toBe(false);
});

it("lists a set's languages, falling back to its default", () => {
  expect(languagesOf({ ...project, languages: [{ target_lang: "en", guide_version: 1 }, { target_lang: "es", guide_version: null }] })).toEqual(["en", "es"]);
  expect(languagesOf({ ...project, languages: [] })).toEqual(["en"]);
  expect(languagesOf({ ...project, languages: [], target_lang: null })).toEqual([]);
});

it("finds a file's runs in one language, newest first", () => {
  expect(runsIn(doc, "es").map((r) => r.id)).toEqual(["r-3", "r-2"]);
  expect(newestIn(doc, "ES")?.id).toBe("r-3");
  expect(newestIn(doc, "de")).toBeUndefined();
  expect(newestRuns(doc).map((r) => r.id)).toEqual(["r-3", "r-1"]);
});

it("sums up one language's files", () => {
  expect(languageStatus([doc], "es")?.text).toBe("translating…");
  expect(languageStatus([doc], "en")?.text).toBe("ready");
  expect(languageStatus([{ ...doc, runs: [{ ...runSummary, state: "failed" }] }, doc], "en")?.text).toBe("needs attention");
  expect(languageStatus([doc], "de")).toBeNull();
});

it("names languages in a sentence", () => {
  expect(listLanguages(["es"])).toBe("Spanish");
  expect(listLanguages(["es", "de", "fr"])).toBe("Spanish, German and French");
});
```

(`listLanguages` uses `languageName` with the test locale `en`; if the suite's locale yields "Spanish, German, and French", match the formatter's output by building the sentence by hand: `a, b and c` — see Step 3.)

In `web/src/api/sets.test.ts`, add:

```ts
it("translates a file again in the language asked for", async () => {
  let body: unknown;
  server.use(
    http.post("*/api/documents/d-1/runs", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...runSummary, id: "r-9", target_lang: "es" }, { status: 202 });
    }),
  );
  const { result } = renderHook(() => useTranslateAgain("p-1"), { wrapper: queryWrapper() });
  await act(() => result.current.mutateAsync({ documentId: "d-1", targetLang: "es" }));
  expect(body).toEqual({ checkpoint: false, use_project_guide: false, target_lang: "es" });
});

it("rechecks only the files of one language", async () => {
  const swept: string[] = [];
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([{ ...documentSummary, runs: [{ ...runSummary, id: "r-es", target_lang: "es" }, runSummary] }]),
    ),
    http.post("*/api/runs/:id/sweep", ({ params }) => {
      swept.push(params["id"] as string);
      return HttpResponse.json({}, { status: 202 });
    }),
  );
  expect((await recheckSet("p-1", "en")).started).toEqual(["r-1"]);
  expect(swept).toEqual(["r-1"]);
});

it("starts every file in another language", async () => {
  let body: unknown;
  server.use(
    http.post("*/api/projects/p-1/languages", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json([{ ...runSummary, id: "r-es", target_lang: "es" }], { status: 202 });
    }),
  );
  const { result } = renderHook(() => useAddLanguage("p-1"), { wrapper: queryWrapper() });
  const runs = await act(() => result.current.mutateAsync("es"));
  expect(body).toEqual({ target_lang: "es" });
  expect(runs.map((r) => r.id)).toEqual(["r-es"]);
});
```

In `web/src/api/guide.test.ts`, add a test that `fetchProjectGuide("p-1", "es")` requests `…/guide?target_lang=es` (capture `new URL(request.url).searchParams.get("target_lang")`). In `web/src/api/projects.test.ts`, add one that `uploadDocument` with `all_languages: true` sends the form field `all_languages=true`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && pnpm vitest run src/set/languages.test.ts src/api`
Expected: FAIL — module `./languages` not found; `useAddLanguage` not exported; body lacks `target_lang`.

- [ ] **Step 3: Implement**

`web/src/api/types.ts`: `export type SetLanguage = S["SetLanguage"];`

`web/src/set/languages.ts`:

```ts
import type { DocumentSummary, ProjectInfo, RunSummary } from "../api/types";
import { languageName } from "../lib/languages";
import { runStateStatus, type Status } from "../lib/status";

/** Language codes compare case-insensitively, with - and _ alike (BCP 47); as the engine does. */
export function languageKey(code: string): string {
  return code.toLowerCase().replaceAll("_", "-");
}

export function sameLanguage(a: string, b: string): boolean {
  return languageKey(a) === languageKey(b);
}

/** CGL-24: the languages a set is translated into, its first language first. */
export function languagesOf(project: ProjectInfo): string[] {
  if (project.languages.length > 0) return project.languages.map((l) => l.target_lang);
  return project.target_lang ? [project.target_lang] : [];
}

/** A file's runs in one language, newest first (the API lists them newest first). */
export function runsIn(doc: DocumentSummary, lang: string): RunSummary[] {
  return doc.runs.filter((r) => sameLanguage(r.target_lang, lang));
}

export function newestIn(doc: DocumentSummary, lang: string): RunSummary | undefined {
  return doc.runs.find((r) => sameLanguage(r.target_lang, lang));
}

/** The newest run of a file in each of its languages. */
export function newestRuns(doc: DocumentSummary): RunSummary[] {
  const seen = new Set<string>();
  return doc.runs.filter((r) => {
    const key = languageKey(r.target_lang);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

const ORDER: Status["tone"][] = ["bad", "busy", "warn", "ok", "none"];

/** A language tab's status: the most pressing status of its files' newest runs. */
export function languageStatus(docs: DocumentSummary[], lang: string): Status | null {
  const statuses = docs.flatMap((d) => {
    const run = newestIn(d, lang);
    return run ? [runStateStatus(run)] : [];
  });
  if (statuses.length === 0) return null;
  return statuses.reduce((worst, s) => (ORDER.indexOf(s.tone) < ORDER.indexOf(worst.tone) ? s : worst));
}

/** "Spanish", "Spanish and German", "Spanish, German and French". */
export function listLanguages(codes: string[]): string {
  const names = codes.map((c) => languageName(c));
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}
```

`web/src/api/sets.ts`:
- `useSetDocuments` refetch and `useSetBusy`: replace `d.runs[0] !== undefined && !isTerminal(d.runs[0].state)` with `newestRuns(d).some((r) => !isTerminal(r.state))`.
- `useTranslateAgain`: `mutationFn: ({ documentId, targetLang }: { documentId: string; targetLang: string }) => … body: { checkpoint: false, use_project_guide: false, target_lang: targetLang }`.
- `recheckSet(projectId: string, targetLang: string, only?: string[])`: `const run = newestIn(doc, targetLang);` (rest unchanged); docstring: "…of every file in the set, in one language (CGL-24)…". `useRecheckSet(projectId: string, targetLang: string)` passes it.
- New:

```ts
/** CGL-24: every file of the set in another language. */
export function useAddLanguage(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (targetLang: string) =>
      unwrap(
        client.POST("/api/projects/{project_id}/languages", {
          params: { path: { project_id: projectId } },
          body: { target_lang: targetLang },
        }),
      ),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
      void queryClient.invalidateQueries({ queryKey: projectKeys.one(projectId), exact: true });
      return queryClient.invalidateQueries({ queryKey: projectKeys.documents(projectId) });
    },
  });
}
```

(`onSettled`, not `onSuccess`: after a 409 the set is refreshed so the language that exists shows.)

`web/src/api/guide.ts`:

```ts
export const guideKeys = {
  all: (projectId: string) => ["projects", projectId, "guide"] as const,
  project: (projectId: string, lang: string) => ["projects", projectId, "guide", lang] as const,
  versions: (projectId: string, lang: string) => ["projects", projectId, "guide", lang, "versions"] as const,
};
```

`fetchProjectGuide(projectId, lang, version?)` sends `query: version === undefined ? { target_lang: lang } : { target_lang: lang, version }`. `useProjectGuide(projectId, lang)` and `useSaveProjectGuide(projectId, lang)` (`params: { path, query: { target_lang: lang } }`) use the language keys; `onSuccess: (view) => guideSaved(queryClient, projectId, lang, view)`. `guideChanged(queryClient, projectId)` invalidates `guideKeys.all(projectId)` (every language: an approval or Always translate changes one, refreshing all is cheap) plus the project keys as before; `guideSaved(queryClient, projectId, lang, view)` sets `guideKeys.project(projectId, lang)` and then calls the same counts refresh.

`web/src/api/projects.ts`: `Upload` gains `all_languages?: boolean` (the body serializer already sends booleans as strings). `web/src/upload/queue.ts`: `UploadOptions` gains `all_languages?: boolean` (spread into the upload as today).

`web/src/test/fixtures.ts`: `project.languages = [{ target_lang: "en", guide_version: 1 }]`; `otherProject.languages = [{ target_lang: "ar", guide_version: null }]`; `guideView.info.target_lang = "en"` (and any `GuideVersionInfo` fixture).

Callers (behaviour unchanged for one language):
- `SetHeader`: `useProjectGuide(project.id, target ?? "")` where `target` is computed as today; `again.mutate({ documentId: picked.doc.id, targetLang: run?.target_lang ?? target ?? "" }, …)`. (Task 6 replaces these with the current language.)
- `SummaryBanner`: `again.mutate({ documentId: run.document_id, targetLang: run.target_lang }, …)`.
- `GuidePanel`: takes a new prop `targetLang: string`; `ProjectGuide` uses `useProjectGuide(projectId, targetLang)`; the editor's save uses `useSaveProjectGuide(projectId, targetLang)`; `RecheckPrompt` uses `useRecheckSet(projectId, targetLang)`; add `targetLang` to `Shared`.
- `SetPage`: passes `targetLang={picked?.run?.target_lang ?? p.target_lang ?? ""}` to `GuidePanel` (Task 6 replaces it).
- `AlwaysTranslate`: `useRecheckSet(projectId, languages.target)`.
- Update existing tests that call these hooks (`useTranslateAgain(...).mutate("d-1")` → object form, `recheckSet("p-1")` → `recheckSet("p-1", "en")`, MSW handlers that match `*/api/projects/p-1/guide` keep working since the query string does not affect MSW path matching).

- [ ] **Step 4: Run the web suite**

Run: `cd web && pnpm typecheck && pnpm test`
Expected: PASS, coverage ≥ 95% (all four metrics).

- [ ] **Step 5: Commit**

```bash
git add web
git commit -m "feat(web): the set's data hooks take a language (CGL-24)"
```

---

### Task 6: The set page follows the current language

**Files:**
- Create: `web/src/set/LanguageTabs.tsx`, `web/src/set/LanguageTabs.test.tsx`
- Modify: `web/src/set/SetPage.tsx` (`currentLanguage`, `pickRun`, wiring), `web/src/set/FileTabs.tsx`, `web/src/set/SetHeader.tsx`, `web/src/set/AddFilesDialog.tsx`, `web/src/components/SetDialogs.tsx`, `web/src/styles/*.css` (language tabs reuse the file-tab styles; add only layout if needed, tokens only)
- Test: `web/src/set/SetPage.test.tsx`, `web/src/set/FileTabs.test.tsx`, `web/src/components/SetDialogs.test.ts` (or its `.tsx` test)

**Interfaces:**
- Consumes: Task 5 helpers and hooks.
- Produces:
  - `currentLanguage(project: ProjectInfo, docs: DocumentSummary[], runId: string | undefined, asked: string | null): string`
  - `pickRun(docs, runId, docId, lang): Picked | null`
  - `<LanguageTabs project docs lang picked />`
  - `SetHeader` props gain `lang: string` (Task 7 adds `onAddLanguage`)
  - `FileTabs` props gain `lang: string`; `AddFilesDialog` props gain `lang: string`

- [ ] **Step 1: Write the failing tests**

`web/src/set/SetPage.test.tsx` — add (reuse `serve`, `open`, `second` already in the file):

```ts
const spanishProject = {
  ...project,
  languages: [
    { target_lang: "en", guide_version: 1 },
    { target_lang: "es", guide_version: 1 },
  ],
};
const bilingual = {
  ...documentSummary,
  runs: [{ ...runSummary, id: "r-es", target_lang: "es" }, runSummary],
};

function serveBilingual(docs = [bilingual]) {
  serve(docs);
  server.use(http.get("*/api/projects/p-1", () => HttpResponse.json(spanishProject)));
}

it("pickRun keeps to one language", () => {
  expect(pickRun([bilingual], undefined, null, "en")?.run?.id).toBe("r-1");
  expect(pickRun([bilingual], undefined, null, "es")?.run?.id).toBe("r-es");
  expect(pickRun([bilingual], "r-1", null, "es")).toMatchObject({ run: { id: "r-1" }, latest: true });
});

it("shows no language tabs for one language", async () => {
  serve([documentSummary]);
  open();
  await screen.findByRole("heading", { name: "Atlas" });
  expect(screen.queryByRole("navigation", { name: "Languages" })).toBeNull();
});

it("switches language and keeps the file", async () => {
  serveBilingual();
  const { router } = open("/t/p-1/r-1");
  const tabs = await screen.findByRole("navigation", { name: "Languages" });
  expect(within(tabs).getByRole("link", { name: /English/ })).toHaveAttribute("aria-current", "page");
  await userEvent.click(within(tabs).getByRole("link", { name: /Spanish/ }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/t/p-1/r-es"));
  expect(screen.getByText(/French → Spanish/)).toBeInTheDocument();
});

it("the run in the address decides the language over ?lang=", async () => {
  serveBilingual();
  open("/t/p-1/r-1?lang=es");
  const tabs = await screen.findByRole("navigation", { name: "Languages" });
  expect(within(tabs).getByRole("link", { name: /English/ })).toHaveAttribute("aria-current", "page");
});

it("offers to translate a file that is missing in this language", async () => {
  let body: unknown;
  serveBilingual([bilingual, second]); // second has no Spanish run
  server.use(
    http.post("*/api/documents/d-2/runs", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...runSummary, id: "r-new", document_id: "d-2", target_lang: "es" }, { status: 202 });
    }),
  );
  const { router } = open("/t/p-1?file=d-2&lang=es");
  expect(await screen.findByText("This file hasn't been translated into Spanish yet.")).toBeInTheDocument();
  const files = screen.getByRole("navigation", { name: "Files" });
  expect(within(files).getByRole("link", { name: /b\.txt/ })).toHaveTextContent("not started");
  await userEvent.click(screen.getByRole("button", { name: "Translate into Spanish" }));
  expect(body).toMatchObject({ target_lang: "es" });
  await waitFor(() => expect(router.state.location.pathname).toBe("/t/p-1/r-new"));
});

it("opens the guide of the current language", async () => {
  let asked: string | null = null;
  serveBilingual();
  server.use(
    http.get("*/api/projects/p-1/guide", ({ request }) => {
      asked = new URL(request.url).searchParams.get("target_lang");
      return HttpResponse.json({ ...guideView, info: { ...guideView.info, target_lang: "es" } });
    }),
  );
  open("/t/p-1/r-es");
  await userEvent.click(await screen.findByRole("button", { name: /Translation guide/ }));
  await waitFor(() => expect(asked).toBe("es"));
});
```

(If `renderAt` does not return `router`, assert on `window.location`/the rendered page the way the file's existing navigation tests do; `userEvent` is imported as the file's other tests do.)

`FileTabs.test.tsx`: Translate again / earlier versions use the current language (header test can live in `SetPage.test.tsx`):

```ts
it("lists earlier versions in the current language only", async () => {
  serveBilingual([{ ...bilingual, runs: [{ ...runSummary, id: "r-es", target_lang: "es" }, { ...runSummary, id: "r-es0", target_lang: "es", created_at: "2026-09-01T00:00:00Z" }, runSummary] }]);
  open("/t/p-1/r-es");
  await userEvent.click(await screen.findByRole("button", { name: "More actions" }));
  expect(screen.getAllByRole("menuitem", { name: /^Started/ })).toHaveLength(1);
});
```

`SetDialogs` test: with `languages` of length 2, the Delete text says "in every language".

`AddFilesDialog` (in `SetPage.test.tsx`, where `enqueueUploads` is mocked):

```ts
it("adds files in every language of the set", async () => {
  serveBilingual();
  open("/t/p-1/r-es");
  await userEvent.click(await screen.findByRole("button", { name: "More actions" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "+ Add files" }));
  expect(screen.getByText("Translated into English and Spanish.")).toBeInTheDocument();
  await userEvent.upload(screen.getByLabelText("Choose files"), new File(["x"], "c.txt", { type: "text/plain" }));
  await userEvent.click(screen.getByRole("button", { name: "Add and translate" }));
  expect(vi.mocked(enqueueUploads)).toHaveBeenCalledWith(
    "p-1", expect.any(Array), expect.objectContaining({ target_lang: "es", all_languages: true }), expect.anything(),
  );
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && pnpm vitest run src/set src/components`
Expected: FAIL — `pickRun` arity, no "Languages" navigation, missing texts.

- [ ] **Step 3: Implement**

`SetPage.tsx`:

```ts
/** CGL-24: the run in the address decides the language; else `?lang=`; else the set's first. */
export function currentLanguage(
  project: ProjectInfo,
  docs: DocumentSummary[],
  runId: string | undefined,
  asked: string | null,
): string {
  if (runId !== undefined) {
    for (const doc of docs) {
      const run = doc.runs.find((r) => r.id === runId);
      if (run) return run.target_lang;
    }
  }
  return asked ?? languagesOf(project)[0] ?? "";
}

/** The run the address names, else the file it names (`?file=`), else the first file; each
 * file's newest run in `lang` (spec §3.2, CGL-24). A run that is not in the set picks nothing. */
export function pickRun(
  docs: DocumentSummary[],
  runId: string | undefined,
  docId: string | null | undefined,
  lang: string,
): Picked | null {
  if (runId !== undefined) {
    for (const doc of docs) {
      const run = doc.runs.find((r) => r.id === runId);
      if (run) return { doc, run, latest: newestIn(doc, run.target_lang)?.id === run.id };
    }
    return null;
  }
  const doc = docs.find((d) => d.id === docId) ?? docs[0];
  return doc ? { doc, run: newestIn(doc, lang) ?? null, latest: true } : null;
}
```

In `SetPage()`: `const lang = currentLanguage(p, docs, runId, search.get("lang"));` `const picked = pickRun(docs, runId, search.get("file"), lang);`. Render `<LanguageTabs project={p} docs={docs} lang={lang} picked={picked} />` between `SetHeader` and `FileTabs`; pass `lang` to `SetHeader`, `FileTabs`, `AddFilesDialog`, and `targetLang={lang}` to `GuidePanel`. "See the latest" links to `newestIn(picked.doc, lang)!.id`. Replace the "hasn't started yet" notice:

```tsx
{picked && !picked.run ? (
  picked.doc.runs.length > 0 ? (
    <MissingLanguage projectId={p.id} doc={picked.doc} lang={lang} />
  ) : (
    <p className="notice">This file hasn&apos;t started yet.</p>
  )
) : null}
```

with, in the same file:

```tsx
function MissingLanguage({ projectId, doc, lang }: { projectId: string; doc: DocumentSummary; lang: string }) {
  const again = useTranslateAgain(projectId);
  const navigate = useNavigate();
  const name = languageName(lang);
  return (
    <div className="notice">
      <p>This file hasn&apos;t been translated into {name} yet.</p>
      <button
        type="button"
        className="primary"
        disabled={again.isPending}
        onClick={() =>
          again.mutate(
            { documentId: doc.id, targetLang: lang },
            { onSuccess: (run) => navigate(`/t/${projectId}/${run.id}`) },
          )
        }
      >
        Translate into {name}
      </button>
      {again.isError ? <ErrorMessage error={again.error} /> : null}
    </div>
  );
}
```

`LanguageTabs.tsx`:

```tsx
import { Link } from "react-router";
import type { DocumentSummary, ProjectInfo } from "../api/types";
import { StatusText } from "../components/StatusText";
import { languageName } from "../lib/languages";
import { languagesOf, languageStatus, newestIn, sameLanguage } from "./languages";
import type { Picked } from "./SetPage";

type Props = { project: ProjectInfo; docs: DocumentSummary[]; lang: string; picked: Picked | null };

/** CGL-24: one tab per language, shown when the set has two or more. A tab opens the same file
 * in that language, else the first file translated into it. */
export function LanguageTabs({ project, docs, lang, picked }: Props) {
  const languages = languagesOf(project);
  if (languages.length < 2) return null;
  const target = (code: string) => {
    const same = picked ? newestIn(picked.doc, code) : undefined;
    const run = same ?? docs.map((d) => newestIn(d, code)).find((r) => r !== undefined);
    if (run) return `/t/${project.id}/${run.id}`;
    const file = picked ? `&file=${picked.doc.id}` : "";
    return `/t/${project.id}?lang=${encodeURIComponent(code)}${file}`;
  };
  return (
    <nav aria-label="Languages" className="file-tabs language-tabs">
      <ul>
        {languages.map((code) => {
          const current = sameLanguage(code, lang);
          const status = languageStatus(docs, code);
          return (
            <li key={code}>
              <Link
                to={target(code)}
                className={current ? "tab current" : "tab"}
                aria-current={current ? "page" : undefined}
              >
                <span>{languageName(code)}</span>
                {status ? <StatusText status={status} /> : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
```

`FileTabs.tsx`: prop `lang`; `const run = newestIn(doc, lang);` link without a run: `` `/t/${projectId}?file=${doc.id}&lang=${encodeURIComponent(lang)}` ``; after the filename: `{run ? <StatusText status={runStateStatus(run)} /> : doc.runs.length > 0 ? <StatusText status={NOT_STARTED} /> : null}` with `const NOT_STARTED: Status = { text: "not started", symbol: "○", tone: "none" };` (import `Status` from `lib/status`).

`SetHeader.tsx`: prop `lang: string`; `const target = run?.target_lang ?? (lang || project.target_lang);` `useProjectGuide(project.id, lang)`; `MoreMenu` gets `lang`; `const earlier = picked ? runsIn(picked.doc, lang).filter((r) => r.id !== runsIn(picked.doc, lang)[0]?.id) : [];` (i.e. `runsIn(...).slice(1)`); Translate again `again.mutate({ documentId: picked.doc.id, targetLang: lang }, …)`.

`AddFilesDialog.tsx`: prop `lang`; `const known = lang;` `const languages = languagesOf(project);` text: `languages.length > 1 ? <p>Translated into {listLanguages(languages)}.</p> : known ? <p>Translating into {languageName(known)}.</p> : <LanguagePicker …/>`; `enqueueUploads(project.id, usable, { target_lang: target, source_lang: …, pipeline_profile: …, ...(languages.length > 1 ? { all_languages: true } : {}) }, queryClient)`.

`SetDialogs.tsx` Delete text: `project.languages.length > 1 ? "This deletes the translation in every language, its N files, its translation guides and every review decision. It can't be undone." : <existing text>` (keep `plural(project.documents, "file")`).

- [ ] **Step 4: Run the web suite**

Run: `cd web && pnpm typecheck && pnpm lint && pnpm test`
Expected: PASS, coverage ≥ 95%. Existing SetPage/FileTabs tests pass unchanged except for the `pickRun` call sites in tests (add the `"en"` argument).

- [ ] **Step 5: Commit**

```bash
git add web
git commit -m "feat(web): language tabs; the set page follows the current language (CGL-24)"
```

---

### Task 7: Translate into another language (dialog)

**Files:**
- Create: `web/src/set/AddLanguageDialog.tsx`, `web/src/set/AddLanguageDialog.test.tsx`
- Modify: `web/src/translate/LanguagePicker.tsx` (`taken` prop), `web/src/translate/LanguagePicker.test.tsx`
- Modify: `web/src/set/SetHeader.tsx` (menu item), `web/src/set/SetPage.tsx` (dialog state)

**Interfaces:**
- Consumes: `useAddLanguage`, `languagesOf`, `sameLanguage` (Task 5); `ApiError.code` (`api/client.ts`).
- Produces: `<AddLanguageDialog project docsCount open onClose />`; `LanguagePicker` prop `taken?: string[]`; `SetHeader` prop `onAddLanguage: () => void`.

- [ ] **Step 1: Write the failing tests**

`LanguagePicker.test.tsx`:

```ts
it("shows languages the set already has, disabled", () => {
  render(<LanguagePicker label="To" value="" onChange={() => {}} tiers={tiers} taken={["fr"]} />);
  const option = screen.getByRole("option", { name: "French — already in this set" });
  expect(option).toBeDisabled();
});
```

(`fr` must be in the `tiers` fixture's languages; if not, use a code that is.)

`AddLanguageDialog.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { project, runSummary, tiers } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { AddLanguageDialog } from "./AddLanguageDialog";

function openDialog() {
  server.use(http.get("*/api/system/tiers", () => HttpResponse.json(tiers)));
  return renderAt(<AddLanguageDialog project={project} files={2} open onClose={() => {}} />, {
    path: "/t/p-1",
    pattern: "/t/:projectId",
  });
}

it("starts every file in the chosen language and opens it", async () => {
  server.use(
    http.post("*/api/projects/p-1/languages", () =>
      HttpResponse.json([{ ...runSummary, id: "r-es", target_lang: "es" }], { status: 202 }),
    ),
  );
  const { router } = openDialog();
  await userEvent.selectOptions(await screen.findByLabelText("Language"), "Other language…");
  await userEvent.type(screen.getByLabelText("Language: language code"), "es");
  expect(screen.getByText("All 2 files will be translated into Spanish. It gets its own translation guide.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Translate" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/t/p-1/r-es"));
});

it("refuses a language the set already has", async () => {
  openDialog();
  await userEvent.selectOptions(await screen.findByLabelText("Language"), "Other language…");
  await userEvent.type(screen.getByLabelText("Language: language code"), "EN");
  expect(screen.getByText("This set is already translated into English.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Translate" })).toBeDisabled();
});

it("explains a language someone else added meanwhile", async () => {
  server.use(
    http.post("*/api/projects/p-1/languages", () =>
      HttpResponse.json({ detail: "already", code: "language_exists" }, { status: 409 }),
    ),
  );
  openDialog();
  await userEvent.selectOptions(await screen.findByLabelText("Language"), "Other language…");
  await userEvent.type(screen.getByLabelText("Language: language code"), "de");
  await userEvent.click(screen.getByRole("button", { name: "Translate" }));
  expect(await screen.findByText("This set is already translated into German.")).toBeInTheDocument();
});
```

`SetPage.test.tsx`: the ⋯ menu shows "Translate into another language…" when the set has files, and not when `project.documents === 0`; choosing it opens the dialog (`screen.getByRole("dialog", { name: "Translate into another language" })`).

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && pnpm vitest run src/set src/translate`
Expected: FAIL — module `./AddLanguageDialog` not found; no disabled option.

- [ ] **Step 3: Implement**

`LanguagePicker`: `taken?: string[]` (default `[]`); in both option lists:

```tsx
{recent.map((code) => {
  const isTaken = taken.some((t) => sameLanguage(t, code));
  return (
    <option key={code} value={code} disabled={isTaken}>
      {languageName(code)}
      {isTaken ? " — already in this set" : ""}
    </option>
  );
})}
```

(same for `known`; import `sameLanguage` from `../set/languages`).

`AddLanguageDialog.tsx`:

```tsx
import { useState } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { useAddLanguage } from "../api/sets";
import { useTiers } from "../api/system";
import type { ProjectInfo } from "../api/types";
import { Dialog } from "../components/Dialog";
import { ErrorMessage } from "../components/ErrorMessage";
import { languageOk } from "../components/LanguageInput";
import { plural } from "../lib/flags";
import { languageName, rememberLanguage } from "../lib/languages";
import { LanguagePicker } from "../translate/LanguagePicker";
import { languagesOf, sameLanguage } from "./languages";

type Props = { project: ProjectInfo; files: number; open: boolean; onClose: () => void };

function AddLanguageForm({ project, files, onClose }: Omit<Props, "open">) {
  const tiers = useTiers();
  const add = useAddLanguage(project.id);
  const navigate = useNavigate();
  const [target, setTarget] = useState("");
  const taken = languagesOf(project);
  const already = target !== "" && taken.some((t) => sameLanguage(t, target));
  const exists = add.error instanceof ApiError && add.error.code === "language_exists";
  const ready = languageOk(target, true) && !already && !add.isPending;
  return (
    <div>
      <h2>Translate into another language</h2>
      <LanguagePicker label="Language" value={target} onChange={setTarget} tiers={tiers.data} taken={taken} />
      {already || exists ? (
        <p className="error">This set is already translated into {languageName(target)}.</p>
      ) : target && languageOk(target, true) ? (
        <p>
          All {plural(files, "file")} will be translated into {languageName(target)}. It gets its
          own translation guide.
        </p>
      ) : null}
      {add.isError && !exists ? <ErrorMessage error={add.error} /> : null}
      <div className="row end">
        <button type="button" onClick={onClose}>Cancel</button>
        <button
          type="button"
          className="primary"
          disabled={!ready}
          onClick={() =>
            add.mutate(target, {
              onSuccess: (runs) => {
                rememberLanguage(target);
                onClose();
                if (runs[0]) navigate(`/t/${project.id}/${runs[0].id}`);
              },
            })
          }
        >
          Translate
        </button>
      </div>
    </div>
  );
}

/** CGL-24: every file of the set in another language, without uploading anything again. */
export function AddLanguageDialog({ open, onClose, ...rest }: Props) {
  return (
    <Dialog open={open} label="Translate into another language" onClose={onClose}>
      <AddLanguageForm {...rest} onClose={onClose} />
    </Dialog>
  );
}
```

(`plural(2, "file")` yields "2 files": the copy reads "All 2 files will be translated…". If `plural` formats differently, match its output in the test.) Reset `add` when `target` changes so a stale 409 message clears: `onChange={(code) => { add.reset(); setTarget(code); }}`.

`SetHeader` `MoreMenu`: after "+ Add files", when `project.documents > 0`:

```tsx
<button role="menuitem" type="button" onClick={() => { close(); onAddLanguage(); }}>
  Translate into another language…
</button>
```

`SetPage`: `const [addingLanguage, setAddingLanguage] = useState(false);` pass `onAddLanguage={() => setAddingLanguage(true)}`; render `<AddLanguageDialog project={p} files={docs.length} open={addingLanguage} onClose={() => setAddingLanguage(false)} />`.

- [ ] **Step 4: Run the web suite**

Run: `cd web && pnpm typecheck && pnpm lint && pnpm test`
Expected: PASS, coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add web
git commit -m "feat(web): translate a set into another language (CGL-24)"
```

---

### Task 8: End-to-end journey and documents

**Files:**
- Modify: `web/e2e/journey.spec.ts`
- Modify: `README.md`, `docs/superpowers/specs/2026-10-16-coglossa-ui-ux-design.md`, `docs/superpowers/specs/2026-10-18-set-languages-design.md`

- [ ] **Step 1: Write the e2e test**

In `web/e2e/journey.spec.ts` (reuse `translateStory`, `pickCode`; the synthetic models translate as `"[<target_lang>] <source>"`):

```ts
test("a set in a second language", async ({ page }) => {
  await translateStory(page, "deux-langues.txt");
  await expect(page.getByText("[en] Le chat dort", { exact: false }).first()).toBeVisible({ timeout: SETTLE_MS });
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Translate into another language…" }).click();
  const dialog = page.getByRole("dialog", { name: "Translate into another language" });
  await dialog.getByLabel("Language", { exact: true }).selectOption({ label: "Other language…" });
  await dialog.getByLabel("Language: language code").fill("es");
  await dialog.getByRole("button", { name: "Translate" }).click();
  const languages = page.getByRole("navigation", { name: "Languages" });
  await expect(languages.getByRole("link", { name: /Spanish/ })).toHaveAttribute("aria-current", "page");
  await expect(page.getByText("[es] Le chat dort", { exact: false }).first()).toBeVisible({ timeout: SETTLE_MS });
  await page.getByRole("button", { name: /Translation guide/ }).click();
  await expect(page.getByRole("dialog", { name: "Translation guide" })).toBeVisible();
  await page.keyboard.press("Escape");
  await languages.getByRole("link", { name: /English/ }).click();
  await expect(page.getByText("[en] Le chat dort", { exact: false }).first()).toBeVisible();
});
```

- [ ] **Step 2: Run it**

Run: `make e2e`
Expected: every test passes, the new one included. If a selector differs from the app's real labels, fix the selector to the app's label — never the app's copy to the test.

- [ ] **Step 3: Update the documents**

- `README.md`, next to the "Documents in one project share a guide" paragraph:
  > A set can be translated into several languages (**⋯ ▸ Translate into another language…**). Its files are stored once; each language has its own translation guide, and files added to the set are translated into every language. Through the API: `POST /projects/{id}/languages`, and `target_lang` on the project-guide endpoints.
- UI spec `2026-10-16-coglossa-ui-ux-design.md`: §1.3 remove "Translating an existing set into a second language" from out of scope and point to the CGL-24 spec; §3.2 add "The current language: the run in the address, else `?lang=`, else the set's first language"; §5.1 add the language tabs and the ⋯ item.
- CGL-24 spec §4.3: "400 if the set has no files" → "422 (the API's code for an invalid request) if the set has no files".

- [ ] **Step 4: Full verification**

Run: `make lint && make test && make e2e`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add web/e2e README.md docs
git commit -m "test(e2e): a set in a second language; docs for CGL-24"
```
