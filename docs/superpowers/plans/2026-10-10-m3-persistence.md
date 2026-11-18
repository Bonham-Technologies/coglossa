# Coglossa M3 — Persistence and Orchestration: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Runs move from one in-memory CLI process to durable, resumable jobs (spec §5, §7). M3 adds:
- **Postgres state:** every run, stage, chunk, model call, span, score, flag and revision.
- **A worker:** it processes queued runs on `procrastinate` and survives crashes and restarts.
- **The run lifecycle:** checkpoint approval, cancel, retry of failed chunks.
- **Context-overflow splitting.**
- **The consistency sweep.**
- **`coglossa doctor`.**

**Architecture:**
- **The library stays pure.** `coglossa.stages`, `coglossa.pipeline` and the other library modules never import a database package. They gain two pieces:
  - a per-chunk pipeline that splits oversized chunks
  - a sweep stage
- **New layers on top of the library:**
  - `coglossa.store`: SQLAlchemy models, Alembic migrations, data access
  - `coglossa.storage`: uploaded files
  - `coglossa.service`: control plane — submit, approve, cancel, retry, progress, report. M4's API reuses it.
  - `coglossa.worker`: job bodies plus procrastinate wiring
  - `coglossa.doctor`: installation checks
- **The CLI path is unchanged.** `coglossa run` still works with no database. New CLI commands drive the queued path.

**Tech Stack:**
- Unchanged from M2: Python ≥3.12, uv, Pydantic v2, httpx, pytest + Hypothesis, ruff.
- New dependencies:
  - SQLAlchemy 2 (asyncio) with psycopg 3
  - Alembic
  - procrastinate 3.10
  - PostgreSQL 16 (Docker for development and CI)

**Spec:** `docs/superpowers/specs/2026-10-09-coglossa-v1-design.md`, in particular:
- §5 (data model)
- §7.1–§7.4 and §7.6 (jobs, concurrency, failure handling, doctor, observability)
- §4.1 (the sweep row)
- §3 (configuration)
- §11 (milestone M3)

M1 and M2 are on `main`. Before Tasks 2–4, read `engine/coglossa/pipeline.py`, `stages/score.py`, `stages/translate.py` and `stages/reconcile.py`.

## Global Constraints

- **Every M1/M2 constraint still holds:**
  - Python `>=3.12`, uv only.
  - 95% branch coverage (`--cov-branch --cov-fail-under=95`).
  - `pytest -W error` clean; `make lint` clean; `make schema` shows no drift.
  - Tests touch no network except `127.0.0.1`.
  - Every model call is logged.
  - **Scores are never invented.**
  - The engine never imports torch, transformers, sentence-transformers or numpy.
- **Library boundary** (spec §2.1, "no knowledge of the database"):
  - These modules must not import `sqlalchemy`, `alembic`, `psycopg` or `procrastinate`, directly or transitively: `coglossa.schema`, `coglossa.config`, `coglossa.ingest`, `coglossa.segment`, `coglossa.llm`, `coglossa.prompts`, `coglossa.scoring`, `coglossa.stages`, `coglossa.pipeline`, `coglossa.testing.fake_backend`, `coglossa.testing.fake_openai`, `coglossa.testing.fake_scorer`, `coglossa.testing.synthetic`.
  - Database code lives in `coglossa.store`, `coglossa.service`, `coglossa.worker`, `coglossa.doctor` and `coglossa.cli_ops`.
  - Task 14 adds a test that enforces this.
- **Dependency pins** in `engine/pyproject.toml`:
  - `sqlalchemy[asyncio]>=2.0.36,<3`
  - `alembic>=1.14,<2`
  - `psycopg[binary]>=3.2,<4`
  - `procrastinate>=3.10,<3.11`. procrastinate's own SQL schema is applied by our Alembic migration, so a procrastinate upgrade needs a new Alembic revision that applies its migration SQL. Pinning the minor version keeps the two in step.
- **Database URL:**
  - It comes only from the environment variable named by `database.url_env` (default `COGLOSSA_DATABASE_URL`), in the form `postgresql://user:password@host:5432/dbname`.
  - It is never printed with its password; use `coglossa.store.db.redact`.
  - SQLAlchemy uses the `postgresql+psycopg://` form (`to_sqlalchemy_url`), procrastinate the plain libpq form (`to_libpq_url`).
- **Tests that need Postgres:**
  - They read the admin URL from `COGLOSSA_TEST_DATABASE_URL`. Each session creates a throwaway database, migrates it and drops it at the end. Each test starts from truncated tables.
  - `make test-engine` starts the dev container (`make db-up`, `127.0.0.1:55432`) and sets the variable. CI uses a `postgres:16-alpine` service.
  - When the variable is missing, those tests **fail** with instructions; they never skip, so coverage can't silently drop.
- **All timestamps are UTC** and timezone-aware (`datetime.now(UTC)`; `TIMESTAMP WITH TIME ZONE` columns).
- **Every run state change** happens in a transaction that holds the run row lock (`get_run(s, run_id, lock=True)` → `SELECT … FOR UPDATE`).
- **No long transactions:** never hold a transaction open across a model call. Load inputs in one short session, do the model work with no session open, then save outputs and mark the stage complete in **one** transaction, so a crash can't leave half-saved output behind.
- **Run states** (spec §7.1):
  - `created → ingesting → profiling → building_bible → [awaiting_bible_approval] → translating → sweeping → complete | complete_with_errors | failed | cancelled`
  - The last four are terminal.
- **Idempotency** (spec §7.1): `stage_result` is unique on `(run_id, stage, chunk_id)`, with `chunk_id = ""` for run-level stages.
  - Run-level stage names: `config`, `ingest`, `profile`, `bible`, `sweep`.
  - Chunk stage names: `translate`, `reconcile`, `score`.
  - A chunk is done when its `score` row has status `complete` or `failed`.
  - A job that finds its unit already done does nothing.
- **Job names:**
  - `coglossa.start_run`
  - `coglossa.translate_run`
  - `coglossa.process_chunk`
  - `coglossa.finish_run`
  - `coglossa.requeue_stalled` (periodic)
  - All jobs go on queue `coglossa`.
  - Job arguments are strings (`run_id`, `chunk_id`). Queueing locks are `"{run_id}:start"`, `"{run_id}:translate"`, `"{run_id}:{chunk_id}"` and `"{run_id}:finish"`.
- **Failure policy** (spec §7.3):
  - A run's failure never crashes the worker.
  - `StageError`, `IngestError` and `ConfigError` in a run-level stage → run `failed`, with the error message stored.
  - Any other exception → run `failed` (run-level stage) or that chunk's `score` row `failed` (chunk job). The message is `"internal error: <Type>: <message>"`.
  - **Exception:** `sqlalchemy.exc.OperationalError` and `InterfaceError` (database unreachable) are re-raised, so procrastinate retries the job.
- **The sweep never invents content.** It changes text only to apply a locked bible entry, validates that the entry now appears, and records a `Revision` with a reason. Scores keep describing the reviewed draft they were computed on.
- **User-facing CLI errors** are one line, `error: <message>`, with exit code 1 — never a traceback.

## Review Focus

These are inputs the spec implies but no task's main tests exercise. Each gets a test in the task named.

1. **A worker crashes mid-chunk and the job is re-run.** Completed chunks are not redone; the interrupted chunk restarts cleanly with **no duplicate** spans, candidates or flags (Task 10: run the same chunk job twice; Task 11: stalled-job re-queue).
2. **Two chunk jobs finish at the same moment.** `finish_run` is queued **exactly once** and the run never sticks in `translating` (Task 10: two chunks processed with `asyncio.gather`).
3. **A cancel arrives while chunks are queued or mid-flight.** No new chunk starts model calls afterwards and completed results are kept. The state ends `cancelled` and a late-finishing job never overwrites it (Task 10).
4. **Config changes between submit and execution** (an operator fixes a backend URL and restarts the worker). The run proceeds, records a `config` error naming both hashes, and its report shows the submit-time snapshot (Task 10).
5. **The database is unreachable or not migrated.** `coglossa worker` and the CLI commands refuse with one actionable line (`run coglossa db upgrade`, `set COGLOSSA_DATABASE_URL`), never a traceback, and `doctor` reports it (Tasks 6, 11, 12, 13).

## Deferred to Later Milestones (do not build in M3)
- REST API, SSE, web UI, review actions that write `review_event`, exports (DOCX/HTML): M4. M3 creates the `review_event` table only.
- Dockerfiles, bundle, operator guide: M5. `deploy/compose.dev.yaml` here is a development database only.
- Re-scoring after sweep edits, learned calibration: later. Scores describe the reviewed draft.
- More than one worker process: the design allows it (spec §7.2), but M3 tests one.

## File Structure

```
engine/pyproject.toml                         # + sqlalchemy, alembic, psycopg, procrastinate
engine/coglossa/config.py                     # + DatabaseConfig, StorageConfig, WorkerConfig, profiles.sweep
engine/coglossa/schema/translation.py         # + FlagStatus, Flag.status/dismiss_reason, Revision, RevisionSource
engine/coglossa/schema/bible.py               # + Entity.locked
engine/coglossa/schema/run.py                 # RunState, TERMINAL_STATES, RunProgress
engine/coglossa/schema/doctor.py              # CheckStatus, DoctorCheck, DoctorReport
engine/coglossa/schema/report.py              # + Report.run_id, Report.revisions
engine/coglossa/llm/types.py                  # + OVERSIZE_ERRORS
engine/coglossa/stages/chunk.py               # ChunkOutcome, process_chunk (split on overflow)
engine/coglossa/stages/sweep.py               # run_sweep, resolve_proposals, span_sources
engine/coglossa/prompts/templates/sweep/v1.j2
engine/coglossa/storage.py                    # Storage protocol, LocalStorage, build_storage
engine/coglossa/store/{__init__,models,db,migrate,runs,outputs}.py
engine/coglossa/store/migrations/{env.py,script.py.mako,versions/0001_initial.py}
engine/coglossa/service.py                    # control plane: submit/approve/cancel/retry/progress/report/delete
engine/coglossa/worker/{__init__,execute,app}.py
engine/coglossa/doctor.py
engine/coglossa/logs.py                       # JSON log formatter for the worker
engine/coglossa/cli_ops.py                    # db/worker/submit/status/bible/approve-bible/cancel/retry/report/delete-project/doctor
engine/coglossa/testing/queue.py              # RecordingQueue
deploy/compose.dev.yaml                       # dev/test Postgres
Makefile, .github/workflows/ci.yml            # Postgres for tests
schema/run-progress.schema.json, schema/doctor-report.schema.json (generated)
README.md, engine/coglossa.example.yaml, docs/m2-followups.md
```

---

### Task 1: Configuration and schema additions

**Files:**
- Modify:
  - `engine/coglossa/config.py`
  - `engine/coglossa/schema/translation.py`
  - `engine/coglossa/schema/bible.py`
  - `engine/coglossa/schema/report.py`
  - `engine/coglossa/schema/__init__.py`
  - `engine/coglossa/stages/bible.py`
  - `engine/coglossa/scoring/rules.py`
  - `engine/coglossa/scoring/aggregate.py`
  - `engine/coglossa.example.yaml`
- Create: `engine/coglossa/schema/run.py`
- Regenerate: `schema/report.schema.json`, `schema/bible.schema.json` (`make schema`)
- Test:
  - `engine/tests/test_config_m3.py`
  - `engine/tests/schema/test_run_models.py`
  - additions to `engine/tests/stages/test_bible.py` and `engine/tests/scoring/test_rules.py`

**Interfaces:**
- Produces:
  - `DatabaseConfig(url_env: str = "COGLOSSA_DATABASE_URL")` with `.url() -> str | None`
  - `StorageConfig(kind: Literal["local"] = "local", path: Path = Path("/data/files"))`
  - `WorkerConfig(concurrency: int = 8, stalled_after_s: float = 60.0)`
  - `ProfilesConfig.sweep: dict[PipelineProfile, bool]` (defaults short False, standard True, long-form True, merged with user values)
  - `CoglossaConfig.database`, `.storage`, `.worker`
  - `FlagStatus` (`open`, `accepted`, `dismissed`, `resolved`), plus `Flag.status: FlagStatus = OPEN` and `Flag.dismiss_reason: str | None = None`
  - `RevisionSource` (`engine`, `sweep`, `user`) and `Revision(span_id, source, before: str | None, after, reason)`
  - `Entity.locked: bool = False`
  - `RunState`, `TERMINAL_STATES`
  - `Report.run_id: str | None = None` and `Report.revisions: list[Revision] = []`
  - `BibleViolation.locked` is now `entity.locked` for entities

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_config_m3.py`:

```python
from pathlib import Path

import pytest
import yaml

from coglossa.config import ConfigError, load_config, validate_config
from coglossa.schema import PipelineProfile
from tests.conftest import BASE_CONFIG, build_config


def _write(tmp_path: Path, **sections) -> Path:
    path = tmp_path / "coglossa.yaml"
    path.write_text(yaml.safe_dump({**BASE_CONFIG, **sections}), encoding="utf-8")
    return path


def test_defaults():
    cfg = build_config()
    assert cfg.database.url_env == "COGLOSSA_DATABASE_URL"
    assert cfg.storage.kind == "local"
    assert cfg.storage.path == Path("/data/files")
    assert cfg.worker.concurrency == 8
    assert cfg.worker.stalled_after_s == 60.0
    assert cfg.profiles.sweep == {
        PipelineProfile.SHORT: False,
        PipelineProfile.STANDARD: True,
        PipelineProfile.LONG_FORM: True,
    }


def test_sweep_overrides_merge_with_defaults():
    cfg = build_config(profiles={"sweep": {"short": True}})
    assert cfg.profiles.sweep[PipelineProfile.SHORT] is True
    assert cfg.profiles.sweep[PipelineProfile.LONG_FORM] is True


def test_database_url_comes_from_the_environment(monkeypatch):
    cfg = build_config(database={"url_env": "MY_DB"})
    monkeypatch.delenv("MY_DB", raising=False)
    assert cfg.database.url() is None
    monkeypatch.setenv("MY_DB", "postgresql://u:p@h/db")
    assert cfg.database.url() == "postgresql://u:p@h/db"
    monkeypatch.setenv("MY_DB", "")
    assert cfg.database.url() is None


def test_null_sections_are_accepted(tmp_path):
    cfg, _ = load_config(_write(tmp_path, database=None, storage=None, worker=None))
    assert cfg.storage.path == Path("/data/files")


def test_relative_storage_path_resolves_against_the_config_file(tmp_path):
    cfg, _ = load_config(_write(tmp_path, storage={"kind": "local", "path": "files"}))
    assert cfg.storage.path == tmp_path / "files"


def test_spec_storage_section_is_accepted(tmp_path):
    cfg, _ = load_config(_write(tmp_path, storage={"kind": "local", "path": "/data/files"}))
    assert cfg.storage.path == Path("/data/files")


def test_unknown_storage_kind_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="storage"):
        load_config(_write(tmp_path, storage={"kind": "s3", "path": "x"}))


def test_short_threshold_must_not_exceed_standard():
    cfg = build_config(profiles={"short_max_words": 5000, "standard_max_words": 1000})
    messages = [i.message for i in validate_config(cfg) if i.level == "error"]
    assert "profiles.short_max_words must not exceed profiles.standard_max_words" in messages


def test_worker_limits_are_validated(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, worker={"concurrency": 0}))
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, worker={"stalled_after_s": 1}))
```

`engine/tests/schema/test_run_models.py`:

```python
from coglossa.schema import (
    TERMINAL_STATES,
    Entity,
    Flag,
    FlagCode,
    FlagStatus,
    Report,
    Revision,
    RevisionSource,
    RunState,
)


def test_run_states_follow_the_spec_lifecycle():
    assert [s.value for s in RunState] == [
        "created",
        "ingesting",
        "profiling",
        "building_bible",
        "awaiting_bible_approval",
        "translating",
        "sweeping",
        "complete",
        "complete_with_errors",
        "failed",
        "cancelled",
    ]
    assert TERMINAL_STATES == {
        RunState.COMPLETE,
        RunState.COMPLETE_WITH_ERRORS,
        RunState.FAILED,
        RunState.CANCELLED,
    }


def test_flags_default_to_open():
    flag = Flag(code=FlagCode.LOW_MEANING, explanation="x")
    assert flag.status is FlagStatus.OPEN
    assert flag.dismiss_reason is None
    assert [s.value for s in FlagStatus] == ["open", "accepted", "dismissed", "resolved"]


def test_revision_round_trips():
    rev = Revision(span_id="p3", source=RevisionSource.SWEEP, before="a", after="b", reason="r")
    assert Revision.model_validate_json(rev.model_dump_json()) == rev


def test_entities_are_unlocked_by_default():
    assert Entity(name="Ana", rendering="Ana", kind="person").locked is False


def test_report_has_run_id_and_revisions():
    fields = Report.model_fields
    assert fields["run_id"].default is None
    assert fields["revisions"].default_factory() == []
```

Append to `engine/tests/stages/test_bible.py`:

```python
def test_apply_locked_keeps_locked_entities_from_the_prior_bible():
    from coglossa.schema import Entity
    from coglossa.stages.bible import apply_locked

    prior = Bible(
        summary="",
        style_guide="",
        entities=[
            Entity(name="Ana", rendering="Anna", kind="person", locked=True),
            Entity(name="Rio", rendering="Rio", kind="place"),
        ],
    )
    engine = Bible(
        summary="s",
        style_guide="g",
        entities=[
            Entity(name="ana", rendering="Ana", kind="person"),
            Entity(name="Lisboa", rendering="Lisbon", kind="place"),
        ],
    )
    merged = apply_locked(prior, engine)
    assert [(e.name, e.rendering, e.locked) for e in merged.entities] == [
        ("Ana", "Anna", True),
        ("Lisboa", "Lisbon", False),
    ]
```

(If the test file doesn't already import `Bible`, add `from coglossa.schema import Bible` at the top.)

Append to `engine/tests/scoring/test_rules.py`:

```python
def test_locked_entity_violations_are_locked():
    from coglossa.schema import Bible, Entity

    bible = Bible(
        summary="",
        style_guide="",
        entities=[
            Entity(name="Ana", rendering="Anna", kind="person", locked=True),
            Entity(name="Rio", rendering="Rio de Janeiro", kind="place"),
        ],
    )
    _, violations = bible_check("Ana fue a Rio.", "She went to the city.", bible)
    assert [(v.term, v.kind, v.locked) for v in violations] == [
        ("Ana", "entity", True),
        ("Rio", "entity", False),
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && uv run pytest tests/test_config_m3.py tests/schema/test_run_models.py tests/stages/test_bible.py tests/scoring/test_rules.py -q --no-cov`

Expected: FAIL. The imports error (`FlagStatus`, `RunState`, `DatabaseConfig` don't exist).

- [ ] **Step 3: Implement**

`engine/coglossa/schema/run.py`:

```python
"""Run lifecycle (spec §7.1)."""

from enum import StrEnum


class RunState(StrEnum):
    CREATED = "created"
    INGESTING = "ingesting"
    PROFILING = "profiling"
    BUILDING_BIBLE = "building_bible"
    AWAITING_BIBLE_APPROVAL = "awaiting_bible_approval"
    TRANSLATING = "translating"
    SWEEPING = "sweeping"
    COMPLETE = "complete"
    COMPLETE_WITH_ERRORS = "complete_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {RunState.COMPLETE, RunState.COMPLETE_WITH_ERRORS, RunState.FAILED, RunState.CANCELLED}
)
```

In `engine/coglossa/schema/translation.py`, add after `FlagCode`, and replace `Flag`:

```python
class FlagStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"  # a reviewer confirmed the passage is fine as it is
    DISMISSED = "dismissed"  # a reviewer rejected the flag; dismiss_reason says why
    RESOLVED = "resolved"  # the sweep fixed the problem; the fix is a Revision


class Flag(BaseModel):
    code: FlagCode
    explanation: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: FlagStatus = FlagStatus.OPEN
    dismiss_reason: str | None = None
```

Add at the end of the same file:

```python
class RevisionSource(StrEnum):
    ENGINE = "engine"  # the reviewer's reconciled text
    SWEEP = "sweep"  # consistency fixes after all chunks (spec §4.1)
    USER = "user"  # edits in the review view (M4)


class Revision(BaseModel):
    """One change to a span's text. Span.text is always the latest revision."""

    span_id: str
    source: RevisionSource
    before: str | None = None
    after: str
    reason: str
```

In `engine/coglossa/schema/bible.py`, add to `Entity` (after `speech_style`):

```python
    locked: bool = False  # set only by humans; locked names are enforced like locked terms
```

In `engine/coglossa/schema/report.py`:
- import `Revision` from `coglossa.schema.translation`
- add the fields `run_id: str | None = None  # set when the worker ran it` (after `coglossa_version`) and `revisions: list[Revision] = Field(default_factory=list)  # sweep and user edits; reconciled text is the baseline` (after `spans`)

In `engine/coglossa/schema/__init__.py`:
- export `FlagStatus`, `Revision`, `RevisionSource` (from translation) and `RunState`, `TERMINAL_STATES` (from `coglossa.schema.run`)
- keep `__all__` sorted

In `engine/coglossa/stages/bible.py`, make locks cover entities too:

```python
def _unlocked(bible: Bible) -> Bible:
    glossary = [g.model_copy(update={"locked": False}) for g in bible.glossary]
    entities = [e.model_copy(update={"locked": False}) for e in bible.entities]
    return bible.model_copy(update={"glossary": glossary, "entities": entities})


def apply_locked(prior: Bible | None, bible: Bible) -> Bible:
    """Human-locked entries (glossary terms and entities) from the prior bible are kept
    verbatim and override engine entries with the same term or name."""
    if prior is None:
        return bible
    locked_terms = [g for g in prior.glossary if g.locked]
    term_keys = {g.term.casefold() for g in locked_terms}
    locked_names = [e for e in prior.entities if e.locked]
    name_keys = {e.name.casefold() for e in locked_names}
    return bible.model_copy(
        update={
            "glossary": [
                *locked_terms,
                *(g for g in bible.glossary if g.term.casefold() not in term_keys),
            ],
            "entities": [
                *locked_names,
                *(e for e in bible.entities if e.name.casefold() not in name_keys),
            ],
        }
    )
```

In `engine/coglossa/scoring/rules.py`, `bible_check`: the entity violation becomes `BibleViolation(e.name, e.rendering, "entity", e.locked)`.

In `engine/coglossa/scoring/aggregate.py`, `_flags`: the terminology explanation now covers names too:

```python
        rules = "; ".join(f"'{v.term}' should be rendered as '{v.expected}'" for v in locked)
        flags.append(
            Flag(
                code=FlagCode.TERMINOLOGY_VIOLATION,
                explanation=f"Locked bible entries were not followed: {rules}.",
                evidence={"violations": [v.as_evidence() for v in locked]},
            )
        )
```

In `engine/coglossa/config.py`:

```python
class DatabaseConfig(_Strict):
    url_env: str = "COGLOSSA_DATABASE_URL"  # env var holding postgresql://user:pass@host:port/db

    def url(self) -> str | None:
        return os.environ.get(self.url_env) or None


class StorageConfig(_Strict):
    kind: Literal["local"] = "local"  # S3/MinIO later, behind the same Storage interface
    path: Path = Path("/data/files")


class WorkerConfig(_Strict):
    concurrency: int = Field(8, ge=1)  # chunk jobs one worker process runs at the same time
    stalled_after_s: float = Field(60.0, ge=10)  # re-queue jobs of a worker silent this long


def _default_sweep() -> dict[PipelineProfile, bool]:
    return {
        PipelineProfile.SHORT: False,
        PipelineProfile.STANDARD: True,
        PipelineProfile.LONG_FORM: True,
    }
```

Add to `ProfilesConfig`:

```python
    sweep: dict[PipelineProfile, bool] = Field(default_factory=_default_sweep)

    @field_validator("sweep")
    @classmethod
    def _merge_sweep(cls, value: dict[PipelineProfile, bool]) -> dict[PipelineProfile, bool]:
        return {**_default_sweep(), **value}
```

`CoglossaConfig`:
- Replace `storage: dict[str, Any] | None = None` with:

```python
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
```

- Keep `auth: dict[str, Any] | None = None`.
- Generalise the null-section validator:

```python
    @field_validator("scoring", "database", "storage", "worker", mode="before")
    @classmethod
    def _null_section(cls, value: Any) -> Any:
        return {} if value is None else value
```

In `validate_config`, after the chunking check:

```python
    if cfg.profiles.short_max_words > cfg.profiles.standard_max_words:
        add("error", "profiles.short_max_words must not exceed profiles.standard_max_words")
```

In `load_config`, after the tiers-file block:

```python
    if not cfg.storage.path.is_absolute():
        storage = cfg.storage.model_copy(update={"path": path.parent / cfg.storage.path})
        cfg = cfg.model_copy(update={"storage": storage})
```

Append to `engine/coglossa.example.yaml`:

```yaml

# Queued runs (coglossa submit / coglossa worker; spec §7). `coglossa run` needs none of this.
# database: { url_env: COGLOSSA_DATABASE_URL }  # postgresql://user:password@host:5432/coglossa
# storage: { kind: local, path: /data/files }    # uploaded documents
# worker: { concurrency: 8, stalled_after_s: 60 }
# profiles:
#   sweep: { short: false, standard: true, long-form: true }  # enforce locked bible entries
```

- [ ] **Step 4: Regenerate the schema and run the tests**

Run: `cd engine && uv run python -m coglossa.schema.export ../schema && uv run pytest -q`

Expected: PASS. Coverage ≥ 95%. Any existing test that built `storage={...}` as a free-form dict must use the new shape.

- [ ] **Step 5: Commit**

```bash
git add engine schema
git commit -m "feat(config,schema): database/storage/worker config, run states, flag status, revisions, locked entities"
```

---

### Task 2: Per-chunk pipeline with context-overflow splitting

**Files:**
- Create: `engine/coglossa/stages/chunk.py`
- Modify:
  - `engine/coglossa/llm/types.py`
  - `engine/coglossa/stages/translate.py`
  - `engine/coglossa/stages/reconcile.py`
  - `engine/coglossa/pipeline.py`
- Test: `engine/tests/stages/test_chunk.py`

**Interfaces:**
- Consumes:
  - `translate_chunk`, `reconcile_chunk`, `score_chunk`, `initial_flags`, `competing_candidates`, `unscored`
  - `ChunkInput` and `CONTEXT_CHARS` (`stages/translate.py`)
- Produces:
  - `OVERSIZE_ERRORS: tuple[type[Exception], ...] = (ContextOverflowError, OutputTruncatedError)` in `coglossa.llm.types`
  - `ChunkOutcome` (Pydantic): `chunk_id`, `candidates`, `spans: list[Span]` (ids chunk-local, `"c3.1"`), `proposed_terms: list[list[GlossaryEntry]]` (one list per span), `errors: list[RunError]`, `engine_error: bool`
  - `StageHook = Callable[[str], Awaitable[None]]`
  - `async process_chunk(ctx, scorer, profile, bible, ci, *, tier, diverse, on_stage: StageHook | None = None) -> ChunkOutcome`. It calls `on_stage("translate")` and `on_stage("reconcile")` after those stages (once per half when split).
  - `split_chunk_input(ci) -> tuple[ChunkInput, ChunkInput]`
  - `merge_outcomes(chunk_id: str, parts: list[ChunkOutcome]) -> ChunkOutcome`
  - `translate_chunk` and `reconcile_chunk` now **re-raise** `OVERSIZE_ERRORS` instead of recording them.

Spec §7.2: "If a model still reports an overflow, the chunk is split in two and retried."
- Both a context overflow and a truncated output (`finish_reason=length`) mean the chunk is too big for that model. Either one raised by a translator or the reviewer triggers a split.
- Judge and roundtrip failures keep M2 behavior: their signals are null.
- A one-segment chunk that is still too large becomes an `engine_error` passage with empty text, never a crash.

- [ ] **Step 1: Write the failing tests**

`engine/tests/stages/test_chunk.py`:

```python
import json

from coglossa.llm.types import ContextOverflowError, OutputTruncatedError
from coglossa.schema import (
    Bible,
    Chunk,
    DocumentProfile,
    FlagCode,
    Genre,
    PipelineProfile,
    Reliability,
)
from coglossa.stages.chunk import ChunkOutcome, merge_outcomes, process_chunk, split_chunk_input
from coglossa.stages.context import StageContext
from coglossa.stages.translate import build_chunk_inputs
from tests.conftest import make_segments

PROFILE = DocumentProfile(
    source_lang="fr",
    target_lang="en",
    mixed_language=False,
    genre=Genre.OTHER,
    tone_register="neutral",
    word_count=8,
    pipeline_profile=PipelineProfile.STANDARD,
)
BIBLE = Bible(summary="", style_guide="")
TEXTS = ("Il pleut.", "Le chat dort.", "Demain, le soleil.", "Fin.")


def _ci(texts=TEXTS):
    segments = make_segments(*texts)
    chunk = Chunk(id="c1", segment_ids=[s.id for s in segments])
    return build_chunk_inputs([chunk], segments)[0]


def _ctx(make_runner):
    runner = make_runner()
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


async def _run(make_runner, ci, hook=None):
    return await process_chunk(
        _ctx(make_runner),
        None,
        PROFILE,
        BIBLE,
        ci,
        tier=Reliability.A,
        diverse=False,
        on_stage=hook,
    )


def _tasks(fake_backend):
    return [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["task"]
        for r in fake_backend.requests
    ]


async def test_normal_chunk(make_runner, fake_backend):
    seen = []

    async def hook(stage):
        seen.append(stage)

    out = await _run(make_runner, _ci(), hook)
    assert isinstance(out, ChunkOutcome)
    assert out.chunk_id == "c1"
    assert [s.id for s in out.spans] == ["c1.1", "c1.2", "c1.3", "c1.4"]
    assert [s.text for s in out.spans] == [f"[en] {t}" for t in TEXTS]
    assert out.proposed_terms == [[], [], [], []]
    assert out.errors == [] and out.engine_error is False
    assert {c.translator for c in out.candidates} == {"literal", "contextual"}
    assert seen == ["translate", "reconcile"]


async def test_translator_overflow_splits_the_chunk(make_runner, fake_backend):
    fake_backend.script("translate", ContextOverflowError("maximum context length exceeded"))
    seen = []

    async def hook(stage):
        seen.append(stage)

    out = await _run(make_runner, _ci(), hook)
    assert [(s.id, s.start, s.end) for s in out.spans] == [
        ("c1.1", "s1", "s1"),
        ("c1.2", "s2", "s2"),
        ("c1.3", "s3", "s3"),
        ("c1.4", "s4", "s4"),
    ]
    assert [s.text for s in out.spans] == [f"[en] {t}" for t in TEXTS]
    assert out.errors == [] and out.engine_error is False
    assert all(c.chunk_id == "c1" for c in out.candidates)
    # 2 translators on the whole chunk, then 2 translators + 1 reviewer per half
    assert _tasks(fake_backend).count("translate") == 6
    assert _tasks(fake_backend).count("reconcile") == 2
    assert seen.count("translate") == 2 and seen.count("reconcile") == 2


async def test_truncated_review_splits_the_chunk(make_runner, fake_backend):
    fake_backend.script("reconcile", OutputTruncatedError("finish_reason=length"))
    out = await _run(make_runner, _ci())
    assert [s.text for s in out.spans] == [f"[en] {t}" for t in TEXTS]
    assert out.errors == [] and out.engine_error is False


async def test_single_sentence_that_never_fits_is_an_engine_error(make_runner, fake_backend):
    fake_backend.script("translate", *[ContextOverflowError("too long")] * 2)
    out = await _run(make_runner, _ci(("Une phrase interminable.",)))
    assert len(out.spans) == 1
    span = out.spans[0]
    assert span.text == "" and span.chosen_from == "none"
    assert span.score.reliability is Reliability.UNSCORED
    assert [f.code for f in span.flags] == [FlagCode.ENGINE_ERROR]
    assert out.engine_error is True
    assert len(out.errors) == 1
    assert out.errors[0].stage == "translate" and out.errors[0].chunk_id == "c1"
    assert "too large" in out.errors[0].message


async def test_repeated_overflow_splits_down_to_single_segments(make_runner, fake_backend):
    # whole chunk, then both halves overflow once each: 4 single-segment chunks result
    fake_backend.script("translate", *[ContextOverflowError("too long")] * 3)
    out = await _run(make_runner, _ci())
    assert [s.start for s in out.spans] == ["s1", "s2", "s3", "s4"]
    assert out.engine_error is False


def test_split_keeps_neighbouring_context():
    ci = _ci()
    first, second = split_chunk_input(ci)
    assert first.chunk.id == second.chunk.id == "c1"
    assert first.chunk.segment_ids == ["s1", "s2"]
    assert second.chunk.segment_ids == ["s3", "s4"]
    assert first.context_before == ci.context_before
    assert first.context_after == "Demain, le soleil.\nFin."
    assert second.context_before == "Il pleut.\nLe chat dort."
    assert second.context_after == ci.context_after


def test_merge_renumbers_spans(make_runner):
    a = ChunkOutcome(chunk_id="c1", spans=[], proposed_terms=[])
    merged = merge_outcomes("c1", [a, a])
    assert merged.spans == [] and merged.engine_error is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && uv run pytest tests/stages/test_chunk.py -q --no-cov`

Expected: FAIL with `ModuleNotFoundError: coglossa.stages.chunk`.

- [ ] **Step 3: Implement**

Append to `engine/coglossa/llm/types.py`:

```python
# The request was too big for the model: the caller should send less (spec §7.2).
OVERSIZE_ERRORS: tuple[type[Exception], ...] = (ContextOverflowError, OutputTruncatedError)
```

In `engine/coglossa/stages/translate.py`:
- import `OVERSIZE_ERRORS` from `coglossa.llm.types`
- right after the `asyncio.gather(...)` line in `translate_chunk`, add:

```python
    oversize = next((r for r in results if isinstance(r, OVERSIZE_ERRORS)), None)
    if oversize is not None:
        raise oversize  # the caller splits the chunk (spec §7.2)
```

In `engine/coglossa/stages/reconcile.py`:
- import `OVERSIZE_ERRORS`
- in `reconcile_chunk`, put a clause before `except AGENT_FAILURES as e:`:

```python
    except OVERSIZE_ERRORS:
        raise  # the caller splits the chunk (spec §7.2)
```

`engine/coglossa/stages/chunk.py`:

```python
"""Per-chunk pipeline (spec §4.1, §7.2): translate → reconcile → score. When a model reports
that the chunk is too large (context overflow or truncated output), the chunk is split in two
and each half is processed on its own. A single sentence that is still too large becomes an
engine_error passage instead of failing the run."""

import asyncio
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from coglossa.llm.types import OVERSIZE_ERRORS
from coglossa.schema import (
    Agreement,
    Bible,
    Candidate,
    Chunk,
    DocumentProfile,
    GlossaryEntry,
    ReconciledSpan,
    Reliability,
    RunError,
    Span,
)
from coglossa.scoring.client import Scorer
from coglossa.stages.context import StageContext
from coglossa.stages.reconcile import ReconcileResult, reconcile_chunk
from coglossa.stages.score import (
    ChunkScores,
    competing_candidates,
    initial_flags,
    score_chunk,
    unscored,
)
from coglossa.stages.translate import CONTEXT_CHARS, ChunkInput, translate_chunk

StageHook = Callable[[str], Awaitable[None]]


class ChunkOutcome(BaseModel):
    """Everything one chunk produced. Span ids are chunk-local ("c3.1", "c3.2", …); the
    report renumbers them p1, p2, … in reading order. proposed_terms has one list per span."""

    chunk_id: str
    candidates: list[Candidate] = Field(default_factory=list)
    spans: list[Span] = Field(default_factory=list)
    proposed_terms: list[list[GlossaryEntry]] = Field(default_factory=list)
    errors: list[RunError] = Field(default_factory=list)
    engine_error: bool = False


async def process_chunk(
    ctx: StageContext,
    scorer: Scorer | None,
    profile: DocumentProfile,
    bible: Bible,
    ci: ChunkInput,
    *,
    tier: Reliability,
    diverse: bool,
    on_stage: StageHook | None = None,
) -> ChunkOutcome:
    try:
        return await _attempt(
            ctx, scorer, profile, bible, ci, tier=tier, diverse=diverse, on_stage=on_stage
        )
    except OVERSIZE_ERRORS as e:
        if len(ci.segments) < 2:
            return _too_large(ci, str(e))
        parts = await asyncio.gather(
            *(
                process_chunk(
                    ctx, scorer, profile, bible, half, tier=tier, diverse=diverse, on_stage=on_stage
                )
                for half in split_chunk_input(ci)
            )
        )
        return merge_outcomes(ci.chunk.id, list(parts))


async def _attempt(
    ctx: StageContext,
    scorer: Scorer | None,
    profile: DocumentProfile,
    bible: Bible,
    ci: ChunkInput,
    *,
    tier: Reliability,
    diverse: bool,
    on_stage: StageHook | None,
) -> ChunkOutcome:
    candidates, translate_errors = await translate_chunk(ctx, profile, bible, ci)
    if on_stage is not None:
        await on_stage("translate")
    reconciled = await reconcile_chunk(ctx, profile, bible, ci, candidates)
    if on_stage is not None:
        await on_stage("reconcile")
    scored = await score_chunk(
        ctx, scorer, profile, bible, ci, candidates, reconciled, tier=tier, diverse=diverse
    )
    return build_outcome(ci, candidates, reconciled, scored, translate_errors)


def build_outcome(
    ci: ChunkInput,
    candidates: list[Candidate],
    reconciled: ReconcileResult,
    scored: ChunkScores,
    translate_errors: list[RunError],
) -> ChunkOutcome:
    positions = {s.id: i for i, s in enumerate(ci.segments)}
    spans: list[Span] = []
    for j, rs in enumerate(reconciled.spans):
        competing = competing_candidates(candidates, rs.start, rs.end, positions)
        spans.append(
            Span(
                id=f"{ci.chunk.id}.{j + 1}",
                chunk_id=ci.chunk.id,
                start=rs.start,
                end=rs.end,
                text=rs.text,
                agreement=rs.agreement,
                chosen_from=rs.chosen_from,
                rationale=rs.rationale,
                issues=rs.issues,
                score=scored.scores[j],
                flags=initial_flags(rs, engine_error=reconciled.engine_error, competing=competing)
                + scored.flags[j],
                judge_errors=scored.judge_errors[j],
                back_translation=scored.back_translations[j],
            )
        )
    return ChunkOutcome(
        chunk_id=ci.chunk.id,
        candidates=candidates,
        spans=spans,
        proposed_terms=[list(rs.proposed_terms) for rs in reconciled.spans],
        errors=translate_errors + reconciled.errors + scored.run_errors,
        engine_error=reconciled.engine_error is not None,
    )


def split_chunk_input(ci: ChunkInput) -> tuple[ChunkInput, ChunkInput]:
    mid = len(ci.segments) // 2
    first, second = ci.segments[:mid], ci.segments[mid:]
    first_text = "\n".join(s.text for s in first)
    second_text = "\n".join(s.text for s in second)

    def half(segments, before: str, after: str) -> ChunkInput:
        chunk = Chunk(id=ci.chunk.id, segment_ids=[s.id for s in segments])
        return ChunkInput(
            chunk=chunk, segments=segments, context_before=before, context_after=after
        )

    return (
        half(first, ci.context_before, second_text[:CONTEXT_CHARS]),
        half(second, first_text[-CONTEXT_CHARS:], ci.context_after),
    )


def merge_outcomes(chunk_id: str, parts: list[ChunkOutcome]) -> ChunkOutcome:
    spans = [s for p in parts for s in p.spans]
    return ChunkOutcome(
        chunk_id=chunk_id,
        candidates=[c for p in parts for c in p.candidates],
        spans=[s.model_copy(update={"id": f"{chunk_id}.{k + 1}"}) for k, s in enumerate(spans)],
        proposed_terms=[t for p in parts for t in p.proposed_terms],
        errors=[e for p in parts for e in p.errors],
        engine_error=any(p.engine_error for p in parts),
    )


def _too_large(ci: ChunkInput, message: str) -> ChunkOutcome:
    error = f"too large for the configured models even as a single sentence: {message}"
    spans: list[Span] = []
    for k, segment in enumerate(ci.segments):
        rs = ReconciledSpan(
            start=segment.id,
            end=segment.id,
            text="",
            agreement=Agreement.UNKNOWN,
            chosen_from="none",
        )
        spans.append(
            Span(
                id=f"{ci.chunk.id}.{k + 1}",
                chunk_id=ci.chunk.id,
                start=segment.id,
                end=segment.id,
                text="",
                agreement=Agreement.UNKNOWN,
                chosen_from="none",
                score=unscored(),
                flags=initial_flags(rs, engine_error=error, competing=[]),
            )
        )
    return ChunkOutcome(
        chunk_id=ci.chunk.id,
        spans=spans,
        proposed_terms=[[] for _ in spans],
        errors=[RunError(stage="translate", chunk_id=ci.chunk.id, message=error)],
        engine_error=True,
    )
```

In `engine/coglossa/pipeline.py`, use the new module. Behavior stays identical, so every existing pipeline test must still pass unchanged.
- Delete `_ChunkOutcome` and `_process_chunk`. Import `ChunkOutcome` and `process_chunk` from `coglossa.stages.chunk`.
- In `run_document`, `process` becomes:

```python
    async def process(ci: ChunkInput) -> ChunkOutcome:
        nonlocal done
        outcome = await process_chunk(
            ctx, scorer, profile, bible, ci, tier=tier, diverse=diverse
        )
        done += 1
        say(f"chunk {done}/{len(chunks)} done")
        return outcome
```

- `_assemble` takes `outcomes: list[ChunkOutcome]`. Its span loop becomes:

```python
    for outcome in outcomes:
        candidates.extend(outcome.candidates)
        errors.extend(outcome.errors)
        any_engine_error = any_engine_error or outcome.engine_error
        for span, terms in zip(outcome.spans, outcome.proposed_terms, strict=True):
            spans.append(span.model_copy(update={"id": f"p{len(spans) + 1}"}))
            for term in terms:
                key = term.term.casefold()
                if key not in known_terms:
                    known_terms.add(key)
                    proposals.append(term.model_copy(update={"locked": False}))
```

- Remove the now-unused `positions`, `competing_candidates` and `initial_flags` imports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd engine && uv run pytest -q`

Expected: PASS. All M1/M2 tests pass unchanged; coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add engine
git commit -m "feat(stages): per-chunk pipeline that splits a chunk on context overflow or truncated output"
```

---
### Task 3: Sweep stage and proposed-term resolution

**Files:**
- Create:
  - `engine/coglossa/stages/sweep.py`
  - `engine/coglossa/prompts/templates/sweep/v1.j2`
- Modify:
  - `engine/coglossa/testing/synthetic.py`
  - `engine/coglossa/prompts/check.py`
- Test: `engine/tests/stages/test_sweep.py`, plus additions to `engine/tests/prompts/test_check.py`

**Interfaces:**
- Consumes:
  - `bible_check`, `term_in`, `BibleViolation` (`scoring/rules.py`)
  - `bible_slice` (`stages/bible.py`)
  - `AGENT_FAILURES`
  - `Span`, `Flag`, `FlagStatus`, `Revision`, `RevisionSource`
- Produces:
  - `SweepFix(text: str)` (agent output)
  - `SpanChange(span_id: str, before: str, after: str, reason: str, resolved: tuple[FlagCode, ...])` (frozen dataclass)
  - `SweepResult(spans: list[Span], changes: list[SpanChange], errors: list[RunError])` with a `.revisions -> list[Revision]` property
  - `span_sources(spans: list[Span], segments: list[Segment]) -> list[str]`
  - `async run_sweep(ctx, profile, bible, spans: list[Span], segments: list[Segment]) -> SweepResult`
  - `resolve_proposals(bible: Bible, proposals: list[GlossaryEntry]) -> list[GlossaryEntry]`
  - `run_prompt_ids(cfg) -> list[str]` in `prompts/check.py`: the prompt ids a run uses, in the order the report lists them. `validate_prompts` also renders `sweep`.

Spec §4.1, sweep row: "Enforces locked terms and checks names and forms of address for consistency across chunks. Resolves the proposed bible terms. Every change is stored as a revision with a reason."

The rulings this task implements:
- **What gets enforced:** locked glossary terms **and locked entities** (Task 1 added `Entity.locked`). The engine never enforces its own unreviewed guesses. Unlocked entries only feed the `bible_check` signal.
- **How:** one reviewer-model call per span that misses a locked entry. The span's entities and forms of address go in as context, so a fix keeps names and address forms consistent. The output is validated: every required rendering must appear (`term_in`). After one repair turn, a failure records a `RunError(stage="sweep")` and leaves the span unchanged, with its flag still open.
- **When a fix succeeds:** the open `terminology_violation` flag becomes `resolved` and a `SpanChange` records before, after and the reason. Scores are not recomputed: they describe the reviewed draft.
- **Proposals:**
  - Group by case-folded term and skip terms already in the bible.
  - Pick the most frequent rendering; a tie goes to the first seen.
  - When renderings conflict, note the alternatives in `notes`.
  - Proposals are always unlocked.

- [ ] **Step 1: Write the failing tests**

`engine/tests/stages/test_sweep.py`:

```python
import json

from coglossa.llm.types import PermanentBackendError
from coglossa.schema import (
    Agreement,
    Bible,
    DocumentProfile,
    Entity,
    Flag,
    FlagCode,
    FlagStatus,
    Genre,
    GlossaryEntry,
    PipelineProfile,
    RevisionSource,
    Span,
)
from coglossa.stages.context import StageContext
from coglossa.stages.score import unscored
from coglossa.stages.sweep import resolve_proposals, run_sweep, span_sources
from tests.conftest import make_segments

PROFILE = DocumentProfile(
    source_lang="fr",
    target_lang="en",
    mixed_language=False,
    genre=Genre.OTHER,
    tone_register="neutral",
    word_count=10,
    pipeline_profile=PipelineProfile.STANDARD,
)
SEGMENTS = make_segments("Le chat dort.", "Ana arrive.", "Il pleut.")
LOCKED = Bible(
    summary="",
    style_guide="",
    glossary=[
        GlossaryEntry(term="chat", rendering="cat", locked=True),
        GlossaryEntry(term="pleut", rendering="pours", locked=False),
    ],
    entities=[Entity(name="Ana", rendering="Anna", kind="person", locked=True)],
)


def _span(sid, seg, text, flags=()):
    return Span(
        id=sid,
        chunk_id="c1",
        start=seg,
        end=seg,
        text=text,
        agreement=Agreement.AGREE,
        chosen_from="literal",
        score=unscored(),
        flags=list(flags),
    )


def _violation_flag():
    return Flag(code=FlagCode.TERMINOLOGY_VIOLATION, explanation="'chat' → 'cat'")


def _ctx(make_runner):
    runner = make_runner()
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


def _sweep_requests(fake_backend):
    payloads = [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1]) for r in fake_backend.requests
    ]
    return [p for p in payloads if p["task"] == "sweep"]


def test_span_sources_join_the_covered_segments():
    span = _span("p1", "s1", "x").model_copy(update={"end": "s2"})
    assert span_sources([span], SEGMENTS) == ["Le chat dort. Ana arrive."]


async def test_nothing_to_enforce_makes_no_calls(make_runner, fake_backend):
    spans = [_span("p1", "s1", "The cat sleeps."), _span("p3", "s3", "It rains.")]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    assert result.spans == spans
    assert result.changes == [] and result.errors == []
    assert fake_backend.requests == []  # unlocked 'pleut' → 'pours' is not enforced


async def test_locked_term_is_enforced_and_its_flag_resolved(make_runner, fake_backend):
    spans = [_span("p1", "s1", "The feline sleeps.", [_violation_flag()])]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    (change,) = result.changes
    assert change.span_id == "p1"
    assert change.before == "The feline sleeps."
    assert "cat" in change.after
    assert change.reason == "Applied locked bible entries: 'chat' → 'cat'."
    assert change.resolved == (FlagCode.TERMINOLOGY_VIOLATION,)
    assert result.spans[0].text == change.after
    assert result.spans[0].flags[0].status is FlagStatus.RESOLVED
    (rev,) = result.revisions
    assert rev.source is RevisionSource.SWEEP and rev.before == "The feline sleeps."
    (payload,) = _sweep_requests(fake_backend)
    assert payload["requirements"] == [{"term": "chat", "rendering": "cat", "kind": "glossary"}]
    assert payload["source"] == "Le chat dort."


async def test_locked_entity_is_enforced_with_entity_context(make_runner, fake_backend):
    spans = [_span("p2", "s2", "Ana arrives.")]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    assert "Anna" in result.spans[0].text
    (payload,) = _sweep_requests(fake_backend)
    assert payload["requirements"][0]["kind"] == "entity"
    assert payload["entities"][0]["name"] == "Ana"


async def test_a_fix_that_never_validates_is_an_error(make_runner, fake_backend):
    bad = '{"text": "The feline sleeps."}'
    fake_backend.script("sweep", bad, bad)
    spans = [_span("p1", "s1", "The feline sleeps.", [_violation_flag()])]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    assert result.changes == []
    assert result.spans[0].flags[0].status is FlagStatus.OPEN
    (error,) = result.errors
    assert error.stage == "sweep" and error.role == "sweep" and error.chunk_id == "c1"
    assert error.message.startswith("p1: ")
    assert "must contain 'cat'" in error.message


async def test_backend_failure_is_an_error_not_a_crash(make_runner, fake_backend):
    fake_backend.script("sweep", PermanentBackendError("HTTP 400"))
    spans = [_span("p1", "s1", "The feline sleeps.")]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    assert result.changes == [] and len(result.errors) == 1


async def test_empty_spans_are_skipped(make_runner, fake_backend):
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, [_span("p1", "s1", "")], SEGMENTS)
    assert result.changes == [] and fake_backend.requests == []


def test_resolve_proposals():
    bible = Bible(summary="", style_guide="", glossary=[GlossaryEntry(term="Ville", rendering="City")])
    proposals = [
        GlossaryEntry(term="mer", rendering="sea", notes="n1"),
        GlossaryEntry(term="Mer", rendering="ocean"),
        GlossaryEntry(term="mer ", rendering=" ocean "),
        GlossaryEntry(term="ville", rendering="town"),  # already in the bible
        GlossaryEntry(term="ciel", rendering="sky", locked=True),
        GlossaryEntry(term="  ", rendering="x"),
    ]
    resolved = resolve_proposals(bible, proposals)
    assert [(g.term, g.rendering, g.locked) for g in resolved] == [
        ("Mer", "ocean", False),
        ("ciel", "sky", False),
    ]
    assert resolved[0].notes == "Reviewers proposed different renderings: 'sea' (1), 'ocean' (2)."


def test_resolve_proposals_tie_keeps_the_first_seen():
    bible = Bible(summary="", style_guide="")
    resolved = resolve_proposals(
        bible,
        [GlossaryEntry(term="mer", rendering="sea"), GlossaryEntry(term="mer", rendering="ocean")],
    )
    assert resolved[0].rendering == "sea"
```

Append to `engine/tests/prompts/test_check.py`:

```python
def test_run_prompt_ids_lists_optional_roles_when_set(make_config):
    from coglossa.prompts.check import run_prompt_ids

    cfg = make_config()
    assert run_prompt_ids(cfg) == [
        "profile", "bible", "bible_merge", "reconcile", "sweep", "literal", "contextual"
    ]
    roles = {**cfg.roles.model_dump(), "judge": {"model": "model-b"}, "roundtrip": {"model": "model-b"}}
    with_roles = make_config(roles=roles)
    assert run_prompt_ids(with_roles)[-2:] == ["judge", "back_translate"]


def test_validate_prompts_renders_sweep(make_config, tmp_path):
    import pytest

    from coglossa.config import ConfigError
    from coglossa.prompts.check import validate_prompts
    from coglossa.prompts.library import PromptLibrary

    (tmp_path / "sweep").mkdir()
    (tmp_path / "sweep" / "v9.j2").write_text("{{ missing_variable }}", encoding="utf-8")
    with pytest.raises(ConfigError, match="sweep"):
        validate_prompts(make_config(), PromptLibrary(tmp_path))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && uv run pytest tests/stages/test_sweep.py tests/prompts/test_check.py -q --no-cov`

Expected: FAIL with `ModuleNotFoundError: coglossa.stages.sweep`.

- [ ] **Step 3: Implement**

`engine/coglossa/prompts/templates/sweep/v1.j2`:

```
You are a careful translation editor working from {{ source_lang }} into {{ target_lang }}.
INPUT_JSON.translation is a finished translation of INPUT_JSON.source. It breaks binding decisions from the translation bible: each item in INPUT_JSON.requirements names a source term (a glossary term or a name) and the rendering that must be used for it.
Revise the translation so that every required rendering appears exactly as written. Change nothing else: keep the meaning, sentence structure, tone and punctuation of the rest of the passage. If {{ target_lang }} grammar would inflect the rendering, rephrase minimally so the rendering can appear as written.
INPUT_JSON.entities lists the people and places in this passage with their renderings and forms of address; keep them consistent.
Return a JSON object with one field, "text": the full revised translation. Respond with a single JSON object only.
```

Add to `engine/coglossa/testing/synthetic.py`, and register `"sweep": _sweep` in `_HANDLERS`:

```python
def _sweep(p: Payload) -> Payload:
    text = p["translation"]
    missing = [
        r["rendering"] for r in p["requirements"] if r["rendering"].casefold() not in text.casefold()
    ]
    return {"text": " ".join([text, *missing])}
```

In `engine/coglossa/prompts/check.py`, add:

```python
def run_prompt_ids(cfg: CoglossaConfig) -> list[str]:
    """Prompt ids a run may use, in report order (spec §4.6: every result names its prompt)."""
    translators = list(dict.fromkeys(t.prompt for t in cfg.roles.translators()))
    ids = ["profile", "bible", "bible_merge", "reconcile", "sweep", *translators]
    if cfg.roles.judge is not None:
        ids.append("judge")
    if cfg.roles.roundtrip is not None:
        ids.append("back_translate")
    return ids
```

Add `("sweep", dict(_LANGS)),` to `validate_prompts`' `checks` list, after the reconcile entry.

In `engine/coglossa/pipeline.py`, `_assemble`: replace the hand-built `prompt_ids` list (and its judge and roundtrip appends) with `prompt_ids = run_prompt_ids(cfg)`, imported from `coglossa.prompts.check`. Any existing test that pins `report.config.prompt_versions` must now also expect `"sweep": 1`.

`engine/coglossa/stages/sweep.py`:

```python
"""Sweep stage (spec §4.1): after every chunk is done, enforce the locked bible entries
(glossary terms and names a human locked) across the whole document, and resolve the terms the
reviewers proposed. Every text change is a SpanChange with a reason; nothing changes silently.
Scores keep describing the reviewed draft they were computed on."""

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel, Field

from coglossa.llm.agent import AGENT_FAILURES
from coglossa.schema import (
    Bible,
    DocumentProfile,
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    Revision,
    RevisionSource,
    RunError,
    Segment,
    Span,
)
from coglossa.scoring.rules import BibleViolation, bible_check, term_in
from coglossa.stages.bible import bible_slice
from coglossa.stages.context import StageContext


class SweepFix(BaseModel):
    text: str = Field(description="The full revised translation of the passage")


@dataclass(frozen=True)
class SpanChange:
    span_id: str
    before: str
    after: str
    reason: str
    resolved: tuple[FlagCode, ...]  # flag codes the change fixed


@dataclass
class SweepResult:
    spans: list[Span]  # same order as the input; text and flag status updated
    changes: list[SpanChange]
    errors: list[RunError]

    @property
    def revisions(self) -> list[Revision]:
        return [
            Revision(
                span_id=c.span_id,
                source=RevisionSource.SWEEP,
                before=c.before,
                after=c.after,
                reason=c.reason,
            )
            for c in self.changes
        ]


def span_sources(spans: list[Span], segments: list[Segment]) -> list[str]:
    positions = {s.id: i for i, s in enumerate(segments)}
    return [
        " ".join(seg.text for seg in segments[positions[sp.start] : positions[sp.end] + 1])
        for sp in spans
    ]


def _locked_violations(source: str, text: str, bible: Bible) -> list[BibleViolation]:
    _, violations = bible_check(source, text, bible)
    return [v for v in violations if v.locked]


async def run_sweep(
    ctx: StageContext,
    profile: DocumentProfile,
    bible: Bible,
    spans: list[Span],
    segments: list[Segment],
) -> SweepResult:
    sources = span_sources(spans, segments)
    todo = [
        (i, violations)
        for i, span in enumerate(spans)
        if span.text.strip() and (violations := _locked_violations(sources[i], span.text, bible))
    ]
    results = await asyncio.gather(
        *(_fix(ctx, profile, bible, spans[i], sources[i], v) for i, v in todo),
        return_exceptions=True,
    )
    new_spans = list(spans)
    changes: list[SpanChange] = []
    errors: list[RunError] = []
    for (i, violations), result in zip(todo, results, strict=True):
        span = spans[i]
        if isinstance(result, AGENT_FAILURES):
            errors.append(
                RunError(
                    stage="sweep", chunk_id=span.chunk_id, role="sweep", message=f"{span.id}: {result}"
                )
            )
            continue
        if isinstance(result, BaseException):
            raise result
        # validation guarantees the text changed: it now contains a rendering it lacked
        rules = "; ".join(f"'{v.term}' → '{v.expected}'" for v in violations)
        flags = [
            f.model_copy(update={"status": FlagStatus.RESOLVED})
            if f.code is FlagCode.TERMINOLOGY_VIOLATION and f.status is FlagStatus.OPEN
            else f
            for f in span.flags
        ]
        new_spans[i] = span.model_copy(update={"text": result, "flags": flags})
        changes.append(
            SpanChange(
                span_id=span.id,
                before=span.text,
                after=result,
                reason=f"Applied locked bible entries: {rules}.",
                resolved=(FlagCode.TERMINOLOGY_VIOLATION,),
            )
        )
    return SweepResult(spans=new_spans, changes=changes, errors=errors)


async def _fix(
    ctx: StageContext,
    profile: DocumentProfile,
    bible: Bible,
    span: Span,
    source: str,
    violations: list[BibleViolation],
) -> str:
    required = [v.expected for v in violations]

    def check(out: SweepFix) -> list[str]:
        problems = [
            f"the revised text must contain '{r}' exactly as written"
            for r in required
            if not term_in(r, out.text)
        ]
        if not out.text.strip():
            problems.append("text must not be empty")
        return problems

    out = await ctx.runner.run(
        role="sweep",
        model_key=ctx.cfg.roles.reviewer.model,
        prompt_id="sweep",
        prompt_vars={"source_lang": profile.source_lang, "target_lang": profile.target_lang},
        payload={
            "task": "sweep",
            "source_lang": profile.source_lang,
            "target_lang": profile.target_lang,
            "source": source,
            "translation": span.text,
            "requirements": [
                {"term": v.term, "rendering": v.expected, "kind": v.kind} for v in violations
            ],
            "entities": [e.model_dump() for e in bible_slice(bible, source).entities],
        },
        output_type=SweepFix,
        validate=check,
    )
    return out.text


def resolve_proposals(bible: Bible, proposals: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """One unlocked entry per new term: the most frequent rendering (ties: first seen), with
    conflicting alternatives noted. Terms already in the bible are skipped."""
    known = {g.term.strip().casefold() for g in bible.glossary}
    groups: dict[str, list[GlossaryEntry]] = {}
    for p in proposals:
        key = p.term.strip().casefold()
        if key and key not in known:
            groups.setdefault(key, []).append(p)
    resolved: list[GlossaryEntry] = []
    for entries in groups.values():
        counts: dict[str, int] = {}
        first: dict[str, GlossaryEntry] = {}
        for e in entries:
            k = e.rendering.strip().casefold()
            counts[k] = counts.get(k, 0) + 1
            first.setdefault(k, e)
        best = max(counts, key=lambda k: counts[k])  # max keeps the first of equal counts
        chosen = first[best]
        notes = chosen.notes
        if len(counts) > 1:
            alternatives = ", ".join(f"'{first[k].rendering.strip()}' ({n})" for k, n in counts.items())
            notes = f"{notes} " if notes else ""
            notes += f"Reviewers proposed different renderings: {alternatives}."
        resolved.append(
            chosen.model_copy(
                update={
                    "term": chosen.term.strip(),
                    "rendering": chosen.rendering.strip(),
                    "notes": notes,
                    "locked": False,
                }
            )
        )
    return resolved
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd engine && uv run pytest -q`

Expected: PASS; coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add engine
git commit -m "feat(stages): sweep enforces locked bible entries with revisions; resolve proposed terms"
```

---

### Task 4: Sweep in the CLI pipeline, open-flag counts, wording

**Files:**
- Modify:
  - `engine/coglossa/pipeline.py`
  - `engine/coglossa/cli.py`
  - `engine/coglossa/scoring/aggregate.py`
  - `README.md`
  - `docs/m2-followups.md`
- Test: `engine/tests/test_pipeline_sweep.py`, plus additions to `engine/tests/test_cli.py`

**Interfaces:**
- Consumes: `run_sweep`, `resolve_proposals`, `SweepResult`, `cfg.profiles.sweep`
- Produces:
  - `run_document` runs the sweep when `cfg.profiles.sweep[profile.pipeline_profile]` is true.
  - The report carries `revisions` and resolved flags.
  - Progress lines: `sweep: N passage(s) revised` or `sweep: off for the <profile> profile`.
  - `any_scored(spans: list[Span]) -> bool` in `coglossa.scoring.aggregate`, moved from `pipeline._any_scored`.
  - The CLI summary counts **open** flags only and adds `, N revised by the sweep` when revisions exist.
  - `report.bible_proposals` now comes from `resolve_proposals`.

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_pipeline_sweep.py`:

```python
from datetime import UTC, datetime

from coglossa.pipeline import RunOptions, run_document
from coglossa.schema import (
    Bible,
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    PipelineProfile,
    RevisionSource,
    RunStatus,
)
from coglossa.scoring.aggregate import any_scored

TEXT = "Le chat dort. Il pleut."
BIBLE = Bible(
    summary="s",
    style_guide="g",
    glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
)


async def _no_sleep(_):
    return None


async def _run(tmp_path, cfg, fake_backend, **opts):
    path = tmp_path / "doc.txt"
    path.write_text(TEXT, encoding="utf-8")
    lines = []
    report = await run_document(
        path,
        cfg,
        {"fake": fake_backend},
        RunOptions(target_lang="en", source_lang="fr", bible=BIBLE, bible_approved=True, **opts),
        sleep=_no_sleep,
        now=lambda: datetime(2026, 10, 10, tzinfo=UTC),
        progress=lines.append,
    )
    return report, lines


async def test_sweep_enforces_locked_terms(tmp_path, make_config, fake_backend):
    cfg = make_config(profiles={"sweep": {"short": True}})
    report, lines = await _run(tmp_path, cfg, fake_backend)
    first = report.spans[0]
    assert "cat" in first.text
    (rev,) = report.revisions
    assert rev.span_id == "p1" and rev.source is RevisionSource.SWEEP
    assert rev.before == "[en] Le chat dort."
    assert rev.after == first.text
    violation = [f for f in first.flags if f.code is FlagCode.TERMINOLOGY_VIOLATION]
    assert [f.status for f in violation] == [FlagStatus.RESOLVED]
    assert "sweep: 1 passage(s) revised" in lines
    assert report.status is RunStatus.COMPLETE


async def test_sweep_is_off_for_short_documents_by_default(tmp_path, make_config, fake_backend):
    report, lines = await _run(tmp_path, make_config(), fake_backend)
    assert report.revisions == []
    assert "sweep: off for the short profile" in lines
    violation = [f for f in report.spans[0].flags if f.code is FlagCode.TERMINOLOGY_VIOLATION]
    assert [f.status for f in violation] == [FlagStatus.OPEN]


async def test_sweep_errors_make_the_run_complete_with_errors(tmp_path, make_config, fake_backend):
    fake_backend.script("sweep", '{"text": "nope"}', '{"text": "nope"}')
    cfg = make_config(profiles={"sweep": {"short": True}})
    report, _ = await _run(tmp_path, cfg, fake_backend)
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    assert [e.stage for e in report.errors] == ["sweep"]


async def test_standard_profile_sweeps(tmp_path, make_config, fake_backend):
    report, _ = await _run(
        tmp_path, make_config(), fake_backend, pipeline_profile=PipelineProfile.STANDARD
    )
    assert len(report.revisions) == 1


def test_any_scored():
    assert any_scored([]) is False
```

Add to `engine/tests/test_cli.py`, in the file's own style (it already has helpers that write a config and input and call `main([...])`):
- A run whose report has one `resolved` and one `open` flag prints `1 flags`, followed by `, 1 revised by the sweep`.
- The checkpoint message no longer contains `not yet checked automatically` and does contain `the sweep enforces them`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && uv run pytest tests/test_pipeline_sweep.py -q --no-cov`

Expected: FAIL with `ImportError: any_scored`, and no revisions.

- [ ] **Step 3: Implement**

In `engine/coglossa/scoring/aggregate.py`, add:

```python
def any_scored(spans: list[Span]) -> bool:
    return any(s.score.meaning is not None or s.score.style is not None for s in spans)
```

Import `Span` from `coglossa.schema`. In `pipeline.py`, delete `_any_scored` and use `any_scored`.

In `engine/coglossa/pipeline.py`, split `_assemble` into two parts:
1. **`_collect`**, which renumbers spans and gathers candidates, raw proposals, errors and `any_engine_error` from the outcomes. It returns them; `_assemble` itself no longer loops over outcomes.
2. **Report building**, which takes the final spans, revisions and resolved proposals.

```python
@dataclass
class _Collected:
    spans: list[Span]
    candidates: list[Candidate]
    proposals: list[GlossaryEntry]
    errors: list[RunError]
    engine_error: bool


def _collect(outcomes: list[ChunkOutcome]) -> _Collected:
    collected = _Collected([], [], [], [], False)
    for outcome in outcomes:
        collected.candidates.extend(outcome.candidates)
        collected.errors.extend(outcome.errors)
        collected.engine_error = collected.engine_error or outcome.engine_error
        for span, terms in zip(outcome.spans, outcome.proposed_terms, strict=True):
            collected.spans.append(
                span.model_copy(update={"id": f"p{len(collected.spans) + 1}"})
            )
            collected.proposals.extend(terms)
    return collected
```

In `run_document`, after `outcomes = await asyncio.gather(...)`:

```python
    collected = _collect(list(outcomes))
    errors.extend(collected.errors)
    spans = collected.spans
    revisions: list[Revision] = []
    if cfg.profiles.sweep.get(profile.pipeline_profile, True):
        swept = await run_sweep(ctx, profile, bible, spans, segments)
        spans, revisions = swept.spans, swept.revisions
        errors.extend(swept.errors)
        say(f"sweep: {len(swept.changes)} passage(s) revised")
    else:
        say(f"sweep: off for the {profile.pipeline_profile.value} profile")
```

Pass `spans`, `collected.candidates`, `resolve_proposals(bible, collected.proposals)`, `revisions` and `collected.engine_error` into `_assemble`. That replaces the old outcome loop, `known_terms` and proposals logic. `Report(...)` gains `revisions=revisions`. The status line remains `COMPLETE_WITH_ERRORS if errors or engine_error else COMPLETE`.

The `_assemble` signature becomes:

```python
def _assemble(
    doc: SourceDocument,
    segments: list[Segment],
    chunks: list[Chunk],
    profile: DocumentProfile,
    bible: Bible,
    tier: Reliability,
    spans: list[Span],
    candidates: list[Candidate],
    proposals: list[GlossaryEntry],
    revisions: list[Revision],
    engine_error: bool,
    errors: list[RunError],
    cfg: CoglossaConfig,
    prompts: PromptLibrary,
    log: CallLog,
    created_at: datetime,
    scorer_health: ScorerHealth | None,
) -> Report:
```

In `engine/coglossa/cli.py`:
- `_write_report`: `flags = sum(1 for s in report.spans for f in s.flags if f.status is FlagStatus.OPEN)`. After the `{tokens} tokens ({tier})` part, append `f", {len(report.revisions)} revised by the sweep"` when `report.revisions` is non-empty.
- Replace the checkpoint instructions with:

```python
    print(
        f"Bible ready for review: {bible_path}\n"
        'Edit it (set "locked": true on glossary entries and entities that must be used as\n'
        "written: locked entries are mandatory, the sweep enforces them and flags any it cannot\n"
        "fix), then run:\n"
        f"  {_resume_command(args, out_dir)}"
    )
```

In `README.md`:
- Replace the "not yet checked automatically" wording with the same meaning: locked entries are enforced by the sweep and flagged when missing.
- In the Quick start comment, say `set "locked": true on terms and names that must be used as written`.
- Add a short "Sweep" paragraph under Scoring: after all chunks, the sweep rewrites passages that miss a locked bible entry, records each change as a revision with a reason, and marks the fixed flag `resolved`. It is on for `standard` and `long-form` (`profiles.sweep`). Scores describe the reviewed draft.

In `docs/m2-followups.md`, under "Should fix soon", change the "Stale wording" bullet to "(fixed in M3)".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd engine && uv run pytest -q`

Expected: PASS; coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add engine README.md docs/m2-followups.md
git commit -m "feat(pipeline): run the sweep, report revisions and resolved proposals; count open flags"
```

---

### Task 5: File storage

**Files:**
- Create: `engine/coglossa/storage.py`
- Test: `engine/tests/test_storage.py`

**Interfaces:**
- Consumes: `StorageConfig` (Task 1)
- Produces:
  - `StorageError(Exception)`: the message is user-facing
  - `Storage` (Protocol): `put(key: str, data: bytes) -> None`, `get(key: str) -> bytes`, `local_file(key: str) -> AbstractContextManager[Path]`, `delete_prefix(prefix: str) -> None`, `check_writable() -> None`
  - `LocalStorage(root: Path)` implements it
  - `build_storage(cfg: StorageConfig) -> Storage`
  - `safe_filename(name: str) -> str`

Spec §2.1: "Files live on a local volume behind a Storage interface; S3/MinIO is a future implementation of that interface."
- Keys are relative, `/`-separated paths, for example `projects/<uuid>/documents/<uuid>/story.docx`.
- `LocalStorage` rejects absolute keys, `..` and backslashes with a `StorageError`, so a crafted filename can never escape the root.
- Writes are atomic: a temporary file, then `os.replace`.
- `local_file` yields a path an ingestor can open. For local storage it is the stored file itself; an object store would download to a temporary file.

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_storage.py`:

```python
import pytest

from coglossa.config import StorageConfig
from coglossa.storage import LocalStorage, StorageError, build_storage, safe_filename


def test_put_get_and_local_file(tmp_path):
    store = LocalStorage(tmp_path / "files")
    store.put("projects/p1/documents/d1/story.txt", b"hello")
    assert store.get("projects/p1/documents/d1/story.txt") == b"hello"
    with store.local_file("projects/p1/documents/d1/story.txt") as path:
        assert path.read_bytes() == b"hello"
        assert path.suffix == ".txt"
    assert not list((tmp_path / "files").rglob("*.tmp"))


def test_put_overwrites_atomically(tmp_path):
    store = LocalStorage(tmp_path)
    store.put("a/b.txt", b"one")
    store.put("a/b.txt", b"two")
    assert store.get("a/b.txt") == b"two"


@pytest.mark.parametrize("key", ["", "/etc/passwd", "../x", "a/../../x", "a\\b", "a//b", "a/./b"])
def test_unsafe_keys_are_rejected(tmp_path, key):
    with pytest.raises(StorageError):
        LocalStorage(tmp_path).put(key, b"x")


def test_missing_key(tmp_path):
    store = LocalStorage(tmp_path)
    with pytest.raises(StorageError, match="not found"):
        store.get("nope.txt")
    with pytest.raises(StorageError, match="not found"), store.local_file("nope.txt"):
        pass


def test_delete_prefix(tmp_path):
    store = LocalStorage(tmp_path)
    store.put("projects/p1/a.txt", b"1")
    store.put("projects/p1/sub/b.txt", b"2")
    store.put("projects/p2/c.txt", b"3")
    store.delete_prefix("projects/p1/")
    assert not (tmp_path / "projects" / "p1").exists()
    assert store.get("projects/p2/c.txt") == b"3"
    store.delete_prefix("projects/none/")  # missing prefix is fine


def test_check_writable(tmp_path):
    LocalStorage(tmp_path / "new").check_writable()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StorageError, match="not writable"):
        LocalStorage(blocker / "sub").check_writable()


def test_write_failure_is_a_storage_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StorageError, match="cannot store"):
        LocalStorage(blocker).put("a.txt", b"x")


def test_build_storage(tmp_path):
    store = build_storage(StorageConfig(path=tmp_path))
    assert isinstance(store, LocalStorage) and store.root == tmp_path


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("story.docx", "story.docx"),
        ("mon récit (v2).TXT", "mon_r_cit__v2_.TXT"),
        ("../../etc/passwd", "passwd"),
        ("...", "document"),
        ("", "document"),
    ],
)
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && uv run pytest tests/test_storage.py -q --no-cov`

Expected: FAIL with `ModuleNotFoundError: coglossa.storage`.

- [ ] **Step 3: Implement**

`engine/coglossa/storage.py`:

```python
"""Uploaded files (spec §2.1): a Storage interface with a local-volume implementation.
Keys are relative '/'-separated paths; nothing outside the storage root is reachable."""

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path, PurePosixPath
from typing import Protocol

from coglossa.config import StorageConfig


class StorageError(Exception):
    """A storage operation failed; the message is safe to show to an operator."""


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def local_file(self, key: str) -> AbstractContextManager[Path]: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def check_writable(self) -> None: ...


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_filename(name: str) -> str:
    """A storage-safe basename that keeps the extension (ingestors pick a format by it)."""
    base = PurePosixPath(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", base).strip(".")
    return cleaned or "document"


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if (
            not key
            or key.startswith("/")
            or "\\" in key
            or any(p in ("", ".", "..") for p in parts)
        ):
            raise StorageError(f"invalid storage key: {key!r}")
        return self.root.joinpath(*parts)

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError as e:
            raise StorageError(f"cannot store {key}: {e.strerror or e}") from e

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as e:
            raise StorageError(f"stored file not found: {key}") from e
        except OSError as e:
            raise StorageError(f"cannot read {key}: {e.strerror or e}") from e

    @contextlib.contextmanager
    def local_file(self, key: str) -> Iterator[Path]:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"stored file not found: {key}")
        yield path

    def delete_prefix(self, prefix: str) -> None:
        target = self._path(prefix.rstrip("/"))
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    def check_writable(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".coglossa-check-"):
                pass
        except OSError as e:
            raise StorageError(
                f"storage path {self.root} is not writable: {e.strerror or e}"
            ) from e


def build_storage(cfg: StorageConfig) -> Storage:
    return LocalStorage(cfg.path)  # kind is validated by StorageConfig (local only in v1)
```

Note: `"a//b"` splits into `["a", "", "b"]`, and the empty part is rejected. `"a/./b"` contains `"."`, which is rejected.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd engine && uv run pytest -q`

Expected: PASS; coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add engine
git commit -m "feat(storage): Storage interface with an atomic, path-safe local implementation"
```

---
### Task 6: Database schema, migrations and Postgres test infrastructure

**Files:**
- Modify:
  - `engine/pyproject.toml`
  - `Makefile`
  - `.github/workflows/ci.yml`
  - `engine/tests/conftest.py`
- Create:
  - `engine/coglossa/store/__init__.py`
  - `engine/coglossa/store/models.py`
  - `engine/coglossa/store/db.py`
  - `engine/coglossa/store/migrate.py`
  - `engine/coglossa/store/migrations/env.py`
  - `engine/coglossa/store/migrations/script.py.mako`
  - `engine/coglossa/store/migrations/versions/0001_initial_schema.py` (autogenerated, then edited)
  - `deploy/compose.dev.yaml`
- Test: `engine/tests/store/__init__.py`, `engine/tests/store/test_migrations.py`, `engine/tests/store/test_db.py`

**Interfaces:**
- Produces:
  - ORM models in `coglossa.store.models`:
    - `Base`, `utcnow()`
    - `Project`, `BibleVersion`, `Document`, `BlockRow`, `SegmentRow`, `Run`, `ChunkRow`, `StageResult`, `LLMCall`, `CandidateRow`, `SpanRow`, `SpanScoreRow`, `FlagRow`, `RevisionRow`, `ReviewEvent`
  - `coglossa.store.db`:
    - `to_sqlalchemy_url(url) -> str`, `to_libpq_url(url) -> str`, `redact(url) -> str`
    - `database_url(cfg) -> str`, raising `ConfigError` when the variable is unset or not a Postgres URL
    - `Database(url)` with `.engine`, `.session() -> AsyncSession`, `async .ping()`, `async .dispose()`
  - `coglossa.store.migrate`:
    - `alembic_config(url)`, `upgrade(url, revision="head")`, `downgrade(url, revision="base")`
    - `MigrationState(current, head)` with `.up_to_date`
    - `migration_state(url) -> MigrationState`
  - Test fixtures in `engine/tests/conftest.py`:
    - `pg_admin` (session): the admin URL
    - `pg_url` (session): a migrated throwaway database
    - `db` (function, async): a `Database` on `pg_url` with every table truncated
    - `create_test_database(admin) -> str`, `drop_test_database(admin, url) -> None`

**Schema** (spec §5). JSONB holds Pydantic dumps (`model_dump(mode="json")`). `ON DELETE CASCADE` flows from `project` down, so deleting a project removes all of its rows (spec §5). The columns differ from the spec's sketch in five places, each a ruling:
- **`chunk` belongs to the run, not the document.** Chunk sizes depend on the run's configured context windows.
- **`bible_version.source_run_id` has no foreign key.** Runs reference bible versions; a foreign key the other way would be circular.
- **`stage_result.error_count`** is the number of errors in the stage's output (or 1 for a failed stage). Progress and the final status are then cheap SQL queries.
- **`span.proposed_terms`** stores the reviewer's proposals per span, so reports can re-resolve them.
- **`flag.status` adds `resolved`** to spec §5's `open|accepted|dismissed`. The sweep sets it when its revision fixes the problem; it stays distinct from a human's `accepted`.

- [ ] **Step 1: Dependencies, dev database, Makefile and CI**

In `engine/pyproject.toml`, add to `dependencies`:

```toml
  "sqlalchemy[asyncio]>=2.0.36,<3",
  "alembic>=1.14,<2",
  "psycopg[binary]>=3.2,<4",
  "procrastinate>=3.10,<3.11",
```

Then run `cd engine && uv lock && uv sync --all-groups`.

`deploy/compose.dev.yaml`:

```yaml
# Development and test database only (the deployable compose file is built in M5).
name: coglossa-dev
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: coglossa
      POSTGRES_PASSWORD: coglossa
      POSTGRES_DB: coglossa
    ports: ["127.0.0.1:55432:5432"]
    tmpfs: [/var/lib/postgresql/data]  # throwaway: tests create and drop their own databases
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U coglossa"]
      interval: 2s
      timeout: 3s
      retries: 30
```

`Makefile`:
- add `db-up db-down` to `.PHONY`
- add near the top:

```make
DB_URL ?= postgresql://coglossa:coglossa@127.0.0.1:55432/postgres
COMPOSE_DEV = docker compose -f deploy/compose.dev.yaml
```

- add targets:

```make
db-up:
	$(COMPOSE_DEV) up -d --wait postgres

db-down:
	$(COMPOSE_DEV) down
```

- change `test-engine`:

```make
test-engine: db-up
	cd engine && COGLOSSA_TEST_DATABASE_URL=$${COGLOSSA_TEST_DATABASE_URL:-$(DB_URL)} uv run pytest
```

In `.github/workflows/ci.yml`, give the `engine` job a Postgres service and the variable:

```yaml
  engine:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: coglossa
          POSTGRES_PASSWORD: coglossa
          POSTGRES_DB: coglossa
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U coglossa"
          --health-interval 2s --health-timeout 3s --health-retries 30
    env:
      COGLOSSA_TEST_DATABASE_URL: postgresql://coglossa:coglossa@127.0.0.1:5432/postgres
    defaults:
      run:
        working-directory: engine
```

The steps stay as they are.

- [ ] **Step 2: Write the failing tests**

Append to `engine/tests/conftest.py`:

```python
import os
import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url

TEST_DB_ENV = "COGLOSSA_TEST_DATABASE_URL"
PROCRASTINATE_TABLES = (
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_jobs",
    "procrastinate_workers",
)


def create_test_database(admin: str) -> str:
    from coglossa.store.db import to_libpq_url

    name = f"coglossa_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(to_libpq_url(admin), autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return make_url(admin).set(database=name).render_as_string(hide_password=False)


def drop_test_database(admin: str, url: str) -> None:
    from coglossa.store.db import to_libpq_url

    name = make_url(url).database
    with psycopg.connect(to_libpq_url(admin), autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def pg_admin() -> str:
    admin = os.environ.get(TEST_DB_ENV)
    if not admin:
        pytest.fail(
            f"{TEST_DB_ENV} is not set. Start Postgres with `make db-up` and set "
            f"{TEST_DB_ENV}=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres "
            "(`make test-engine` does both).",
            pytrace=False,
        )
    return admin


@pytest.fixture(scope="session")
def pg_url(pg_admin) -> Iterator[str]:
    from coglossa.store.migrate import upgrade

    url = create_test_database(pg_admin)
    upgrade(url)
    yield url
    drop_test_database(pg_admin, url)


@pytest.fixture
async def db(pg_url) -> AsyncIterator["Database"]:
    from coglossa.store.db import Database
    from coglossa.store.models import Base

    database = Database(pg_url)
    tables = [t.name for t in Base.metadata.sorted_tables] + list(PROCRASTINATE_TABLES)
    async with database.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield database
    await database.dispose()
```

Add `if TYPE_CHECKING: from coglossa.store.db import Database` (and `from typing import TYPE_CHECKING`) so the annotation resolves. Keep the imports inside the fixtures so tests without a database never import SQLAlchemy.

`engine/tests/store/test_db.py`:

```python
import pytest

from coglossa.config import ConfigError
from coglossa.store.db import Database, database_url, redact, to_libpq_url, to_sqlalchemy_url
from tests.conftest import build_config


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgres://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
        ("postgresql+psycopg://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ],
)
def test_to_sqlalchemy_url(url, expected):
    assert to_sqlalchemy_url(url) == expected


def test_to_libpq_url():
    assert to_libpq_url("postgresql+psycopg://u:p@h/d") == "postgresql://u:p@h/d"


@pytest.mark.parametrize("url", ["mysql://u:p@h/d", "not a url", "sqlite:///x.db"])
def test_non_postgres_urls_are_rejected(url):
    with pytest.raises(ConfigError, match="postgresql://"):
        to_sqlalchemy_url(url)


def test_redact_hides_the_password():
    assert redact("postgresql://u:secret@h/d") == "postgresql://u:***@h/d"


def test_database_url(monkeypatch):
    cfg = build_config()
    monkeypatch.delenv("COGLOSSA_DATABASE_URL", raising=False)
    with pytest.raises(ConfigError, match="set COGLOSSA_DATABASE_URL"):
        database_url(cfg)
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", "mysql://x")
    with pytest.raises(ConfigError, match="postgresql://"):
        database_url(cfg)
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", "postgresql://u:p@h/d")
    assert database_url(cfg) == "postgresql://u:p@h/d"


async def test_ping(db):
    await db.ping()


async def test_ping_fails_cleanly_for_an_unreachable_server():
    from sqlalchemy.exc import OperationalError

    database = Database("postgresql://u:p@127.0.0.1:9/nowhere")
    with pytest.raises(OperationalError):
        await database.ping()
    await database.dispose()
```

`engine/tests/store/test_migrations.py`:

```python
import uuid

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.pool import NullPool

from coglossa.store.db import to_sqlalchemy_url
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
from tests.conftest import create_test_database, drop_test_database

OUR_TABLES = {
    "project",
    "bible_version",
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


def test_upgrade_creates_our_tables_and_the_job_queue(pg_url):
    tables = _tables(pg_url)
    assert OUR_TABLES <= tables
    assert {"procrastinate_jobs", "procrastinate_workers"} <= tables
    assert set(Base.metadata.tables) == OUR_TABLES


def test_migration_state_is_current(pg_url):
    state = migration_state(pg_url)
    assert state.current == state.head == "0001"
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
```

`s.delete(project)` issues a single `DELETE`, and `ON DELETE CASCADE` does the rest. Every relationship-free model keeps SQLAlchemy from loading children. If SQLAlchemy complains, use `await s.execute(delete(Project).where(Project.id == project.id))`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `make db-up && cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/store -q --no-cov`

Expected: FAIL with `ModuleNotFoundError: coglossa.store`.

- [ ] **Step 4: Implement models, URL handling and migrations**

`engine/coglossa/store/__init__.py`:

```python
"""Persistence (spec §5): Postgres tables, migrations and data access. The engine library
(stages, pipeline, scoring) never imports this package."""
```

`engine/coglossa/store/models.py`:

```python
"""Postgres schema (spec §5). JSONB columns hold Pydantic models dumped with mode="json"; their
shapes are the exported schemas in schema/. Rows cascade from their project, so deleting a
project deletes everything that belongs to it."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Json = dict[str, Any]
JsonList = list[Any]


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {
        Json: JSONB,
        JsonList: JSONB,
        datetime: TIMESTAMP(timezone=True),
        uuid.UUID: UUID(as_uuid=True),
        str: Text,
    }


def _cascade(target: str, **kwargs: Any) -> Any:
    return mapped_column(ForeignKey(target, ondelete="CASCADE"), **kwargs)


class Project(Base):
    __tablename__ = "project"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class BibleVersion(Base):
    """Append-only (spec §5). source_run_id has no foreign key: runs point at versions."""

    __tablename__ = "bible_version"
    __table_args__ = (UniqueConstraint("project_id", "version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = _cascade("project.id", index=True)
    version: Mapped[int]
    status: Mapped[str]  # draft | approved
    author: Mapped[str]  # engine | user
    source_run_id: Mapped[uuid.UUID | None]
    content: Mapped[Json]  # Bible
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Document(Base):
    __tablename__ = "document"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = _cascade("project.id", index=True)
    filename: Mapped[str]
    format: Mapped[str]
    sha256: Mapped[str]
    storage_key: Mapped[str]
    ingested_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class BlockRow(Base):
    __tablename__ = "block"
    document_id: Mapped[uuid.UUID] = _cascade("document.id", primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    block_id: Mapped[str]
    kind: Mapped[str]
    text: Mapped[str]
    provenance: Mapped[Json]


class SegmentRow(Base):
    __tablename__ = "segment"
    document_id: Mapped[uuid.UUID] = _cascade("document.id", primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[str]
    block_id: Mapped[str]
    start: Mapped[int] = mapped_column("char_start")
    end: Mapped[int] = mapped_column("char_end")
    text: Mapped[str]


class Run(Base):
    __tablename__ = "run"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = _cascade("document.id", index=True)
    state: Mapped[str] = mapped_column(default="created", index=True)
    target_lang: Mapped[str]
    source_lang: Mapped[str | None]  # user override
    pipeline_profile: Mapped[str | None]  # user override
    checkpoint: Mapped[bool | None]  # user override
    use_project_bible: Mapped[bool] = mapped_column(default=False)
    config_hash: Mapped[str]
    config_snapshot: Mapped[Json]
    prompt_versions: Mapped[Json]
    profile: Mapped[Json | None]  # DocumentProfile, after profiling
    language_tier: Mapped[str | None]
    bible_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bible_version.id", ondelete="SET NULL")
    )
    scorer_health: Mapped[Json | None]
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class ChunkRow(Base):
    """Chunks belong to the run: their size depends on the run's configured context windows."""

    __tablename__ = "chunk"
    __table_args__ = (UniqueConstraint("run_id", "chunk_id"),)
    run_id: Mapped[uuid.UUID] = _cascade("run.id", primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[str]
    segment_ids: Mapped[JsonList]


class StageResult(Base):
    """Idempotency key (run, stage, chunk) — spec §7.1. chunk_id is "" for run-level stages."""

    __tablename__ = "stage_result"
    __table_args__ = (UniqueConstraint("run_id", "stage", "chunk_id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = _cascade("run.id", index=True)
    stage: Mapped[str]
    chunk_id: Mapped[str] = mapped_column(default="")
    status: Mapped[str]  # running | complete | failed
    attempt: Mapped[int] = mapped_column(default=1)
    output: Mapped[Json | None]
    error: Mapped[str | None]
    error_count: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    duration_ms: Mapped[int | None]


class LLMCall(Base):
    __tablename__ = "llm_call"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = _cascade("run.id", index=True)
    stage: Mapped[str]
    chunk_id: Mapped[str | None]
    role: Mapped[str]
    backend: Mapped[str]
    model: Mapped[str]
    prompt_id: Mapped[str]
    prompt_version: Mapped[int]
    attempt: Mapped[int]
    ok: Mapped[bool]
    latency_ms: Mapped[int]
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    error: Mapped[str | None]
    request: Mapped[Json | None]  # only when audit.store_llm_io
    response: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CandidateRow(Base):
    __tablename__ = "candidate"
    __table_args__ = (Index("ix_candidate_run_chunk", "run_id", "chunk_id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = _cascade("run.id")
    chunk_id: Mapped[str]
    chunk_ordinal: Mapped[int]
    translator: Mapped[str]
    model: Mapped[str]
    seg_start: Mapped[str]
    seg_end: Mapped[str]
    text: Mapped[str]
    notes: Mapped[str]


class SpanRow(Base):
    __tablename__ = "span"
    __table_args__ = (UniqueConstraint("run_id", "chunk_ordinal", "ordinal"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = _cascade("run.id", index=True)
    chunk_id: Mapped[str]
    chunk_ordinal: Mapped[int]
    ordinal: Mapped[int]  # position within the chunk
    seg_start: Mapped[str]
    seg_end: Mapped[str]
    agreement: Mapped[str]
    chosen_from: Mapped[str]
    rationale: Mapped[str]
    issues: Mapped[JsonList]
    judge_errors: Mapped[JsonList | None]
    back_translation: Mapped[str | None]
    proposed_terms: Mapped[JsonList]


class SpanScoreRow(Base):
    __tablename__ = "span_score"
    span_id: Mapped[uuid.UUID] = _cascade("span.id", primary_key=True)
    signals: Mapped[Json]
    meaning: Mapped[float | None]
    style: Mapped[float | None]
    reliability: Mapped[str]


class FlagRow(Base):
    __tablename__ = "flag"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    span_id: Mapped[uuid.UUID] = _cascade("span.id", index=True)
    code: Mapped[str]
    explanation: Mapped[str]
    evidence: Mapped[Json]
    status: Mapped[str] = mapped_column(default="open")
    dismiss_reason: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class RevisionRow(Base):
    """Append-only; a span's current text is its latest revision (spec §5)."""

    __tablename__ = "revision"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    span_id: Mapped[uuid.UUID] = _cascade("span.id", index=True)
    source: Mapped[str]  # engine | sweep | user
    before: Mapped[str | None]
    after: Mapped[str]
    reason: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ReviewEvent(Base):
    """Feedback log (spec §6.5); written by the review UI in M4."""

    __tablename__ = "review_event"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = _cascade("run.id", index=True)
    span_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("span.id", ondelete="CASCADE"))
    action: Mapped[str]
    payload: Mapped[Json]
    at: Mapped[datetime] = mapped_column(default=utcnow)
```

`engine/coglossa/store/db.py`:

```python
"""Database URLs and the async engine (SQLAlchemy 2 + psycopg 3)."""

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from coglossa.config import CoglossaConfig, ConfigError

_EXAMPLE = "postgresql://user:password@host:5432/coglossa"


def _parse(url: str) -> URL:
    try:
        parsed = make_url(url)
    except ArgumentError as e:
        raise ConfigError(f"invalid database URL; expected {_EXAMPLE}") from e
    if parsed.drivername.split("+")[0] not in ("postgresql", "postgres"):
        raise ConfigError(f"unsupported database '{parsed.drivername}'; expected {_EXAMPLE}")
    return parsed


def to_sqlalchemy_url(url: str) -> str:
    return _parse(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def to_libpq_url(url: str) -> str:
    return _parse(url).set(drivername="postgresql").render_as_string(hide_password=False)


def redact(url: str) -> str:
    return _parse(url).render_as_string(hide_password=True)


def database_url(cfg: CoglossaConfig) -> str:
    url = cfg.database.url()
    if url is None:
        raise ConfigError(f"no database configured: set {cfg.database.url_env} to {_EXAMPLE}")
    _parse(url)
    return url


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine: AsyncEngine = create_async_engine(to_sqlalchemy_url(url), pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        return self.sessions()

    async def ping(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
```

`redact` renders the password as `***` (SQLAlchemy's `hide_password=True`). Adjust the test if your SQLAlchemy version renders it differently; the requirement is that the password is absent.

`engine/coglossa/store/migrate.py`:

```python
"""Schema migrations (spec §5: Alembic). Revision 0001 also installs procrastinate's job-queue
schema; a procrastinate upgrade needs a new revision that applies its migration SQL."""

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from coglossa.store.db import to_sqlalchemy_url

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["url"] = to_sqlalchemy_url(url)  # attributes avoid ConfigParser '%' escaping
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


@dataclass(frozen=True)
class MigrationState:
    current: str | None
    head: str

    @property
    def up_to_date(self) -> bool:
        return self.current == self.head


def migration_state(url: str) -> MigrationState:
    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    engine = create_engine(to_sqlalchemy_url(url), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
    return MigrationState(current=current, head=head or "")
```

`engine/coglossa/store/migrations/env.py`:

```python
"""Alembic environment: online migrations against the URL coglossa.store.migrate passes in."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from coglossa.store.models import Base


def _ours(name: str | None, type_: str, parent_names: object) -> bool:
    # procrastinate's tables share the database; autogenerate must never touch them
    return type_ != "table" or name in Base.metadata.tables


engine = create_engine(context.config.attributes["url"], poolclass=NullPool)
with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_name=_ours,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
```

`engine/coglossa/store/migrations/script.py.mako`: the standard Alembic generic template (copy it from `alembic/templates/generic/script.py.mako` in the installed alembic package).

Create the empty directory `engine/coglossa/store/migrations/versions/`, then **autogenerate** revision 0001 against an empty database:

```bash
cd engine && uv run python - <<'EOF'
import psycopg
from alembic import command
from coglossa.store.migrate import alembic_config
admin = "postgresql://coglossa:coglossa@127.0.0.1:55432/postgres"
with psycopg.connect(admin, autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS coglossa_autogen")
    c.execute("CREATE DATABASE coglossa_autogen")
command.revision(
    alembic_config("postgresql://coglossa:coglossa@127.0.0.1:55432/coglossa_autogen"),
    message="initial schema", autogenerate=True, rev_id="0001",
)
EOF
```

Edit the generated `versions/0001_initial_schema.py`:
- Keep every autogenerated `create_table`, `create_index` and `drop_*` call.
- Add the job-queue schema at the **end** of `upgrade()`.
- Add the job-queue removal at the **start** of `downgrade()`.

```python
from procrastinate.schema import SchemaManager

_DROP_PROCRASTINATE = """
DROP TABLE IF EXISTS procrastinate_events, procrastinate_periodic_defers, procrastinate_jobs,
    procrastinate_workers CASCADE;
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT oid::regprocedure AS fn FROM pg_proc WHERE proname LIKE 'procrastinate%' LOOP
        EXECUTE 'DROP FUNCTION IF EXISTS ' || r.fn || ' CASCADE';
    END LOOP;
END $$;
DROP TYPE IF EXISTS procrastinate_job_status, procrastinate_job_event_type,
    procrastinate_job_to_defer_v1 CASCADE;
"""


def _raw(sql: str) -> None:
    # The raw psycopg connection: this SQL contains '%' that SQLAlchemy would read as
    # placeholders. It runs inside the migration's transaction.
    op.get_bind().connection.driver_connection.execute(sql)


def upgrade() -> None:
    ...  # autogenerated table creation stays here
    _raw(SchemaManager.get_schema())  # procrastinate's job queue (spec §2.1)


def downgrade() -> None:
    _raw(_DROP_PROCRASTINATE)
    ...  # autogenerated drops stay here
```

Drop the scratch database afterwards: `DROP DATABASE coglossa_autogen`. Run `uv run ruff format coglossa/store` so the generated file passes lint.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `make test-engine`

Expected: PASS. All earlier tests plus `tests/store`; coverage ≥ 95%. Also run `make lint`.

- [ ] **Step 6: Commit**

```bash
git add engine deploy Makefile .github/workflows/ci.yml
git commit -m "feat(store): Postgres schema, Alembic migrations with the procrastinate queue, test database fixtures"
```

---

### Task 7: Data access for projects, documents, bibles, runs and stages

**Files:**
- Create: `engine/coglossa/store/runs.py`
- Test: `engine/tests/store/test_runs.py`

**Interfaces:**
- Consumes: the models, `utcnow`, `Database` (Task 6)
- Produces (in `coglossa.store.runs`). Every function takes an `AsyncSession` as its first argument (`s`) and **never commits**:
  - **Constants:**
    - `RUNNING`, `COMPLETE`, `FAILED` (stage statuses)
    - `DRAFT`, `APPROVED` (bible statuses)
    - `ENGINE`, `USER` (bible authors)
  - **Errors:** `NotFoundError(LookupError)`, with a user-facing message.
  - **Projects and documents:**
    - `get_or_create_project(s, name) -> Project`, race-free via `ON CONFLICT DO NOTHING`
    - `find_project(s, name) -> Project`
    - `add_document(s, project_id, *, document_id, filename, format, sha256, storage_key) -> Document`
    - `get_document(s, document_id) -> Document`
    - `save_source(s, document_id, blocks, segments) -> None`: idempotent, batched by 1000; sets `ingested_at` once
    - `load_source(s, document_id) -> tuple[list[Block], list[Segment]]`
  - **Bibles:**
    - `latest_bible(s, project_id, *, approved_only: bool) -> BibleVersion | None`
    - `get_bible_version(s, version_id) -> BibleVersion`
    - `add_bible_version(s, project_id, bible, *, status, author, source_run_id=None) -> BibleVersion`: numbers versions 1, 2, … per project under a project row lock
  - **Runs and chunks:**
    - `create_run(s, document_id, *, target_lang, config_hash, config_snapshot, prompt_versions, source_lang=None, pipeline_profile=None, checkpoint=None, use_project_bible=False) -> Run`
    - `get_run(s, run_id, *, lock=False) -> Run`: `lock=True` → `SELECT … FOR UPDATE` with `populate_existing`
    - `save_chunks(s, run_id, chunks)` (replaces) and `load_chunks(s, run_id) -> list[Chunk]`
  - **Stage results:**
    - `get_stage(s, run_id, stage, chunk_id="") -> StageResult | None`
    - `begin_stage(s, run_id, stage, chunk_id="") -> StageResult`: an upsert. A new row gets attempt 1; an existing row is reset to running with attempt + 1 and its output/error/timing cleared.
    - `finish_stage(row, output: dict | None) -> None`: sets `error_count` from the output (`len(output["errors"])` + 1 if `output.get("engine_error")`)
    - `fail_stage(row, error: str) -> None`: `error_count = 1`
    - `delete_stages(s, run_id, *, stages=None, chunk_ids=None, status=None) -> None`
    - `stage_rows(s, run_id) -> list[StageResult]`, ordered by id
    - `chunk_progress(s, run_id) -> dict[str, int]`: completed chunk-level stage rows, keyed by stage
    - `finished_chunk_count(s, run_id) -> int`: `score` rows that are complete or failed
    - `chunks_with_errors(s, run_id) -> list[str]`: `score` rows failed or with `error_count > 0`
    - `total_error_count(s, run_id) -> int`
  - **Model calls and deletion:**
    - `save_llm_calls(s, run_id, records, *, stage, chunk_id=None) -> None`
    - `load_llm_calls(s, run_id) -> list[LLMCallRecord]`
    - `delete_project(s, project_id) -> None`: one `DELETE`; the database cascades

- [ ] **Step 1: Write the failing tests**

`engine/tests/store/test_runs.py`:

```python
import asyncio
import uuid

import pytest

from coglossa.schema import (
    Bible,
    Block,
    BlockKind,
    Chunk,
    LLMCallRecord,
    Provenance,
    Segment,
)
from coglossa.store import runs
from coglossa.store.models import Document, Run


async def _run(db, name="p"):
    async with db.session() as s, s.begin():
        project = await runs.get_or_create_project(s, name)
        doc = await runs.add_document(
            s,
            project.id,
            document_id=uuid.uuid4(),
            filename="a.txt",
            format="txt",
            sha256="abc",
            storage_key="projects/x/a.txt",
        )
        run = await runs.create_run(
            s,
            doc.id,
            target_lang="en",
            config_hash="h1",
            config_snapshot={"k": 1},
            prompt_versions={"literal": 1},
        )
    return project, doc, run


async def test_projects_are_unique_by_name_even_under_concurrency(db):
    async def create():
        async with db.session() as s, s.begin():
            return (await runs.get_or_create_project(s, "same")).id

    ids = await asyncio.gather(*(create() for _ in range(5)))
    assert len(set(ids)) == 1
    async with db.session() as s:
        assert (await runs.find_project(s, "same")).id == ids[0]
        with pytest.raises(runs.NotFoundError, match="project 'other' not found"):
            await runs.find_project(s, "other")


async def test_source_round_trip_is_idempotent(db):
    _, doc, _ = await _run(db)
    blocks = [
        Block(
            id="b1",
            kind=BlockKind.PARAGRAPH,
            text="Il pleut. Fin.",
            provenance=Provenance(source_path="a.txt", index=0),
        )
    ]
    segments = [
        Segment(id="s1", block_id="b1", start=0, end=9, text="Il pleut."),
        Segment(id="s2", block_id="b1", start=10, end=14, text="Fin."),
    ]
    async with db.session() as s, s.begin():
        await runs.save_source(s, doc.id, blocks, segments)
        await runs.save_source(s, doc.id, blocks, segments)  # second ingest: no-op
    async with db.session() as s:
        assert await runs.load_source(s, doc.id) == (blocks, segments)
        assert (await s.get(Document, doc.id)).ingested_at is not None


async def test_large_sources_are_batched(db):
    _, doc, _ = await _run(db)
    blocks = [
        Block(id="b1", kind=BlockKind.PARAGRAPH, text="x", provenance=Provenance(source_path="a", index=0))
    ]
    segments = [Segment(id=f"s{i}", block_id="b1", start=0, end=1, text="x") for i in range(2500)]
    async with db.session() as s, s.begin():
        await runs.save_source(s, doc.id, blocks, segments)
    async with db.session() as s:
        assert len((await runs.load_source(s, doc.id))[1]) == 2500


async def test_bible_versions_are_numbered_per_project(db):
    project, _, run = await _run(db)
    bible = Bible(summary="s", style_guide="g")
    async with db.session() as s, s.begin():
        v1 = await runs.add_bible_version(
            s, project.id, bible, status=runs.DRAFT, author=runs.ENGINE, source_run_id=run.id
        )
        v2 = await runs.add_bible_version(s, project.id, bible, status=runs.APPROVED, author=runs.USER)
        v3 = await runs.add_bible_version(s, project.id, bible, status=runs.DRAFT, author=runs.ENGINE)
    assert (v1.version, v2.version, v3.version) == (1, 2, 3)
    async with db.session() as s:
        assert (await runs.latest_bible(s, project.id, approved_only=True)).id == v2.id
        assert (await runs.latest_bible(s, project.id, approved_only=False)).id == v3.id
        assert Bible.model_validate((await runs.get_bible_version(s, v1.id)).content) == bible
        with pytest.raises(runs.NotFoundError):
            await runs.get_bible_version(s, uuid.uuid4())
        assert await runs.latest_bible(s, uuid.uuid4(), approved_only=False) is None


async def test_get_run_and_lock_sees_fresh_state(db):
    _, doc, run = await _run(db)
    async with db.session() as s:
        stale = await runs.get_run(s, run.id)
        assert stale.state == "created" and stale.document_id == doc.id
        async with db.session() as other, other.begin():
            (await runs.get_run(other, run.id, lock=True)).state = "ingesting"
        async with s.begin():
            fresh = await runs.get_run(s, run.id, lock=True)
            assert fresh.state == "ingesting"
        with pytest.raises(runs.NotFoundError, match="not found"):
            await runs.get_run(s, uuid.uuid4())
        with pytest.raises(runs.NotFoundError):
            await runs.get_document(s, uuid.uuid4())


async def test_chunks_round_trip_and_replace(db):
    _, _, run = await _run(db)
    chunks = [Chunk(id="c1", segment_ids=["s1", "s2"]), Chunk(id="c2", segment_ids=["s3"])]
    async with db.session() as s, s.begin():
        await runs.save_chunks(s, run.id, chunks)
        await runs.save_chunks(s, run.id, chunks[:1])
    async with db.session() as s:
        assert await runs.load_chunks(s, run.id) == chunks[:1]


async def test_stage_lifecycle(db):
    _, _, run = await _run(db)
    async with db.session() as s, s.begin():
        row = await runs.begin_stage(s, run.id, "ingest")
        assert (row.status, row.attempt, row.chunk_id) == (runs.RUNNING, 1, "")
        runs.fail_stage(row, "boom")
    async with db.session() as s, s.begin():
        row = await runs.begin_stage(s, run.id, "ingest")
        assert (row.status, row.attempt, row.error, row.error_count) == (runs.RUNNING, 2, None, 0)
        runs.finish_stage(row, {"errors": [{"stage": "x", "message": "m"}], "chunks": 2})
    async with db.session() as s:
        row = await runs.get_stage(s, run.id, "ingest")
        assert row.status == runs.COMPLETE and row.error_count == 1
        assert row.duration_ms is not None and row.finished_at is not None
        assert await runs.get_stage(s, run.id, "profile") is None


async def test_chunk_counters(db):
    _, _, run = await _run(db)
    async with db.session() as s, s.begin():
        for cid, outcome in (("c1", {"errors": []}), ("c2", {"errors": [], "engine_error": True})):
            for stage in ("translate", "reconcile", "score"):
                runs.finish_stage(await runs.begin_stage(s, run.id, stage, cid), outcome)
        runs.fail_stage(await runs.begin_stage(s, run.id, "score", "c3"), "boom")
        await runs.begin_stage(s, run.id, "translate", "c4")
        runs.finish_stage(await runs.begin_stage(s, run.id, "bible"), {"errors": [{"stage": "bible", "message": "m"}]})
    async with db.session() as s:
        assert await runs.chunk_progress(s, run.id) == {"translate": 2, "reconcile": 2, "score": 2}
        assert await runs.finished_chunk_count(s, run.id) == 3
        assert sorted(await runs.chunks_with_errors(s, run.id)) == ["c2", "c3"]
        assert await runs.total_error_count(s, run.id) == 5  # c2: 3 rows x 1, c3: 1, bible: 1
        assert [r.stage for r in await runs.stage_rows(s, run.id)][:3] == ["translate", "reconcile", "score"]
    async with db.session() as s, s.begin():
        await runs.delete_stages(s, run.id, chunk_ids=["c2", "c3"])
        await runs.delete_stages(s, run.id, stages=["bible"])
        await runs.delete_stages(s, run.id, status=runs.RUNNING)
    async with db.session() as s:
        assert await runs.finished_chunk_count(s, run.id) == 1
        assert await runs.total_error_count(s, run.id) == 0


async def test_llm_calls_round_trip(db):
    _, _, run = await _run(db)
    records = [
        LLMCallRecord(
            role="translator:literal",
            backend="fake",
            model="m",
            prompt_id="literal",
            prompt_version=1,
            attempt=1,
            ok=True,
            tokens_in=10,
            tokens_out=5,
            request={"messages": []},
            response="{}",
        ),
        LLMCallRecord(
            role="reviewer", backend="fake", model="m", prompt_id="reconcile", prompt_version=1,
            attempt=2, ok=False, error="timeout",
        ),
    ]
    async with db.session() as s, s.begin():
        await runs.save_llm_calls(s, run.id, records, stage="chunk", chunk_id="c1")
        await runs.save_llm_calls(s, run.id, [], stage="profile")
    async with db.session() as s:
        assert await runs.load_llm_calls(s, run.id) == records


async def test_delete_project_cascades(db):
    project, _, run = await _run(db)
    async with db.session() as s, s.begin():
        await runs.delete_project(s, project.id)
    async with db.session() as s:
        assert await s.get(Run, run.id) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/store/test_runs.py -q --no-cov`

Expected: FAIL with `ImportError: cannot import name 'runs'`.

- [ ] **Step 3: Implement**

`engine/coglossa/store/runs.py`:

```python
"""Data access for projects, documents, bible versions, runs, chunks, stage results and model
calls. Functions take an AsyncSession and never commit: the caller owns the transaction
(`async with db.session() as s, s.begin(): ...`)."""

import uuid
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from sqlalchemy import Table, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from coglossa.schema import Bible, Block, BlockKind, Chunk, LLMCallRecord, Provenance, Segment
from coglossa.store.models import (
    BibleVersion,
    BlockRow,
    ChunkRow,
    Document,
    LLMCall,
    Project,
    Run,
    SegmentRow,
    StageResult,
    utcnow,
)

RUNNING, COMPLETE, FAILED = "running", "complete", "failed"
DRAFT, APPROVED = "draft", "approved"
ENGINE, USER = "engine", "user"
_BATCH = 1000


class NotFoundError(LookupError):
    """A requested row does not exist; the message is safe to show to a user."""


async def get_or_create_project(s: AsyncSession, name: str) -> Project:
    await s.execute(
        pg_insert(Project)
        .values(id=uuid.uuid4(), name=name, created_at=utcnow())
        .on_conflict_do_nothing(index_elements=[Project.name])
    )
    return (await s.execute(select(Project).where(Project.name == name))).scalar_one()


async def find_project(s: AsyncSession, name: str) -> Project:
    project = await s.scalar(select(Project).where(Project.name == name))
    if project is None:
        raise NotFoundError(f"project '{name}' not found")
    return project


async def add_document(
    s: AsyncSession,
    project_id: uuid.UUID,
    *,
    document_id: uuid.UUID,
    filename: str,
    format: str,
    sha256: str,
    storage_key: str,
) -> Document:
    doc = Document(
        id=document_id,
        project_id=project_id,
        filename=filename,
        format=format,
        sha256=sha256,
        storage_key=storage_key,
    )
    s.add(doc)
    await s.flush()
    return doc


async def get_document(s: AsyncSession, document_id: uuid.UUID) -> Document:
    doc = await s.get(Document, document_id)
    if doc is None:
        raise NotFoundError(f"document {document_id} not found")
    return doc


async def _insert_many(s: AsyncSession, table: Table, rows: Sequence[dict[str, Any]]) -> None:
    for i in range(0, len(rows), _BATCH):
        await s.execute(pg_insert(table).values(list(rows[i : i + _BATCH])).on_conflict_do_nothing())


async def save_source(
    s: AsyncSession, document_id: uuid.UUID, blocks: list[Block], segments: list[Segment]
) -> None:
    """Blocks and segments are immutable after ingest (spec §5): a second save is a no-op."""
    await _insert_many(
        s,
        BlockRow.__table__,
        [
            {
                "document_id": document_id,
                "ordinal": k,
                "block_id": b.id,
                "kind": b.kind.value,
                "text": b.text,
                "provenance": b.provenance.model_dump(mode="json"),
            }
            for k, b in enumerate(blocks)
        ],
    )
    await _insert_many(
        s,
        SegmentRow.__table__,
        [
            {
                "document_id": document_id,
                "ordinal": k,
                "segment_id": seg.id,
                "block_id": seg.block_id,
                "char_start": seg.start,
                "char_end": seg.end,
                "text": seg.text,
            }
            for k, seg in enumerate(segments)
        ],
    )
    await s.execute(
        update(Document)
        .where(Document.id == document_id, Document.ingested_at.is_(None))
        .values(ingested_at=utcnow())
    )


async def load_source(
    s: AsyncSession, document_id: uuid.UUID
) -> tuple[list[Block], list[Segment]]:
    block_rows = await s.scalars(
        select(BlockRow).where(BlockRow.document_id == document_id).order_by(BlockRow.ordinal)
    )
    segment_rows = await s.scalars(
        select(SegmentRow).where(SegmentRow.document_id == document_id).order_by(SegmentRow.ordinal)
    )
    blocks = [
        Block(
            id=r.block_id,
            kind=BlockKind(r.kind),
            text=r.text,
            provenance=Provenance.model_validate(r.provenance),
        )
        for r in block_rows
    ]
    segments = [
        Segment(id=r.segment_id, block_id=r.block_id, start=r.start, end=r.end, text=r.text)
        for r in segment_rows
    ]
    return blocks, segments


async def latest_bible(
    s: AsyncSession, project_id: uuid.UUID, *, approved_only: bool
) -> BibleVersion | None:
    query = select(BibleVersion).where(BibleVersion.project_id == project_id)
    if approved_only:
        query = query.where(BibleVersion.status == APPROVED)
    return await s.scalar(query.order_by(BibleVersion.version.desc()).limit(1))


async def get_bible_version(s: AsyncSession, version_id: uuid.UUID) -> BibleVersion:
    row = await s.get(BibleVersion, version_id)
    if row is None:
        raise NotFoundError(f"bible version {version_id} not found")
    return row


async def add_bible_version(
    s: AsyncSession,
    project_id: uuid.UUID,
    bible: Bible,
    *,
    status: Literal["draft", "approved"],
    author: Literal["engine", "user"],
    source_run_id: uuid.UUID | None = None,
) -> BibleVersion:
    await s.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    current = await s.scalar(
        select(func.max(BibleVersion.version)).where(BibleVersion.project_id == project_id)
    )
    row = BibleVersion(
        project_id=project_id,
        version=(current or 0) + 1,
        status=status,
        author=author,
        source_run_id=source_run_id,
        content=bible.model_dump(mode="json"),
    )
    s.add(row)
    await s.flush()
    return row


async def create_run(
    s: AsyncSession,
    document_id: uuid.UUID,
    *,
    target_lang: str,
    config_hash: str,
    config_snapshot: dict[str, Any],
    prompt_versions: dict[str, int],
    source_lang: str | None = None,
    pipeline_profile: str | None = None,
    checkpoint: bool | None = None,
    use_project_bible: bool = False,
) -> Run:
    run = Run(
        document_id=document_id,
        target_lang=target_lang,
        source_lang=source_lang,
        pipeline_profile=pipeline_profile,
        checkpoint=checkpoint,
        use_project_bible=use_project_bible,
        config_hash=config_hash,
        config_snapshot=config_snapshot,
        prompt_versions=prompt_versions,
    )
    s.add(run)
    await s.flush()
    return run


async def get_run(s: AsyncSession, run_id: uuid.UUID, *, lock: bool = False) -> Run:
    query = select(Run).where(Run.id == run_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    run = await s.scalar(query)
    if run is None:
        raise NotFoundError(f"run {run_id} not found")
    return run


async def save_chunks(s: AsyncSession, run_id: uuid.UUID, chunks: list[Chunk]) -> None:
    await s.execute(delete(ChunkRow).where(ChunkRow.run_id == run_id))
    await _insert_many(
        s,
        ChunkRow.__table__,
        [
            {"run_id": run_id, "ordinal": k, "chunk_id": c.id, "segment_ids": c.segment_ids}
            for k, c in enumerate(chunks)
        ],
    )


async def load_chunks(s: AsyncSession, run_id: uuid.UUID) -> list[Chunk]:
    rows = await s.scalars(
        select(ChunkRow).where(ChunkRow.run_id == run_id).order_by(ChunkRow.ordinal)
    )
    return [Chunk(id=r.chunk_id, segment_ids=list(r.segment_ids)) for r in rows]


async def get_stage(
    s: AsyncSession, run_id: uuid.UUID, stage: str, chunk_id: str = ""
) -> StageResult | None:
    return await s.scalar(
        select(StageResult)
        .where(
            StageResult.run_id == run_id,
            StageResult.stage == stage,
            StageResult.chunk_id == chunk_id,
        )
        .execution_options(populate_existing=True)
    )


async def begin_stage(
    s: AsyncSession, run_id: uuid.UUID, stage: str, chunk_id: str = ""
) -> StageResult:
    now = utcnow()
    statement = (
        pg_insert(StageResult)
        .values(
            run_id=run_id,
            stage=stage,
            chunk_id=chunk_id,
            status=RUNNING,
            attempt=1,
            error_count=0,
            started_at=now,
        )
        .on_conflict_do_update(
            index_elements=["run_id", "stage", "chunk_id"],
            set_={
                "status": RUNNING,
                "attempt": StageResult.attempt + 1,
                "output": None,
                "error": None,
                "error_count": 0,
                "started_at": now,
                "finished_at": None,
                "duration_ms": None,
            },
        )
        .returning(StageResult)
    )
    result = await s.execute(statement, execution_options={"populate_existing": True})
    return result.scalar_one()


def _finish(row: StageResult) -> None:
    now = utcnow()
    row.finished_at = now
    row.duration_ms = int((now - row.started_at).total_seconds() * 1000)


def count_errors(output: dict[str, Any] | None) -> int:
    if not output:
        return 0
    return len(output.get("errors") or []) + (1 if output.get("engine_error") else 0)


def finish_stage(row: StageResult, output: dict[str, Any] | None) -> None:
    row.status = COMPLETE
    row.output = output
    row.error = None
    row.error_count = count_errors(output)
    _finish(row)


def fail_stage(row: StageResult, error: str) -> None:
    row.status = FAILED
    row.error = error
    row.error_count = 1
    _finish(row)


async def delete_stages(
    s: AsyncSession,
    run_id: uuid.UUID,
    *,
    stages: Iterable[str] | None = None,
    chunk_ids: Iterable[str] | None = None,
    status: str | None = None,
) -> None:
    statement = delete(StageResult).where(StageResult.run_id == run_id)
    if stages is not None:
        statement = statement.where(StageResult.stage.in_(list(stages)))
    if chunk_ids is not None:
        statement = statement.where(StageResult.chunk_id.in_(list(chunk_ids)))
    if status is not None:
        statement = statement.where(StageResult.status == status)
    await s.execute(statement)


async def stage_rows(s: AsyncSession, run_id: uuid.UUID) -> list[StageResult]:
    rows = await s.scalars(
        select(StageResult).where(StageResult.run_id == run_id).order_by(StageResult.id)
    )
    return list(rows)


async def chunk_progress(s: AsyncSession, run_id: uuid.UUID) -> dict[str, int]:
    rows = await s.execute(
        select(StageResult.stage, func.count())
        .where(
            StageResult.run_id == run_id,
            StageResult.chunk_id != "",
            StageResult.status == COMPLETE,
        )
        .group_by(StageResult.stage)
    )
    return {stage: count for stage, count in rows.all()}


async def finished_chunk_count(s: AsyncSession, run_id: uuid.UUID) -> int:
    count = await s.scalar(
        select(func.count()).where(
            StageResult.run_id == run_id,
            StageResult.stage == "score",
            StageResult.status.in_([COMPLETE, FAILED]),
        )
    )
    return count or 0


async def chunks_with_errors(s: AsyncSession, run_id: uuid.UUID) -> list[str]:
    rows = await s.scalars(
        select(StageResult.chunk_id).where(
            StageResult.run_id == run_id,
            StageResult.stage == "score",
            (StageResult.status == FAILED) | (StageResult.error_count > 0),
        )
    )
    return list(rows)


async def total_error_count(s: AsyncSession, run_id: uuid.UUID) -> int:
    total = await s.scalar(
        select(func.coalesce(func.sum(StageResult.error_count), 0)).where(
            StageResult.run_id == run_id
        )
    )
    return int(total or 0)


async def save_llm_calls(
    s: AsyncSession,
    run_id: uuid.UUID,
    records: list[LLMCallRecord],
    *,
    stage: str,
    chunk_id: str | None = None,
) -> None:
    for r in records:
        s.add(LLMCall(run_id=run_id, stage=stage, chunk_id=chunk_id, **r.model_dump()))
    await s.flush()


async def load_llm_calls(s: AsyncSession, run_id: uuid.UUID) -> list[LLMCallRecord]:
    rows = await s.scalars(select(LLMCall).where(LLMCall.run_id == run_id).order_by(LLMCall.id))
    return [
        LLMCallRecord(
            role=r.role,
            backend=r.backend,
            model=r.model,
            prompt_id=r.prompt_id,
            prompt_version=r.prompt_version,
            attempt=r.attempt,
            ok=r.ok,
            latency_ms=r.latency_ms,
            tokens_in=r.tokens_in,
            tokens_out=r.tokens_out,
            error=r.error,
            request=r.request,
            response=r.response,
        )
        for r in rows
    ]


async def delete_project(s: AsyncSession, project_id: uuid.UUID) -> None:
    await s.execute(delete(Project).where(Project.id == project_id))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make test-engine`

Expected: PASS; coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add engine
git commit -m "feat(store): data access for projects, documents, bible versions, runs, stages and model calls"
```

---

### Task 8: Data access for chunk outputs, spans, sweep changes and candidates

**Files:**
- Create: `engine/coglossa/store/outputs.py`
- Test: `engine/tests/store/test_outputs.py`

**Interfaces:**
- Consumes:
  - `ChunkOutcome` (Task 2)
  - `SpanChange` (Task 3)
  - the models (Task 6)
  - `runs.create_run` etc. (Task 7)
- Produces (in `coglossa.store.outputs`):
  - `async save_chunk_outcome(s, run_id, chunk_ordinal: int, outcome: ChunkOutcome) -> None`: writes candidates, spans, scores and flags, plus an `engine` revision holding the reconciled text
  - `async delete_chunk_outputs(s, run_id, chunk_ids: list[str]) -> None`
  - `StoredSpans(spans: list[Span], row_ids: dict[str, uuid.UUID], proposed_terms: list[GlossaryEntry])` (dataclass):
    - `spans` are in reading order with ids `p1…pN`
    - `text` is the latest revision
    - flags carry their stored status
  - `async load_spans(s, run_id) -> StoredSpans`
  - `async apply_sweep(s, row_ids, changes: list[SpanChange]) -> None`: adds a `sweep` revision and sets matching open flags to `resolved`
  - `async undo_sweep(s, run_id) -> None`: deletes `sweep` revisions and reopens `resolved` flags
  - `async load_candidates(s, run_id) -> list[Candidate]`, in chunk order
  - `async load_revisions(s, run_id, ids_by_row: dict[uuid.UUID, str]) -> list[Revision]`: everything except `engine` revisions, oldest first

- [ ] **Step 1: Write the failing tests**

`engine/tests/store/test_outputs.py`:

```python
import uuid

from coglossa.schema import (
    Agreement,
    Candidate,
    ErrorCategory,
    ErrorSeverity,
    Flag,
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    JudgeError,
    Reliability,
    RevisionSource,
    RunError,
    Span,
    SpanScore,
)
from coglossa.stages.chunk import ChunkOutcome
from coglossa.stages.sweep import SpanChange
from coglossa.store import outputs, runs


async def _run_id(db):
    async with db.session() as s, s.begin():
        project = await runs.get_or_create_project(s, "p")
        doc = await runs.add_document(
            s, project.id, document_id=uuid.uuid4(), filename="a.txt", format="txt",
            sha256="x", storage_key="k",
        )
        run = await runs.create_run(
            s, doc.id, target_lang="en", config_hash="h", config_snapshot={}, prompt_versions={}
        )
    return run.id


def _span(chunk, k, text, *, flags=(), judged=True):
    return Span(
        id=f"{chunk}.{k}",
        chunk_id=chunk,
        start=f"s{k}",
        end=f"s{k}",
        text=text,
        agreement=Agreement.MINOR,
        chosen_from="contextual",
        rationale="why",
        issues=["i"],
        score=SpanScore(
            signals={"qe": 0.9, "xsim": None}, meaning=81.5, style=None, reliability=Reliability.B
        ),
        flags=list(flags),
        judge_errors=(
            [
                JudgeError(
                    category=ErrorCategory.FLUENCY,
                    severity=ErrorSeverity.MINOR,
                    excerpt="x",
                    explanation="e",
                )
            ]
            if judged
            else None
        ),
        back_translation="bt" if judged else None,
    )


def _outcome(chunk, texts, **kw):
    spans = [_span(chunk, k + 1, t, **kw) for k, t in enumerate(texts)]
    return ChunkOutcome(
        chunk_id=chunk,
        candidates=[
            Candidate(chunk_id=chunk, translator="literal", model="m", start="s1", end="s1", text="c")
        ],
        spans=spans,
        proposed_terms=[[GlossaryEntry(term=f"{chunk}-t", rendering="r")]] + [[]] * (len(spans) - 1),
        errors=[RunError(stage="judge", chunk_id=chunk, message="m")],
    )


async def test_outcome_round_trip_in_reading_order(db):
    run_id = await _run_id(db)
    flag = Flag(code=FlagCode.TERMINOLOGY_VIOLATION, explanation="x", evidence={"a": [1]})
    async with db.session() as s, s.begin():
        # chunk 2 finishes before chunk 1
        await outputs.save_chunk_outcome(s, run_id, 1, _outcome("c2", ["third"]))
        await outputs.save_chunk_outcome(
            s, run_id, 0, _outcome("c1", ["first", "second"], flags=[flag])
        )
    async with db.session() as s:
        stored = await outputs.load_spans(s, run_id)
        candidates = await outputs.load_candidates(s, run_id)
    assert [(sp.id, sp.chunk_id, sp.text) for sp in stored.spans] == [
        ("p1", "c1", "first"),
        ("p2", "c1", "second"),
        ("p3", "c2", "third"),
    ]
    first = stored.spans[0]
    expected = _span("c1", 1, "first", flags=[flag]).model_copy(update={"id": "p1"})
    assert first == expected
    assert set(stored.row_ids) == {"p1", "p2", "p3"}
    assert [t.term for t in stored.proposed_terms] == ["c1-t", "c2-t"]
    assert [c.chunk_id for c in candidates] == ["c1", "c2"]


async def test_unjudged_spans_keep_nulls(db):
    run_id = await _run_id(db)
    async with db.session() as s, s.begin():
        await outputs.save_chunk_outcome(s, run_id, 0, _outcome("c1", ["x"], judged=False))
    async with db.session() as s:
        (span,) = (await outputs.load_spans(s, run_id)).spans
    assert span.judge_errors is None and span.back_translation is None


async def test_sweep_apply_and_undo(db):
    run_id = await _run_id(db)
    flag = Flag(code=FlagCode.TERMINOLOGY_VIOLATION, explanation="x")
    other = Flag(code=FlagCode.LOW_MEANING, explanation="y")
    async with db.session() as s, s.begin():
        await outputs.save_chunk_outcome(s, run_id, 0, _outcome("c1", ["old"], flags=[flag, other]))
    async with db.session() as s, s.begin():
        stored = await outputs.load_spans(s, run_id)
        change = SpanChange("p1", "old", "new", "Applied locked bible entries: 'a' → 'b'.", (FlagCode.TERMINOLOGY_VIOLATION,))
        await outputs.apply_sweep(s, stored.row_ids, [change])
    async with db.session() as s:
        stored = await outputs.load_spans(s, run_id)
        (span,) = stored.spans
        assert span.text == "new"
        assert [f.status for f in span.flags] == [FlagStatus.RESOLVED, FlagStatus.OPEN]
        ids = {v: k for k, v in stored.row_ids.items()}
        (rev,) = await outputs.load_revisions(s, run_id, ids)
        assert (rev.span_id, rev.source, rev.before, rev.after) == ("p1", RevisionSource.SWEEP, "old", "new")
    async with db.session() as s, s.begin():
        await outputs.undo_sweep(s, run_id)
    async with db.session() as s:
        (span,) = (await outputs.load_spans(s, run_id)).spans
        assert span.text == "old"
        assert [f.status for f in span.flags] == [FlagStatus.OPEN, FlagStatus.OPEN]
        assert await outputs.load_revisions(s, run_id, {}) == []


async def test_delete_chunk_outputs(db):
    run_id = await _run_id(db)
    async with db.session() as s, s.begin():
        await outputs.save_chunk_outcome(s, run_id, 0, _outcome("c1", ["a"]))
        await outputs.save_chunk_outcome(s, run_id, 1, _outcome("c2", ["b"]))
        await outputs.delete_chunk_outputs(s, run_id, ["c1"])
    async with db.session() as s:
        assert [sp.chunk_id for sp in (await outputs.load_spans(s, run_id)).spans] == ["c2"]
        assert [c.chunk_id for c in await outputs.load_candidates(s, run_id)] == ["c2"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/store/test_outputs.py -q --no-cov`

Expected: FAIL with `ImportError: cannot import name 'outputs'`.

- [ ] **Step 3: Implement**

`engine/coglossa/store/outputs.py`:

```python
"""Data access for what chunks produce (spec §5: candidate, span, span_score, flag, revision)
and for the sweep's changes. A span's current text is its latest revision."""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from coglossa.schema import (
    Agreement,
    Candidate,
    Flag,
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    JudgeError,
    Reliability,
    Revision,
    RevisionSource,
    Span,
    SpanScore,
)
from coglossa.stages.chunk import ChunkOutcome
from coglossa.stages.sweep import SpanChange
from coglossa.store.models import CandidateRow, FlagRow, RevisionRow, SpanRow, SpanScoreRow


async def save_chunk_outcome(
    s: AsyncSession, run_id: uuid.UUID, chunk_ordinal: int, outcome: ChunkOutcome
) -> None:
    for c in outcome.candidates:
        s.add(
            CandidateRow(
                run_id=run_id,
                chunk_id=c.chunk_id,
                chunk_ordinal=chunk_ordinal,
                translator=c.translator,
                model=c.model,
                seg_start=c.start,
                seg_end=c.end,
                text=c.text,
                notes=c.notes,
            )
        )
    for k, (span, terms) in enumerate(zip(outcome.spans, outcome.proposed_terms, strict=True)):
        row = SpanRow(
            id=uuid.uuid4(),
            run_id=run_id,
            chunk_id=outcome.chunk_id,
            chunk_ordinal=chunk_ordinal,
            ordinal=k,
            seg_start=span.start,
            seg_end=span.end,
            agreement=span.agreement.value,
            chosen_from=span.chosen_from,
            rationale=span.rationale,
            issues=list(span.issues),
            judge_errors=(
                [e.model_dump(mode="json") for e in span.judge_errors]
                if span.judge_errors is not None
                else None
            ),
            back_translation=span.back_translation,
            proposed_terms=[t.model_dump(mode="json") for t in terms],
        )
        s.add(row)
        s.add(
            SpanScoreRow(
                span_id=row.id,
                signals=dict(span.score.signals),
                meaning=span.score.meaning,
                style=span.score.style,
                reliability=span.score.reliability.value,
            )
        )
        for f in span.flags:
            s.add(
                FlagRow(
                    span_id=row.id,
                    code=f.code.value,
                    explanation=f.explanation,
                    evidence=f.model_dump(mode="json")["evidence"],
                    status=f.status.value,
                    dismiss_reason=f.dismiss_reason,
                )
            )
        s.add(
            RevisionRow(
                span_id=row.id,
                source=RevisionSource.ENGINE.value,
                before=None,
                after=span.text,
                reason="reconciled translation",
            )
        )
    await s.flush()


async def delete_chunk_outputs(s: AsyncSession, run_id: uuid.UUID, chunk_ids: list[str]) -> None:
    await s.execute(
        delete(CandidateRow).where(CandidateRow.run_id == run_id, CandidateRow.chunk_id.in_(chunk_ids))
    )
    await s.execute(
        delete(SpanRow).where(SpanRow.run_id == run_id, SpanRow.chunk_id.in_(chunk_ids))
    )


@dataclass
class StoredSpans:
    spans: list[Span]  # reading order, ids p1..pN, text = latest revision
    row_ids: dict[str, uuid.UUID]  # report span id -> span row id
    proposed_terms: list[GlossaryEntry]  # reading order


def _run_spans(run_id: uuid.UUID):
    return select(SpanRow.id).where(SpanRow.run_id == run_id)


async def load_spans(s: AsyncSession, run_id: uuid.UUID) -> StoredSpans:
    rows = list(
        await s.scalars(
            select(SpanRow)
            .where(SpanRow.run_id == run_id)
            .order_by(SpanRow.chunk_ordinal, SpanRow.ordinal)
        )
    )
    scores = {
        r.span_id: r
        for r in await s.scalars(
            select(SpanScoreRow).where(SpanScoreRow.span_id.in_(_run_spans(run_id)))
        )
    }
    flags: dict[uuid.UUID, list[FlagRow]] = defaultdict(list)
    for f in await s.scalars(
        select(FlagRow).where(FlagRow.span_id.in_(_run_spans(run_id))).order_by(FlagRow.id)
    ):
        flags[f.span_id].append(f)
    latest = dict(
        (
            await s.execute(
                select(RevisionRow.span_id, RevisionRow.after)
                .where(RevisionRow.span_id.in_(_run_spans(run_id)))
                .distinct(RevisionRow.span_id)
                .order_by(RevisionRow.span_id, RevisionRow.id.desc())
            )
        ).all()
    )
    spans: list[Span] = []
    row_ids: dict[str, uuid.UUID] = {}
    proposed: list[GlossaryEntry] = []
    for n, row in enumerate(rows, start=1):
        score = scores[row.id]
        sid = f"p{n}"
        row_ids[sid] = row.id
        spans.append(
            Span(
                id=sid,
                chunk_id=row.chunk_id,
                start=row.seg_start,
                end=row.seg_end,
                text=latest[row.id],
                agreement=Agreement(row.agreement),
                chosen_from=row.chosen_from,
                rationale=row.rationale,
                issues=list(row.issues),
                score=SpanScore(
                    signals=dict(score.signals),
                    meaning=score.meaning,
                    style=score.style,
                    reliability=Reliability(score.reliability),
                ),
                flags=[
                    Flag(
                        code=FlagCode(f.code),
                        explanation=f.explanation,
                        evidence=dict(f.evidence),
                        status=FlagStatus(f.status),
                        dismiss_reason=f.dismiss_reason,
                    )
                    for f in flags[row.id]
                ],
                judge_errors=(
                    [JudgeError.model_validate(e) for e in row.judge_errors]
                    if row.judge_errors is not None
                    else None
                ),
                back_translation=row.back_translation,
            )
        )
        proposed.extend(GlossaryEntry.model_validate(t) for t in row.proposed_terms)
    return StoredSpans(spans=spans, row_ids=row_ids, proposed_terms=proposed)


async def apply_sweep(
    s: AsyncSession, row_ids: dict[str, uuid.UUID], changes: list[SpanChange]
) -> None:
    for c in changes:
        span_row = row_ids[c.span_id]
        s.add(
            RevisionRow(
                span_id=span_row,
                source=RevisionSource.SWEEP.value,
                before=c.before,
                after=c.after,
                reason=c.reason,
            )
        )
        await s.execute(
            update(FlagRow)
            .where(
                FlagRow.span_id == span_row,
                FlagRow.code.in_([code.value for code in c.resolved]),
                FlagRow.status == FlagStatus.OPEN.value,
            )
            .values(status=FlagStatus.RESOLVED.value)
        )
    await s.flush()


async def undo_sweep(s: AsyncSession, run_id: uuid.UUID) -> None:
    await s.execute(
        delete(RevisionRow).where(
            RevisionRow.span_id.in_(_run_spans(run_id)),
            RevisionRow.source == RevisionSource.SWEEP.value,
        )
    )
    await s.execute(
        update(FlagRow)
        .where(
            FlagRow.span_id.in_(_run_spans(run_id)),
            FlagRow.status == FlagStatus.RESOLVED.value,
        )
        .values(status=FlagStatus.OPEN.value)
    )


async def load_candidates(s: AsyncSession, run_id: uuid.UUID) -> list[Candidate]:
    rows = await s.scalars(
        select(CandidateRow)
        .where(CandidateRow.run_id == run_id)
        .order_by(CandidateRow.chunk_ordinal, CandidateRow.id)
    )
    return [
        Candidate(
            chunk_id=r.chunk_id,
            translator=r.translator,
            model=r.model,
            start=r.seg_start,
            end=r.seg_end,
            text=r.text,
            notes=r.notes,
        )
        for r in rows
    ]


async def load_revisions(
    s: AsyncSession, run_id: uuid.UUID, ids_by_row: dict[uuid.UUID, str]
) -> list[Revision]:
    rows = await s.scalars(
        select(RevisionRow)
        .where(
            RevisionRow.span_id.in_(_run_spans(run_id)),
            RevisionRow.source != RevisionSource.ENGINE.value,
        )
        .order_by(RevisionRow.id)
    )
    return [
        Revision(
            span_id=ids_by_row[r.span_id],
            source=RevisionSource(r.source),
            before=r.before,
            after=r.after,
            reason=r.reason,
        )
        for r in rows
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make test-engine`

Expected: PASS; coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add engine
git commit -m "feat(store): persist chunk outcomes, load spans at their latest revision, apply and undo sweep changes"
```

---
### Task 9: Control plane: submit, approve, cancel, retry, progress, report, delete

**Files:**
- Create:
  - `engine/coglossa/service.py`
  - `engine/coglossa/testing/queue.py`
- Modify:
  - `engine/coglossa/schema/run.py` (`RunProgress`)
  - `engine/coglossa/schema/__init__.py`
  - `engine/coglossa/schema/export.py` (export `run-progress`)
  - `engine/coglossa/ingest/registry.py` (`check_supported`)
- Regenerate: `schema/run-progress.schema.json`
- Test: `engine/tests/test_service.py`

**Interfaces:**
- Consumes:
  - `coglossa.store.runs`, `coglossa.store.outputs` (Tasks 7–8)
  - `Storage`, `safe_filename` (Task 5)
  - `run_prompt_ids`, `resolve_proposals` (Task 3)
  - `any_scored`, `document_notices`, `config_hash`
- Produces (in `coglossa.service`):
  - **Task names:** `TASK_START_RUN = "coglossa.start_run"`, `TASK_TRANSLATE_RUN = "coglossa.translate_run"`, `TASK_PROCESS_CHUNK = "coglossa.process_chunk"`, `TASK_FINISH_RUN = "coglossa.finish_run"`.
  - **`JobQueue`** (Protocol): `async defer(task: str, *, lock: str | None = None, **kwargs: str) -> None`.
  - **`InvalidRunStateError(Exception)`**, with a user-facing message.
  - **`SubmitOptions`** (frozen dataclass): `project`, `target_lang`, `source_lang=None`, `pipeline_profile: PipelineProfile | None = None`, `checkpoint: bool | None = None`, `use_project_bible=False`.
  - **Run actions:**
    - `async submit_run(db, storage, queue, cfg, prompts, path: Path, opts) -> uuid.UUID`
    - `async run_bible(db, run_id) -> Bible`: the run's current bible version
    - `async approve_bible(db, queue, run_id, bible: Bible | None = None) -> int`: returns the new version number
    - `async cancel_run(db, run_id) -> RunState`
    - `async retry_run(db, queue, run_id) -> str`: a message saying what was re-queued
    - `async run_progress(db, run_id) -> RunProgress`
    - `async build_report(db, run_id) -> Report`
    - `async delete_project(db, storage, name: str) -> None`
  - **`RunProgress`** (schema): `run_id`, `state`, `chunks_total`, `chunks_done: dict[str, int]` (translate/reconcile/score), `chunks_with_errors`, `error`.
  - **`RecordingQueue`** (`coglossa.testing.queue`):
    - `.jobs: list[QueuedJob]` holds queued jobs, with queueing locks honoured as in procrastinate: a lock already queued is not queued twice.
    - `.attempts: list[QueuedJob]` records every `defer` call.
    - `.pop() -> QueuedJob | None`.
    - `QueuedJob(task, lock, kwargs)`.
  - **`check_supported(path) -> None`** (ingest registry): raises `IngestError` for a missing file and `UnsupportedFormatError` for an unknown extension, with `ingest_file`'s messages. `ingest_file` calls it.

**Retry semantics.** Spec §7.3 says the user can retry failed chunks. `retry_run` does that and doubles as "resume" for an operator after a lost job:

| State | Action |
|---|---|
| `complete` | `InvalidRunStateError("… completed without errors; nothing to retry")` |
| `cancelled` | `InvalidRunStateError("… was cancelled; submit a new run")` |
| `awaiting_bible_approval` | `InvalidRunStateError("… is waiting for bible approval; approve the bible to continue")` |
| `failed` | Delete failed stage rows. State becomes `created`, `error`/`finished_at` are cleared, and `start_run` is queued. Message: `"restarting from the failed stage"` |
| `complete_with_errors` | Chunks with errors (`runs.chunks_with_errors`) lose their outputs and stage rows. The sweep is undone and its stage row deleted. State becomes `translating` and `translate_run` is queued. Message: `"retrying N chunk(s)"`, or `"re-running the sweep"` when no chunk had errors |
| `created`/`ingesting`/`profiling`/`building_bible` | Queue `start_run` (`"resumed"`) |
| `translating` | Queue `translate_run` (`"resumed"`) |
| `sweeping` | Queue `finish_run` (`"resumed"`) |

Every job is idempotent, so resuming a run that is in fact progressing is harmless.

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_service.py`:

```python
import uuid

import pytest

from coglossa import service
from coglossa.ingest.base import IngestError, UnsupportedFormatError
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import (
    Agreement,
    Bible,
    Block,
    BlockKind,
    Chunk,
    DocumentProfile,
    FlagCode,
    Genre,
    GlossaryEntry,
    LLMCallRecord,
    PipelineProfile,
    Provenance,
    Reliability,
    RunError,
    RunState,
    RunStatus,
    Segment,
    Span,
    SpanScore,
)
from coglossa.stages.chunk import ChunkOutcome
from coglossa.storage import LocalStorage
from coglossa.store import outputs, runs
from coglossa.testing.queue import RecordingQueue
from tests.conftest import build_config

PROFILE = DocumentProfile(
    source_lang="fr",
    target_lang="en",
    mixed_language=False,
    genre=Genre.OTHER,
    tone_register="neutral",
    word_count=3,
    pipeline_profile=PipelineProfile.SHORT,
)


@pytest.fixture
def env(db, tmp_path):
    cfg = build_config()
    return db, LocalStorage(tmp_path / "files"), RecordingQueue(), cfg, PromptLibrary(None)


async def _submit(env, tmp_path, name="story.txt", text="Il pleut.", **opts):
    db, storage, queue, cfg, prompts = env
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    options = service.SubmitOptions(project="novel", target_lang="en", **opts)
    return await service.submit_run(db, storage, queue, cfg, prompts, path, options)


async def _set_state(db, run_id, state, **fields):
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        run.state = state
        for k, v in fields.items():
            setattr(run, k, v)


async def test_submit_stores_the_file_and_queues_the_run(env, tmp_path):
    db, storage, queue, cfg, prompts = env
    run_id = await _submit(env, tmp_path, source_lang="fr", checkpoint=True)
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        doc = await runs.get_document(s, run.document_id)
    assert run.state == RunState.CREATED
    assert (run.target_lang, run.source_lang, run.checkpoint) == ("en", "fr", True)
    assert run.config_snapshot == cfg.model_dump(mode="json")
    assert run.prompt_versions["sweep"] == 1
    assert doc.filename == "story.txt" and doc.format == "txt"
    assert storage.get(doc.storage_key) == "Il pleut.".encode()
    assert doc.storage_key.endswith("/story.txt")
    assert [(j.task, j.lock, j.kwargs) for j in queue.jobs] == [
        (service.TASK_START_RUN, f"{run_id}:start", {"run_id": str(run_id)})
    ]


async def test_submit_rejects_bad_input_before_storing_anything(env, tmp_path):
    db, storage, queue, cfg, prompts = env
    with pytest.raises(UnsupportedFormatError):
        await _submit(env, tmp_path, name="image.png")
    with pytest.raises(IngestError, match="not found"):
        await service.submit_run(
            db, storage, queue, cfg, prompts, tmp_path / "nope.txt",
            service.SubmitOptions(project="p", target_lang="en"),
        )
    assert queue.jobs == [] and not (tmp_path / "files").exists()


async def _awaiting(env, tmp_path):
    db = env[0]
    run_id = await _submit(env, tmp_path)
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        doc = await runs.get_document(s, run.document_id)
        draft = await runs.add_bible_version(
            s, doc.project_id, Bible(summary="s", style_guide="g"),
            status=runs.DRAFT, author=runs.ENGINE, source_run_id=run_id,
        )
        run.bible_version_id = draft.id
        run.state = RunState.AWAITING_BIBLE_APPROVAL
    env[2].jobs.clear()
    return run_id


async def test_approve_the_generated_bible(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _awaiting(env, tmp_path)
    assert await service.run_bible(db, run_id) == Bible(summary="s", style_guide="g")
    assert await service.approve_bible(db, queue, run_id) == 2
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        version = await runs.get_bible_version(s, run.bible_version_id)
    assert run.state == RunState.TRANSLATING
    assert (version.version, version.status, version.author) == (2, "approved", "engine")
    assert [j.task for j in queue.jobs] == [service.TASK_TRANSLATE_RUN]


async def test_approve_an_edited_bible(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _awaiting(env, tmp_path)
    edited = Bible(
        summary="s", style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    await service.approve_bible(db, queue, run_id, edited)
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        version = await runs.get_bible_version(s, run.bible_version_id)
    assert version.author == "user" and Bible.model_validate(version.content) == edited
    with pytest.raises(service.InvalidRunStateError, match="awaiting bible approval"):
        await service.approve_bible(db, queue, run_id)


async def test_run_bible_before_one_exists(env, tmp_path):
    run_id = await _submit(env, tmp_path)
    with pytest.raises(service.InvalidRunStateError, match="not ready"):
        await service.run_bible(env[0], run_id)


async def test_cancel(env, tmp_path):
    db = env[0]
    created = await _submit(env, tmp_path)
    assert await service.cancel_run(db, created) is RunState.CANCELLED
    running = await _submit(env, tmp_path)
    await _set_state(db, running, RunState.TRANSLATING)
    assert await service.cancel_run(db, running) is RunState.TRANSLATING
    async with db.session() as s:
        assert (await runs.get_run(s, running)).cancel_requested is True
    with pytest.raises(service.InvalidRunStateError, match="already cancelled"):
        await service.cancel_run(db, created)
    with pytest.raises(runs.NotFoundError):
        await service.cancel_run(db, uuid.uuid4())


@pytest.mark.parametrize(
    ("state", "match"),
    [
        (RunState.COMPLETE, "nothing to retry"),
        (RunState.CANCELLED, "submit a new run"),
        (RunState.AWAITING_BIBLE_APPROVAL, "approve the bible"),
    ],
)
async def test_retry_refusals(env, tmp_path, state, match):
    db, _, queue, _, _ = env
    run_id = await _submit(env, tmp_path)
    await _set_state(db, run_id, state)
    with pytest.raises(service.InvalidRunStateError, match=match):
        await service.retry_run(db, queue, run_id)


@pytest.mark.parametrize(
    ("state", "task"),
    [
        (RunState.CREATED, service.TASK_START_RUN),
        (RunState.PROFILING, service.TASK_START_RUN),
        (RunState.TRANSLATING, service.TASK_TRANSLATE_RUN),
        (RunState.SWEEPING, service.TASK_FINISH_RUN),
    ],
)
async def test_retry_resumes_an_active_run(env, tmp_path, state, task):
    db, _, queue, _, _ = env
    run_id = await _submit(env, tmp_path)
    queue.jobs.clear()
    await _set_state(db, run_id, state)
    assert await service.retry_run(db, queue, run_id) == "resumed"
    assert [j.task for j in queue.jobs] == [task]


async def test_retry_a_failed_run_restarts_the_failed_stage(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _submit(env, tmp_path)
    queue.jobs.clear()
    async with db.session() as s, s.begin():
        runs.finish_stage(await runs.begin_stage(s, run_id, "ingest"), {"errors": []})
        runs.fail_stage(await runs.begin_stage(s, run_id, "profile"), "profiling failed: x")
    await _set_state(db, run_id, RunState.FAILED, error="profiling failed: x")
    assert await service.retry_run(db, queue, run_id) == "restarting from the failed stage"
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        assert (run.state, run.error) == (RunState.CREATED, None)
        assert await runs.get_stage(s, run_id, "profile") is None
        assert (await runs.get_stage(s, run_id, "ingest")).status == runs.COMPLETE
    assert [j.task for j in queue.jobs] == [service.TASK_START_RUN]


async def _complete_run(env, tmp_path, *, chunk_errors=True):
    """A finished two-chunk run written straight to the store."""
    db = env[0]
    run_id = await _submit(env, tmp_path, text="Il pleut. Fin.")
    blocks = [
        Block(id="b1", kind=BlockKind.PARAGRAPH, text="Il pleut. Fin.",
              provenance=Provenance(source_path="story.txt", index=0))
    ]
    segments = [
        Segment(id="s1", block_id="b1", start=0, end=9, text="Il pleut."),
        Segment(id="s2", block_id="b1", start=10, end=14, text="Fin."),
    ]
    bible = Bible(summary="s", style_guide="g")

    def outcome(cid, sid, text, errors):
        span = Span(
            id=f"{cid}.1", chunk_id=cid, start=sid, end=sid, text=text,
            agreement=Agreement.AGREE, chosen_from="literal",
            score=SpanScore(signals={"qe": 0.9}, meaning=90.0, style=None,
                            reliability=Reliability.A),
        )
        return ChunkOutcome(
            chunk_id=cid, spans=[span],
            proposed_terms=[[GlossaryEntry(term="pluie", rendering="rain")]],
            errors=errors,
        )

    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        doc = await runs.get_document(s, run.document_id)
        await runs.save_source(s, doc.id, blocks, segments)
        await runs.save_chunks(s, run_id, [Chunk(id="c1", segment_ids=["s1"]),
                                           Chunk(id="c2", segment_ids=["s2"])])
        version = await runs.add_bible_version(s, doc.project_id, bible, status=runs.DRAFT,
                                               author=runs.ENGINE, source_run_id=run_id)
        run.bible_version_id = version.id
        run.profile = PROFILE.model_dump(mode="json")
        run.language_tier = "A"
        bad = [RunError(stage="judge", chunk_id="c2", message="judge timed out")]
        for k, (cid, sid, text, errors) in enumerate(
            [("c1", "s1", "It rains.", []), ("c2", "s2", "End.", bad if chunk_errors else [])]
        ):
            out = outcome(cid, sid, text, errors)
            await outputs.save_chunk_outcome(s, run_id, k, out)
            runs.finish_stage(await runs.begin_stage(s, run_id, "score", cid),
                              out.model_dump(mode="json"))
        runs.finish_stage(await runs.begin_stage(s, run_id, "sweep"), {"errors": [], "revised": 0})
        await runs.save_llm_calls(
            s, run_id,
            [LLMCallRecord(role="reviewer", backend="fake", model="m", prompt_id="reconcile",
                           prompt_version=1, attempt=1, ok=True, tokens_in=3, tokens_out=4)],
            stage="chunk", chunk_id="c1",
        )
        run.state = RunState.COMPLETE_WITH_ERRORS if chunk_errors else RunState.COMPLETE
    return run_id


async def test_progress(env, tmp_path):
    run_id = await _complete_run(env, tmp_path)
    progress = await service.run_progress(env[0], run_id)
    assert progress.state is RunState.COMPLETE_WITH_ERRORS
    assert progress.chunks_total == 2
    assert progress.chunks_done == {"translate": 0, "reconcile": 0, "score": 2}
    assert progress.chunks_with_errors == 1 and progress.error is None


async def test_build_report(env, tmp_path):
    db = env[0]
    run_id = await _complete_run(env, tmp_path)
    async with db.session() as s:
        submitted_hash = (await runs.get_run(s, run_id)).config_hash
    report = await service.build_report(db, run_id)
    assert report.run_id == str(run_id)
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    assert report.document.source_path == "story.txt"
    assert [(s.id, s.text) for s in report.spans] == [("p1", "It rains."), ("p2", "End.")]
    assert [e.message for e in report.errors] == ["judge timed out"]
    assert [g.term for g in report.bible_proposals] == ["pluie"]
    assert report.usage.tokens_in == 3 and report.usage.calls == 1
    assert report.config.hash == submitted_hash  # the snapshot taken at submission
    assert report.config.prompt_versions["sweep"] == 1
    assert report.config.roles["reviewer"] == "model-a"
    assert report.language_tier is Reliability.A
    assert report.notices == []
    assert len(report.segments) == 2 and [c.id for c in report.chunks] == ["c1", "c2"]


async def test_report_needs_a_finished_run(env, tmp_path):
    run_id = await _submit(env, tmp_path)
    with pytest.raises(service.InvalidRunStateError, match="once it is complete"):
        await service.build_report(env[0], run_id)


async def test_retry_failed_chunks(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _complete_run(env, tmp_path)
    queue.jobs.clear()
    assert await service.retry_run(db, queue, run_id) == "retrying 1 chunk(s)"
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        assert run.state == RunState.TRANSLATING
        assert await runs.get_stage(s, run_id, "score", "c2") is None
        assert await runs.get_stage(s, run_id, "sweep") is None
        assert (await runs.get_stage(s, run_id, "score", "c1")).status == runs.COMPLETE
        assert [sp.chunk_id for sp in (await outputs.load_spans(s, run_id)).spans] == ["c1"]
    assert [j.task for j in queue.jobs] == [service.TASK_TRANSLATE_RUN]


async def test_retry_with_only_run_level_errors_reruns_the_sweep(env, tmp_path):
    db, _, queue, _, _ = env
    run_id = await _complete_run(env, tmp_path, chunk_errors=False)
    await _set_state(db, run_id, RunState.COMPLETE_WITH_ERRORS)
    assert await service.retry_run(db, queue, run_id) == "re-running the sweep"


async def test_delete_project_removes_rows_and_files(env, tmp_path):
    db, storage, _, _, _ = env
    run_id = await _submit(env, tmp_path)
    await service.delete_project(db, storage, "novel")
    async with db.session() as s:
        with pytest.raises(runs.NotFoundError):
            await runs.get_run(s, run_id)
    assert not any((tmp_path / "files" / "projects").iterdir())
    with pytest.raises(runs.NotFoundError, match="project 'novel' not found"):
        await service.delete_project(db, storage, "novel")


def test_recording_queue_honours_locks():
    import asyncio

    queue = RecordingQueue()

    async def go():
        await queue.defer("t", lock="a", run_id="1")
        await queue.defer("t", lock="a", run_id="1")
        await queue.defer("t", run_id="2")
        await queue.defer("t", run_id="2")

    asyncio.run(go())
    assert len(queue.jobs) == 3 and len(queue.attempts) == 4
    assert queue.pop().kwargs == {"run_id": "1"}
    assert len(queue.jobs) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make db-up && cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/test_service.py -q --no-cov`

Expected: FAIL with `ImportError: cannot import name 'service'`.

- [ ] **Step 3: Implement**

Add to `engine/coglossa/schema/run.py`:

```python
from pydantic import BaseModel


class RunProgress(BaseModel):
    """Progress of a queued run (spec §7.1); the API streams it over SSE in M4."""

    run_id: str
    state: RunState
    chunks_total: int
    chunks_done: dict[str, int]  # translate / reconcile / score -> chunks finished
    chunks_with_errors: int
    error: str | None = None
```

Export `RunProgress` from `coglossa.schema`. Add `"run-progress": RunProgress` to `EXPORTED` in `schema/export.py`, plus whatever the export test expects (it lists names), then run `make schema`.

In `engine/coglossa/ingest/registry.py`:

```python
def check_supported(path: Path) -> None:
    """Fail fast on a missing file or an extension no ingestor handles."""
    if not path.is_file():
        raise IngestError(f"input file not found: {path}")
    ext = path.suffix.lower()
    ingestors = available_ingestors()
    if not any(ext in i.extensions for i in ingestors):
        supported = sorted({e for i in ingestors for e in i.extensions})
        raise UnsupportedFormatError(
            f"unsupported file type '{ext or path.name}'; supported: {', '.join(supported)}"
        )
```

Make `ingest_file` start with `check_supported(path)` and drop its own not-found check and trailing unsupported branch. That loop always finds an ingestor now; end it with a defensive `raise AssertionError` marked `# pragma: no cover - check_supported guarantees a match`. The existing ingest tests must pass unchanged.

`engine/coglossa/testing/queue.py`:

```python
"""In-memory JobQueue for tests. Like procrastinate, a queueing lock that is already queued
is not queued again; `attempts` records every defer call so tests can see duplicates."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QueuedJob:
    task: str
    lock: str | None
    kwargs: dict[str, str] = field(default_factory=dict)


class RecordingQueue:
    def __init__(self) -> None:
        self.jobs: list[QueuedJob] = []
        self.attempts: list[QueuedJob] = []

    async def defer(self, task: str, *, lock: str | None = None, **kwargs: str) -> None:
        job = QueuedJob(task, lock, kwargs)
        self.attempts.append(job)
        if lock is not None and any(j.lock == lock for j in self.jobs):
            return
        self.jobs.append(job)

    def pop(self) -> QueuedJob | None:
        return self.jobs.pop(0) if self.jobs else None
```

`engine/coglossa/service.py`:

```python
"""Control plane (spec §7.1): submit runs, approve bibles, cancel, retry, report progress and
build reports. The CLI uses it now and the REST API will in M4. It never calls a model: work
goes to the worker through a JobQueue."""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import coglossa
from coglossa.config import CoglossaConfig, config_hash
from coglossa.ingest.base import IngestError
from coglossa.ingest.registry import check_supported
from coglossa.llm.agent import CallLog
from coglossa.prompts.check import run_prompt_ids
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import (
    Bible,
    ConfigSnapshot,
    DocumentInfo,
    DocumentProfile,
    PipelineProfile,
    Reliability,
    Report,
    RunError,
    RunProgress,
    RunState,
    RunStatus,
    ScorerHealth,
    TERMINAL_STATES,
)
from coglossa.scoring.aggregate import any_scored, document_notices
from coglossa.stages.sweep import resolve_proposals
from coglossa.storage import Storage, safe_filename
from coglossa.store import outputs, runs
from coglossa.store.db import Database
from coglossa.store.models import StageResult, utcnow

TASK_START_RUN = "coglossa.start_run"
TASK_TRANSLATE_RUN = "coglossa.translate_run"
TASK_PROCESS_CHUNK = "coglossa.process_chunk"
TASK_FINISH_RUN = "coglossa.finish_run"
_LOCK_SUFFIX = {TASK_START_RUN: "start", TASK_TRANSLATE_RUN: "translate", TASK_FINISH_RUN: "finish"}


class JobQueue(Protocol):
    async def defer(self, task: str, *, lock: str | None = None, **kwargs: str) -> None: ...


class InvalidRunStateError(Exception):
    """The run is not in a state that allows the action; the message says why."""


@dataclass(frozen=True)
class SubmitOptions:
    project: str
    target_lang: str
    source_lang: str | None = None
    pipeline_profile: PipelineProfile | None = None
    checkpoint: bool | None = None
    use_project_bible: bool = False


async def _queue_run_job(queue: JobQueue, task: str, run_id: uuid.UUID) -> None:
    await queue.defer(task, lock=f"{run_id}:{_LOCK_SUFFIX[task]}", run_id=str(run_id))


async def submit_run(
    db: Database,
    storage: Storage,
    queue: JobQueue,
    cfg: CoglossaConfig,
    prompts: PromptLibrary,
    path: Path,
    opts: SubmitOptions,
) -> uuid.UUID:
    check_supported(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise IngestError(f"{path.name} cannot be read: {e.strerror or e}") from e
    document_id = uuid.uuid4()
    async with db.session() as s, s.begin():
        project = await runs.get_or_create_project(s, opts.project)
        key = f"projects/{project.id}/documents/{document_id}/{safe_filename(path.name)}"
        storage.put(key, data)
        doc = await runs.add_document(
            s,
            project.id,
            document_id=document_id,
            filename=path.name,
            format=path.suffix.lower().lstrip("."),
            sha256=hashlib.sha256(data).hexdigest(),
            storage_key=key,
        )
        run = await runs.create_run(
            s,
            doc.id,
            target_lang=opts.target_lang,
            source_lang=opts.source_lang,
            pipeline_profile=opts.pipeline_profile.value if opts.pipeline_profile else None,
            checkpoint=opts.checkpoint,
            use_project_bible=opts.use_project_bible,
            config_hash=config_hash(cfg),
            config_snapshot=cfg.model_dump(mode="json"),
            prompt_versions=prompts.versions(run_prompt_ids(cfg)),
        )
    await _queue_run_job(queue, TASK_START_RUN, run.id)
    return run.id


async def run_bible(db: Database, run_id: uuid.UUID) -> Bible:
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        if run.bible_version_id is None:
            raise InvalidRunStateError(f"the bible is not ready yet; run {run_id} is {run.state}")
        return Bible.model_validate((await runs.get_bible_version(s, run.bible_version_id)).content)


async def approve_bible(
    db: Database, queue: JobQueue, run_id: uuid.UUID, bible: Bible | None = None
) -> int:
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        if run.state != RunState.AWAITING_BIBLE_APPROVAL or run.bible_version_id is None:
            raise InvalidRunStateError(
                f"run {run_id} is {run.state}; only a run awaiting bible approval can be approved"
            )
        draft = await runs.get_bible_version(s, run.bible_version_id)
        generated = Bible.model_validate(draft.content)
        content = bible if bible is not None else generated
        doc = await runs.get_document(s, run.document_id)
        version = await runs.add_bible_version(
            s,
            doc.project_id,
            content,
            status=runs.APPROVED,
            author=runs.ENGINE if content == generated else runs.USER,
            source_run_id=run.id,
        )
        run.bible_version_id = version.id
        run.state = RunState.TRANSLATING
        number = version.version
    await _queue_run_job(queue, TASK_TRANSLATE_RUN, run_id)
    return number


async def cancel_run(db: Database, run_id: uuid.UUID) -> RunState:
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        state = RunState(run.state)
        if state in TERMINAL_STATES:
            raise InvalidRunStateError(f"run {run_id} is already {state}")
        run.cancel_requested = True
        if state in (RunState.CREATED, RunState.AWAITING_BIBLE_APPROVAL):
            run.state = RunState.CANCELLED  # nothing is running for it
            run.finished_at = utcnow()
        return RunState(run.state)


async def retry_run(db: Database, queue: JobQueue, run_id: uuid.UUID) -> str:
    async with db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        state = RunState(run.state)
        if state is RunState.COMPLETE:
            raise InvalidRunStateError(f"run {run_id} completed without errors; nothing to retry")
        if state is RunState.CANCELLED:
            raise InvalidRunStateError(f"run {run_id} was cancelled; submit a new run")
        if state is RunState.AWAITING_BIBLE_APPROVAL:
            raise InvalidRunStateError(
                f"run {run_id} is waiting for bible approval; approve the bible to continue"
            )
        if state is RunState.FAILED:
            await runs.delete_stages(s, run_id, status=runs.FAILED)
            run.state, run.error, run.finished_at = RunState.CREATED, None, None
            task, message = TASK_START_RUN, "restarting from the failed stage"
        elif state is RunState.COMPLETE_WITH_ERRORS:
            failed = await runs.chunks_with_errors(s, run_id)
            await outputs.delete_chunk_outputs(s, run_id, failed)
            await runs.delete_stages(s, run_id, chunk_ids=failed)
            await outputs.undo_sweep(s, run_id)
            await runs.delete_stages(s, run_id, stages=["sweep"])
            run.state, run.finished_at = RunState.TRANSLATING, None
            task = TASK_TRANSLATE_RUN
            message = f"retrying {len(failed)} chunk(s)" if failed else "re-running the sweep"
        elif state is RunState.TRANSLATING:
            task, message = TASK_TRANSLATE_RUN, "resumed"
        elif state is RunState.SWEEPING:
            task, message = TASK_FINISH_RUN, "resumed"
        else:  # created, ingesting, profiling, building_bible
            task, message = TASK_START_RUN, "resumed"
    await _queue_run_job(queue, task, run_id)
    return message


async def run_progress(db: Database, run_id: uuid.UUID) -> RunProgress:
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        total = len(await runs.load_chunks(s, run_id))
        done = await runs.chunk_progress(s, run_id)
        with_errors = len(await runs.chunks_with_errors(s, run_id))
    return RunProgress(
        run_id=str(run_id),
        state=RunState(run.state),
        chunks_total=total,
        chunks_done={stage: done.get(stage, 0) for stage in ("translate", "reconcile", "score")},
        chunks_with_errors=with_errors,
        error=run.error,
    )


def _stage_errors(rows: list[StageResult]) -> list[RunError]:
    errors: list[RunError] = []
    for row in rows:
        if row.status == runs.FAILED:
            errors.append(
                RunError(stage=row.stage, chunk_id=row.chunk_id or None, message=row.error or "")
            )
        elif row.output:
            errors.extend(RunError.model_validate(e) for e in row.output.get("errors") or [])
    return errors


async def build_report(db: Database, run_id: uuid.UUID) -> Report:
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        state = RunState(run.state)
        if state not in (RunState.COMPLETE, RunState.COMPLETE_WITH_ERRORS):
            raise InvalidRunStateError(
                f"run {run_id} is {state}; a report is available once it is complete"
            )
        doc = await runs.get_document(s, run.document_id)
        blocks, segments = await runs.load_source(s, doc.id)
        chunks = await runs.load_chunks(s, run_id)
        bible = Bible.model_validate(
            (await runs.get_bible_version(s, run.bible_version_id)).content
        )
        stored = await outputs.load_spans(s, run_id)
        candidates = await outputs.load_candidates(s, run_id)
        revisions = await outputs.load_revisions(
            s, run_id, {row: sid for sid, row in stored.row_ids.items()}
        )
        llm_calls = await runs.load_llm_calls(s, run_id)
        errors = _stage_errors(await runs.stage_rows(s, run_id))
    profile = DocumentProfile.model_validate(run.profile)
    tier = Reliability(run.language_tier)
    snapshot = CoglossaConfig.model_validate(run.config_snapshot)
    return Report(
        run_id=str(run.id),
        coglossa_version=coglossa.__version__,
        created_at=run.finished_at or run.created_at,
        status=RunStatus(state.value),
        document=DocumentInfo(source_path=doc.filename, format=doc.format, sha256=doc.sha256),
        profile=profile,
        bible=bible,
        bible_proposals=resolve_proposals(bible, stored.proposed_terms),
        blocks=blocks,
        segments=segments,
        chunks=chunks,
        candidates=candidates,
        spans=stored.spans,
        revisions=revisions,
        language_tier=tier,
        notices=document_notices(
            tier, profile.source_lang, profile.target_lang, scored=any_scored(stored.spans)
        ),
        errors=errors,
        usage=CallLog(records=llm_calls).usage(),
        config=ConfigSnapshot(
            hash=run.config_hash,
            roles=dict(snapshot.roles.all_roles()),
            prompt_versions=dict(run.prompt_versions),
            scorer=ScorerHealth.model_validate(run.scorer_health) if run.scorer_health else None,
        ),
        llm_calls=llm_calls,
    )


async def delete_project(db: Database, storage: Storage, name: str) -> None:
    """Spec §5: deleting a project removes every row, stored file and model-call payload."""
    async with db.session() as s, s.begin():
        project = await runs.find_project(s, name)
        await runs.delete_project(s, project.id)
    storage.delete_prefix(f"projects/{project.id}/")
```

`CallLog(records=llm_calls)` relies on `CallLog` being a dataclass with a `records` field, which it is (M1). `CoglossaConfig.model_validate(run.config_snapshot)` re-reads the submit-time snapshot; it validates because it was dumped from a valid config.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make test-engine`

Expected: PASS; coverage ≥ 95%. `make schema` shows no drift after the regeneration.

- [ ] **Step 5: Commit**

```bash
git add engine schema
git commit -m "feat(service): control plane for queued runs: submit, approve, cancel, retry, progress, report, delete"
```

---

### Task 10: Worker job bodies (run lifecycle)

**Files:**
- Create:
  - `engine/coglossa/worker/__init__.py`
  - `engine/coglossa/worker/execute.py`
- Test: `engine/tests/worker/__init__.py`, `engine/tests/worker/conftest.py`, `engine/tests/worker/test_execute.py`

**Interfaces:**
- Consumes:
  - everything from Tasks 2–9
  - `run_profile`, `run_bible`, `ingest_file`, `segment_document`, `chunk_segments`, `compute_budget`, `build_chunk_inputs`, `model_diversity`
- Produces (in `coglossa.worker.execute`):
  - `DB_ERRORS = (sqlalchemy.exc.OperationalError, sqlalchemy.exc.InterfaceError)`
  - `WorkerServices` (dataclass):
    - fields: `cfg`, `db`, `storage`, `queue`, `backends`, `prompts`, `tiers`, `scorer=None`, `scorer_health=None`, `sleep=asyncio.sleep`
    - methods: `.context(calls, target_lang) -> StageContext`, `.call_log() -> CallLog`, `async .aclose()`
  - job functions, each taking `(svc, run_id: uuid.UUID[, chunk_id: str])` and returning `None`: `async start_run`, `async translate_run`, `async process_chunk_job`, `async finish_run`
  - `async run_job(svc, task: str, kwargs: dict[str, str]) -> None`: dispatches by task name and raises `ValueError` for an unknown task
  - every job does nothing when its run no longer exists (`runs.NotFoundError`, e.g. the project was deleted)

**Flow** (spec §7.1). Every step runs under the run row lock and begins with a cancel check:
- **`start_run`:**
  1. Records `started_at` and the scorer health. If the worker's config hash differs from the run's, it writes a one-time `config` stage whose output holds a `RunError`.
  2. Runs the stages `ingest` → `profile` → `bible`, each through `_stage`. A completed stage is skipped and returns its stored output.
  3. Then sets `awaiting_bible_approval` when the bible output says `checkpoint`. Otherwise it sets `translating` and calls `translate_run`.
- **`_stage`:** marks the run state and begins the row (short transaction), then calls the stage function with no transaction open. The function returns `(output, save)`. `save(s)`, `finish_stage` and the model-call records then commit in **one** transaction.
  - `RUN_FAILURES` (`StageError`, `IngestError`, `ConfigError`, `StorageError`) fail the run with the message.
  - Other exceptions fail it with `internal error: …`.
  - `DB_ERRORS` are re-raised.
- **`translate_run`:** queues `process_chunk` for every chunk whose `score` row is not finished. If none remain, it calls `_maybe_finish`.
- **`process_chunk_job`:**
  1. Skips a finished chunk.
  2. Otherwise begins `translate` and runs `process_chunk`. The stage hook finishes the current stage row and begins the next (translate → reconcile → score).
  3. Saves the outcome, finishes `score` with the outcome as output, finishes any still-running translate/reconcile rows and stores the model calls, all in one transaction.
  4. An unexpected exception fails the `score` row instead. Either way it then calls `_maybe_finish`.
- **`_maybe_finish`:** under the run lock, when every chunk's `score` row is finished and the state is `translating`, sets `sweeping` and queues `finish_run` **once** (the state change is the guard).
- **`finish_run`:**
  1. Runs the sweep when `cfg.profiles.sweep[profile]` is true; otherwise records `{"skipped": true}`.
  2. Applies its changes and finishes the `sweep` row with `{"errors": […], "revised": n}`.
  3. Sets `complete_with_errors` when `total_error_count > 0`, otherwise `complete`.
- **Cancel:** a run with `cancel_requested` becomes `cancelled` at the next lock in any job. Outputs already saved are kept. A terminal state is never changed again.

- [ ] **Step 1: Write the failing tests**

`engine/tests/worker/conftest.py`:

```python
import uuid

import pytest

from coglossa import service
from coglossa.prompts.library import PromptLibrary
from coglossa.scoring.tiers import LanguageTiers
from coglossa.storage import LocalStorage
from coglossa.testing.queue import RecordingQueue
from coglossa.worker.execute import WorkerServices, run_job
from tests.conftest import build_config


async def _no_sleep(_: float) -> None:
    return None


def make_services(db, fake_backend, tmp_path, **cfg_overrides) -> WorkerServices:
    cfg = build_config(**cfg_overrides)
    return WorkerServices(
        cfg=cfg,
        db=db,
        storage=LocalStorage(tmp_path / "files"),
        queue=RecordingQueue(),
        backends={"fake": fake_backend},
        prompts=PromptLibrary(cfg.prompts_dir),
        tiers=LanguageTiers.load(None),
        sleep=_no_sleep,
    )


@pytest.fixture
def services(db, fake_backend, tmp_path):
    return make_services(db, fake_backend, tmp_path)


async def submit(svc, tmp_path, text="Le chat dort. Il pleut.", name="story.txt", **opts):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    options = service.SubmitOptions(project="novel", target_lang="en", source_lang="fr", **opts)
    return await service.submit_run(svc.db, svc.storage, svc.queue, svc.cfg, svc.prompts, path, options)


async def drain(svc, *, limit: int = 500) -> int:
    """Run queued jobs in order until the queue is empty (a deterministic in-test worker)."""
    count = 0
    while (job := svc.queue.pop()) is not None:
        await run_job(svc, job.task, job.kwargs)
        count += 1
        assert count < limit, "job loop did not settle"
    return count


def tasks_sent(fake_backend) -> list[str]:
    import json

    return [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["task"]
        for r in fake_backend.requests
    ]


def uid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
```

`engine/tests/worker/test_execute.py`:

```python
import asyncio

import pytest
from sqlalchemy.exc import OperationalError

from coglossa import service
from coglossa.schema import (
    Bible,
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    PipelineProfile,
    RevisionSource,
    RunState,
    RunStatus,
    ScorerHealth,
)
from coglossa.store import outputs, runs
from coglossa.worker import execute
from tests.worker.conftest import drain, make_services, submit, tasks_sent

LONG_TEXT = "\n\n".join(" ".join(["Le chat dort."] * 45) for _ in range(4))


async def _state(svc, run_id):
    async with svc.db.session() as s:
        return RunState((await runs.get_run(s, run_id)).state)


async def test_a_run_goes_from_created_to_complete(services, tmp_path, fake_backend):
    services.scorer_health = ScorerHealth(device="cpu")
    run_id = await submit(services, tmp_path)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE
    report = await service.build_report(services.db, run_id)
    assert report.status is RunStatus.COMPLETE
    assert [s.text for s in report.spans] == ["[en] Le chat dort.", "[en] Il pleut."]
    assert report.config.scorer == ScorerHealth(device="cpu")
    assert report.usage.calls == len(fake_backend.requests)
    async with services.db.session() as s:
        stages = {(r.stage, r.chunk_id): r for r in await runs.stage_rows(s, run_id)}
        run = await runs.get_run(s, run_id)
    for key in [("ingest", ""), ("profile", ""), ("bible", ""), ("translate", "c1"),
                ("reconcile", "c1"), ("score", "c1"), ("sweep", "")]:
        assert stages[key].status == runs.COMPLETE, key
        assert stages[key].duration_ms is not None
    assert stages[("sweep", "")].output == {"errors": [], "skipped": True}  # short profile
    assert run.started_at is not None and run.finished_at is not None
    assert ("config", "") not in stages


async def test_checkpoint_waits_for_approval(services, tmp_path, fake_backend):
    run_id = await submit(services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM)
    await drain(services)
    assert await _state(services, run_id) is RunState.AWAITING_BIBLE_APPROVAL
    assert "translate" not in tasks_sent(fake_backend)
    bible = Bible(
        summary="s", style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    await service.approve_bible(services.db, services.queue, run_id, bible)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE
    report = await service.build_report(services.db, run_id)
    assert report.bible == bible
    # long-form sweeps: the locked term is enforced and its flag resolved
    (rev,) = report.revisions
    assert rev.source is RevisionSource.SWEEP and "cat" in report.spans[0].text
    statuses = [f.status for f in report.spans[0].flags if f.code is FlagCode.TERMINOLOGY_VIOLATION]
    assert statuses == [FlagStatus.RESOLVED]


async def test_project_bible_is_reused_without_a_bible_call(services, tmp_path, fake_backend):
    first = await submit(services, tmp_path, checkpoint=True)
    await drain(services)
    await service.approve_bible(services.db, services.queue, first)
    await drain(services)
    fake_backend.requests.clear()
    second = await submit(services, tmp_path, use_project_bible=True)
    await drain(services)
    assert await _state(services, second) is RunState.COMPLETE
    assert "bible" not in tasks_sent(fake_backend)


async def test_profiling_failure_fails_the_run_and_retry_recovers(services, tmp_path, fake_backend):
    fake_backend.script("profile", "not json", "not json")
    run_id = await submit(services, tmp_path)
    await drain(services)
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
        profile_row = await runs.get_stage(s, run_id, "profile")
    assert run.state == RunState.FAILED and run.error.startswith("profiling failed")
    assert profile_row.status == runs.FAILED
    assert await service.retry_run(services.db, services.queue, run_id) == "restarting from the failed stage"
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_unreadable_document_fails_the_run(services, tmp_path):
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff\xfe\x00broken")
    opts = service.SubmitOptions(project="p", target_lang="en")
    run_id = await service.submit_run(
        services.db, services.storage, services.queue, services.cfg, services.prompts, path, opts
    )
    await drain(services)
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
    assert run.state == RunState.FAILED and "bad.txt" in run.error


async def test_unexpected_stage_error_is_an_internal_error(services, tmp_path, monkeypatch):
    def boom(_):
        raise RuntimeError("segmenter exploded")

    monkeypatch.setattr(execute, "segment_document", boom)
    run_id = await submit(services, tmp_path)
    await drain(services)
    async with services.db.session() as s:
        run = await runs.get_run(s, run_id)
    assert run.state == RunState.FAILED
    assert run.error == "internal error: RuntimeError: segmenter exploded"


async def test_rerunning_a_finished_chunk_does_nothing(services, tmp_path, fake_backend):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))  # start_run → queues c1
    job = services.queue.jobs[0]
    await execute.process_chunk_job(services, run_id, "c1")
    calls = len(fake_backend.requests)
    async with services.db.session() as s, s.begin():
        (await runs.get_run(s, run_id, lock=True)).state = RunState.TRANSLATING  # pretend in flight
    await execute.process_chunk_job(services, run_id, job.kwargs["chunk_id"])
    assert len(fake_backend.requests) == calls
    async with services.db.session() as s:
        assert len((await outputs.load_spans(s, run_id)).spans) == 2


async def test_a_chunk_interrupted_mid_flight_restarts_cleanly(services, tmp_path, monkeypatch):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))
    real = execute.process_chunk

    async def killed(*args, **kwargs):
        await kwargs["on_stage"]("translate")
        raise asyncio.CancelledError  # the worker process died here

    monkeypatch.setattr(execute, "process_chunk", killed)
    with pytest.raises(asyncio.CancelledError):
        await execute.process_chunk_job(services, run_id, "c1")
    monkeypatch.setattr(execute, "process_chunk", real)
    await execute.process_chunk_job(services, run_id, "c1")  # the re-queued job
    await drain(services)
    async with services.db.session() as s:
        assert len((await outputs.load_spans(s, run_id)).spans) == 2
        assert (await runs.get_stage(s, run_id, "translate", "c1")).attempt == 2
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_parallel_chunks_queue_finish_exactly_once(db, fake_backend, tmp_path):
    svc = make_services(db, fake_backend, tmp_path, chunking={"target_words": 100, "max_words": 200})
    run_id = await submit(svc, tmp_path, text=LONG_TEXT)
    await execute.run_job(svc, *_pop(svc))
    chunk_jobs = list(svc.queue.jobs)
    svc.queue.jobs.clear()
    assert len(chunk_jobs) >= 2
    await asyncio.gather(*(execute.run_job(svc, j.task, j.kwargs) for j in chunk_jobs))
    finishes = [j for j in svc.queue.attempts if j.task == service.TASK_FINISH_RUN]
    assert len(finishes) == 1
    await drain(svc)
    assert await _state(svc, run_id) is RunState.COMPLETE


async def test_cancel_stops_new_work_and_keeps_results(services, tmp_path, fake_backend, monkeypatch):
    svc = services
    run_id = await submit(svc, tmp_path)
    await execute.run_job(svc, *_pop(svc))
    real = execute.process_chunk

    async def cancel_mid_flight(*args, **kwargs):
        await service.cancel_run(svc.db, run_id)  # arrives while the chunk is running
        return await real(*args, **kwargs)

    monkeypatch.setattr(execute, "process_chunk", cancel_mid_flight)
    await drain(svc)
    assert await _state(svc, run_id) is RunState.CANCELLED
    async with svc.db.session() as s:
        assert len((await outputs.load_spans(s, run_id)).spans) == 2  # kept
    await execute.finish_run(svc, run_id)  # a late job never revives the run
    await execute.translate_run(svc, run_id)
    assert await _state(svc, run_id) is RunState.CANCELLED


async def test_cancel_before_any_chunk_runs(services, tmp_path, fake_backend):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))
    before = len(fake_backend.requests)
    await service.cancel_run(services.db, run_id)
    await drain(services)
    assert len(fake_backend.requests) == before
    assert await _state(services, run_id) is RunState.CANCELLED


async def test_config_change_is_recorded(db, fake_backend, tmp_path):
    submitter = make_services(db, fake_backend, tmp_path)
    run_id = await submit(submitter, tmp_path)
    worker = make_services(db, fake_backend, tmp_path, retry={"max_attempts": 2, "base_delay_s": 0, "max_delay_s": 0})
    worker.queue = submitter.queue
    await drain(worker)
    report = await service.build_report(db, run_id)
    (error,) = [e for e in report.errors if e.stage == "config"]
    assert "differs from the one this run was submitted with" in error.message
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    async with db.session() as s:
        assert report.config.hash == (await runs.get_run(s, run_id)).config_hash


async def test_an_unexpected_chunk_error_fails_the_chunk_then_retry_recovers(services, tmp_path, monkeypatch):
    run_id = await submit(services, tmp_path)
    real = execute.process_chunk
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bug")
        return await real(*args, **kwargs)

    monkeypatch.setattr(execute, "process_chunk", flaky)
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE_WITH_ERRORS
    report = await service.build_report(services.db, run_id)
    assert [e.message for e in report.errors] == ["internal error: RuntimeError: bug"]
    assert await service.retry_run(services.db, services.queue, run_id) == "retrying 1 chunk(s)"
    await drain(services)
    assert await _state(services, run_id) is RunState.COMPLETE


async def test_database_errors_propagate_for_queue_retry(services, tmp_path, monkeypatch):
    run_id = await submit(services, tmp_path)
    await execute.run_job(services, *_pop(services))

    async def db_down(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(execute, "process_chunk", db_down)
    with pytest.raises(OperationalError):
        await execute.process_chunk_job(services, run_id, "c1")


async def test_jobs_for_a_deleted_project_are_skipped(services, tmp_path):
    await submit(services, tmp_path)
    await service.delete_project(services.db, services.storage, "novel")
    assert await drain(services) == 1  # start_run ran and found nothing


async def test_unknown_job_name(services):
    with pytest.raises(ValueError, match="unknown job"):
        await execute.run_job(services, "coglossa.nope", {"run_id": "00000000-0000-0000-0000-000000000000"})


def _pop(svc):
    job = svc.queue.pop()
    return job.task, job.kwargs
```

Notes for the implementer:
- `test_rerunning_a_finished_chunk_does_nothing` processes chunk c1 directly, then forces the run back to `translating` to simulate a duplicate delivery while the run is still in flight. A short document has one chunk, `c1`.
- `test_a_chunk_interrupted_mid_flight_restarts_cleanly` simulates a killed worker with `asyncio.CancelledError`. It is a `BaseException`, so it bypasses the `except Exception` handler, as a real kill bypasses everything. procrastinate's stalled-job recovery (Task 11) re-delivers the job.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/worker -q --no-cov`

Expected: FAIL with `ModuleNotFoundError: coglossa.worker`.

- [ ] **Step 3: Implement**

`engine/coglossa/worker/__init__.py`:

```python
"""The worker (spec §2.1): takes stage jobs from the queue, calls the engine library, saves
outputs and moves runs through their lifecycle."""
```

`engine/coglossa/worker/execute.py`:

```python
"""Worker job bodies (spec §7.1–§7.3). Each public coroutine is one queue job. Jobs are
idempotent, keyed by (run, stage, chunk) in stage_result: a re-delivered or re-queued job skips
work that already completed. The library stages do the work; this module loads inputs, saves
outputs and moves the run through its lifecycle. No transaction is held across a model call."""

import asyncio
import functools
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from coglossa.config import CoglossaConfig, ConfigError, config_hash
from coglossa.ingest.base import IngestError
from coglossa.ingest.registry import ingest_file
from coglossa.llm.agent import AgentRunner, CallLog
from coglossa.llm.types import Backend
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import (
    TERMINAL_STATES,
    Bible,
    DocumentProfile,
    PipelineProfile,
    Reliability,
    RunError,
    RunState,
    ScorerHealth,
    SourceDocument,
)
from coglossa.scoring.aggregate import model_diversity
from coglossa.scoring.client import Scorer
from coglossa.scoring.tiers import LanguageTiers
from coglossa.segment.chunking import chunk_segments, compute_budget
from coglossa.segment.segmenter import segment_document
from coglossa.service import (
    TASK_FINISH_RUN,
    TASK_PROCESS_CHUNK,
    TASK_START_RUN,
    TASK_TRANSLATE_RUN,
    JobQueue,
)
from coglossa.stages.bible import run_bible
from coglossa.stages.chunk import ChunkOutcome, process_chunk
from coglossa.stages.context import StageContext, StageError
from coglossa.stages.profile import run_profile
from coglossa.stages.sweep import SpanChange, run_sweep
from coglossa.stages.translate import build_chunk_inputs
from coglossa.storage import Storage, StorageError
from coglossa.store import outputs, runs
from coglossa.store.db import Database
from coglossa.store.models import Run, StageResult, utcnow

log = logging.getLogger("coglossa.worker")

DB_ERRORS: tuple[type[Exception], ...] = (OperationalError, InterfaceError)
RUN_FAILURES: tuple[type[Exception], ...] = (StageError, IngestError, ConfigError, StorageError)
_BEFORE_TRANSLATION = (
    RunState.CREATED,
    RunState.INGESTING,
    RunState.PROFILING,
    RunState.BUILDING_BIBLE,
)
_NEXT_STAGE = {"translate": "reconcile", "reconcile": "score"}


@dataclass
class WorkerServices:
    cfg: CoglossaConfig
    db: Database
    storage: Storage
    queue: JobQueue
    backends: dict[str, Backend]
    prompts: PromptLibrary
    tiers: LanguageTiers
    scorer: Scorer | None = None
    scorer_health: ScorerHealth | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def call_log(self) -> CallLog:
        return CallLog(store_io=self.cfg.audit.store_llm_io)

    def context(self, calls: CallLog, target_lang: str) -> StageContext:
        runner = AgentRunner(self.cfg, self.backends, self.prompts, calls, sleep=self.sleep)
        return StageContext(cfg=self.cfg, runner=runner, target_lang=target_lang)

    async def aclose(self) -> None:
        for backend in self.backends.values():
            await backend.aclose()
        if self.scorer is not None:
            await self.scorer.aclose()
        await self.db.dispose()


class _Stop(Exception):
    """The run cannot continue in this job (failed or cancelled); already recorded."""


def _internal(e: BaseException) -> str:
    return f"internal error: {type(e).__name__}: {e}"


def _cancel_if_requested(run: Run) -> bool:
    """True when the run must stop: a cancel was requested (the state becomes cancelled)."""
    if not run.cancel_requested:
        return False
    if RunState(run.state) not in TERMINAL_STATES:
        run.state = RunState.CANCELLED
        run.finished_at = utcnow()
    return True


def _job[**P](fn: Callable[P, Awaitable[None]]) -> Callable[P, Awaitable[None]]:
    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            await fn(*args, **kwargs)
        except runs.NotFoundError as e:
            log.info("job skipped: %s", e)

    return wrapper


# ---------------------------------------------------------------- run-level stages

Saver = Callable[[AsyncSession], Awaitable[None]]
StageFn = Callable[["WorkerServices", uuid.UUID, CallLog], Awaitable[tuple[dict[str, Any], Saver]]]


async def _stage(
    svc: WorkerServices, run_id: uuid.UUID, name: str, state: RunState, fn: StageFn
) -> dict[str, Any]:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        cancelled = _cancel_if_requested(run)
        done = None if cancelled else await runs.get_stage(s, run_id, name)
        row_id: int | None = None
        if not cancelled and (done is None or done.status != runs.COMPLETE):
            run.state = state
            row_id = (await runs.begin_stage(s, run_id, name)).id
    if cancelled:
        raise _Stop
    if row_id is None:
        assert done is not None
        return done.output or {}
    calls = svc.call_log()
    try:
        output, save = await fn(svc, run_id, calls)
    except DB_ERRORS:
        raise
    except RUN_FAILURES as e:
        await _fail_run(svc, run_id, row_id, str(e), calls, name)
        raise _Stop from e
    except Exception as e:
        log.exception("stage %s failed", name, extra={"run_id": str(run_id), "stage": name})
        await _fail_run(svc, run_id, row_id, _internal(e), calls, name)
        raise _Stop from e
    async with svc.db.session() as s, s.begin():
        await save(s)
        row = await s.get(StageResult, row_id)
        assert row is not None
        runs.finish_stage(row, output)
        await runs.save_llm_calls(s, run_id, calls.records, stage=name)
    log.info("stage complete", extra={"run_id": str(run_id), "stage": name})
    return output


async def _fail_run(
    svc: WorkerServices, run_id: uuid.UUID, row_id: int, message: str, calls: CallLog, stage: str
) -> None:
    async with svc.db.session() as s, s.begin():
        row = await s.get(StageResult, row_id)
        assert row is not None
        runs.fail_stage(row, message)
        await runs.save_llm_calls(s, run_id, calls.records, stage=stage)
        run = await runs.get_run(s, run_id, lock=True)
        if RunState(run.state) not in TERMINAL_STATES:
            run.state, run.error, run.finished_at = RunState.FAILED, message, utcnow()


async def _ingest(
    svc: WorkerServices, run_id: uuid.UUID, calls: CallLog
) -> tuple[dict[str, Any], Saver]:
    async with svc.db.session() as s:
        run = await runs.get_run(s, run_id)
        doc = await runs.get_document(s, run.document_id)
        blocks, segments = await runs.load_source(s, doc.id)
    fresh = not blocks
    if fresh:
        with svc.storage.local_file(doc.storage_key) as path:
            source = ingest_file(path)
        blocks = [
            b.model_copy(
                update={"provenance": b.provenance.model_copy(update={"source_path": doc.filename})}
            )
            for b in source.blocks
        ]
        source = source.model_copy(update={"source_path": doc.filename, "blocks": blocks})
        segments = segment_document(source)
    else:
        source = SourceDocument(
            source_path=doc.filename, format=doc.format, sha256=doc.sha256, blocks=blocks
        )
    chunks = chunk_segments(source, segments, compute_budget(svc.cfg))

    async def save(s: AsyncSession) -> None:
        if fresh:
            await runs.save_source(s, doc.id, source.blocks, segments)
        await runs.save_chunks(s, run_id, chunks)

    return {"errors": [], "segments": len(segments), "chunks": len(chunks)}, save


async def _profile(
    svc: WorkerServices, run_id: uuid.UUID, calls: CallLog
) -> tuple[dict[str, Any], Saver]:
    async with svc.db.session() as s:
        run = await runs.get_run(s, run_id)
        _, segments = await runs.load_source(s, run.document_id)
    profile = await run_profile(
        svc.context(calls, run.target_lang),
        segments,
        source_lang=run.source_lang,
        pipeline_profile=PipelineProfile(run.pipeline_profile) if run.pipeline_profile else None,
    )
    tier = svc.tiers.tier(profile.source_lang, profile.target_lang)

    async def save(s: AsyncSession) -> None:
        locked = await runs.get_run(s, run_id, lock=True)
        locked.profile = profile.model_dump(mode="json")
        locked.language_tier = tier.value

    return {"errors": []}, save


async def _bible(
    svc: WorkerServices, run_id: uuid.UUID, calls: CallLog
) -> tuple[dict[str, Any], Saver]:
    async with svc.db.session() as s:
        run = await runs.get_run(s, run_id)
        doc = await runs.get_document(s, run.document_id)
        _, segments = await runs.load_source(s, doc.id)
        chunks = await runs.load_chunks(s, run_id)
        prior_row = await runs.latest_bible(s, doc.project_id, approved_only=True)
    profile = DocumentProfile.model_validate(run.profile)
    if run.use_project_bible and prior_row is not None:
        reused_id, reused_version = prior_row.id, prior_row.version

        async def reuse(s: AsyncSession) -> None:
            (await runs.get_run(s, run_id, lock=True)).bible_version_id = reused_id

        return {"errors": [], "checkpoint": False, "reused_version": reused_version}, reuse
    prior = Bible.model_validate(prior_row.content) if prior_row is not None else None
    bible, errors = await run_bible(
        svc.context(calls, run.target_lang), profile, segments, chunks, prior=prior
    )
    checkpoint = (
        run.checkpoint
        if run.checkpoint is not None
        else svc.cfg.profiles.checkpoint.get(profile.pipeline_profile, False)
    )

    async def save(s: AsyncSession) -> None:
        version = await runs.add_bible_version(
            s, doc.project_id, bible, status=runs.DRAFT, author=runs.ENGINE, source_run_id=run_id
        )
        (await runs.get_run(s, run_id, lock=True)).bible_version_id = version.id

    return {"errors": [e.model_dump() for e in errors], "checkpoint": checkpoint}, save


@_job
async def start_run(svc: WorkerServices, run_id: uuid.UUID) -> None:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        stop = _cancel_if_requested(run) or RunState(run.state) not in _BEFORE_TRANSLATION
        if not stop:
            run.started_at = run.started_at or utcnow()
            if svc.scorer_health is not None:
                run.scorer_health = svc.scorer_health.model_dump(mode="json")
            current = config_hash(svc.cfg)
            if run.config_hash != current and await runs.get_stage(s, run_id, "config") is None:
                message = (
                    f"the worker's configuration ({current}) differs from the one this run was "
                    f"submitted with ({run.config_hash}); results reflect the worker's configuration"
                )
                row = await runs.begin_stage(s, run_id, "config")
                runs.finish_stage(
                    row, {"errors": [RunError(stage="config", message=message).model_dump()]}
                )
    if stop:
        return
    try:
        await _stage(svc, run_id, "ingest", RunState.INGESTING, _ingest)
        await _stage(svc, run_id, "profile", RunState.PROFILING, _profile)
        bible = await _stage(svc, run_id, "bible", RunState.BUILDING_BIBLE, _bible)
    except _Stop:
        return
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        proceed = not _cancel_if_requested(run) and run.state == RunState.BUILDING_BIBLE
        if proceed:
            if bible.get("checkpoint"):
                run.state = RunState.AWAITING_BIBLE_APPROVAL
                proceed = False
            else:
                run.state = RunState.TRANSLATING
    if proceed:
        await translate_run(svc, run_id)


# ---------------------------------------------------------------- chunks

@_job
async def translate_run(svc: WorkerServices, run_id: uuid.UUID) -> None:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        if _cancel_if_requested(run) or run.state != RunState.TRANSLATING:
            return
        chunks = await runs.load_chunks(s, run_id)
        finished = {
            r.chunk_id
            for r in await runs.stage_rows(s, run_id)
            if r.stage == "score" and r.status in (runs.COMPLETE, runs.FAILED)
        }
    pending = [c.id for c in chunks if c.id not in finished]
    for chunk_id in pending:
        await svc.queue.defer(
            TASK_PROCESS_CHUNK,
            lock=f"{run_id}:{chunk_id}",
            run_id=str(run_id),
            chunk_id=chunk_id,
        )
    if not pending:
        await _maybe_finish(svc, run_id)


@_job
async def process_chunk_job(svc: WorkerServices, run_id: uuid.UUID, chunk_id: str) -> None:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        if _cancel_if_requested(run) or run.state != RunState.TRANSLATING:
            return
        done = await runs.get_stage(s, run_id, "score", chunk_id)
        finished = done is not None and done.status in (runs.COMPLETE, runs.FAILED)
        if not finished:
            chunks = await runs.load_chunks(s, run_id)
            _, segments = await runs.load_source(s, run.document_id)
            bible = Bible.model_validate(
                (await runs.get_bible_version(s, run.bible_version_id)).content
            )
            profile = DocumentProfile.model_validate(run.profile)
            tier = Reliability(run.language_tier)
            target_lang = run.target_lang
            await runs.begin_stage(s, run_id, "translate", chunk_id)
    if finished:
        await _maybe_finish(svc, run_id)
        return

    calls = svc.call_log()

    async def on_stage(stage: str) -> None:
        # with a split chunk this fires once per half; the rows settle when the chunk ends
        following = _NEXT_STAGE[stage]
        async with svc.db.session() as s, s.begin():
            row = await runs.get_stage(s, run_id, stage, chunk_id)
            if row is not None and row.status == runs.RUNNING:
                runs.finish_stage(row, None)
            nxt = await runs.get_stage(s, run_id, following, chunk_id)
            if nxt is None or nxt.status != runs.RUNNING:
                await runs.begin_stage(s, run_id, following, chunk_id)

    try:
        ordinal = [c.id for c in chunks].index(chunk_id)
        ci = build_chunk_inputs(chunks, segments)[ordinal]
        outcome = await process_chunk(
            svc.context(calls, target_lang),
            svc.scorer,
            profile,
            bible,
            ci,
            tier=tier,
            diverse=model_diversity(svc.cfg),
            on_stage=on_stage,
        )
    except DB_ERRORS:
        raise
    except Exception as e:
        log.exception("chunk failed", extra={"run_id": str(run_id), "chunk_id": chunk_id})
        await _save_chunk(svc, run_id, chunk_id, None, None, calls, error=_internal(e))
    else:
        await _save_chunk(svc, run_id, chunk_id, ordinal, outcome, calls)
    await _maybe_finish(svc, run_id)


async def _save_chunk(
    svc: WorkerServices,
    run_id: uuid.UUID,
    chunk_id: str,
    ordinal: int | None,
    outcome: ChunkOutcome | None,
    calls: CallLog,
    *,
    error: str | None = None,
) -> None:
    async with svc.db.session() as s, s.begin():
        score = await runs.get_stage(s, run_id, "score", chunk_id)
        if score is None:
            score = await runs.begin_stage(s, run_id, "score", chunk_id)
        if outcome is not None and ordinal is not None:
            await outputs.delete_chunk_outputs(s, run_id, [chunk_id])
            await outputs.save_chunk_outcome(s, run_id, ordinal, outcome)
            for stage in ("translate", "reconcile"):
                row = await runs.get_stage(s, run_id, stage, chunk_id)
                if row is not None and row.status == runs.RUNNING:
                    runs.finish_stage(row, None)
            runs.finish_stage(score, outcome.model_dump(mode="json"))
        else:
            runs.fail_stage(score, error or "chunk failed")
        await runs.save_llm_calls(s, run_id, calls.records, stage="chunk", chunk_id=chunk_id)


async def _maybe_finish(svc: WorkerServices, run_id: uuid.UUID) -> None:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        ready = not _cancel_if_requested(run) and run.state == RunState.TRANSLATING
        if ready:
            total = len(await runs.load_chunks(s, run_id))
            ready = await runs.finished_chunk_count(s, run_id) >= total
            if ready:
                run.state = RunState.SWEEPING  # the state change guards against a second finish
    if ready:
        await svc.queue.defer(TASK_FINISH_RUN, lock=f"{run_id}:finish", run_id=str(run_id))


# ---------------------------------------------------------------- sweep and final state

@_job
async def finish_run(svc: WorkerServices, run_id: uuid.UUID) -> None:
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        if _cancel_if_requested(run) or run.state != RunState.SWEEPING:
            return
        swept = await runs.get_stage(s, run_id, "sweep")
        needed = swept is None or swept.status != runs.COMPLETE
        if needed:
            profile = DocumentProfile.model_validate(run.profile)
            enabled = svc.cfg.profiles.sweep.get(profile.pipeline_profile, True)
            row_id = (await runs.begin_stage(s, run_id, "sweep")).id
            target_lang = run.target_lang
            if enabled:
                stored = await outputs.load_spans(s, run_id)
                _, segments = await runs.load_source(s, run.document_id)
                bible = Bible.model_validate(
                    (await runs.get_bible_version(s, run.bible_version_id)).content
                )
    if needed:
        calls = svc.call_log()
        changes: list[SpanChange] = []
        if enabled:
            errors: list[RunError]
            try:
                result = await run_sweep(
                    svc.context(calls, target_lang), profile, bible, stored.spans, segments
                )
                changes, errors = result.changes, result.errors
            except DB_ERRORS:
                raise
            except Exception as e:
                log.exception("sweep failed", extra={"run_id": str(run_id), "stage": "sweep"})
                errors = [RunError(stage="sweep", message=_internal(e))]
            output: dict[str, Any] = {
                "errors": [e.model_dump() for e in errors],
                "revised": len(changes),
            }
        else:
            output = {"errors": [], "skipped": True}
        async with svc.db.session() as s, s.begin():
            if changes:
                await outputs.apply_sweep(s, stored.row_ids, changes)
            row = await s.get(StageResult, row_id)
            assert row is not None
            runs.finish_stage(row, output)
            await runs.save_llm_calls(s, run_id, calls.records, stage="sweep")
    async with svc.db.session() as s, s.begin():
        run = await runs.get_run(s, run_id, lock=True)
        if _cancel_if_requested(run) or run.state != RunState.SWEEPING:
            return
        problems = await runs.total_error_count(s, run_id)
        run.state = RunState.COMPLETE_WITH_ERRORS if problems else RunState.COMPLETE
        run.finished_at = utcnow()
    log.info("run finished", extra={"run_id": str(run_id), "state": str(run.state)})


async def run_job(svc: WorkerServices, task: str, kwargs: dict[str, str]) -> None:
    run_id = uuid.UUID(kwargs["run_id"])
    if task == TASK_START_RUN:
        await start_run(svc, run_id)
    elif task == TASK_TRANSLATE_RUN:
        await translate_run(svc, run_id)
    elif task == TASK_PROCESS_CHUNK:
        await process_chunk_job(svc, run_id, kwargs["chunk_id"])
    elif task == TASK_FINISH_RUN:
        await finish_run(svc, run_id)
    else:
        raise ValueError(f"unknown job '{task}'")
```

Implementation notes:
- `process_chunk` must be looked up as the module attribute `execute.process_chunk` at call time. It is: the module imports the name and calls it through module globals, so `monkeypatch.setattr(execute, "process_chunk", …)` works. The same applies to `segment_document`.
- `_job` uses PEP 695 generics (`def _job[**P]`), which Python 3.12 supports.
- The checkpoint test approves an edited bible with a locked `chat → cat` entry. The synthetic translator writes `[en] Le chat dort.`, so `bible_check` raises a `terminology_violation`, and the long-form sweep fixes it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make test-engine`

Expected: PASS; coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add engine
git commit -m "feat(worker): idempotent job bodies for the run lifecycle: stages, chunks, sweep, cancel"
```

---
### Task 11: procrastinate wiring, worker startup and JSON logs

**Files:**
- Create:
  - `engine/coglossa/worker/app.py`
  - `engine/coglossa/logs.py`
- Modify:
  - `engine/coglossa/scoring/client.py` (`probe_scorer`)
  - `engine/coglossa/cli.py` (`_check_scorer` uses `probe_scorer`; stderr text unchanged)
- Test: `engine/tests/worker/test_app.py`, `engine/tests/test_logs.py`

**Interfaces:**
- Consumes: `execute.run_job`, `WorkerServices`, `DB_ERRORS` (Task 10); `database_url`, `to_libpq_url`, `redact`, `migration_state`
- Produces:
  - **In `coglossa.worker.app`:**
    - `QUEUE = "coglossa"`, `SERVICES_KEY = "coglossa_services"`, `TASK_REQUEUE_STALLED = "coglossa.requeue_stalled"`
    - module-level `app: procrastinate.App` with the four job tasks plus the periodic `requeue_stalled` task (every minute)
    - `open_app(url)`: an async context manager that swaps in a `PsycopgConnector` for `url` and opens the app
    - `ProcrastinateQueue(app)`: implements `JobQueue` and swallows `AlreadyEnqueued`
    - `async requeue_stalled(manager, seconds: float) -> int`
    - `async build_services(cfg, queue) -> WorkerServices`
    - `async run_worker(cfg, *, burst=False, install_signal_handlers=True, services_factory=build_services) -> None`:
      - refuses to start (`ConfigError`) when the database is unreachable or not migrated
      - `burst=True` returns once the queue is empty
  - **In `coglossa.scoring.client`:** `async probe_scorer(scorer, cfg) -> tuple[ScorerHealth | None, list[str]]`, which returns the health and plain-language warnings (the text the CLI printed in M2, without the `warning: ` prefix).
  - **In `coglossa.logs`:**
    - `JsonFormatter`: one JSON object per line with `ts`, `level`, `logger`, `message`, plus `run_id`, `chunk_id`, `stage`, `state`, `duration_ms` when given in `extra`, and `exception`
    - `configure_json_logging(level=logging.INFO, stream=None) -> None`: attaches one handler to the `coglossa` and `procrastinate` loggers. It is idempotent and leaves the root logger alone.

How the procrastinate API is used (checked against procrastinate 3.10):
- `App.replace_connector(connector)` is a context manager. `with_connector` is deprecated: its tasks still point at the old app.
- `run_worker_async(queues=…, concurrency=…, wait=…, additional_context=…, install_signal_handlers=…, delete_jobs="successful")`. Jobs read services from `context.additional_context[SERVICES_KEY]` with `pass_context=True`.
- `configure_task(name, queue=…, queueing_lock=…)` raises `procrastinate.exceptions.AlreadyEnqueued` when the lock is already queued.
- `job_manager.get_stalled_jobs(seconds_since_heartbeat=…)` and `job_manager.retry_job(job)`.
- `RetryStrategy(max_attempts=…, wait=…, exponential_wait=…, retry_exceptions={…})`.
- The periodic decorator `@app.periodic(cron="* * * * *", periodic_id="…")` goes above `@app.task(…)`, and the task takes `timestamp: int`.

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_logs.py`:

```python
import io
import json
import logging

from coglossa.logs import JsonFormatter, configure_json_logging


def test_json_lines_carry_context_fields():
    stream = io.StringIO()
    configure_json_logging(stream=stream)
    configure_json_logging(stream=stream)  # idempotent: still one handler
    log = logging.getLogger("coglossa.worker")
    log.info("stage complete", extra={"run_id": "r1", "stage": "ingest", "duration_ms": 12})
    try:
        raise ValueError("bad")
    except ValueError:
        log.exception("failed", extra={"chunk_id": "c1"})
    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert first["message"] == "stage complete" and first["level"] == "info"
    assert (first["run_id"], first["stage"], first["duration_ms"]) == ("r1", "ingest", 12)
    assert first["logger"] == "coglossa.worker" and first["ts"].endswith("+00:00")
    assert second["chunk_id"] == "c1" and "ValueError: bad" in second["exception"]
    assert not any(isinstance(h.formatter, JsonFormatter) for h in logging.getLogger().handlers)
```

`engine/tests/worker/test_app.py`:

```python
import dataclasses

import pytest
from sqlalchemy import text

from coglossa import service
from coglossa.config import ConfigError
from coglossa.schema import PipelineProfile, RunState
from coglossa.store import runs
from coglossa.store.db import Database
from coglossa.store.migrate import MigrationState
from coglossa.worker import app as worker_app
from coglossa.worker.app import ProcrastinateQueue, open_app, requeue_stalled, run_worker
from tests.worker.conftest import make_services


@pytest.fixture
def worker_env(db, fake_backend, tmp_path, monkeypatch, pg_url):
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    svc = make_services(db, fake_backend, tmp_path)

    async def factory(cfg, queue):
        # its own Database: run_worker closes the services when it stops
        return dataclasses.replace(svc, db=Database(pg_url), queue=queue)

    return svc, factory


async def _submit(svc, tmp_path, pg_url, **opts):
    path = tmp_path / "story.txt"
    path.write_text("Le chat dort. Il pleut.", encoding="utf-8")
    async with open_app(pg_url) as opened:
        return await service.submit_run(
            svc.db, svc.storage, ProcrastinateQueue(opened), svc.cfg, svc.prompts, path,
            service.SubmitOptions(project="p", target_lang="en", source_lang="fr", **opts),
        )


async def _state(db, run_id):
    async with db.session() as s:
        return RunState((await runs.get_run(s, run_id)).state)


async def test_the_worker_completes_a_queued_run(worker_env, tmp_path, pg_url):
    svc, factory = worker_env
    run_id = await _submit(svc, tmp_path, pg_url)
    await run_worker(svc.cfg, burst=True, install_signal_handlers=False, services_factory=factory)
    assert await _state(svc.db, run_id) is RunState.COMPLETE


async def test_checkpoint_then_approval_through_the_queue(worker_env, tmp_path, pg_url):
    svc, factory = worker_env
    run_id = await _submit(svc, tmp_path, pg_url, pipeline_profile=PipelineProfile.LONG_FORM)
    await run_worker(svc.cfg, burst=True, install_signal_handlers=False, services_factory=factory)
    assert await _state(svc.db, run_id) is RunState.AWAITING_BIBLE_APPROVAL
    async with open_app(pg_url) as opened:
        await service.approve_bible(svc.db, ProcrastinateQueue(opened), run_id)
    await run_worker(svc.cfg, burst=True, install_signal_handlers=False, services_factory=factory)
    assert await _state(svc.db, run_id) is RunState.COMPLETE


async def test_queueing_lock_deduplicates(db, pg_url):
    async with open_app(pg_url) as opened:
        queue = ProcrastinateQueue(opened)
        await queue.defer(service.TASK_FINISH_RUN, lock="r:finish", run_id="r")
        await queue.defer(service.TASK_FINISH_RUN, lock="r:finish", run_id="r")
    async with db.engine.connect() as conn:
        count = await conn.scalar(text("SELECT count(*) FROM procrastinate_jobs"))
    assert count == 1


async def test_requeue_stalled():
    class Manager:
        def __init__(self):
            self.retried = []

        async def get_stalled_jobs(self, *, seconds_since_heartbeat):
            assert seconds_since_heartbeat == 60
            return ["job-1", "job-2"]

        async def retry_job(self, job):
            self.retried.append(job)

    manager = Manager()
    assert await requeue_stalled(manager, 60) == 2
    assert manager.retried == ["job-1", "job-2"]


async def test_worker_refuses_an_unmigrated_database(worker_env, monkeypatch):
    svc, factory = worker_env
    monkeypatch.setattr(worker_app, "migration_state", lambda url: MigrationState(None, "0001"))
    with pytest.raises(ConfigError, match="coglossa db upgrade"):
        await run_worker(svc.cfg, burst=True, install_signal_handlers=False, services_factory=factory)


async def test_worker_refuses_an_unreachable_database(worker_env, monkeypatch):
    svc, factory = worker_env
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", "postgresql://u:secret@127.0.0.1:9/x")
    with pytest.raises(ConfigError, match="cannot connect") as info:
        await run_worker(svc.cfg, burst=True, install_signal_handlers=False, services_factory=factory)
    assert "secret" not in str(info.value)


async def test_build_services(tmp_path, monkeypatch, pg_url, make_config, caplog):
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    cfg = make_config(
        storage={"path": str(tmp_path / "files")},
        scorer={"url": "http://127.0.0.1:9", "connect_timeout_s": 0.2, "timeout_s": 0.5},
    )
    svc = await worker_app.build_services(cfg, queue=None)
    try:
        assert svc.scorer is not None and svc.scorer_health is None
        assert set(svc.backends) == {"fake"}
        assert "scorer unavailable" in caplog.text
    finally:
        await svc.aclose()
```

Move the scorer-warning tests from `tests/test_cli_scoring.py` that target `_check_scorer` so they call `probe_scorer` directly: they assert the returned warnings, without the `warning: ` prefix. Keep one CLI-level test asserting the printed `warning: …` lines.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/worker/test_app.py tests/test_logs.py -q --no-cov`

Expected: FAIL with `ModuleNotFoundError: coglossa.worker.app` and `coglossa.logs`.

- [ ] **Step 3: Implement**

`engine/coglossa/logs.py`:

```python
"""Structured JSON logs (spec §7.6) for long-running processes (the worker)."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import TextIO

_FIELDS = ("run_id", "chunk_id", "stage", "state", "duration_ms")
_LOGGERS = ("coglossa", "procrastinate")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in _FIELDS:
            if hasattr(record, name):
                data[name] = getattr(record, name)
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_json_logging(level: int = logging.INFO, stream: TextIO | None = None) -> None:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = [h for h in logger.handlers if not isinstance(h.formatter, JsonFormatter)]
        logger.addHandler(handler)
        logger.setLevel(level)
```

Add to `engine/coglossa/scoring/client.py`:

```python
async def probe_scorer(
    scorer: Scorer, cfg: CoglossaConfig
) -> tuple[ScorerHealth | None, list[str]]:
    """Ask the scorer what it has loaded; warnings are plain-language and unprefixed."""
    try:
        health = await scorer.health()
    except ScorerUnavailableError as e:
        sc = cfg.scorer
        where = (sc.url or sc.kind) if sc is not None else "unknown"
        return None, [f"scorer unavailable at {where}: {e}; model-based scores will be empty"]
    warnings: list[str] = []
    if health.qe_model is None:
        warnings.append(
            "scorer has no quality-estimation model loaded; qe signals will be empty"
        )
    if health.embed_model is None:
        warnings.append("scorer has no embedding model loaded; xsim signals will be empty")
    return health, warnings
```

In `cli.py`, `_check_scorer` becomes:

```python
async def _check_scorer(scorer: Scorer, cfg: CoglossaConfig) -> ScorerHealth | None:
    health, warnings = await probe_scorer(scorer, cfg)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return health
```

`engine/coglossa/worker/app.py`:

```python
"""procrastinate wiring (spec §2.1, §7.3). Queue jobs call coglossa.worker.execute; a periodic
job re-queues jobs whose worker stopped sending heartbeats (a crashed or killed worker), and
idempotent jobs make the re-run safe."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import procrastinate
from procrastinate import JobContext, RetryStrategy
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy.exc import InterfaceError, OperationalError

from coglossa.config import CoglossaConfig, ConfigError
from coglossa.llm.registry import build_backends
from coglossa.prompts.check import validate_prompts
from coglossa.prompts.library import PromptLibrary
from coglossa.scoring.client import build_scorer, probe_scorer
from coglossa.scoring.tiers import LanguageTiers
from coglossa.service import (
    TASK_FINISH_RUN,
    TASK_PROCESS_CHUNK,
    TASK_START_RUN,
    TASK_TRANSLATE_RUN,
    JobQueue,
)
from coglossa.storage import build_storage
from coglossa.store.db import Database, database_url, redact, to_libpq_url
from coglossa.store.migrate import migration_state
from coglossa.worker.execute import DB_ERRORS, WorkerServices, run_job

log = logging.getLogger("coglossa.worker")

QUEUE = "coglossa"
SERVICES_KEY = "coglossa_services"
TASK_REQUEUE_STALLED = "coglossa.requeue_stalled"
_DB_RETRY = RetryStrategy(
    max_attempts=5,
    wait=5,
    exponential_wait=2,
    retry_exceptions={OperationalError, InterfaceError},
)

# The connector is replaced by open_app(); this one is never opened.
app = procrastinate.App(connector=procrastinate.PsycopgConnector())


def _services(context: JobContext) -> WorkerServices:
    return context.additional_context[SERVICES_KEY]


@app.task(name=TASK_START_RUN, queue=QUEUE, pass_context=True, retry=_DB_RETRY)
async def start_run_task(context: JobContext, run_id: str) -> None:
    await run_job(_services(context), TASK_START_RUN, {"run_id": run_id})


@app.task(name=TASK_TRANSLATE_RUN, queue=QUEUE, pass_context=True, retry=_DB_RETRY)
async def translate_run_task(context: JobContext, run_id: str) -> None:
    await run_job(_services(context), TASK_TRANSLATE_RUN, {"run_id": run_id})


@app.task(name=TASK_PROCESS_CHUNK, queue=QUEUE, pass_context=True, retry=_DB_RETRY)
async def process_chunk_task(context: JobContext, run_id: str, chunk_id: str) -> None:
    await run_job(
        _services(context), TASK_PROCESS_CHUNK, {"run_id": run_id, "chunk_id": chunk_id}
    )


@app.task(name=TASK_FINISH_RUN, queue=QUEUE, pass_context=True, retry=_DB_RETRY)
async def finish_run_task(context: JobContext, run_id: str) -> None:
    await run_job(_services(context), TASK_FINISH_RUN, {"run_id": run_id})


async def requeue_stalled(manager: Any, seconds: float) -> int:
    jobs = await manager.get_stalled_jobs(seconds_since_heartbeat=seconds)
    for job in jobs:
        await manager.retry_job(job)
    if jobs:
        log.warning("re-queued %d job(s) from a worker that stopped responding", len(jobs))
    return len(jobs)


@app.periodic(cron="* * * * *", periodic_id="requeue-stalled")
@app.task(name=TASK_REQUEUE_STALLED, queue=QUEUE, pass_context=True)
async def requeue_stalled_task(context: JobContext, timestamp: int) -> None:
    await requeue_stalled(context.app.job_manager, _services(context).cfg.worker.stalled_after_s)


@contextlib.asynccontextmanager
async def open_app(url: str) -> AsyncIterator[procrastinate.App]:
    connector = procrastinate.PsycopgConnector(conninfo=to_libpq_url(url))
    with app.replace_connector(connector) as opened:
        async with opened.open_async():
            yield opened


class ProcrastinateQueue:
    def __init__(self, opened: procrastinate.App) -> None:
        self._app = opened

    async def defer(self, task: str, *, lock: str | None = None, **kwargs: str) -> None:
        try:
            await self._app.configure_task(task, queue=QUEUE, queueing_lock=lock).defer_async(
                **kwargs
            )
        except AlreadyEnqueued:
            pass  # the same job is already waiting; idempotent by design


async def build_services(cfg: CoglossaConfig, queue: JobQueue) -> WorkerServices:
    prompts = PromptLibrary(cfg.prompts_dir)
    validate_prompts(cfg, prompts)
    tiers = LanguageTiers.load(cfg.scoring.language_tiers_file)
    storage = build_storage(cfg.storage)
    storage.check_writable()
    backends = build_backends(cfg)
    scorer = build_scorer(cfg)
    health = None
    if scorer is not None:
        health, warnings = await probe_scorer(scorer, cfg)
        for warning in warnings:
            log.warning(warning)
    return WorkerServices(
        cfg=cfg,
        db=Database(database_url(cfg)),
        storage=storage,
        queue=queue,
        backends=backends,
        prompts=prompts,
        tiers=tiers,
        scorer=scorer,
        scorer_health=health,
    )


ServicesFactory = Callable[[CoglossaConfig, JobQueue], Awaitable[WorkerServices]]


async def run_worker(
    cfg: CoglossaConfig,
    *,
    burst: bool = False,
    install_signal_handlers: bool = True,
    services_factory: ServicesFactory = build_services,
) -> None:
    url = database_url(cfg)
    try:
        state = await asyncio.to_thread(migration_state, url)
    except DB_ERRORS as e:
        reason = str(getattr(e, "orig", None) or e).strip().splitlines()[0]
        raise ConfigError(f"cannot connect to the database at {redact(url)}: {reason}") from e
    if not state.up_to_date:
        raise ConfigError(
            f"the database schema is at revision {state.current or 'none'} but this version "
            f"needs {state.head}: run `coglossa db upgrade`"
        )
    async with open_app(url) as opened:
        services = await services_factory(cfg, ProcrastinateQueue(opened))
        try:
            await opened.run_worker_async(
                queues=[QUEUE],
                concurrency=cfg.worker.concurrency,
                wait=not burst,
                name="coglossa-worker",
                additional_context={SERVICES_KEY: services},
                install_signal_handlers=install_signal_handlers,
                delete_jobs="successful",
            )
        finally:
            await services.aclose()
```

In `test_build_services`, `queue=None` is acceptable because the services are only inspected and closed. Pass `RecordingQueue()` instead if the type checker or a reviewer prefers.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make test-engine`

Expected: PASS; coverage ≥ 95%. The task bodies are covered by the two worker runs. `requeue_stalled_task` runs once at worker start, because procrastinate defers periodic jobs for the current minute. If coverage shows it unexecuted, add a test that calls `requeue_stalled_task` through a minimal stand-in context: an object with `.app.job_manager` and `.additional_context`.

- [ ] **Step 5: Commit**

```bash
git add engine
git commit -m "feat(worker): procrastinate tasks, stalled-job recovery, worker startup checks, JSON logs"
```

---

### Task 12: CLI commands for queued runs

**Files:**
- Create: `engine/coglossa/cli_ops.py`
- Modify: `engine/coglossa/cli.py` (register and dispatch the new commands)
- Test: `engine/tests/test_cli_ops.py`

**Interfaces:**
- Consumes: `service.*` (Task 9), `run_worker`, `open_app`, `ProcrastinateQueue` (Task 11), `upgrade` (Task 6), `configure_json_logging`, and `_load_bible` and `_write_report` from `cli.py`
- Produces:
  - `add_commands(sub) -> None` and `COMMANDS: dict[str, Callable[[argparse.Namespace], int]]`
  - `run_command(args) -> int`
- Commands (each takes `--config PATH`, default `coglossa.yaml`):

| Command | Does | Prints / exit |
|---|---|---|
| `db upgrade` | `upgrade(database_url(cfg))` | `database at <redacted url> is at revision 0001` / 0 |
| `worker [--burst]` | `configure_json_logging()`; `run_worker(cfg, burst=…)` | JSON logs on stderr / 0, 130 on Ctrl-C |
| `submit FILE --target-lang L [--source-lang] [--profile] [--project NAME] [--checkpoint \| --no-checkpoint] [--use-project-bible]` | `submit_run` | `run <id> queued` and next-step hints / 0 |
| `status RUN` | `run_progress` | state, chunk counts, error, next step / 0 |
| `bible RUN [--out FILE]` | `run_bible` | JSON to stdout or file / 0 |
| `approve-bible RUN [--bible FILE]` | `approve_bible` | `bible version N approved; translation queued` / 0 |
| `cancel RUN` | `cancel_run` | `run <id>: cancelled` or `cancel requested; running work stops after the current passage` / 0 |
| `retry RUN` | `retry_run` | `run <id>: <message>` / 0 |
| `report RUN [--out DIR]` | `build_report` then `cli._write_report` | as `coglossa run` / 0, or 3 with errors |
| `delete-project NAME --yes` | `delete_project` | `project NAME deleted` / 0; without `--yes`: error / 1 |

- Errors: `ConfigError`, `IngestError`, `StorageError`, `NotFoundError`, `InvalidRunStateError` → `error: <message>`, exit 1.
  - A database connection error → `error: cannot reach the database: <reason>` (with the password redacted), exit 1.
  - A run id that is not a UUID → `error: '<text>' is not a run id`, exit 1.
  - Anything else → `error: internal error: <Type>: <message>`, exit 1.
  - Ctrl-C → exit 130.

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_cli_ops.py`:

```python
import json
import re
from pathlib import Path

import pytest
import yaml

from coglossa.cli import main
from coglossa.testing.fake_openai import FakeOpenAIServer


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
            "profiler_bible": {"model": "fake-model"},
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
    assert code == 0 and "at revision 0001" in out and "coglossa:coglossa@" not in out


def test_submit_work_status_report(setup, capsys, tmp_path):
    config, doc = setup
    code, out, _ = _cli(capsys, "submit", str(doc), "--target-lang", "en", "--source-lang", "fr", "--config", str(config))
    assert code == 0
    run_id = _run_id(out)
    assert "coglossa worker" in out and f"coglossa status {run_id}" in out
    code, out, _ = _cli(capsys, "status", run_id, "--config", str(config))
    assert code == 0 and f"run {run_id}: created" in out
    code, _, _ = _cli(capsys, "worker", "--burst", "--config", str(config))
    assert code == 0
    code, out, _ = _cli(capsys, "status", run_id, "--config", str(config))
    assert "complete" in out and "score 1/1" in out and f"coglossa report {run_id}" in out
    code, out, _ = _cli(capsys, "report", run_id, "--out", str(tmp_path / "out"), "--config", str(config))
    assert code == 0 and "complete: 2 spans" in out
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["run_id"] == run_id
    code, _, err = _cli(capsys, "retry", run_id, "--config", str(config))
    assert code == 1 and "nothing to retry" in err
    code, _, err = _cli(capsys, "cancel", run_id, "--config", str(config))
    assert code == 1 and "already complete" in err


def test_checkpoint_review_and_approval(setup, capsys, tmp_path):
    config, doc = setup
    _, out, _ = _cli(capsys, "submit", str(doc), "--target-lang", "en", "--profile", "long-form", "--config", str(config))
    run_id = _run_id(out)
    _cli(capsys, "worker", "--burst", "--config", str(config))
    code, out, _ = _cli(capsys, "status", run_id, "--config", str(config))
    assert "awaiting_bible_approval" in out and f"coglossa bible {run_id}" in out
    bible_path = tmp_path / "bible.json"
    code, _, _ = _cli(capsys, "bible", run_id, "--out", str(bible_path), "--config", str(config))
    assert code == 0
    bible = json.loads(bible_path.read_text(encoding="utf-8"))
    bible["glossary"].append({"term": "chat", "rendering": "cat", "notes": "", "locked": True})
    bible_path.write_text(json.dumps(bible), encoding="utf-8")
    code, out, _ = _cli(capsys, "approve-bible", run_id, "--bible", str(bible_path), "--config", str(config))
    assert code == 0 and "bible version 2 approved" in out
    _cli(capsys, "worker", "--burst", "--config", str(config))
    code, out, _ = _cli(capsys, "report", run_id, "--out", str(tmp_path / "o2"), "--config", str(config))
    assert code == 0 and "1 revised by the sweep" in out


def test_bible_prints_json_to_stdout(setup, capsys):
    config, doc = setup
    _, out, _ = _cli(capsys, "submit", str(doc), "--target-lang", "en", "--checkpoint", "--config", str(config))
    run_id = _run_id(out)
    _cli(capsys, "worker", "--burst", "--config", str(config))
    code, out, _ = _cli(capsys, "bible", run_id, "--config", str(config))
    assert code == 0 and json.loads(out)["summary"]


def test_cancel_and_retry_a_queued_run(setup, capsys):
    config, doc = setup
    _, out, _ = _cli(capsys, "submit", str(doc), "--target-lang", "en", "--config", str(config))
    run_id = _run_id(out)
    code, out, _ = _cli(capsys, "cancel", run_id, "--config", str(config))
    assert code == 0 and "cancelled" in out
    code, _, err = _cli(capsys, "retry", run_id, "--config", str(config))
    assert code == 1 and "submit a new run" in err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["status", "not-a-uuid"], "'not-a-uuid' is not a run id"),
        (["status", "00000000-0000-0000-0000-000000000000"], "not found"),
        (["delete-project", "novel"], "pass --yes"),
        (["approve-bible", "00000000-0000-0000-0000-000000000000"], "not found"),
    ],
)
def test_errors_are_one_line(setup, capsys, argv, message):
    config, _ = setup
    code, _, err = _cli(capsys, *argv, "--config", str(config))
    assert code == 1 and message in err and "Traceback" not in err


def test_delete_project(setup, capsys):
    config, doc = setup
    _cli(capsys, "submit", str(doc), "--target-lang", "en", "--project", "novel", "--config", str(config))
    code, out, _ = _cli(capsys, "delete-project", "novel", "--yes", "--config", str(config))
    assert code == 0 and "project novel deleted" in out


def test_missing_database_configuration(setup, capsys, monkeypatch):
    config, doc = setup
    monkeypatch.delenv("COGLOSSA_DATABASE_URL")
    code, _, err = _cli(capsys, "submit", str(doc), "--target-lang", "en", "--config", str(config))
    assert code == 1 and "set COGLOSSA_DATABASE_URL" in err


def test_unreachable_database(setup, capsys, monkeypatch):
    config, doc = setup
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", "postgresql://u:secret@127.0.0.1:9/x")
    code, _, err = _cli(capsys, "status", "00000000-0000-0000-0000-000000000000", "--config", str(config))
    assert code == 1 and "cannot reach the database" in err and "secret" not in err


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
```

Notes:
- The synthetic translation of "Le chat dort." contains "chat" but not "cat", so the locked entry is violated and the long-form sweep fixes it. That is the source of `1 revised by the sweep`.
- `coglossa worker` configures JSON logging on the `coglossa` and `procrastinate` loggers. Tests are unaffected because the root logger is untouched.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/test_cli_ops.py -q --no-cov`

Expected: FAIL. argparse rejects `submit` (invalid choice), exiting with code 2.

- [ ] **Step 3: Implement**

`engine/coglossa/cli_ops.py`:

```python
"""CLI commands for queued runs (spec §7): database migration, the worker, and the run
lifecycle. They talk to Postgres; `coglossa run` does not need any of them."""

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from coglossa import service
from coglossa.config import CoglossaConfig, ConfigError, load_config
from coglossa.ingest.base import IngestError
from coglossa.logs import configure_json_logging
from coglossa.prompts.check import validate_prompts
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import PipelineProfile, RunState
from coglossa.storage import StorageError, build_storage
from coglossa.store.db import Database, database_url, redact
from coglossa.store.migrate import migration_state, upgrade
from coglossa.store.runs import NotFoundError
from coglossa.worker.app import ProcrastinateQueue, open_app, run_worker
from coglossa.worker.execute import DB_ERRORS

T = TypeVar("T")
EXIT_OK, EXIT_FAILED, EXIT_INTERRUPTED = 0, 1, 130
USER_ERRORS = (
    ConfigError,
    IngestError,
    StorageError,
    NotFoundError,
    service.InvalidRunStateError,
)


def _config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=Path("coglossa.yaml"))


def add_commands(sub: argparse._SubParsersAction) -> None:
    db = sub.add_parser("db", help="database maintenance")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    _config_arg(db_sub.add_parser("upgrade", help="create or migrate the database schema"))

    worker = sub.add_parser("worker", help="process queued runs")
    _config_arg(worker)
    worker.add_argument("--burst", action="store_true", help="exit when the queue is empty")

    submit = sub.add_parser("submit", help="queue a document for translation")
    _config_arg(submit)
    submit.add_argument("input", type=Path)
    submit.add_argument("--target-lang", required=True)
    submit.add_argument("--source-lang")
    submit.add_argument("--profile", choices=[p.value for p in PipelineProfile])
    submit.add_argument("--project", default="default", help="documents in a project share a bible")
    checkpoint = submit.add_mutually_exclusive_group()
    checkpoint.add_argument("--checkpoint", dest="checkpoint", action="store_const", const=True)
    checkpoint.add_argument("--no-checkpoint", dest="checkpoint", action="store_const", const=False)
    submit.add_argument(
        "--use-project-bible",
        action="store_true",
        help="use the project's latest approved bible as-is (no bible generation)",
    )

    for name, help_text in (
        ("status", "show a run's progress"),
        ("cancel", "cancel a run (finished passages are kept)"),
        ("retry", "retry failed chunks, or resume a run whose job was lost"),
    ):
        p = sub.add_parser(name, help=help_text)
        _config_arg(p)
        p.add_argument("run_id")

    bible = sub.add_parser("bible", help="print or save a run's bible")
    _config_arg(bible)
    bible.add_argument("run_id")
    bible.add_argument("--out", type=Path)

    approve = sub.add_parser("approve-bible", help="approve a run's bible and start translating")
    _config_arg(approve)
    approve.add_argument("run_id")
    approve.add_argument("--bible", type=Path, help="edited bible JSON (default: as generated)")

    report = sub.add_parser("report", help="write a finished run's report.json")
    _config_arg(report)
    report.add_argument("run_id")
    report.add_argument("--out", type=Path)

    delete = sub.add_parser("delete-project", help="delete a project and everything in it")
    _config_arg(delete)
    delete.add_argument("name")
    delete.add_argument("--yes", action="store_true", help="confirm the deletion")


def _load(args: argparse.Namespace) -> CoglossaConfig:
    cfg, warnings = load_config(args.config)
    for warning in warnings:
        print(f"warning: {warning.message}", file=sys.stderr)
    return cfg


def _run_id(text: str) -> uuid.UUID:
    try:
        return uuid.UUID(text)
    except ValueError:
        raise ConfigError(f"'{text}' is not a run id") from None


def _with_db(cfg: CoglossaConfig, fn: Callable[[Database], Awaitable[T]]) -> T:
    async def go() -> T:
        db = Database(database_url(cfg))
        try:
            return await fn(db)
        finally:
            await db.dispose()

    return asyncio.run(go())


def _with_queue(
    cfg: CoglossaConfig, fn: Callable[[Database, service.JobQueue], Awaitable[T]]
) -> T:
    async def go(db: Database) -> T:
        async with open_app(db.url) as opened:
            return await fn(db, ProcrastinateQueue(opened))

    return _with_db(cfg, go)


def _db(args: argparse.Namespace) -> int:
    cfg = _load(args)
    url = database_url(cfg)
    upgrade(url)
    print(f"database at {redact(url)} is at revision {migration_state(url).head}")
    return EXIT_OK


def _worker(args: argparse.Namespace) -> int:
    cfg = _load(args)
    configure_json_logging()
    asyncio.run(run_worker(cfg, burst=args.burst))
    return EXIT_OK


def _submit(args: argparse.Namespace) -> int:
    cfg = _load(args)
    prompts = PromptLibrary(cfg.prompts_dir)
    validate_prompts(cfg, prompts)
    storage = build_storage(cfg.storage)
    opts = service.SubmitOptions(
        project=args.project,
        target_lang=args.target_lang,
        source_lang=args.source_lang,
        pipeline_profile=PipelineProfile(args.profile) if args.profile else None,
        checkpoint=args.checkpoint,
        use_project_bible=args.use_project_bible,
    )
    run_id = _with_queue(
        cfg, lambda db, q: service.submit_run(db, storage, q, cfg, prompts, args.input, opts)
    )
    print(
        f"run {run_id} queued\n"
        f"  progress: coglossa status {run_id}\n"
        "  a worker must be running: coglossa worker"
    )
    return EXIT_OK


_NEXT = {
    RunState.AWAITING_BIBLE_APPROVAL: (
        "review the bible: coglossa bible {id} --out bible.json, then "
        "coglossa approve-bible {id} [--bible bible.json]"
    ),
    RunState.COMPLETE: "coglossa report {id}",
    RunState.COMPLETE_WITH_ERRORS: "coglossa report {id} (or coglossa retry {id})",
    RunState.FAILED: "fix the cause, then coglossa retry {id}",
}


def _status(args: argparse.Namespace) -> int:
    cfg = _load(args)
    run_id = _run_id(args.run_id)
    p = _with_db(cfg, lambda db: service.run_progress(db, run_id))
    lines = [f"run {run_id}: {p.state.value}"]
    if p.chunks_total:
        done = ", ".join(f"{stage} {n}/{p.chunks_total}" for stage, n in p.chunks_done.items())
        lines.append(f"  chunks: {done} ({p.chunks_with_errors} with errors)")
    if p.error:
        lines.append(f"  error: {p.error}")
    if p.state in _NEXT:
        lines.append(f"  next: {_NEXT[p.state].format(id=run_id)}")
    print("\n".join(lines))
    return EXIT_OK


def _bible(args: argparse.Namespace) -> int:
    cfg = _load(args)
    run_id = _run_id(args.run_id)
    bible = _with_db(cfg, lambda db: service.run_bible(db, run_id))
    text = bible.model_dump_json(indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"bible written to {args.out}")
    else:
        print(text)
    return EXIT_OK


def _approve(args: argparse.Namespace) -> int:
    from coglossa.cli import _load_bible

    cfg = _load(args)
    run_id = _run_id(args.run_id)
    bible = _load_bible(args.bible) if args.bible else None
    version = _with_queue(cfg, lambda db, q: service.approve_bible(db, q, run_id, bible))
    print(f"bible version {version} approved; translation queued")
    return EXIT_OK


def _cancel(args: argparse.Namespace) -> int:
    cfg = _load(args)
    run_id = _run_id(args.run_id)
    state = _with_db(cfg, lambda db: service.cancel_run(db, run_id))
    if state is RunState.CANCELLED:
        print(f"run {run_id}: cancelled")
    else:
        print(f"run {run_id}: cancel requested; running work stops after the current passage")
    return EXIT_OK


def _retry(args: argparse.Namespace) -> int:
    cfg = _load(args)
    run_id = _run_id(args.run_id)
    message = _with_queue(cfg, lambda db, q: service.retry_run(db, q, run_id))
    print(f"run {run_id}: {message}")
    return EXIT_OK


def _report(args: argparse.Namespace) -> int:
    from coglossa.cli import _prepare_out_dir, _write_report

    cfg = _load(args)
    run_id = _run_id(args.run_id)
    out_dir = _prepare_out_dir(args.out or Path("coglossa-out") / str(run_id))
    report = _with_db(cfg, lambda db: service.build_report(db, run_id))
    return _write_report(report, out_dir)


def _delete_project(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ConfigError(
            f"deleting project '{args.name}' removes all of its documents, runs and files; "
            "pass --yes to confirm"
        )
    cfg = _load(args)
    storage = build_storage(cfg.storage)
    _with_db(cfg, lambda db: service.delete_project(db, storage, args.name))
    print(f"project {args.name} deleted")
    return EXIT_OK


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "db": _db,
    "worker": _worker,
    "submit": _submit,
    "status": _status,
    "bible": _bible,
    "approve-bible": _approve,
    "cancel": _cancel,
    "retry": _retry,
    "report": _report,
    "delete-project": _delete_project,
}


def _reason(e: BaseException) -> str:
    original = getattr(e, "orig", None) or e
    lines = str(original).strip().splitlines()
    return lines[0] if lines else type(original).__name__


def run_command(args: argparse.Namespace) -> int:
    try:
        return COMMANDS[args.command](args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except USER_ERRORS as e:
        print(f"error: {e}", file=sys.stderr)
    except DB_ERRORS as e:
        print(f"error: cannot reach the database: {_reason(e)}", file=sys.stderr)
    except Exception as e:  # last resort: never show a traceback to the user
        print(f"error: internal error: {type(e).__name__}: {e}", file=sys.stderr)
    return EXIT_FAILED
```

Connection failures from psycopg used directly (e.g. `upgrade` → Alembic → SQLAlchemy) surface as `sqlalchemy.exc.OperationalError` and are handled by `DB_ERRORS`. libpq error text does not include the password, so `_reason` is safe. The test pins this.

In `engine/coglossa/cli.py`:
- `build_parser`: call `add_commands(sub)` (`from coglossa.cli_ops import add_commands, COMMANDS, run_command`) after the existing subparsers.
- `main`: before the `fake-openai` dispatch, add:

```python
    if args.command in COMMANDS:
        return run_command(args)
```

Also update the module docstring's exit-code line if needed (unchanged: 0, 1, 2, 3, 130).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make test-engine`

Expected: PASS; coverage ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add engine
git commit -m "feat(cli): db upgrade, worker, submit, status, bible, approve-bible, cancel, retry, report, delete-project"
```

---

### Task 13: `coglossa doctor`

**Files:**
- Create:
  - `engine/coglossa/doctor.py`
  - `engine/coglossa/schema/doctor.py`
- Modify:
  - `engine/coglossa/schema/__init__.py`
  - `engine/coglossa/schema/export.py` (`doctor-report`)
  - `engine/coglossa/testing/synthetic.py` (`ping`, `probe` tasks)
  - `engine/coglossa/testing/fake_openai.py` (`context_window`, `max_tokens == 1` → `finish_reason: length`)
  - `engine/coglossa/cli_ops.py` (`doctor` command)
- Regenerate: `schema/doctor-report.schema.json`
- Test: `engine/tests/test_doctor.py`, additions to `engine/tests/testing/test_fake_openai.py`

**Interfaces:**
- Produces:
  - **Schema:**
    - `CheckStatus` (`pass`, `warn`, `fail`)
    - `DoctorCheck(name, status, message, fix="")`
    - `DoctorReport(checks: list[DoctorCheck], ok: bool)`, with `DoctorReport.of(checks)` setting `ok = no FAIL`
  - **`async run_doctor(config_path: Path, *, probe_context=True, backends_factory=build_backends) -> DoctorReport`** in `coglossa.doctor`
  - **CLI:** `coglossa doctor [--config] [--json] [--no-probe]`.
    - Text output is one `PASS |WARN |FAIL ` line per check, with an indented `fix:` line when a fix exists.
    - `--json` prints the `DoctorReport`.
    - Exit 0 when nothing failed, 1 otherwise.
  - **`FakeOpenAIServer(..., context_window: int | None = None)`:** when set, a request whose message characters ÷ 2 exceed it gets HTTP 400 `"This model's maximum context length is N tokens"`.
  - **The fake server and the synthetic responder also handle:**
    - `max_tokens == 1`: the reply has `finish_reason: "length"`
    - payload tasks `ping` → `{"ok": true}` and `probe` → `{"ok": true}`

**The checks** (spec §7.4). Each one is pass/warn/fail with a fix:

| Check name | Pass | Warn | Fail (fix) |
|---|---|---|---|
| `config` | file valid | one per validation warning | `ConfigError` text. The report stops here, since the other checks need a config (fix: "edit the file; see coglossa.example.yaml") |
| `prompts` | every prompt renders | — | `validate_prompts` error (fix: "fix or remove the override in prompts_dir") |
| `tiers` | tiers file loads | — | load error |
| `backend <name>` | `/models` reachable; lists up to 5 served ids | — | unreachable (fix: "check base_url, that the server is running, and network access from this host") |
| `model <key>` | served; test completion OK; schema-constrained output OK when declared; context probe OK | probe inconclusive (any other backend error) | not served (fix: "set models.<key>.name to one of: …"); completion failed; structured output not honoured (fix: "set models.<key>.structured_output to json_object or none"); context too small (fix: "lower models.<key>.context_window") |
| `scorer` | health ok, with models and device | no scorer configured; a model not loaded | unreachable or misconfigured |
| `database` | reachable and at the head revision | URL variable not set (needed only by `worker` and `submit`) | unreachable; not migrated (fix: "run coglossa db upgrade") |
| `storage` | writable | — | not writable |

- **Test completion:**
  - request `ChatRequest(model=<served id>, messages=[system: 'Reply with the JSON object {"ok": true}.', user: render_input({"task": "ping"})], schema_name="Ping", json_schema=PING_SCHEMA, structured_output=NONE, temperature=0, max_tokens=50)`
  - Any reply passes.
- **Structured output** (when the model declares `json_schema` or `json_object`):
  - the same request with the model's declared mode
  - It passes when `json.loads(extract_json(reply))` is an object with a boolean `ok`.
- **Context probe:**
  - The user message is `render_input({"task": "probe", "filler": "a " * n})` with `n = int(context_window * 0.9)`, so the probe is about 90% of the declared window (most tokenizers read `"a "` as one token), with `max_tokens=1`.
  - Any reply passes, and so does an `OutputTruncatedError`: `max_tokens=1` truncates by design.
  - `ContextOverflowError` fails. Any other `BackendError` warns that the probe was inconclusive.
  - `--no-probe` skips it: the probe costs one long prompt per model.
  - Message on failure: `"the server rejected a prompt of about {n} tokens; context_window {w} is larger than it accepts"`.

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_doctor.py`:

```python
import json

import pytest
import yaml
from sqlalchemy.engine import make_url

from coglossa.cli import main
from coglossa.doctor import run_doctor
from coglossa.llm.types import PermanentBackendError
from coglossa.schema import CheckStatus
from coglossa.testing.fake_backend import FakeBackend
from coglossa.testing.fake_openai import FakeOpenAIServer
from coglossa.testing.fake_scorer import FakeScorerServer
from tests.conftest import create_test_database, drop_test_database


def _config(tmp_path, server_url, **extra):
    cfg = {
        "backends": {"local": {"base_url": server_url, "max_concurrency": 2, "timeout_s": 5}},
        "models": {"fake-model": {"backend": "local", "context_window": 4096}},
        "roles": {
            "literal_translator": {"model": "fake-model"},
            "contextual_translator": {"model": "fake-model"},
            "reviewer": {"model": "fake-model"},
            "profiler_bible": {"model": "fake-model"},
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
    for name in ("config", "prompts", "tiers", "backend local", "model fake-model", "scorer", "database", "storage"):
        assert checks[name].status is CheckStatus.PASS, (name, checks[name])
    assert "fake-model" in checks["backend local"].message
    assert "0001" in checks["database"].message
    assert f":{make_url(pg_url).password}@" not in checks["database"].message  # redacted


async def test_model_not_served(tmp_path, server, monkeypatch):
    monkeypatch.delenv("COGLOSSA_DATABASE_URL", raising=False)
    path = _config(tmp_path, server.base_url, models={
        "qwen": {"backend": "local", "context_window": 4096}
    }, roles={r: {"model": "qwen"} for r in ("literal_translator", "contextual_translator", "reviewer", "profiler_bible")})
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
    assert "about 3686 tokens" in model.message and "lower models.fake-model.context_window" == model.fix


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
    path = _config(tmp_path, server.base_url, scorer={"url": "http://127.0.0.1:9", "connect_timeout_s": 0.2})
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
    path = _config(tmp_path, server.base_url, roles={
        "literal_translator": {"model": "fake-model"},
        "contextual_translator": {"model": "fake-model"},
        "reviewer": {"model": "fake-model"},
        "profiler_bible": {"model": "fake-model"},
        "judge": {"model": "fake-model"},
    })
    report = await run_doctor(path, probe_context=False)
    assert any(c.name == "config" and c.status is CheckStatus.WARN for c in report.checks)


def test_cli_text_and_json(tmp_path, server, capsys, monkeypatch):
    monkeypatch.delenv("COGLOSSA_DATABASE_URL", raising=False)
    path = _config(tmp_path, server.base_url)
    assert main(["doctor", "--config", str(path), "--no-probe"]) == 0
    out = capsys.readouterr().out
    assert "PASS  backend local" in out and "WARN  database" in out and "fix: set COGLOSSA_DATABASE_URL" in out
    assert main(["doctor", "--config", str(path), "--json", "--no-probe"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["checks"][0]["name"] == "config"
    bad = _config(tmp_path, "http://127.0.0.1:9/v1")
    assert main(["doctor", "--config", str(bad), "--no-probe"]) == 1
```

Add to `engine/tests/testing/test_fake_openai.py`:
- a request over `context_window` returns 400 with "maximum context length"
- `max_tokens: 1` returns `finish_reason == "length"`
- `ping` and `probe` tasks answer `{"ok": true}`

**Arithmetic for `test_context_window_larger_than_the_server_accepts`:** `n = int(4096 * 0.9) = 3686` filler tokens, 7372 characters, plus a little JSON. The fake server computes total characters ÷ 2 ≈ 3700, which exceeds its 1000-token window, so it returns 400.

**`test_structured_output_not_honoured`:** the first `ping` reply ("OK") satisfies the plain completion check. The second reply (not JSON) fails the structured check, because the default model declares `json_schema`.

**`test_failed_completion_and_inconclusive_probe`:** the scripted `PermanentBackendError` for `probe` is not a `ContextOverflowError`, so the probe warns. Report the model check's overall status as the worst of its sub-checks: FAIL > WARN > PASS. Join the messages with `"; "` and take the fix from the first non-pass sub-check.

`FakeScorerServer(qe_model=None)`: check how the existing `FakeScorerServer` reports health. If it has no switch for a missing QE model, add an optional `qe_model: str | None = "fake-metricx"` parameter used by its `/health` handler, and test it in `tests/testing/test_fake_scorer.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/test_doctor.py tests/testing -q --no-cov`

Expected: FAIL with `ModuleNotFoundError: coglossa.doctor`.

- [ ] **Step 3: Implement**

`engine/coglossa/schema/doctor.py`:

```python
"""`coglossa doctor` results (spec §7.4), shown by the CLI and the System page (M4)."""

from enum import StrEnum

from pydantic import BaseModel


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class DoctorCheck(BaseModel):
    name: str
    status: CheckStatus
    message: str
    fix: str = ""


class DoctorReport(BaseModel):
    checks: list[DoctorCheck]
    ok: bool

    @classmethod
    def of(cls, checks: list[DoctorCheck]) -> "DoctorReport":
        return cls(checks=checks, ok=all(c.status is not CheckStatus.FAIL for c in checks))
```

Export `CheckStatus`, `DoctorCheck` and `DoctorReport` from `coglossa.schema`. Add `"doctor-report": DoctorReport` to `EXPORTED`, then run `make schema`.

Add to `engine/coglossa/testing/synthetic.py`, registering both in `_HANDLERS`:

```python
def _ok(p: Payload) -> Payload:
    return {"ok": True}
```

Register both `"ping": _ok` and `"probe": _ok`.

In `engine/coglossa/testing/fake_openai.py`:
- add `context_window: int | None = None` to `FakeOpenAIServer.__init__`, stored on the server
- in `do_POST`, after parsing the body:

```python
        window = self.server.context_window
        chars = sum(len(m.get("content") or "") for m in body.get("messages", []))
        if window is not None and chars / 2 > window:
            self._send(
                400,
                {"error": {"message": f"This model's maximum context length is {window} tokens"}},
            )
            return
```

- set `finish_reason="length" if body.get("max_tokens") == 1 else "stop"` when building the reply. `chat_completion` already takes `finish_reason`.

`engine/coglossa/doctor.py`:

```python
"""`coglossa doctor` (spec §7.4): check an installation end to end and say how to fix what is
wrong. Every check reports pass, warn or fail with a plain-language fix."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from coglossa.config import CoglossaConfig, ConfigError, ModelConfig, StructuredOutput, load_config
from coglossa.llm.agent import extract_json
from coglossa.llm.payload import render_input
from coglossa.llm.registry import build_backends
from coglossa.llm.types import (
    Backend,
    BackendError,
    ChatMessage,
    ChatRequest,
    ContextOverflowError,
    OutputTruncatedError,
)
from coglossa.prompts.check import validate_prompts
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import CheckStatus, DoctorCheck, DoctorReport
from coglossa.scoring.client import build_scorer, probe_scorer
from coglossa.scoring.tiers import LanguageTiers
from coglossa.storage import StorageError, build_storage
from coglossa.store.db import redact
from coglossa.store.migrate import migration_state
from coglossa.worker.execute import DB_ERRORS

PING_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
_ORDER = {CheckStatus.PASS: 0, CheckStatus.WARN: 1, CheckStatus.FAIL: 2}

BackendsFactory = Callable[[CoglossaConfig], dict[str, Backend]]


def _check(name: str, status: CheckStatus, message: str, fix: str = "") -> DoctorCheck:
    return DoctorCheck(name=name, status=status, message=message, fix=fix)


def _first_line(e: BaseException) -> str:
    original = getattr(e, "orig", None) or e
    lines = str(original).strip().splitlines()
    return lines[0] if lines else type(original).__name__


async def run_doctor(
    config_path: Path,
    *,
    probe_context: bool = True,
    backends_factory: BackendsFactory = build_backends,
) -> DoctorReport:
    try:
        cfg, issues = load_config(config_path)
    except ConfigError as e:
        return DoctorReport.of(
            [_check("config", CheckStatus.FAIL, str(e), "edit the file; see coglossa.example.yaml")]
        )
    checks = [_check("config", CheckStatus.PASS, f"{config_path} is valid")]
    checks += [_check("config", CheckStatus.WARN, i.message) for i in issues]
    checks.append(_prompts(cfg))
    checks.append(_tiers(cfg))
    checks += await _backends(cfg, probe_context, backends_factory)
    checks.append(await _scorer(cfg))
    checks.append(await _database(cfg))
    checks.append(_storage(cfg))
    return DoctorReport.of(checks)


def _prompts(cfg: CoglossaConfig) -> DoctorCheck:
    try:
        validate_prompts(cfg, PromptLibrary(cfg.prompts_dir))
    except ConfigError as e:
        return _check("prompts", CheckStatus.FAIL, str(e), "fix or remove the override in prompts_dir")
    return _check("prompts", CheckStatus.PASS, "every prompt renders")


def _tiers(cfg: CoglossaConfig) -> DoctorCheck:
    try:
        LanguageTiers.load(cfg.scoring.language_tiers_file)
    except ConfigError as e:
        return _check("tiers", CheckStatus.FAIL, str(e), "fix scoring.language_tiers_file")
    return _check("tiers", CheckStatus.PASS, "language tiers load")


async def _backends(
    cfg: CoglossaConfig, probe: bool, factory: BackendsFactory
) -> list[DoctorCheck]:
    try:
        backends = factory(cfg)
    except ConfigError as e:
        return [_check("backends", CheckStatus.FAIL, str(e), "fix the backend kind")]
    checks: list[DoctorCheck] = []
    try:
        for name, backend in backends.items():
            checks += await _backend(cfg, name, backend, probe)
    finally:
        for backend in backends.values():
            await backend.aclose()
    return checks


async def _backend(
    cfg: CoglossaConfig, name: str, backend: Backend, probe: bool
) -> list[DoctorCheck]:
    label = f"backend {name}"
    base_url = cfg.backends[name].base_url
    try:
        served = await backend.list_models()
    except BackendError as e:
        return [
            _check(
                label,
                CheckStatus.FAIL,
                f"cannot list models at {base_url}: {e}",
                "check base_url, that the server is running, and network access from this host",
            )
        ]
    shown = ", ".join(served[:5]) + (", …" if len(served) > 5 else "")
    checks = [_check(label, CheckStatus.PASS, f"reachable at {base_url}; serves {shown}")]
    for key, model in cfg.models.items():
        if model.backend == name:
            checks.append(await _model(key, model, backend, served, probe))
    return checks


def _ping(model_id: str, mode: StructuredOutput) -> ChatRequest:
    return ChatRequest(
        model=model_id,
        messages=[
            ChatMessage(role="system", content='Reply with the JSON object {"ok": true}.'),
            ChatMessage(role="user", content=render_input({"task": "ping"})),
        ],
        schema_name="Ping",
        json_schema=PING_SCHEMA,
        structured_output=mode,
        temperature=0,
        max_tokens=50,
    )


async def _model(
    key: str, model: ModelConfig, backend: Backend, served: list[str], probe: bool
) -> DoctorCheck:
    label = f"model {key}"
    model_id = model.name or key
    if model_id not in served:
        return _check(
            label,
            CheckStatus.FAIL,
            f"'{model_id}' is not served by backend {model.backend}",
            f"set models.{key}.name to one of: {', '.join(served)}",
        )
    parts: list[DoctorCheck] = []
    try:
        await backend.chat(_ping(model_id, StructuredOutput.NONE))
        parts.append(_check(label, CheckStatus.PASS, "test completion succeeded"))
    except BackendError as e:
        parts.append(
            _check(label, CheckStatus.FAIL, f"test completion failed: {e}", "check the model id and server logs")
        )
    if model.structured_output is not StructuredOutput.NONE and parts[0].status is CheckStatus.PASS:
        parts.append(await _structured(label, key, model, model_id, backend))
    if probe:
        parts.append(await _context(label, key, model, model_id, backend))
    worst = max(parts, key=lambda c: _ORDER[c.status])
    fix = next((c.fix for c in parts if c.status is not CheckStatus.PASS), "")
    return _check(label, worst.status, "; ".join(c.message for c in parts), fix)


async def _structured(
    label: str, key: str, model: ModelConfig, model_id: str, backend: Backend
) -> DoctorCheck:
    fix = f"set models.{key}.structured_output to json_object or none"
    try:
        reply = await backend.chat(_ping(model_id, model.structured_output))
        data = json.loads(extract_json(reply.content))
    except BackendError as e:
        return _check(label, CheckStatus.FAIL, f"{model.structured_output.value} request failed: {e}", fix)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
        return _check(
            label,
            CheckStatus.FAIL,
            f"the server did not return the requested JSON ({model.structured_output.value})",
            fix,
        )
    return _check(label, CheckStatus.PASS, f"{model.structured_output.value} output works")


async def _context(
    label: str, key: str, model: ModelConfig, model_id: str, backend: Backend
) -> DoctorCheck:
    n = int(model.context_window * 0.9)
    request = ChatRequest(
        model=model_id,
        messages=[
            ChatMessage(role="system", content="Reply with OK."),
            ChatMessage(role="user", content=render_input({"task": "probe", "filler": "a " * n})),
        ],
        schema_name="Ping",
        json_schema=PING_SCHEMA,
        structured_output=StructuredOutput.NONE,
        temperature=0,
        max_tokens=1,
    )
    try:
        await backend.chat(request)
    except OutputTruncatedError:
        pass  # max_tokens=1: the prompt was accepted
    except ContextOverflowError:
        return _check(
            label,
            CheckStatus.FAIL,
            f"the server rejected a prompt of about {n} tokens; context_window "
            f"{model.context_window} is larger than it accepts",
            f"lower models.{key}.context_window",
        )
    except BackendError as e:
        return _check(label, CheckStatus.WARN, f"context probe inconclusive: {e}")
    return _check(label, CheckStatus.PASS, f"accepted a prompt of about {n} tokens")


async def _scorer(cfg: CoglossaConfig) -> DoctorCheck:
    if cfg.scorer is None or cfg.scorer.kind == "none":
        return _check(
            "scorer",
            CheckStatus.WARN,
            "no scorer configured: qe and xsim signals will be empty",
            "add a scorer section pointing at coglossa-scorer",
        )
    try:
        scorer = build_scorer(cfg)
    except ConfigError as e:
        return _check("scorer", CheckStatus.FAIL, str(e), "fix the scorer section")
    assert scorer is not None
    try:
        health, warnings = await probe_scorer(scorer, cfg)
    finally:
        await scorer.aclose()
    if health is None:
        return _check(
            "scorer", CheckStatus.FAIL, warnings[0], "start coglossa-scorer or fix scorer.url"
        )
    if warnings:
        return _check("scorer", CheckStatus.WARN, "; ".join(warnings), "load the missing model")
    return _check(
        "scorer",
        CheckStatus.PASS,
        f"qe {health.qe_model}, embeddings {health.embed_model}, device {health.device}",
    )


async def _database(cfg: CoglossaConfig) -> DoctorCheck:
    url = cfg.database.url()
    env = cfg.database.url_env
    if url is None:
        return _check(
            "database",
            CheckStatus.WARN,
            f"{env} is not set: needed by coglossa worker and coglossa submit, not by coglossa run",
            f"set {env}=postgresql://user:password@host:5432/coglossa",
        )
    try:
        state = await asyncio.to_thread(migration_state, url)
    except ConfigError as e:
        return _check("database", CheckStatus.FAIL, str(e), f"fix {env}")
    except DB_ERRORS as e:
        return _check(
            "database",
            CheckStatus.FAIL,
            f"cannot connect to {redact(url)}: {_first_line(e)}",
            "check the URL, the credentials and that Postgres is running",
        )
    if not state.up_to_date:
        return _check(
            "database",
            CheckStatus.FAIL,
            f"schema at revision {state.current or 'none'}, expected {state.head}",
            "run coglossa db upgrade",
        )
    return _check("database", CheckStatus.PASS, f"{redact(url)} at revision {state.head}")


def _storage(cfg: CoglossaConfig) -> DoctorCheck:
    try:
        build_storage(cfg.storage).check_writable()
    except StorageError as e:
        return _check("storage", CheckStatus.FAIL, str(e), "fix storage.path or its permissions")
    return _check("storage", CheckStatus.PASS, f"{cfg.storage.path} is writable")
```

Add to `engine/coglossa/cli_ops.py`:

```python
    doctor = sub.add_parser("doctor", help="check the installation")
    _config_arg(doctor)
    doctor.add_argument("--json", action="store_true", help="print the result as JSON")
    doctor.add_argument("--no-probe", action="store_true", help="skip the context-window probe")
```

```python
def _doctor(args: argparse.Namespace) -> int:
    from coglossa.doctor import run_doctor

    report = asyncio.run(run_doctor(args.config, probe_context=not args.no_probe))
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        for c in report.checks:
            print(f"{c.status.value.upper():<5} {c.name}: {c.message}")
            if c.fix and c.status is not CheckStatus.PASS:
                print(f"      fix: {c.fix}")
    return EXIT_OK if report.ok else EXIT_FAILED
```

Register `"doctor": _doctor` in `COMMANDS`, and import `CheckStatus` from `coglossa.schema`.

The database check reuses `migration_state`. A URL with the wrong scheme raises `ConfigError` from `to_sqlalchemy_url`, which the first `except` handles. Unreachable servers raise `OperationalError`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make test-engine`

Expected: PASS; coverage ≥ 95%. `make schema` shows no drift after the regeneration.

- [ ] **Step 5: Commit**

```bash
git add engine schema
git commit -m "feat(doctor): installation checks for config, prompts, backends, models, scorer, database and storage"
```

---

### Task 14: End-to-end over HTTP, the library boundary, docs

**Files:**
- Modify:
  - `engine/coglossa/testing/fake_openai.py` (`delay_s`, `max_in_flight`)
  - `README.md`
- Create: `engine/tests/test_e2e_queue.py`, `engine/tests/test_boundary.py`
- Test: as above, plus additions to `engine/tests/testing/test_fake_openai.py`

**Interfaces:**
- Consumes: the whole of M3
- Produces:
  - `FakeOpenAIServer(..., delay_s: float = 0.0)`, whose `.max_in_flight: int` is the most concurrent chat requests seen (thread-safe)
  - the README's "Queued runs" section

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_e2e_queue.py`:

```python
import json
import re

import yaml

from coglossa.cli import main
from coglossa.testing.fake_openai import FakeOpenAIServer
from coglossa.testing.fake_scorer import FakeScorerServer

TEXT = "\n\n".join(" ".join(["Le chat dort près de la fenêtre."] * 20) for _ in range(6))


def test_queued_run_over_http_respects_backend_concurrency(db, tmp_path, monkeypatch, pg_url, capsys):
    """Spec §7.2: the worker never sends a backend more simultaneous requests than its
    max_concurrency, even with several chunk jobs in flight."""
    monkeypatch.setenv("COGLOSSA_DATABASE_URL", pg_url)
    with FakeOpenAIServer(delay_s=0.05) as llm, FakeScorerServer() as scorer:
        cfg = {
            "backends": {"local": {"base_url": llm.base_url, "max_concurrency": 2}},
            "models": {
                "m1": {"backend": "local", "name": "fake-model", "context_window": 32768},
                "m2": {"backend": "local", "name": "fake-model", "context_window": 32768},
            },
            "roles": {
                "literal_translator": {"model": "m1"},
                "contextual_translator": {"model": "m1"},
                "reviewer": {"model": "m1"},
                "profiler_bible": {"model": "m1"},
                "judge": {"model": "m2"},
                "roundtrip": {"model": "m2"},
            },
            "chunking": {"target_words": 100, "max_words": 200},
            "scorer": {"url": scorer.base_url},
            "worker": {"concurrency": 4},
            "storage": {"path": str(tmp_path / "files")},
        }
        config = tmp_path / "coglossa.yaml"
        config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        doc = tmp_path / "book.txt"
        doc.write_text(TEXT, encoding="utf-8")
        assert main(["submit", str(doc), "--target-lang", "en", "--source-lang", "fr", "--config", str(config)]) == 0
        run_id = re.search(r"run (\S+) queued", capsys.readouterr().out).group(1)
        assert main(["worker", "--burst", "--config", str(config)]) == 0
        capsys.readouterr()
        assert main(["report", run_id, "--out", str(tmp_path / "out"), "--config", str(config)]) == 0
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    assert len(report["chunks"]) >= 4
    assert all(s["score"]["meaning"] is not None for s in report["spans"])
    assert report["config"]["scorer"] is not None
    assert 1 < llm.max_in_flight <= 2
    assert any(r["path"] == "/v1/qe" for r in scorer.requests)  # scored over HTTP
```

`engine/tests/test_boundary.py`:

```python
"""Spec §2.1: the engine library has no knowledge of the database."""

import subprocess
import sys

LIBRARY = [
    "coglossa.schema",
    "coglossa.config",
    "coglossa.ingest.registry",
    "coglossa.segment.segmenter",
    "coglossa.segment.chunking",
    "coglossa.llm.agent",
    "coglossa.llm.registry",
    "coglossa.prompts.check",
    "coglossa.scoring.aggregate",
    "coglossa.scoring.client",
    "coglossa.stages.chunk",
    "coglossa.stages.sweep",
    "coglossa.pipeline",
    "coglossa.storage",
    "coglossa.testing.fake_backend",
    "coglossa.testing.fake_openai",
    "coglossa.testing.fake_scorer",
    "coglossa.testing.synthetic",
]
FORBIDDEN = ("sqlalchemy", "alembic", "psycopg", "procrastinate")


def test_library_modules_do_not_import_database_packages():
    code = (
        "import importlib, sys\n"
        f"for m in {LIBRARY!r}: importlib.import_module(m)\n"
        f"bad = sorted(m for m in sys.modules if m.split('.')[0] in {FORBIDDEN!r})\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"library imports database packages: {result.stdout}"
```

Add to `engine/tests/testing/test_fake_openai.py`: with `delay_s=0.05`, three concurrent requests (threads, or `httpx` calls in `asyncio.gather`) give `max_in_flight == 3`. Sequential requests give 1.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/test_e2e_queue.py tests/test_boundary.py tests/testing/test_fake_openai.py -q --no-cov`

Expected: FAIL. `FakeOpenAIServer` doesn't accept `delay_s`. The boundary test should already pass. If it fails, fix the import that leaks, by moving it into a function or the persistence layer; never weaken the test.

- [ ] **Step 3: Implement**

In `engine/coglossa/testing/fake_openai.py`:
- `FakeOpenAIServer.__init__(..., context_window: int | None = None, delay_s: float = 0.0)` stores `delay_s`, a `threading.Lock`, and counters `in_flight = 0` and `max_in_flight = 0` on the `_Server`.
- Expose a `max_in_flight` property on `FakeOpenAIServer`.
- In `do_POST`, for chat completions, wrap the response work:

```python
        with self.server.lock:
            self.server.in_flight += 1
            self.server.max_in_flight = max(self.server.max_in_flight, self.server.in_flight)
        try:
            if self.server.delay_s:
                time.sleep(self.server.delay_s)
            ...  # existing parsing and reply
        finally:
            with self.server.lock:
                self.server.in_flight -= 1
```

`README.md`: add a section after "Scoring" called **Queued runs (worker and database)**. It covers:
- Why the queue exists: long documents, crash-safe, resumable, reviewable as chunks finish.
- Setup:
  - `export COGLOSSA_DATABASE_URL=postgresql://…`
  - `coglossa db upgrade`
  - `coglossa worker` (long-running; `--burst` drains and exits)
- The lifecycle commands, with one example each: `coglossa submit book.docx --target-lang en --project novel`, `status`, `bible`/`approve-bible` for checkpointed runs, `cancel`, `retry` (also resumes a run whose job was lost), `report`, `delete-project NAME --yes`.
- The run states, one line each.
- `coglossa doctor`: what it checks, `--no-probe`, `--json`, exit code.
- The config sections `database`, `storage`, `worker` and `profiles.sweep`.
- Development: `make test` needs Docker (it starts Postgres on 127.0.0.1:55432 via `make db-up`). `make db-down` stops it.

- [ ] **Step 4: Run the whole suite and lint**

Run: `make test && make lint && make schema && git diff --exit-code schema`

Expected: PASS, with engine and scorer coverage each ≥ 95%. `schema/` shows no drift.

- [ ] **Step 5: Commit**

```bash
git add engine README.md
git commit -m "test(e2e): queued run over HTTP with backend concurrency cap; enforce the library boundary; document queued runs"
```

---

## Self-review notes (plan author)

- **Spec coverage:**
  - §5 data model: Task 6, with tables, cascade and append-only revisions.
  - §7.1 lifecycle and idempotency: Tasks 9–10. Progress counts: Task 9.
  - §7.2 concurrency: per-backend semaphores shared by one worker (Task 11) and verified over HTTP (Task 14). Overflow split: Task 2.
  - §7.3 failure handling: backoff (M1), chunk failure and retry (Tasks 9–10), invalid output (M1), worker crash (Task 11 stalled re-queue plus idempotent jobs), scorer down (M2), cancel (Task 10), config invalid at startup (Task 11 refuses to start).
  - §7.4 doctor: Task 13. §7.6 observability: `llm_call` (Task 7), stage timing (Task 7), JSON logs (Task 11).
  - §4.1 sweep: Tasks 3–4 and 10. §3 config: Task 1.
  - Spec §5 says deleting a project removes its rows, files and payloads: Task 9.
- **Deferred by design (see the top):** API and SSE, the review UI and `review_event` writes, exports.
