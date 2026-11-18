# Coglossa M1 — Schema, Engine Core and CLI: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `coglossa run`, a CLI that takes a TXT, MD or DOCX file and does the following against any OpenAI-compatible model servers:
- ingests and segments the file
- profiles the document
- builds a translation bible, with an optional human checkpoint
- translates every chunk with several translator agents, then reconciles them with a reviewer agent
- writes a JSON report with aligned spans, candidates and flags.

Scoring signals are `null` in M1.

**Architecture:** `coglossa` is a pure Python library and has no database or web dependencies. Each pipeline stage is an async function with typed Pydantic input and output. All model traffic goes through one `Backend` protocol, with an OpenAI-compatible HTTP adapter. An `AgentRunner` adds structured-output validation, one repair retry, retry with backoff for transient errors, and a log entry for every model call. Tests run against an in-process `FakeBackend` and a stdlib-based fake OpenAI server, so no GPU or network is needed.

**Tech Stack:** Python ≥3.12, uv, Pydantic v2, httpx, PyYAML, Jinja2, python-docx, pytest (+ pytest-asyncio, pytest-cov, Hypothesis), ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-09-coglossa-v1-design.md`. This plan covers milestone M1 from §11. Read the spec's §3 (configuration), §4 (engine pipeline) and §5 (data model) before starting.

## Global Constraints

- Python `>=3.12`. Package and tool management uses **uv** only. All Python lives under `engine/` for M1.
- `coglossa` must not import any database, web framework or vendor model SDK. Model traffic is plain HTTP through `httpx` to OpenAI-compatible endpoints (spec §2.1).
- Coverage gate: `pytest --cov=coglossa --cov-branch --cov-fail-under=95`. Every `# pragma: no cover` needs a justification comment on the same line.
- Tests never touch the network except `127.0.0.1` (the fake OpenAI server). No GPU is required.
- Every agent output is validated against a Pydantic model. On failure there is **one repair retry**, then `StructuredOutputError`. Failures in translate and reconcile degrade to `engine_error` spans, and the **run continues**. Only profile and bible failures are fatal (spec §4.5, §7.3).
- Transient backend errors (timeouts, connection errors, HTTP 429 and 5xx) are retried with exponential backoff and jitter, using `retry.max_attempts` (default 4), `base_delay_s` (2) and `max_delay_s` (60) (spec §3).
- Every model call is recorded as an `LLMCallRecord`: role, backend, model, prompt id, prompt version, tokens, latency, attempt, ok/error, and the request and response when `audit.store_llm_io` is true (spec §5).
- Config validation (spec §3): roles must reference defined models and models must reference defined backends. `judge` ≠ `reviewer`. `roundtrip` must not use any translator's model. Both of those rules are errors when two or more models are defined and warnings when only one is.
- Scores are never invented. In M1 every signal is `null` and reliability is `"unscored"` (spec §1.2 item 4).
- **Schema source of truth:** the Pydantic models in `coglossa.schema` are canonical. JSON Schema is exported to `/schema/*.schema.json` and CI fails if it drifts. This is a deliberate refinement of spec §2.1; Task 2 updates the spec to match.
- Default pipeline profile thresholds: `short` under 1,500 words, `standard` 1,500–20,000, `long-form` over 20,000. Checkpoint defaults: long-form on, others off (spec §4.2).

## Review Focus

These are inputs the spec implies but doesn't test directly. Each one has a test in the task named.

1. **Non-UTF-8 or binary "text" files** should produce a one-line `IngestError` naming the file, not a traceback (Task 8).
2. **Local models wrapping JSON in prose or Markdown fences**, which is common in `json_object`/`none` mode, should still be extracted and parsed (Task 5).
3. **Agent output that skips, duplicates or reorders segment IDs** triggers the repair retry and then an `engine_error` fallback. It must never produce a silently misaligned translation (Tasks 5, 13, 14).
4. **A model context window too small for the agent roster** should produce a `ConfigError` at startup that names the fix, not overflow errors halfway through a run (Task 10).
5. **Right-to-left scripts and scripts without spaces** (Arabic, Thai, Japanese, Amharic) should segment without crashing, with every non-whitespace character covered exactly once (Task 9).

## Deferred to Later Milestones (do not build in M1)

- Scoring signals, judge, roundtrip, aggregation, tiers, and the other flag codes → M2
- Splitting and retrying a chunk on context overflow, the sweep stage, Postgres persistence, the worker, `coglossa doctor` → M3. In M1, overflow surfaces as a translator or reviewer error, which degrades to `engine_error`.
- HTML, EPUB and text-PDF ingestion, the DOCX/HTML exports, the API and web app → M4
- Bundle, SBOM, air-gap install test → M5. `coglossa bench` → M6.

## File Structure

```
.gitignore
Makefile                                  # test / lint / schema targets
.github/workflows/ci.yml                  # lint + tests + schema drift
schema/report.schema.json                 # exported (generated) — do not hand-edit
schema/bible.schema.json                  # exported (generated)
engine/pyproject.toml
engine/coglossa.example.yaml              # annotated config template
engine/coglossa/__init__.py               # __version__
engine/coglossa/cli.py                    # argparse CLI: run, fake-openai
engine/coglossa/config.py                 # CoglossaConfig, load_config, validate_config, config_hash
engine/coglossa/pipeline.py               # run_document orchestration + report assembly
engine/coglossa/schema/__init__.py        # re-exports
engine/coglossa/schema/document.py        # Block, Segment, Chunk, SourceDocument
engine/coglossa/schema/profile.py         # Genre, PipelineProfile, ProfileAssessment, DocumentProfile
engine/coglossa/schema/bible.py           # GlossaryEntry, Entity, Bible
engine/coglossa/schema/translation.py     # SpanText, TranslationOutput, ReconciledSpan, Candidate, Span, Flag, SpanScore …
engine/coglossa/schema/report.py          # LLMCallRecord, Usage, RunError, Report …
engine/coglossa/schema/export.py          # JSON Schema export (python -m coglossa.schema.export)
engine/coglossa/llm/types.py              # ChatMessage/Request/Response, Backend protocol, errors
engine/coglossa/llm/payload.py            # INPUT_JSON marker: render_input / parse_input
engine/coglossa/llm/openai_compat.py      # OpenAICompatBackend
engine/coglossa/llm/registry.py           # build_backends (+ plugin entry points)
engine/coglossa/llm/retry.py              # RetryPolicy, call_with_retry
engine/coglossa/llm/agent.py              # AgentRunner, CallLog, extract_json, StructuredOutputError
engine/coglossa/prompts/library.py        # PromptLibrary, RenderedPrompt
engine/coglossa/prompts/templates/<id>/v1.j2   # profile, bible, bible_merge, literal, contextual, reconcile
engine/coglossa/ingest/base.py            # IngestError, helpers (make_blocks, sha256, scene breaks)
engine/coglossa/ingest/txt.py             # TextIngestor
engine/coglossa/ingest/markdown.py        # MarkdownIngestor
engine/coglossa/ingest/docx.py            # DocxIngestor
engine/coglossa/ingest/registry.py        # ingest_file (+ plugin entry points)
engine/coglossa/segment/text_metrics.py   # count_words, estimate_tokens
engine/coglossa/segment/sentences.py      # split_sentences
engine/coglossa/segment/segmenter.py      # segment_document
engine/coglossa/segment/chunking.py       # ChunkBudget, compute_budget, chunk_segments
engine/coglossa/stages/context.py         # StageContext
engine/coglossa/stages/profile.py         # run_profile
engine/coglossa/stages/bible.py           # run_bible, apply_locked, bible_slice
engine/coglossa/stages/translate.py       # check_coverage, build_chunk_inputs, translate_chunk
engine/coglossa/stages/reconcile.py       # reconcile_chunk
engine/coglossa/stages/score.py           # unscored, initial_flags
engine/coglossa/testing/synthetic.py      # deterministic synthetic responder
engine/coglossa/testing/fake_backend.py   # FakeBackend (in-process, scriptable faults)
engine/coglossa/testing/fake_openai.py    # FakeOpenAIServer (stdlib HTTP)
engine/tests/...                          # mirrors package layout
```

`coglossa/testing/` ships inside the package on purpose. Later milestones reuse it for the fake-server container and for demos without GPUs.

---

### Task 1: Project scaffold, tooling and CI

**Files:**
- Create: `.gitignore`, `Makefile`, `.github/workflows/ci.yml`, `engine/pyproject.toml`, `engine/coglossa/__init__.py`, `engine/tests/__init__.py`
- Test: `engine/tests/test_version.py`

**Interfaces:**
- Produces: the package `coglossa` with `__version__ = "0.1.0"`; `make test` and `make lint`; the CI workflow.

- [ ] **Step 1: Install uv if it's missing**

Run: `command -v uv || brew install uv` (Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
Expected: `uv --version` prints a version.

- [ ] **Step 2: Create `engine/pyproject.toml`**

```toml
[project]
name = "coglossa"
version = "0.1.0"
description = "Multi-agent translation with verifiable, scored output"
requires-python = ">=3.12"
dependencies = [
  "pydantic>=2.9,<3",
  "httpx>=0.27,<1",
  "pyyaml>=6.0",
  "jinja2>=3.1",
  "python-docx>=1.1",
]

[project.scripts]
coglossa = "coglossa.cli:main"

[project.entry-points."coglossa.backends"]
[project.entry-points."coglossa.ingestors"]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "pytest-cov>=5.0",
  "hypothesis>=6.112",
  "ruff>=0.7",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["coglossa"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "--cov=coglossa --cov-branch --cov-report=term-missing --cov-fail-under=95"

[tool.coverage.run]
branch = true
source = ["coglossa"]

[tool.coverage.report]
exclude_also = [
  "if TYPE_CHECKING:",
  "if __name__ == .__main__.:",
  "^\\s*\\.\\.\\.$",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]
```

If `uv` rejects the empty entry-point tables, delete those two lines. They only document the plugin groups.

- [ ] **Step 3: Create the package, test package and version test**

`engine/coglossa/__init__.py`:
```python
"""Coglossa: multi-agent translation with verifiable, scored output."""

__version__ = "0.1.0"
```

`engine/tests/__init__.py`: an empty file.

`engine/tests/test_version.py`:
```python
import coglossa


def test_version_is_semver():
    parts = coglossa.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
```

- [ ] **Step 4: Create the root `.gitignore`, `Makefile` and CI workflow**

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.coverage
coverage.xml
.pytest_cache/
.ruff_cache/
.hypothesis/
coglossa-out/
dist/
```

`Makefile` (recipe lines are indented with a TAB):
```make
.PHONY: test lint fmt schema

test:
	cd engine && uv run pytest

lint:
	cd engine && uv run ruff check . && uv run ruff format --check .

fmt:
	cd engine && uv run ruff format . && uv run ruff check --fix .

schema:
	cd engine && uv run python -m coglossa.schema.export ../schema
```

`.github/workflows/ci.yml`:
```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
jobs:
  engine:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: engine
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
      - run: uv sync --all-groups
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest
```

- [ ] **Step 5: Install dependencies and run the tests**

Run: `cd engine && uv sync --all-groups && uv run pytest`
Expected: 1 passed and coverage at 100%; `uv.lock` is created.

- [ ] **Step 6: Lint**

Run: `make lint`
Expected: no errors. Run `make fmt` first if formatting differs.

- [ ] **Step 7: Commit**

```bash
git add .gitignore Makefile .github engine/pyproject.toml engine/uv.lock engine/coglossa engine/tests
git commit -m "chore: scaffold coglossa engine package, tooling and CI"
```

### Task 2: Schema models and JSON Schema export

**Files:**
- Create: `engine/coglossa/schema/{__init__,document,profile,bible,translation,report,export}.py`
- Create (generated): `schema/report.schema.json`, `schema/bible.schema.json`
- Modify: `docs/superpowers/specs/2026-10-09-coglossa-v1-design.md` (§2.1 `schema/` row)
- Test: `engine/tests/schema/__init__.py`, `engine/tests/schema/test_models.py`, `engine/tests/schema/test_export.py`

**Interfaces:**
- Produces (all importable from `coglossa.schema`): `BlockKind`, `Provenance`, `Block`, `SourceDocument`, `Segment`, `Chunk`, `Genre`, `PipelineProfile`, `ProfileAssessment`, `DocumentProfile`, `GlossaryEntry`, `Entity`, `Bible`, `SpanText`, `TranslationOutput`, `Agreement`, `ReconciledSpan`, `ReconcileOutput`, `Candidate`, `FlagCode`, `Flag`, `Reliability`, `SpanScore`, `Span`, `LLMCallRecord`, `Usage`, `RunStatus`, `RunError`, `DocumentInfo`, `ConfigSnapshot`, `Report`. Also `coglossa.schema.export.export(out_dir: Path) -> list[Path]` and `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing model tests**

`engine/tests/schema/__init__.py`: an empty file.

`engine/tests/schema/test_models.py`:
```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from coglossa.schema import (
    Agreement,
    Bible,
    Block,
    BlockKind,
    ConfigSnapshot,
    DocumentInfo,
    DocumentProfile,
    Flag,
    FlagCode,
    Genre,
    GlossaryEntry,
    PipelineProfile,
    Provenance,
    Reliability,
    Report,
    RunStatus,
    Span,
    SpanScore,
    Usage,
)


def _profile() -> DocumentProfile:
    return DocumentProfile(
        source_lang="fr",
        target_lang="en",
        mixed_language=False,
        genre=Genre.LITERARY_PROSE,
        tone_register="formal, melancholic",
        word_count=12,
        pipeline_profile=PipelineProfile.SHORT,
    )


def test_bible_defaults_are_independent_lists():
    a = Bible(summary="s", style_guide="g")
    b = Bible(summary="s", style_guide="g")
    a.glossary.append(GlossaryEntry(term="x", rendering="y"))
    assert b.glossary == []
    assert a.glossary[0].locked is False


def test_pipeline_profile_values_match_spec():
    assert [p.value for p in PipelineProfile] == ["short", "standard", "long-form"]


def test_flag_codes_match_spec():
    assert {c.value for c in FlagCode} == {
        "low_meaning",
        "style_drift",
        "agent_disagreement",
        "terminology_violation",
        "possible_omission",
        "cultural_or_wordplay",
        "engine_error",
        "low_reliability_language",
    }


def test_span_score_defaults_unscored():
    score = SpanScore(signals={"qe": None})
    assert score.meaning is None and score.style is None
    assert score.reliability is Reliability.UNSCORED


def test_block_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        Block(id="b1", kind="sidebar", text="x", provenance=Provenance(source_path="a", index=0))


def test_report_round_trips_through_json():
    span = Span(
        id="p1",
        chunk_id="c1",
        start="s1",
        end="s1",
        text="Hello",
        agreement=Agreement.AGREE,
        chosen_from="contextual",
        score=SpanScore(signals={"qe": None}),
        flags=[Flag(code=FlagCode.ENGINE_ERROR, explanation="x")],
    )
    report = Report(
        coglossa_version="0.1.0",
        created_at=datetime(2026, 10, 9, tzinfo=UTC),
        status=RunStatus.COMPLETE,
        document=DocumentInfo(source_path="a.txt", format="txt", sha256="0" * 64),
        profile=_profile(),
        bible=Bible(summary="s", style_guide="g"),
        bible_proposals=[],
        blocks=[Block(id="b1", kind=BlockKind.PARAGRAPH, text="Bonjour",
                      provenance=Provenance(source_path="a.txt", index=0))],
        segments=[],
        chunks=[],
        candidates=[],
        spans=[span],
        errors=[],
        usage=Usage(calls=0, tokens_in=0, tokens_out=0, by_role={}),
        config=ConfigSnapshot(hash="abc", roles={}, prompt_versions={}),
        llm_calls=[],
    )
    again = Report.model_validate_json(report.model_dump_json())
    assert again == report
    assert again.schema_version == "1"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/schema/test_models.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'coglossa.schema'`.

- [ ] **Step 3: Implement the schema modules**

`engine/coglossa/schema/document.py`:
```python
"""Source documents after ingestion, and their segmentation."""

from enum import StrEnum

from pydantic import BaseModel


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    BREAK = "break"  # scene/section break marker; carries no translatable text
    LIST_ITEM = "list_item"
    TABLE_CELL = "table_cell"
    FOOTNOTE = "footnote"


class Provenance(BaseModel):
    source_path: str
    index: int  # ordinal of the block within the source file
    page: int | None = None
    locator: str | None = None  # format-specific, e.g. "table 2 row 1 col 3"


class Block(BaseModel):
    id: str  # "b1", "b2", … in reading order
    kind: BlockKind
    text: str
    provenance: Provenance


class SourceDocument(BaseModel):
    source_path: str
    format: str
    sha256: str
    blocks: list[Block]


class Segment(BaseModel):
    id: str  # "s1", "s2", … document-global, in reading order
    block_id: str
    start: int  # character offset into the block's text
    end: int
    text: str  # == block.text[start:end]


class Chunk(BaseModel):
    id: str  # "c1", "c2", …
    segment_ids: list[str]  # contiguous, in reading order
```

`engine/coglossa/schema/profile.py`:
```python
"""Document profile: language, genre, register and pipeline profile."""

from enum import StrEnum

from pydantic import BaseModel, Field


class Genre(StrEnum):
    LITERARY_PROSE = "literary_prose"
    POETRY = "poetry"
    TECHNICAL = "technical"
    ACADEMIC = "academic"
    LEGAL = "legal"
    NEWS = "news"
    CORRESPONDENCE = "correspondence"
    SOCIAL_MEDIA = "social_media"
    TRANSCRIPT = "transcript"
    OTHER = "other"


class PipelineProfile(StrEnum):
    SHORT = "short"
    STANDARD = "standard"
    LONG_FORM = "long-form"


class ProfileAssessment(BaseModel):
    """What the profiler agent returns."""

    source_lang: str = Field(description="BCP-47 code of the dominant source language")
    mixed_language: bool = Field(description="True if substantial text is in another language")
    genre: Genre
    tone_register: str = Field(description="Short description of tone and register")


class DocumentProfile(BaseModel):
    source_lang: str
    target_lang: str
    mixed_language: bool
    genre: Genre
    tone_register: str
    word_count: int
    pipeline_profile: PipelineProfile
```

`engine/coglossa/schema/bible.py`:
```python
"""The translation bible: decisions every agent must respect."""

from pydantic import BaseModel, Field


class GlossaryEntry(BaseModel):
    term: str = Field(description="Source-language term exactly as it appears")
    rendering: str = Field(description="Target-language rendering to use")
    notes: str = ""
    locked: bool = False  # set only by humans; locked entries are enforced and never dropped


class Entity(BaseModel):
    name: str
    rendering: str
    kind: str = Field(description="person, place, organization, or other")
    gender: str | None = None
    forms_of_address: str = ""
    speech_style: str = ""


class Bible(BaseModel):
    summary: str
    style_guide: str
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    cultural_references: list[str] = Field(default_factory=list)
```

`engine/coglossa/schema/translation.py`:
```python
"""Agent outputs, candidates, final spans, scores and flags."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from coglossa.schema.bible import GlossaryEntry


class SpanText(BaseModel):
    start: str = Field(description="First segment id covered, e.g. s12")
    end: str = Field(description="Last segment id covered (same as start for one segment)")
    text: str
    notes: str = ""


class TranslationOutput(BaseModel):
    spans: list[SpanText]


class Agreement(StrEnum):
    AGREE = "agree"
    MINOR = "minor"
    MAJOR = "major"
    UNKNOWN = "unknown"  # set by the engine when review did not happen


class ReconciledSpan(BaseModel):
    start: str
    end: str
    text: str
    agreement: Agreement
    chosen_from: str = Field(description="Translator name, 'merged', or 'rewritten'")
    rationale: str = ""
    issues: list[str] = Field(default_factory=list)
    cultural_or_wordplay: bool = False
    proposed_terms: list[GlossaryEntry] = Field(default_factory=list)


class ReconcileOutput(BaseModel):
    spans: list[ReconciledSpan]


class Candidate(BaseModel):
    chunk_id: str
    translator: str
    model: str
    start: str
    end: str
    text: str
    notes: str = ""


class FlagCode(StrEnum):
    LOW_MEANING = "low_meaning"
    STYLE_DRIFT = "style_drift"
    AGENT_DISAGREEMENT = "agent_disagreement"
    TERMINOLOGY_VIOLATION = "terminology_violation"
    POSSIBLE_OMISSION = "possible_omission"
    CULTURAL_OR_WORDPLAY = "cultural_or_wordplay"
    ENGINE_ERROR = "engine_error"
    LOW_RELIABILITY_LANGUAGE = "low_reliability_language"


class Flag(BaseModel):
    code: FlagCode
    explanation: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class Reliability(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    UNSCORED = "unscored"


class SpanScore(BaseModel):
    signals: dict[str, float | None]
    meaning: float | None = None
    style: float | None = None
    reliability: Reliability = Reliability.UNSCORED


class Span(BaseModel):
    id: str  # "p1", "p2", … in reading order
    chunk_id: str
    start: str
    end: str
    text: str
    agreement: Agreement
    chosen_from: str
    rationale: str = ""
    issues: list[str] = Field(default_factory=list)
    score: SpanScore
    flags: list[Flag] = Field(default_factory=list)
```

`engine/coglossa/schema/report.py`:
```python
"""The canonical run report."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from coglossa.schema.bible import Bible, GlossaryEntry
from coglossa.schema.document import Block, Chunk, Segment
from coglossa.schema.profile import DocumentProfile
from coglossa.schema.translation import Candidate, Span


class LLMCallRecord(BaseModel):
    role: str
    backend: str
    model: str
    prompt_id: str
    prompt_version: int
    attempt: int
    ok: bool
    latency_ms: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None
    request: dict[str, Any] | None = None  # only when audit.store_llm_io
    response: str | None = None  # only when audit.store_llm_io


class Usage(BaseModel):
    calls: int
    tokens_in: int
    tokens_out: int
    by_role: dict[str, dict[str, int]]


class RunStatus(StrEnum):
    COMPLETE = "complete"
    COMPLETE_WITH_ERRORS = "complete_with_errors"


class RunError(BaseModel):
    stage: str
    message: str
    chunk_id: str | None = None
    role: str | None = None


class DocumentInfo(BaseModel):
    source_path: str
    format: str
    sha256: str


class ConfigSnapshot(BaseModel):
    hash: str
    roles: dict[str, str]  # role label -> model key
    prompt_versions: dict[str, int]


class Report(BaseModel):
    schema_version: Literal["1"] = "1"
    coglossa_version: str
    created_at: datetime
    status: RunStatus
    document: DocumentInfo
    profile: DocumentProfile
    bible: Bible
    bible_proposals: list[GlossaryEntry] = Field(default_factory=list)
    blocks: list[Block]
    segments: list[Segment]
    chunks: list[Chunk]
    candidates: list[Candidate]
    spans: list[Span]
    errors: list[RunError] = Field(default_factory=list)
    usage: Usage
    config: ConfigSnapshot
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
```

`engine/coglossa/schema/__init__.py`:
```python
"""Canonical data contracts. JSON Schema is exported from these models (see export.py)."""

from coglossa.schema.bible import Bible, Entity, GlossaryEntry
from coglossa.schema.document import Block, BlockKind, Chunk, Provenance, Segment, SourceDocument
from coglossa.schema.profile import DocumentProfile, Genre, PipelineProfile, ProfileAssessment
from coglossa.schema.report import (
    ConfigSnapshot,
    DocumentInfo,
    LLMCallRecord,
    Report,
    RunError,
    RunStatus,
    Usage,
)
from coglossa.schema.translation import (
    Agreement,
    Candidate,
    Flag,
    FlagCode,
    ReconciledSpan,
    ReconcileOutput,
    Reliability,
    Span,
    SpanScore,
    SpanText,
    TranslationOutput,
)

__all__ = [
    "Agreement", "Bible", "Block", "BlockKind", "Candidate", "Chunk", "ConfigSnapshot",
    "DocumentInfo", "DocumentProfile", "Entity", "Flag", "FlagCode", "Genre", "GlossaryEntry",
    "LLMCallRecord", "PipelineProfile", "ProfileAssessment", "Provenance", "ReconcileOutput",
    "ReconciledSpan", "Reliability", "Report", "RunError", "RunStatus", "Segment",
    "SourceDocument", "Span", "SpanScore", "SpanText", "TranslationOutput", "Usage",
]
```

- [ ] **Step 4: Run the model tests and confirm they pass**

Run: `cd engine && uv run pytest tests/schema/test_models.py -v --no-cov`
Expected: 6 passed.

- [ ] **Step 5: Write the failing export test**

`engine/tests/schema/test_export.py`:
```python
import json
from pathlib import Path

from coglossa.schema import Report
from coglossa.schema.export import export, main

REPO_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schema"


def test_export_writes_sorted_json_schema(tmp_path):
    paths = export(tmp_path)
    assert sorted(p.name for p in paths) == ["bible.schema.json", "report.schema.json"]
    data = json.loads((tmp_path / "report.schema.json").read_text())
    assert data == json.loads(json.dumps(Report.model_json_schema(), sort_keys=True))


def test_main_exports_to_given_dir(tmp_path):
    assert main([str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "bible.schema.json").exists()


def test_committed_schema_matches_models(tmp_path):
    """Fails when a model changes but `make schema` was not run (schema drift)."""
    export(tmp_path)
    for name in ("report.schema.json", "bible.schema.json"):
        committed = (REPO_SCHEMA_DIR / name).read_text(encoding="utf-8")
        assert committed == (tmp_path / name).read_text(encoding="utf-8"), (
            f"schema/{name} is stale — run `make schema` and commit the result"
        )
```

- [ ] **Step 6: Run the export test and confirm it fails**

Run: `cd engine && uv run pytest tests/schema/test_export.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'coglossa.schema.export'`.

- [ ] **Step 7: Implement the exporter**

`engine/coglossa/schema/export.py`:
```python
"""Export JSON Schema for the canonical models. Usage: python -m coglossa.schema.export DIR"""

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from coglossa.schema.bible import Bible
from coglossa.schema.report import Report

EXPORTED: dict[str, type[BaseModel]] = {"report": Report, "bible": Bible}


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in EXPORTED.items():
        path = out_dir / f"{name}.schema.json"
        text = json.dumps(model.model_json_schema(), indent=2, sort_keys=True, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out_dir = Path(args[0]) if args else Path("../schema")
    for path in export(out_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Generate the committed schema and run all tests**

Run: `make schema && cd engine && uv run pytest`
Expected: `schema/report.schema.json` and `schema/bible.schema.json` are created; all tests pass; coverage ≥95%.

- [ ] **Step 9: Update the spec so it matches the source-of-truth decision**

In `docs/superpowers/specs/2026-10-09-coglossa-v1-design.md`, replace the `schema/` row of the §2.1 table with:
```
| `schema/` | Exported JSON Schema, the published contract. The Pydantic models in `coglossa.schema` are the source; `make schema` regenerates these files, TypeScript types are generated from them (M4), and CI fails on drift. | `coglossa.schema` |
```
In §2.2, change the `schema/` line to: `schema/      exported JSON Schema (generated by make schema)`.

- [ ] **Step 10: Commit**

```bash
git add engine/coglossa/schema engine/tests/schema schema docs/superpowers/specs
git commit -m "feat(schema): canonical Pydantic models with exported JSON Schema and drift check"
```

### Task 3: Configuration loading and validation

**Files:**
- Create: `engine/coglossa/config.py`
- Test: `engine/tests/test_config.py`, `engine/tests/conftest.py`

**Interfaces:**
- Consumes: `PipelineProfile` from `coglossa.schema`.
- Produces:
  - `StructuredOutput` (StrEnum: `json_schema`, `json_object`, `none`)
  - `BackendConfig`, `ModelConfig`, `RoleConfig(model)`, `TranslatorConfig(name, prompt, model)`
  - `RolesConfig`, with `translators() -> list[TranslatorConfig]` and `all_roles() -> list[tuple[str, str]]` (label, model key)
  - `RetryConfig`, `ChunkingConfig`, `ProfilesConfig`, `AuditConfig`, `CoglossaConfig`
  - `ConfigIssue(level, message)`, `ConfigError(Exception)`
  - `validate_config(cfg) -> list[ConfigIssue]`
  - `load_config(path: Path) -> tuple[CoglossaConfig, list[ConfigIssue]]` (returns warnings only; raises `ConfigError` on errors)
  - `config_hash(cfg) -> str`
  - Test fixture `make_config(**overrides) -> CoglossaConfig` in `tests/conftest.py`, used by later tasks.

- [ ] **Step 1: Write the shared test fixture**

`engine/tests/conftest.py`:
```python
import pytest

from coglossa.config import CoglossaConfig

BASE_CONFIG: dict = {
    "backends": {"fake": {"base_url": "http://127.0.0.1:9/v1", "max_concurrency": 4}},
    "models": {
        "model-a": {"backend": "fake", "context_window": 32768},
        "model-b": {"backend": "fake", "context_window": 32768},
    },
    "roles": {
        "literal_translator": {"model": "model-a"},
        "contextual_translator": {"model": "model-b"},
        "reviewer": {"model": "model-a"},
        "profiler_bible": {"model": "model-b"},
    },
    "retry": {"max_attempts": 3, "base_delay_s": 0.01, "max_delay_s": 0.02},
}


def build_config(**overrides) -> CoglossaConfig:
    data = {**BASE_CONFIG, **overrides}
    return CoglossaConfig.model_validate(data)


@pytest.fixture
def make_config():
    return build_config
```

- [ ] **Step 2: Write the failing config tests**

`engine/tests/test_config.py`:
```python
import pytest
import yaml

from coglossa.config import (
    ConfigError,
    StructuredOutput,
    config_hash,
    load_config,
    validate_config,
)
from coglossa.schema import PipelineProfile
from tests.conftest import BASE_CONFIG


def _write(tmp_path, data) -> object:
    path = tmp_path / "coglossa.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_defaults(make_config):
    cfg = make_config()
    assert cfg.models["model-a"].structured_output is StructuredOutput.JSON_SCHEMA
    assert cfg.retry.max_attempts == 3
    assert cfg.chunking.target_words == 1500
    assert cfg.profiles.checkpoint[PipelineProfile.LONG_FORM] is True
    assert cfg.profiles.checkpoint[PipelineProfile.SHORT] is False
    assert cfg.audit.store_llm_io is True


def test_translators_builtin_then_extras(make_config):
    cfg = make_config(
        roles={
            **BASE_CONFIG["roles"],
            "extra_translators": [{"name": "second", "prompt": "contextual", "model": "model-a"}],
        }
    )
    names = [(t.name, t.prompt, t.model) for t in cfg.roles.translators()]
    assert names == [
        ("literal", "literal", "model-a"),
        ("contextual", "contextual", "model-b"),
        ("second", "contextual", "model-a"),
    ]


def test_valid_config_has_no_issues(make_config):
    assert validate_config(make_config()) == []


def test_unknown_backend_and_model_are_errors(make_config):
    cfg = make_config(
        models={"model-a": {"backend": "nope", "context_window": 8192}},
    )
    messages = [i.message for i in validate_config(cfg) if i.level == "error"]
    assert any("unknown backend 'nope'" in m for m in messages)
    assert any("unknown model 'model-b'" in m for m in messages)


def test_judge_equal_reviewer_is_error_with_two_models(make_config):
    roles = {**BASE_CONFIG["roles"], "judge": {"model": "model-a"}}
    issues = validate_config(make_config(roles=roles))
    assert [(i.level, "judge" in i.message) for i in issues] == [("error", True)]


def test_roundtrip_using_translator_model_is_error(make_config):
    roles = {**BASE_CONFIG["roles"], "roundtrip": {"model": "model-b"}}
    issues = validate_config(make_config(roles=roles))
    assert [(i.level, "roundtrip" in i.message) for i in issues] == [("error", True)]


def test_diversity_rules_downgrade_to_warnings_with_one_model(make_config):
    one = {"m": {"backend": "fake", "context_window": 8192}}
    roles = {
        "literal_translator": {"model": "m"},
        "contextual_translator": {"model": "m"},
        "reviewer": {"model": "m"},
        "profiler_bible": {"model": "m"},
        "judge": {"model": "m"},
        "roundtrip": {"model": "m"},
    }
    issues = validate_config(make_config(models=one, roles=roles))
    assert {i.level for i in issues} == {"warning"}
    assert len(issues) == 2


def test_duplicate_translator_names_are_error(make_config):
    roles = {
        **BASE_CONFIG["roles"],
        "extra_translators": [{"name": "literal", "prompt": "literal", "model": "model-a"}],
    }
    assert any("duplicate translator name 'literal'" in i.message
               for i in validate_config(make_config(roles=roles)))


def test_missing_api_key_env_is_error(make_config, monkeypatch):
    monkeypatch.delenv("COGLOSSA_TEST_KEY", raising=False)
    backends = {"fake": {"base_url": "http://x/v1", "api_key_env": "COGLOSSA_TEST_KEY"}}
    issues = validate_config(make_config(backends=backends))
    assert any("COGLOSSA_TEST_KEY" in i.message for i in issues)
    monkeypatch.setenv("COGLOSSA_TEST_KEY", "secret")
    assert validate_config(make_config(backends=backends)) == []


def test_target_words_above_max_is_error(make_config):
    issues = validate_config(make_config(chunking={"target_words": 3000, "max_words": 2000}))
    assert any("target_words" in i.message for i in issues)


def test_load_config_ok_and_resolves_prompts_dir(tmp_path):
    path = _write(tmp_path, {**BASE_CONFIG, "prompts_dir": "my-prompts"})
    cfg, warnings = load_config(path)
    assert warnings == []
    assert cfg.prompts_dir == tmp_path / "my-prompts"


def test_load_config_accepts_reserved_future_sections(tmp_path):
    data = {**BASE_CONFIG, "scorer": {"url": "http://scorer:8080"}, "auth": {"mode": "none"}}
    cfg, _ = load_config(_write(tmp_path, data))
    assert cfg.scorer == {"url": "http://scorer:8080"}


def test_load_config_rejects_unknown_keys(tmp_path):
    with pytest.raises(ConfigError, match="backendz"):
        load_config(_write(tmp_path, {**BASE_CONFIG, "backendz": {}}))


def test_load_config_rejects_non_mapping(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(path)


def test_load_config_rejects_invalid_yaml(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("backends: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(path)


def test_load_config_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")


def test_load_config_raises_on_validation_errors(tmp_path):
    roles = {**BASE_CONFIG["roles"], "judge": {"model": "model-a"}}
    with pytest.raises(ConfigError, match="judge"):
        load_config(_write(tmp_path, {**BASE_CONFIG, "roles": roles}))


def test_load_config_returns_warnings(tmp_path):
    data = {
        "backends": BASE_CONFIG["backends"],
        "models": {"m": {"backend": "fake", "context_window": 8192}},
        "roles": {k: {"model": "m"} for k in
                  ("literal_translator", "contextual_translator", "reviewer", "profiler_bible",
                   "judge")},
    }
    _, warnings = load_config(_write(tmp_path, data))
    assert [w.level for w in warnings] == ["warning"]


def test_config_hash_is_stable_and_sensitive(make_config):
    assert config_hash(make_config()) == config_hash(make_config())
    assert config_hash(make_config()) != config_hash(make_config(retry={"max_attempts": 9}))
    assert len(config_hash(make_config())) == 16
```

- [ ] **Step 3: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/test_config.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'coglossa.config'`.

- [ ] **Step 4: Implement `engine/coglossa/config.py`**

```python
"""coglossa.yaml: the single file that adapts Coglossa to a deployment (spec §3)."""

import hashlib
import os
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from coglossa.schema import PipelineProfile


class ConfigError(Exception):
    """Configuration is invalid; the message is safe to show to an operator."""


class StructuredOutput(StrEnum):
    JSON_SCHEMA = "json_schema"  # server enforces the schema (vLLM/Ollama/TGI guided decoding)
    JSON_OBJECT = "json_object"  # server guarantees JSON; schema is described in the prompt
    NONE = "none"  # plain text; schema described in the prompt and JSON extracted


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackendConfig(_Strict):
    kind: str = "openai_compat"
    base_url: str
    api_key_env: str | None = None
    max_concurrency: int = Field(4, ge=1)
    timeout_s: float = Field(120.0, gt=0)


class ModelConfig(_Strict):
    backend: str
    name: str | None = None  # model id sent to the server; defaults to the config key
    context_window: int = Field(ge=2048)
    structured_output: StructuredOutput = StructuredOutput.JSON_SCHEMA
    bytes_per_token: float = Field(2.5, gt=0)  # conservative UTF-8 bytes per token estimate
    max_output_tokens: int | None = Field(None, ge=1)
    temperature: float = Field(0.2, ge=0)


class RoleConfig(_Strict):
    model: str


class TranslatorConfig(_Strict):
    name: str
    prompt: str  # prompt template id, e.g. "literal", "contextual", or a custom one
    model: str


class RolesConfig(_Strict):
    literal_translator: RoleConfig
    contextual_translator: RoleConfig
    extra_translators: list[TranslatorConfig] = Field(default_factory=list)
    reviewer: RoleConfig
    profiler_bible: RoleConfig
    judge: RoleConfig | None = None  # used from M2
    roundtrip: RoleConfig | None = None  # used from M2

    def translators(self) -> list[TranslatorConfig]:
        return [
            TranslatorConfig(name="literal", prompt="literal", model=self.literal_translator.model),
            TranslatorConfig(
                name="contextual", prompt="contextual", model=self.contextual_translator.model
            ),
            *self.extra_translators,
        ]

    def all_roles(self) -> list[tuple[str, str]]:
        roles = [(f"translator:{t.name}", t.model) for t in self.translators()]
        roles += [("reviewer", self.reviewer.model), ("profiler_bible", self.profiler_bible.model)]
        if self.judge is not None:
            roles.append(("judge", self.judge.model))
        if self.roundtrip is not None:
            roles.append(("roundtrip", self.roundtrip.model))
        return roles


class RetryConfig(_Strict):
    max_attempts: int = Field(4, ge=1)
    base_delay_s: float = Field(2.0, ge=0)
    max_delay_s: float = Field(60.0, ge=0)


class ChunkingConfig(_Strict):
    target_words: int = Field(1500, ge=100)
    max_words: int = Field(2000, ge=200)
    reserve_tokens: int = Field(3000, ge=0)  # prompt + bible + safety margin per call


def _default_checkpoints() -> dict[PipelineProfile, bool]:
    return {
        PipelineProfile.SHORT: False,
        PipelineProfile.STANDARD: False,
        PipelineProfile.LONG_FORM: True,
    }


class ProfilesConfig(_Strict):
    short_max_words: int = Field(1500, ge=1)
    standard_max_words: int = Field(20000, ge=1)
    checkpoint: dict[PipelineProfile, bool] = Field(default_factory=_default_checkpoints)


class AuditConfig(_Strict):
    store_llm_io: bool = True


class CoglossaConfig(_Strict):
    backends: dict[str, BackendConfig]
    models: dict[str, ModelConfig]
    roles: RolesConfig
    retry: RetryConfig = Field(default_factory=RetryConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    profiles: ProfilesConfig = Field(default_factory=ProfilesConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    prompts_dir: Path | None = None
    # Reserved for later milestones (spec §3); accepted so one file serves every version.
    scorer: dict[str, Any] | None = None
    scoring: dict[str, Any] | None = None
    storage: dict[str, Any] | None = None
    auth: dict[str, Any] | None = None


class ConfigIssue(BaseModel):
    level: Literal["error", "warning"]
    message: str


def validate_config(cfg: CoglossaConfig) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []

    def add(level: Literal["error", "warning"], message: str) -> None:
        issues.append(ConfigIssue(level=level, message=message))

    for name, model in cfg.models.items():
        if model.backend not in cfg.backends:
            add("error", f"model '{name}' references unknown backend '{model.backend}'")
    for label, model_key in cfg.roles.all_roles():
        if model_key not in cfg.models:
            add("error", f"role '{label}' references unknown model '{model_key}'")

    seen: set[str] = set()
    for t in cfg.roles.translators():
        if t.name in seen:
            add("error", f"duplicate translator name '{t.name}'")
        seen.add(t.name)

    diversity_level: Literal["error", "warning"] = "warning" if len(cfg.models) == 1 else "error"
    if cfg.roles.judge is not None and cfg.roles.judge.model == cfg.roles.reviewer.model:
        add(
            diversity_level,
            "judge must use a different model from reviewer (scores are less reliable otherwise)",
        )
    translator_models = {t.model for t in cfg.roles.translators()}
    if cfg.roles.roundtrip is not None and cfg.roles.roundtrip.model in translator_models:
        add(
            diversity_level,
            "roundtrip must use a model that no translator uses (scores are less reliable otherwise)",
        )

    for name, backend in cfg.backends.items():
        if backend.api_key_env and os.environ.get(backend.api_key_env) is None:
            add(
                "error",
                f"backend '{name}': environment variable {backend.api_key_env} is not set",
            )

    if cfg.chunking.target_words > cfg.chunking.max_words:
        add("error", "chunking.target_words must not exceed chunking.max_words")
    return issues


def load_config(path: Path) -> tuple[CoglossaConfig, list[ConfigIssue]]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping of sections")
    try:
        cfg = CoglossaConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{path}: {e}") from e
    if cfg.prompts_dir is not None and not cfg.prompts_dir.is_absolute():
        cfg = cfg.model_copy(update={"prompts_dir": path.parent / cfg.prompts_dir})
    issues = validate_config(cfg)
    errors = [i.message for i in issues if i.level == "error"]
    if errors:
        raise ConfigError(f"{path}:\n  - " + "\n  - ".join(errors))
    return cfg, issues


def config_hash(cfg: CoglossaConfig) -> str:
    return hashlib.sha256(cfg.model_dump_json().encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 5: Run the config tests and confirm they pass**

Run: `cd engine && uv run pytest tests/test_config.py -v --no-cov`
Expected: all pass. The tests import `tests.conftest`, so they rely on `engine/tests/__init__.py` from Task 1 and on running from `engine/`.

- [ ] **Step 6: Run the full suite and lint**

Run: `cd engine && uv run pytest && cd .. && make lint`
Expected: pass; coverage ≥95%.

- [ ] **Step 7: Commit**

```bash
git add engine/coglossa/config.py engine/tests/conftest.py engine/tests/test_config.py
git commit -m "feat(config): load and validate coglossa.yaml with role diversity rules"
```

### Task 4: Backend protocol and OpenAI-compatible adapter

**Files:**
- Create: `engine/coglossa/llm/__init__.py` (empty), `engine/coglossa/llm/types.py`, `engine/coglossa/llm/openai_compat.py`, `engine/coglossa/llm/registry.py`
- Test: `engine/tests/llm/__init__.py` (empty), `engine/tests/llm/test_openai_compat.py`, `engine/tests/llm/test_registry.py`

**Interfaces:**
- Consumes: `StructuredOutput`, `BackendConfig`, `CoglossaConfig`, `ConfigError` (Task 3).
- Produces:
  - `ChatMessage(role, content)`
  - `ChatRequest(model, messages, schema_name, json_schema, structured_output, temperature=0.2, max_tokens=None)`
  - `ChatResponse(content, tokens_in=None, tokens_out=None, latency_ms=0)`
  - errors: `BackendError` ⊃ `TransientBackendError`; `BackendError` ⊃ `PermanentBackendError` ⊃ {`ContextOverflowError`, `OutputTruncatedError`}
  - the `Backend` protocol: `name: str`, `async chat(ChatRequest) -> ChatResponse`, `async list_models() -> list[str]`, `async aclose() -> None`
  - `OpenAICompatBackend(name, base_url, *, api_key=None, max_concurrency=4, timeout_s=120.0, transport=None)`
  - `build_backends(cfg) -> dict[str, Backend]`

- [ ] **Step 1: Write the failing adapter tests**

These tests use `httpx.MockTransport`, so the real adapter code runs without a server.

`engine/tests/llm/test_openai_compat.py`:
```python
import asyncio
import json

import httpx
import pytest

from coglossa.config import StructuredOutput
from coglossa.llm.openai_compat import OpenAICompatBackend
from coglossa.llm.types import (
    ChatMessage,
    ChatRequest,
    ContextOverflowError,
    OutputTruncatedError,
    PermanentBackendError,
    TransientBackendError,
)


def _req(mode: StructuredOutput = StructuredOutput.JSON_SCHEMA, max_tokens=None) -> ChatRequest:
    return ChatRequest(
        model="m1",
        messages=[ChatMessage(role="user", content="hi")],
        schema_name="Out",
        json_schema={"type": "object"},
        structured_output=mode,
        max_tokens=max_tokens,
    )


def _ok(content="{}", finish="stop", usage=True) -> httpx.Response:
    body = {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish}]}
    if usage:
        body["usage"] = {"prompt_tokens": 5, "completion_tokens": 7}
    return httpx.Response(200, json=body)


def _backend(handler, **kw) -> OpenAICompatBackend:
    return OpenAICompatBackend("gpu-a", "http://x/v1", transport=httpx.MockTransport(handler), **kw)


async def test_chat_posts_openai_payload_with_json_schema():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _ok('{"a": 1}')

    backend = _backend(handler, api_key="k")
    resp = await backend.chat(_req(max_tokens=64))
    assert seen["url"] == "http://x/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "Out", "schema": {"type": "object"}}
    }
    assert seen["body"]["max_tokens"] == 64
    assert resp.content == '{"a": 1}'
    assert (resp.tokens_in, resp.tokens_out) == (5, 7)
    await backend.aclose()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(StructuredOutput.JSON_OBJECT, {"type": "json_object"}), (StructuredOutput.NONE, None)],
)
async def test_chat_response_format_modes(mode, expected):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _ok(usage=False)

    resp = await _backend(handler).chat(_req(mode))
    assert seen["body"].get("response_format") == expected
    assert "max_tokens" not in seen["body"]
    assert resp.tokens_in is None


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_retryable_statuses_are_transient(status):
    backend = _backend(lambda r: httpx.Response(status, text="busy"))
    with pytest.raises(TransientBackendError, match=f"HTTP {status}"):
        await backend.chat(_req())


async def test_context_overflow_is_detected():
    msg = {"error": {"message": "This model's maximum context length is 8192 tokens"}}
    backend = _backend(lambda r: httpx.Response(400, json=msg))
    with pytest.raises(ContextOverflowError):
        await backend.chat(_req())


async def test_other_client_errors_are_permanent():
    backend = _backend(lambda r: httpx.Response(404, text="model not found"))
    with pytest.raises(PermanentBackendError, match="HTTP 404"):
        await backend.chat(_req())


async def test_timeout_and_connection_errors_are_transient():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TransientBackendError, match="timeout"):
        await _backend(timeout).chat(_req())
    with pytest.raises(TransientBackendError, match="ConnectError"):
        await _backend(refused).chat(_req())


async def test_truncated_output_raises():
    with pytest.raises(OutputTruncatedError):
        await _backend(lambda r: _ok(finish="length")).chat(_req())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    ],
)
async def test_malformed_responses_are_permanent(response):
    with pytest.raises(PermanentBackendError):
        await _backend(lambda r: response).chat(_req())


async def test_list_models():
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})

    assert await _backend(handler).list_models() == ["a", "b"]


async def test_list_models_malformed_and_errors():
    with pytest.raises(PermanentBackendError):
        await _backend(lambda r: httpx.Response(200, json={"nope": 1})).list_models()
    with pytest.raises(TransientBackendError):
        await _backend(lambda r: httpx.Response(502, text="bad gateway")).list_models()


async def test_concurrency_is_limited():
    active = 0
    peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _ok()

    backend = _backend(handler, max_concurrency=2)
    await asyncio.gather(*(backend.chat(_req()) for _ in range(6)))
    assert peak == 2
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/llm/test_openai_compat.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'coglossa.llm'`.

- [ ] **Step 3: Implement `engine/coglossa/llm/types.py`**

```python
"""The Backend protocol: the only way Coglossa talks to models."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel

from coglossa.config import StructuredOutput


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    schema_name: str
    json_schema: dict[str, Any]
    structured_output: StructuredOutput
    temperature: float = 0.2
    max_tokens: int | None = None


class ChatResponse(BaseModel):
    content: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int = 0


class BackendError(Exception):
    """A model call failed."""


class TransientBackendError(BackendError):
    """Worth retrying: timeouts, connection failures, HTTP 429 and 5xx."""


class PermanentBackendError(BackendError):
    """Retrying the same request will not help."""


class ContextOverflowError(PermanentBackendError):
    """The prompt exceeded the model's context window."""


class OutputTruncatedError(PermanentBackendError):
    """The model stopped because it hit its output token limit."""


class Backend(Protocol):
    name: str

    async def chat(self, request: ChatRequest) -> ChatResponse: ...

    async def list_models(self) -> list[str]: ...

    async def aclose(self) -> None: ...
```

- [ ] **Step 4: Implement `engine/coglossa/llm/openai_compat.py`**

```python
"""Adapter for any OpenAI-compatible server: vLLM, Ollama, TGI, llama.cpp, LM Studio."""

import asyncio
import time
from typing import Any

import httpx

from coglossa.config import StructuredOutput
from coglossa.llm.types import (
    ChatRequest,
    ChatResponse,
    ContextOverflowError,
    OutputTruncatedError,
    PermanentBackendError,
    TransientBackendError,
)

_OVERFLOW_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "context window",
    "too many tokens",
    "prompt is too long",
)


class OpenAICompatBackend:
    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        api_key: str | None = None,
        max_concurrency: int = 4,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=timeout_s,
            transport=transport,
        )
        self._sem = asyncio.Semaphore(max_concurrency)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        async with self._sem:
            started = time.monotonic()
            resp = await self._send("POST", "chat/completions", json=_payload(request))
            latency_ms = int((time.monotonic() - started) * 1000)
        return _parse_chat(self.name, resp, latency_ms)

    async def list_models(self) -> list[str]:
        resp = await self._send("GET", "models")
        try:
            return [m["id"] for m in resp.json()["data"]]
        except (ValueError, KeyError, TypeError) as e:
            raise PermanentBackendError(f"{self.name}: malformed /models response") from e

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise TransientBackendError(f"{self.name}: timeout calling {path}") from e
        except httpx.TransportError as e:
            raise TransientBackendError(f"{self.name}: {type(e).__name__}: {e}") from e
        _raise_for_status(self.name, resp)
        return resp


def _payload(request: ChatRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": [m.model_dump() for m in request.messages],
        "temperature": request.temperature,
    }
    if request.max_tokens is not None:
        payload["max_tokens"] = request.max_tokens
    if request.structured_output is StructuredOutput.JSON_SCHEMA:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "schema": request.json_schema},
        }
    elif request.structured_output is StructuredOutput.JSON_OBJECT:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _raise_for_status(name: str, resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    body = resp.text[:500]
    message = f"{name}: HTTP {resp.status_code}: {body}"
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientBackendError(message)
    if any(marker in body.lower() for marker in _OVERFLOW_MARKERS):
        raise ContextOverflowError(message)
    raise PermanentBackendError(message)


def _parse_chat(name: str, resp: httpx.Response, latency_ms: int) -> ChatResponse:
    try:
        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise PermanentBackendError(f"{name}: malformed chat completion response") from e
    if choice.get("finish_reason") == "length":
        raise OutputTruncatedError(f"{name}: output truncated (finish_reason=length)")
    if not isinstance(content, str):
        raise PermanentBackendError(f"{name}: chat completion has no text content")
    usage = data.get("usage") or {}
    return ChatResponse(
        content=content,
        tokens_in=usage.get("prompt_tokens"),
        tokens_out=usage.get("completion_tokens"),
        latency_ms=latency_ms,
    )
```

- [ ] **Step 5: Run the adapter tests and confirm they pass**

Run: `cd engine && uv run pytest tests/llm/test_openai_compat.py -v --no-cov`
Expected: all pass.

- [ ] **Step 6: Write the failing registry tests**

`engine/tests/llm/test_registry.py`:
```python
import pytest

from coglossa.config import ConfigError
from coglossa.llm import registry
from coglossa.llm.openai_compat import OpenAICompatBackend


class _EP:
    def __init__(self, name, obj):
        self.name = name
        self._obj = obj

    def load(self):
        return self._obj


async def test_builds_openai_compat_backends(make_config, monkeypatch):
    monkeypatch.setenv("K", "secret")
    cfg = make_config(backends={"fake": {"base_url": "http://x/v1", "api_key_env": "K"}})
    backends = registry.build_backends(cfg)
    assert isinstance(backends["fake"], OpenAICompatBackend)
    assert backends["fake"].name == "fake"
    await backends["fake"].aclose()


def test_unknown_kind_is_config_error(make_config):
    cfg = make_config(backends={"fake": {"kind": "grpc", "base_url": "http://x"}})
    with pytest.raises(ConfigError, match="unknown kind 'grpc'.*openai_compat"):
        registry.build_backends(cfg)


def test_plugin_backends_are_loaded(make_config, monkeypatch):
    made = {}

    def factory(name, bcfg):
        made[name] = bcfg.base_url
        return object()

    monkeypatch.setattr(registry, "entry_points", lambda group: [_EP("grpc", factory)])
    cfg = make_config(backends={"fake": {"kind": "grpc", "base_url": "grpc://x"}})
    backends = registry.build_backends(cfg)
    assert made == {"fake": "grpc://x"}
    assert set(backends) == {"fake"}
```

- [ ] **Step 7: Implement `engine/coglossa/llm/registry.py`**

```python
"""Construct backends from config; third parties add kinds via the coglossa.backends group."""

import os
from collections.abc import Callable
from importlib.metadata import entry_points

from coglossa.config import BackendConfig, CoglossaConfig, ConfigError
from coglossa.llm.openai_compat import OpenAICompatBackend
from coglossa.llm.types import Backend

BackendFactory = Callable[[str, BackendConfig], Backend]


def _openai_compat(name: str, cfg: BackendConfig) -> Backend:
    api_key = os.environ.get(cfg.api_key_env) if cfg.api_key_env else None
    return OpenAICompatBackend(
        name,
        cfg.base_url,
        api_key=api_key,
        max_concurrency=cfg.max_concurrency,
        timeout_s=cfg.timeout_s,
    )


def available_kinds() -> dict[str, BackendFactory]:
    kinds: dict[str, BackendFactory] = {"openai_compat": _openai_compat}
    for ep in entry_points(group="coglossa.backends"):
        kinds.setdefault(ep.name, ep.load())
    return kinds


def build_backends(cfg: CoglossaConfig) -> dict[str, Backend]:
    kinds = available_kinds()
    backends: dict[str, Backend] = {}
    for name, bcfg in cfg.backends.items():
        factory = kinds.get(bcfg.kind)
        if factory is None:
            raise ConfigError(
                f"backend '{name}': unknown kind '{bcfg.kind}' (available: {', '.join(sorted(kinds))})"
            )
        backends[name] = factory(name, bcfg)
    return backends
```

- [ ] **Step 8: Run the llm tests, the full suite and lint**

Run: `cd engine && uv run pytest tests/llm -v --no-cov && uv run pytest && cd .. && make lint`
Expected: pass; coverage ≥95%.

- [ ] **Step 9: Commit**

```bash
git add engine/coglossa/llm engine/tests/llm
git commit -m "feat(llm): Backend protocol and OpenAI-compatible adapter with error mapping"
```

### Task 5: Retry policy and prompt library with v1 templates

**Files:**
- Create: `engine/coglossa/llm/retry.py`, `engine/coglossa/prompts/__init__.py` (empty), `engine/coglossa/prompts/library.py`
- Create templates: `engine/coglossa/prompts/templates/{profile,bible,bible_merge,literal,contextual,reconcile}/v1.j2`
- Test: `engine/tests/llm/test_retry.py`, `engine/tests/prompts/__init__.py` (empty), `engine/tests/prompts/test_library.py`

**Interfaces:**
- Consumes: `RetryConfig` (Task 3), `TransientBackendError` (Task 4).
- Produces:
  - `RetryPolicy(max_attempts=4, base_delay_s=2.0, max_delay_s=60.0)` with `.from_config(RetryConfig)` and `.delay(attempt: int, rng: random.Random) -> float`
  - `async call_with_retry(fn: Callable[[], Awaitable[T]], policy, *, sleep=asyncio.sleep, rng=None) -> T`
  - `RenderedPrompt(id: str, version: int, text: str)`, `PromptNotFoundError(LookupError)`
  - `PromptLibrary(override_dir: Path | None = None)` with `.render(prompt_id, **vars) -> RenderedPrompt`, `.resolve(prompt_id) -> tuple[int, Path]`, `.versions(ids) -> dict[str, int]`
  - Template variables, which every caller must pass:
    - `profile`: `target_lang`
    - `bible`: `source_lang`, `target_lang`, `depth` (`"light" | "full" | "partial"`)
    - `bible_merge`: `source_lang`, `target_lang`
    - translator templates (`literal`, `contextual`, custom): `source_lang`, `target_lang`, `genre`, `register`
    - `reconcile`: `source_lang`, `target_lang`, `translators` (list of names)

- [ ] **Step 1: Write the failing retry tests**

`engine/tests/llm/test_retry.py`:
```python
import random

import pytest

from coglossa.config import RetryConfig
from coglossa.llm.retry import RetryPolicy, call_with_retry
from coglossa.llm.types import PermanentBackendError, TransientBackendError


def test_delay_grows_exponentially_with_jitter_and_cap():
    policy = RetryPolicy(max_attempts=5, base_delay_s=2, max_delay_s=5)
    rng = random.Random(0)
    delays = [policy.delay(a, rng) for a in (1, 2, 3, 4)]
    assert 1.0 <= delays[0] <= 2.0
    assert 2.0 <= delays[1] <= 4.0
    assert 2.5 <= delays[2] <= 5.0
    assert delays[3] <= 5.0


def test_from_config():
    p = RetryPolicy.from_config(RetryConfig(max_attempts=2, base_delay_s=1, max_delay_s=3))
    assert (p.max_attempts, p.base_delay_s, p.max_delay_s) == (2, 1, 3)


async def test_retries_transient_then_succeeds():
    calls, slept = [], []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TransientBackendError("busy")
        return "ok"

    async def sleep(d):
        slept.append(d)

    result = await call_with_retry(fn, RetryPolicy(max_attempts=3), sleep=sleep,
                                   rng=random.Random(1))
    assert result == "ok"
    assert len(calls) == 3
    assert len(slept) == 2


async def test_gives_up_after_max_attempts():
    async def fn():
        raise TransientBackendError("down")

    async def sleep(d):
        pass

    with pytest.raises(TransientBackendError):
        await call_with_retry(fn, RetryPolicy(max_attempts=2), sleep=sleep)


async def test_permanent_errors_are_not_retried():
    calls = []

    async def fn():
        calls.append(1)
        raise PermanentBackendError("bad request")

    with pytest.raises(PermanentBackendError):
        await call_with_retry(fn, RetryPolicy(max_attempts=5))
    assert calls == [1]
```

- [ ] **Step 2: Run them and confirm they fail, then implement `engine/coglossa/llm/retry.py`**

Run: `cd engine && uv run pytest tests/llm/test_retry.py -v --no-cov` → FAIL (module missing).

```python
"""Exponential backoff with jitter for transient backend failures."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from coglossa.config import RetryConfig
from coglossa.llm.types import TransientBackendError


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_s: float = 2.0
    max_delay_s: float = 60.0

    @classmethod
    def from_config(cls, cfg: RetryConfig) -> "RetryPolicy":
        return cls(cfg.max_attempts, cfg.base_delay_s, cfg.max_delay_s)

    def delay(self, attempt: int, rng: random.Random) -> float:
        """Delay before retry number `attempt` (1-based): capped exponential, 50–100% jitter."""
        return min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1)) * rng.uniform(0.5, 1.0)


async def call_with_retry[T](
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> T:
    rng = rng or random.Random()
    attempt = 1
    while True:
        try:
            return await fn()
        except TransientBackendError:
            if attempt >= policy.max_attempts:
                raise
            await sleep(policy.delay(attempt, rng))
            attempt += 1
```

Run the tests again. Expected: 5 passed.

- [ ] **Step 3: Write the failing prompt-library tests**

`engine/tests/prompts/test_library.py`:
```python
import pytest
from jinja2 import UndefinedError

from coglossa.prompts.library import PromptLibrary, PromptNotFoundError

ALL_VARS = {
    "profile": {"target_lang": "en"},
    "bible": {"source_lang": "fr", "target_lang": "en", "depth": "full"},
    "bible_merge": {"source_lang": "fr", "target_lang": "en"},
    "literal": {"source_lang": "fr", "target_lang": "en", "genre": "poetry", "register": "x"},
    "contextual": {"source_lang": "fr", "target_lang": "en", "genre": "poetry", "register": "x"},
    "reconcile": {"source_lang": "fr", "target_lang": "en", "translators": ["literal", "ctx"]},
}


@pytest.mark.parametrize("prompt_id", sorted(ALL_VARS))
def test_builtin_templates_render(prompt_id):
    rendered = PromptLibrary().render(prompt_id, **ALL_VARS[prompt_id])
    assert rendered.id == prompt_id
    assert rendered.version == 1
    assert "JSON" in rendered.text
    assert "{{" not in rendered.text and "{%" not in rendered.text


@pytest.mark.parametrize("depth", ["light", "full", "partial"])
def test_bible_depth_variants_differ(depth):
    text = PromptLibrary().render("bible", source_lang="fr", target_lang="en", depth=depth).text
    marker = {"light": "short document", "full": "full document", "partial": "one excerpt"}[depth]
    assert marker in text


def test_reconcile_lists_translators():
    text = PromptLibrary().render("reconcile", **ALL_VARS["reconcile"]).text
    assert "literal, ctx" in text


def test_missing_variable_fails_loudly():
    with pytest.raises(UndefinedError):
        PromptLibrary().render("literal", source_lang="fr")


def test_override_dir_wins_and_picks_highest_version(tmp_path):
    d = tmp_path / "literal"
    d.mkdir()
    (d / "v1.j2").write_text("old {{ target_lang }}", encoding="utf-8")
    (d / "v7.j2").write_text("custom into {{ target_lang }} JSON", encoding="utf-8")
    (d / "notes.txt").write_text("ignored", encoding="utf-8")
    lib = PromptLibrary(tmp_path)
    rendered = lib.render("literal", target_lang="de")
    assert (rendered.version, rendered.text) == (7, "custom into de JSON")
    # prompts not overridden still come from the built-in templates
    assert lib.resolve("contextual")[0] == 1
    assert lib.versions(["literal", "profile"]) == {"literal": 7, "profile": 1}


@pytest.mark.parametrize("bad", ["nope", "../etc", "Literal", ""])
def test_unknown_or_unsafe_ids_raise(bad):
    with pytest.raises(PromptNotFoundError):
        PromptLibrary().resolve(bad)
```

- [ ] **Step 4: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/prompts -v --no-cov`
Expected: FAIL (module missing).

- [ ] **Step 5: Implement `engine/coglossa/prompts/library.py`**

```python
"""Versioned prompt templates. Built-ins live in templates/<id>/v<N>.j2; deployments may
override any prompt by placing templates/<id>/v<N>.j2 files in config.prompts_dir."""

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, StrictUndefined

BUILTIN_DIR = Path(__file__).parent / "templates"
_VERSION_RE = re.compile(r"^v(\d+)\.j2$")
_ID_RE = re.compile(r"^[a-z0-9_\-]+$")


class PromptNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class RenderedPrompt:
    id: str
    version: int
    text: str


class PromptLibrary:
    def __init__(self, override_dir: Path | None = None) -> None:
        self._dirs = [d for d in (override_dir, BUILTIN_DIR) if d is not None]
        self._env = Environment(undefined=StrictUndefined, autoescape=False)

    def resolve(self, prompt_id: str) -> tuple[int, Path]:
        if not _ID_RE.match(prompt_id):
            raise PromptNotFoundError(f"invalid prompt id '{prompt_id}'")
        for base in self._dirs:
            pdir = base / prompt_id
            if not pdir.is_dir():
                continue
            versions = sorted(
                int(m.group(1)) for f in pdir.iterdir() if (m := _VERSION_RE.match(f.name))
            )
            if versions:
                return versions[-1], pdir / f"v{versions[-1]}.j2"
        raise PromptNotFoundError(
            f"no template for prompt '{prompt_id}' in {', '.join(str(d) for d in self._dirs)}"
        )

    def render(self, prompt_id: str, **variables: object) -> RenderedPrompt:
        version, path = self.resolve(prompt_id)
        template = self._env.from_string(path.read_text(encoding="utf-8"))
        return RenderedPrompt(prompt_id, version, template.render(**variables).strip())

    def versions(self, prompt_ids: list[str]) -> dict[str, int]:
        return {pid: self.resolve(pid)[0] for pid in prompt_ids}
```

- [ ] **Step 6: Create the six v1 templates**

`engine/coglossa/prompts/templates/profile/v1.j2`:
```jinja
You are the document profiler for a professional translation system.
Read the document sample in INPUT_JSON.sample and identify:
- source_lang: the BCP-47 code of the dominant language (for example "fr", "pt-BR", "yo"). If INPUT_JSON.source_lang_hint is set, use it unless the text is clearly in another language.
- mixed_language: true if a substantial part of the text is in a different language.
- genre: one of literary_prose, poetry, technical, academic, legal, news, correspondence, social_media, transcript, other.
- tone_register: a short phrase describing tone and register (for example "formal, bureaucratic" or "colloquial, ironic").
The document will be translated into {{ target_lang }}.
Respond with a single JSON object only.
```

`engine/coglossa/prompts/templates/bible/v1.j2`:
```jinja
You are the lead translator preparing a translation bible: the decisions every translator on the team must follow.
Source language: {{ source_lang }}. Target language: {{ target_lang }}.
{% if depth == "partial" -%}
INPUT_JSON.text is one excerpt of a longer document. Record only what this excerpt shows; a later step merges the excerpts.
{%- elif depth == "light" -%}
INPUT_JSON.text is a short document. Keep the bible brief: a one-paragraph summary, a short style guide, and only the glossary entries and entities that genuinely need a fixed rendering.
{%- else -%}
INPUT_JSON.text is the full document.
{%- endif %}
Produce:
- summary: what the text is about and how it develops, written in {{ target_lang }}.
- style_guide: instructions to translators, in {{ target_lang }}, covering voice, register, tone, sentence rhythm, dialect, and the treatment of dialogue, profanity, honorifics and units.
- glossary: terms whose rendering must stay consistent (technical terms, invented words, recurring phrases, titles). term = exact source form; rendering = target form; notes = why.
- entities: people, places and organizations, with gender if the text establishes it, forms of address (formal or informal, honorifics, nicknames) and how each person speaks.
- cultural_references: references, idioms, allusions and wordplay a translator must handle deliberately.
If INPUT_JSON.prior_bible is present, keep its decisions unless the text clearly contradicts them.
Never invent facts the text does not support. Leave "locked" false on every glossary entry.
Respond with a single JSON object only.
```

`engine/coglossa/prompts/templates/bible_merge/v1.j2`:
```jinja
You are the lead translator merging partial translation bibles, each built from a different excerpt of the same document, into one bible.
Source language: {{ source_lang }}. Target language: {{ target_lang }}.
INPUT_JSON.partials lists the partial bibles in document order.
- summary: one coherent summary of the whole document.
- style_guide: one consistent style guide; where partials conflict, follow the dominant pattern and say so.
- glossary and entities: merge duplicates (the same term, or the same entity under different spellings) and keep one rendering for each. Prefer what later excerpts confirm, such as a character's gender revealed late in the text.
- cultural_references: remove duplicates.
If INPUT_JSON.prior_bible is present, keep its decisions unless the partials clearly contradict them.
Leave "locked" false on every glossary entry. Respond with a single JSON object only.
```

`engine/coglossa/prompts/templates/literal/v1.j2`:
```jinja
You are a meticulous literal translator from {{ source_lang }} into {{ target_lang }}.
Translate INPUT_JSON.segments faithfully: keep every proposition, negation, number, name and qualifier. Do not add, omit or embellish, and do not smooth over ambiguity. Prefer accuracy to elegance, but write grammatical {{ target_lang }}.
Whenever a term from INPUT_JSON.bible.glossary appears, use its rendering.
Output rules:
- Return "spans" that cover every segment id in INPUT_JSON.segments exactly once, in order.
- A span normally covers one segment (start equals end). Merge consecutive segments into one span (start = first id, end = last id) only when grammar makes separate sentences impossible.
- Put translator notes (ambiguities, untranslatable items) in "notes"; otherwise leave it empty.
Respond with a single JSON object only.
```

`engine/coglossa/prompts/templates/contextual/v1.j2`:
```jinja
You are an expert translator of {{ genre }} from {{ source_lang }} into {{ target_lang }}. Translate for meaning and effect, not word for word.
The register of the text is: {{ register }}.
Follow INPUT_JSON.bible: apply its style_guide, use its glossary renderings and entity names, respect each entity's forms of address and speech style, and handle its cultural_references deliberately.
INPUT_JSON.context_before and INPUT_JSON.context_after are the surrounding source text. Use them to understand the passage, but do not translate them.
Translate INPUT_JSON.segments so a native {{ target_lang }} reader experiences what a {{ source_lang }} reader would: the same meaning, tone, rhythm, imagery and implication. Render idioms as natural equivalents. Recreate wordplay where you can and note it where you cannot.
Output rules:
- Return "spans" that cover every segment id exactly once, in order.
- You may merge consecutive segments into one span (start = first id, end = last id) when natural {{ target_lang }} requires it. Never move content between spans.
- Use "notes" to explain any significant liberty you took; otherwise leave it empty.
Respond with a single JSON object only.
```

`engine/coglossa/prompts/templates/reconcile/v1.j2`:
```jinja
You are the reviewing editor reconciling independent translations from {{ source_lang }} into {{ target_lang }}.
INPUT_JSON.segments is the source text. INPUT_JSON.candidates holds one translation from each translator ({{ translators | join(", ") }}), as spans of segment ids. INPUT_JSON.bible holds the decisions every translation must respect.
For each passage, produce the final translation:
- Choose the best candidate, merge the best parts of several, or rewrite when all of them fall short. Accuracy of meaning comes first, then faithfulness to tone and style, then fluency.
- chosen_from: the name of the translator you used, "merged", or "rewritten".
- agreement: "agree" if the candidates convey the same meaning; "minor" if they differ only in wording or nuance; "major" if they differ in meaning, or one omits or adds content.
- rationale: required unless agreement is "agree". In one or two sentences a translator can check, explain the divergence and why your choice is right.
- issues: specific problems you noticed (for example, "the 'literal' candidate drops the negation").
- cultural_or_wordplay: true if the passage contains a cultural reference, idiom or wordplay that cannot fully carry over.
- proposed_terms: new glossary entries the bible should gain. Leave "locked" false.
Output rules: "spans" must cover every segment id exactly once, in order; a span may cover several consecutive segments. Respond with a single JSON object only.
```

- [ ] **Step 7: Run the prompt tests, the full suite and lint**

Run: `cd engine && uv run pytest tests/prompts tests/llm -v --no-cov && uv run pytest && cd .. && make lint`
Expected: pass; coverage ≥95%.

- [ ] **Step 8: Commit**

```bash
git add engine/coglossa/llm/retry.py engine/coglossa/prompts engine/tests/llm/test_retry.py engine/tests/prompts
git commit -m "feat: retry policy and versioned prompt library with v1 agent templates"
```

### Task 6: Agent input format, synthetic responder and fake backends

**Files:**
- Create: `engine/coglossa/llm/payload.py`, `engine/coglossa/testing/__init__.py` (empty), `engine/coglossa/testing/synthetic.py`, `engine/coglossa/testing/fake_backend.py`, `engine/coglossa/testing/fake_openai.py`
- Test: `engine/tests/testing/__init__.py` (empty), `engine/tests/testing/test_synthetic.py`, `engine/tests/testing/test_fake_backend.py`, `engine/tests/testing/test_fake_openai.py`

**Interfaces:**
- Consumes: `ChatRequest`, `ChatResponse`, `OpenAICompatBackend` (Task 4).
- Produces:
  - `coglossa.llm.payload.INPUT_MARKER = "INPUT_JSON:"`, `render_input(payload: dict) -> str`, `parse_input(messages: list[dict]) -> dict`
  - `coglossa.testing.synthetic.synthetic_response(payload: dict) -> dict` and `chat_completion(content: str, *, finish_reason="stop") -> dict`
  - `coglossa.testing.fake_backend.FakeBackend(name="fake", models=("fake-model",))`, which implements `Backend`. It also has `.script(task: str, *items: str | BaseException)`, `.requests: list[ChatRequest]` and `.closed`
  - `coglossa.testing.fake_openai.FakeOpenAIServer(host="127.0.0.1", port=0, models=("fake-model",))` with `.start()`, `.stop()`, `.base_url`, `.requests`, `.serve_forever()`, used as a context manager
- **Agent payload contract.** Every stage sends `render_input(payload)` as the user message, and every payload has a `"task"` key. The tasks and their keys:
  - `profile`: `sample`, `source_lang_hint`, `target_lang`
  - `bible`: `depth`, `source_lang`, `target_lang`, `genre`, `register`, `text`, `prior_bible`
  - `bible_merge`: `source_lang`, `target_lang`, `partials` (list of bible dicts), `prior_bible`
  - `translate`: `translator`, `source_lang`, `target_lang`, `genre`, `register`, `bible`, `context_before`, `context_after`, `segments` (list of `{"id", "text"}`)
  - `reconcile`: `source_lang`, `target_lang`, `bible`, `segments`, `candidates` (list of `{"translator", "spans": [SpanText dicts]}`)

The synthetic responder is a deterministic fake translator: it returns `"[<target_lang>] <source text>"` for each segment. Tests and demos use it to exercise the full pipeline without a model.

- [ ] **Step 1: Write the failing synthetic and payload tests**

`engine/tests/testing/test_synthetic.py`:
```python
import json

import pytest

from coglossa.llm.payload import INPUT_MARKER, parse_input, render_input
from coglossa.schema import Bible, ProfileAssessment, ReconcileOutput, TranslationOutput
from coglossa.testing.synthetic import chat_completion, synthetic_response

SEGS = [{"id": "s1", "text": "Bonjour."}, {"id": "s2", "text": "Ça va ?"}]


def test_render_and_parse_round_trip():
    payload = {"task": "profile", "sample": "héllo"}
    text = render_input(payload)
    assert text.startswith(INPUT_MARKER)
    assert "héllo" in text  # not ASCII-escaped
    messages = [{"role": "system", "content": "x"}, {"role": "user", "content": text}]
    assert parse_input(messages) == payload


def test_parse_input_without_marker_raises():
    with pytest.raises(ValueError, match="INPUT_JSON"):
        parse_input([{"role": "user", "content": "hello"}])


def test_profile_response_is_valid():
    out = synthetic_response({"task": "profile", "sample": "x", "source_lang_hint": "fr",
                              "target_lang": "en"})
    assert ProfileAssessment.model_validate(out).source_lang == "fr"
    out = synthetic_response({"task": "profile", "sample": "x", "source_lang_hint": None,
                              "target_lang": "en"})
    assert out["source_lang"] == "und"


def test_bible_and_merge_responses_are_valid():
    bible = Bible.model_validate(synthetic_response({"task": "bible", "text": "a b c"}))
    assert "3 words" in bible.summary
    merged = synthetic_response({"task": "bible_merge",
                                 "partials": [bible.model_dump(), bible.model_dump()]})
    assert Bible.model_validate(merged).summary.count("3 words") == 2


def test_translate_response_covers_segments():
    out = synthetic_response({"task": "translate", "target_lang": "en", "segments": SEGS})
    parsed = TranslationOutput.model_validate(out)
    assert [(s.start, s.end, s.text) for s in parsed.spans] == [
        ("s1", "s1", "[en] Bonjour."), ("s2", "s2", "[en] Ça va ?")
    ]


def test_reconcile_picks_first_candidate():
    spans = [{"start": "s1", "end": "s2", "text": "Hi. OK?", "notes": ""}]
    out = synthetic_response({"task": "reconcile", "segments": SEGS,
                              "candidates": [{"translator": "literal", "spans": spans}]})
    parsed = ReconcileOutput.model_validate(out)
    assert parsed.spans[0].chosen_from == "literal"
    assert parsed.spans[0].text == "Hi. OK?"


def test_unknown_task_raises():
    with pytest.raises(ValueError, match="unknown task"):
        synthetic_response({"task": "dance"})


def test_chat_completion_shape():
    body = chat_completion('{"a": 1}', finish_reason="length")
    assert body["choices"][0]["message"]["content"] == '{"a": 1}'
    assert body["choices"][0]["finish_reason"] == "length"
    assert json.dumps(body)
```

- [ ] **Step 2: Run them and confirm they fail, then implement payload and synthetic**

Run: `cd engine && uv run pytest tests/testing/test_synthetic.py -v --no-cov` → FAIL (modules missing).

`engine/coglossa/llm/payload.py`:
```python
"""How structured input is handed to agents: a marker line followed by pretty JSON."""

import json
from typing import Any

INPUT_MARKER = "INPUT_JSON:"


def render_input(payload: dict[str, Any]) -> str:
    return f"{INPUT_MARKER}\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def parse_input(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in messages:
        content = message.get("content") or ""
        if message.get("role") == "user" and INPUT_MARKER in content:
            return json.loads(content.split(INPUT_MARKER, 1)[1])
    raise ValueError(f"no {INPUT_MARKER} block in messages")
```

`engine/coglossa/testing/synthetic.py`:
```python
"""Deterministic stand-in for a model: valid output for every agent task, no intelligence.
Translations are "[<target_lang>] <source>" so tests can see exactly what flowed where."""

from collections.abc import Callable
from typing import Any

Payload = dict[str, Any]


def _profile(p: Payload) -> Payload:
    return {
        "source_lang": p.get("source_lang_hint") or "und",
        "mixed_language": False,
        "genre": "other",
        "tone_register": "neutral",
    }


def _bible(p: Payload) -> Payload:
    words = len(str(p.get("text", "")).split())
    return {
        "summary": f"Synthetic summary of {words} words.",
        "style_guide": "Synthetic style guide.",
        "glossary": [],
        "entities": [],
        "cultural_references": [],
    }


def _bible_merge(p: Payload) -> Payload:
    partials = p["partials"]
    return {
        "summary": " ".join(b["summary"] for b in partials),
        "style_guide": partials[0]["style_guide"],
        "glossary": [g for b in partials for g in b.get("glossary", [])],
        "entities": [e for b in partials for e in b.get("entities", [])],
        "cultural_references": [c for b in partials for c in b.get("cultural_references", [])],
    }


def _translate(p: Payload) -> Payload:
    tag = p.get("target_lang", "xx")
    return {
        "spans": [
            {"start": s["id"], "end": s["id"], "text": f"[{tag}] {s['text']}", "notes": ""}
            for s in p["segments"]
        ]
    }


def _reconcile(p: Payload) -> Payload:
    first = p["candidates"][0]
    return {
        "spans": [
            {
                "start": s["start"],
                "end": s["end"],
                "text": s["text"],
                "agreement": "agree",
                "chosen_from": first["translator"],
                "rationale": "",
                "issues": [],
                "cultural_or_wordplay": False,
                "proposed_terms": [],
            }
            for s in first["spans"]
        ]
    }


_HANDLERS: dict[str, Callable[[Payload], Payload]] = {
    "profile": _profile,
    "bible": _bible,
    "bible_merge": _bible_merge,
    "translate": _translate,
    "reconcile": _reconcile,
}


def synthetic_response(payload: Payload) -> Payload:
    handler = _HANDLERS.get(payload.get("task", ""))
    if handler is None:
        raise ValueError(f"unknown task '{payload.get('task')}'")
    return handler(payload)


def chat_completion(content: str, *, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10},
    }
```

Run the tests again. Expected: all pass.

- [ ] **Step 3: Write the failing FakeBackend tests**

`engine/tests/testing/test_fake_backend.py`:
```python
import json

import pytest

from coglossa.config import StructuredOutput
from coglossa.llm.payload import render_input
from coglossa.llm.types import ChatMessage, ChatRequest, TransientBackendError
from coglossa.testing.fake_backend import FakeBackend


def _req(payload: dict) -> ChatRequest:
    return ChatRequest(
        model="fake-model",
        messages=[ChatMessage(role="system", content="s"),
                  ChatMessage(role="user", content=render_input(payload))],
        schema_name="X",
        json_schema={},
        structured_output=StructuredOutput.JSON_SCHEMA,
    )


PROFILE = {"task": "profile", "sample": "x", "source_lang_hint": "fr", "target_lang": "en"}


async def test_answers_synthetically_and_records_requests():
    backend = FakeBackend()
    resp = await backend.chat(_req(PROFILE))
    assert json.loads(resp.content)["source_lang"] == "fr"
    assert len(backend.requests) == 1
    assert await backend.list_models() == ["fake-model"]


async def test_scripted_items_are_consumed_in_order_per_task():
    backend = FakeBackend()
    backend.script("profile", "not json", TransientBackendError("busy"))
    assert (await backend.chat(_req(PROFILE))).content == "not json"
    with pytest.raises(TransientBackendError):
        await backend.chat(_req(PROFILE))
    # script exhausted -> synthetic again
    assert json.loads((await backend.chat(_req(PROFILE))).content)["source_lang"] == "fr"


async def test_aclose_marks_closed():
    backend = FakeBackend()
    await backend.aclose()
    assert backend.closed is True
```

- [ ] **Step 4: Implement `engine/coglossa/testing/fake_backend.py`**

```python
"""In-process Backend for tests: scripted faults first, synthetic answers otherwise."""

import json
from collections import defaultdict, deque

from coglossa.llm.payload import parse_input
from coglossa.llm.types import ChatRequest, ChatResponse
from coglossa.testing.synthetic import synthetic_response

Scripted = str | BaseException


class FakeBackend:
    def __init__(self, name: str = "fake", models: tuple[str, ...] = ("fake-model",)) -> None:
        self.name = name
        self._models = list(models)
        self._scripts: dict[str, deque[Scripted]] = defaultdict(deque)
        self.requests: list[ChatRequest] = []
        self.closed = False

    def script(self, task: str, *items: Scripted) -> None:
        """Queue raw replies (str) or exceptions to raise for the next calls of `task`."""
        self._scripts[task].extend(items)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        payload = parse_input([m.model_dump() for m in request.messages])
        queue = self._scripts.get(payload["task"])
        if queue:
            item = queue.popleft()
            if isinstance(item, BaseException):
                raise item
            return ChatResponse(content=item, tokens_in=1, tokens_out=1)
        content = json.dumps(synthetic_response(payload), ensure_ascii=False)
        return ChatResponse(content=content, tokens_in=10, tokens_out=10)

    async def list_models(self) -> list[str]:
        return list(self._models)

    async def aclose(self) -> None:
        self.closed = True
```

Run: `cd engine && uv run pytest tests/testing/test_fake_backend.py -v --no-cov`
Expected: 3 passed.

- [ ] **Step 5: Write the failing fake-server tests**

These go through real sockets on 127.0.0.1, using the real `OpenAICompatBackend`.

`engine/tests/testing/test_fake_openai.py`:
```python
import json

import httpx
import pytest

from coglossa.config import StructuredOutput
from coglossa.llm.openai_compat import OpenAICompatBackend
from coglossa.llm.payload import render_input
from coglossa.llm.types import ChatMessage, ChatRequest, PermanentBackendError
from coglossa.testing.fake_openai import FakeOpenAIServer


def _req(content: str) -> ChatRequest:
    return ChatRequest(model="fake-model", messages=[ChatMessage(role="user", content=content)],
                       schema_name="X", json_schema={},
                       structured_output=StructuredOutput.JSON_SCHEMA)


async def test_chat_and_models_over_http():
    with FakeOpenAIServer(models=("m1", "m2")) as server:
        backend = OpenAICompatBackend("fake", server.base_url)
        payload = {"task": "translate", "target_lang": "de",
                   "segments": [{"id": "s1", "text": "Hallo"}]}
        resp = await backend.chat(_req(render_input(payload)))
        assert json.loads(resp.content)["spans"][0]["text"] == "[de] Hallo"
        assert await backend.list_models() == ["m1", "m2"]
        assert server.requests[0]["model"] == "fake-model"
        await backend.aclose()


async def test_bad_requests_get_400_and_unknown_paths_404():
    with FakeOpenAIServer() as server:
        backend = OpenAICompatBackend("fake", server.base_url)
        with pytest.raises(PermanentBackendError, match="HTTP 400"):
            await backend.chat(_req("no marker here"))
        await backend.aclose()
        async with httpx.AsyncClient() as client:
            assert (await client.get(server.base_url + "/nope")).status_code == 404
            assert (await client.post(server.base_url + "/nope", json={})).status_code == 404


def test_start_stop_idempotent():
    server = FakeOpenAIServer().start()
    assert server.base_url.startswith("http://127.0.0.1:")
    server.stop()
    server.stop()
```

- [ ] **Step 6: Implement `engine/coglossa/testing/fake_openai.py`**

```python
"""A tiny OpenAI-compatible HTTP server (stdlib only) answering with the synthetic responder.
Used by integration tests, by `coglossa fake-openai`, and later by the fake-openai container."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from coglossa.llm.payload import parse_input
from coglossa.testing.synthetic import chat_completion, synthetic_response


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    models: list[str]
    requests: list[dict[str, Any]]


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            data = [{"id": m, "object": "model"} for m in self.server.models]
            self._send(200, {"object": "list", "data": data})
        else:
            self._send(404, {"error": {"message": f"no route {self.path}"}})

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": f"no route {self.path}"}})
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.server.requests.append(body)
        try:
            payload = parse_input(body["messages"])
            content = json.dumps(synthetic_response(payload), ensure_ascii=False)
        except (ValueError, KeyError) as e:
            self._send(400, {"error": {"message": str(e)}})
            return
        self._send(200, chat_completion(content))

    def _send(self, status: int, obj: dict[str, Any]) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeOpenAIServer:
    def __init__(
        self, host: str = "127.0.0.1", port: int = 0, models: tuple[str, ...] = ("fake-model",)
    ) -> None:
        self._server = _Server((host, port), _Handler)
        self._server.models = list(models)
        self._server.requests = []
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self._server.requests

    def start(self) -> "FakeOpenAIServer":
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        """Blocking variant for the CLI."""
        self._server.serve_forever()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
        self._thread = None

    def __enter__(self) -> "FakeOpenAIServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
```

`serve_forever` is tested in Task 16 through the CLI, with a monkeypatch.

- [ ] **Step 7: Run the testing suite, the full suite and lint**

Run: `cd engine && uv run pytest tests/testing -v --no-cov && uv run pytest && cd .. && make lint`
Expected: pass; coverage ≥95%. The one-line `serve_forever` is exercised in Task 16.

- [ ] **Step 8: Commit**

```bash
git add engine/coglossa/llm/payload.py engine/coglossa/testing engine/tests/testing
git commit -m "test: synthetic responder, FakeBackend and stdlib fake OpenAI server"
```

### Task 7: AgentRunner (structured output, repair, retry, call log)

**Files:**
- Create: `engine/coglossa/llm/agent.py`
- Modify: `engine/tests/conftest.py` (add `fake_backend` and `make_runner` fixtures)
- Test: `engine/tests/llm/test_agent.py`

**Interfaces:**
- Consumes:
  - `CoglossaConfig`, `StructuredOutput` (Task 3)
  - `Backend`, `BackendError`, `ChatMessage`, `ChatRequest` (Task 4)
  - `RetryPolicy`, `call_with_retry`, `PromptLibrary`, `RenderedPrompt` (Task 5)
  - `render_input` (Task 6)
  - `LLMCallRecord`, `Usage` (Task 2)
- Produces:
  - `StructuredOutputError(message, *, raw=None)` with a `.raw` attribute
  - `AGENT_FAILURES = (StructuredOutputError, BackendError)`, the exceptions a stage catches to degrade gracefully
  - `extract_json(content: str) -> str`
  - `CallLog(store_io=True)` with `.records: list[LLMCallRecord]`, `.add(record)` and `.usage() -> Usage`
  - `AgentRunner(cfg, backends: dict[str, Backend], prompts: PromptLibrary, log: CallLog, *, sleep=asyncio.sleep, rng=None)`, with `.cfg`, `.log` and:
    `async run(*, role: str, model_key: str, prompt_id: str, prompt_vars: dict, payload: dict, output_type: type[M], validate: Callable[[M], list[str]] | None = None) -> M`
  - Test fixtures: `fake_backend` (a `FakeBackend` named `"fake"`), and `make_runner(cfg=None, *, store_io=True) -> AgentRunner`, which wires `{"fake": fake_backend}` with a no-op sleep.

- [ ] **Step 1: Add the fixtures to `engine/tests/conftest.py`**

Add these imports at the top:
```python
from coglossa.llm.agent import AgentRunner, CallLog
from coglossa.prompts.library import PromptLibrary
from coglossa.testing.fake_backend import FakeBackend
```
Append:
```python
async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend(name="fake")


@pytest.fixture
def make_runner(make_config, fake_backend):
    def _make(cfg: CoglossaConfig | None = None, *, store_io: bool = True) -> AgentRunner:
        cfg = cfg or make_config()
        return AgentRunner(
            cfg,
            {"fake": fake_backend},
            PromptLibrary(cfg.prompts_dir),
            CallLog(store_io=store_io),
            sleep=_no_sleep,
        )

    return _make
```

- [ ] **Step 2: Write the failing agent tests**

`engine/tests/llm/test_agent.py`:
```python
import json

import pytest

from coglossa.llm.agent import StructuredOutputError, extract_json
from coglossa.llm.types import PermanentBackendError, TransientBackendError
from coglossa.schema import ProfileAssessment
from tests.conftest import BASE_CONFIG

PAYLOAD = {"task": "profile", "sample": "Bonjour", "source_lang_hint": "fr", "target_lang": "en"}
GOOD = json.dumps({"source_lang": "fr", "mixed_language": False, "genre": "news",
                   "tone_register": "plain"})


async def _run(runner, **kw):
    args = dict(role="profiler", model_key="model-b", prompt_id="profile",
                prompt_vars={"target_lang": "en"}, payload=PAYLOAD, output_type=ProfileAssessment)
    args.update(kw)
    return await runner.run(**args)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('Sure!\n```json\n{"a": 1}\n```\nDone.', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('Here you go: {"a": {"b": 2}} hope it helps', '{"a": {"b": 2}}'),
        ("no json at all", "no json at all"),
    ],
)
def test_extract_json(content, expected):
    assert extract_json(content) == expected


async def test_success_records_call(make_runner, fake_backend):
    runner = make_runner()
    out = await _run(runner)
    assert out.source_lang == "fr"
    [rec] = runner.log.records
    assert (rec.role, rec.backend, rec.model, rec.prompt_id, rec.prompt_version) == (
        "profiler", "fake", "model-b", "profile", 1)
    assert rec.ok and rec.attempt == 1 and rec.tokens_in == 10
    assert rec.request["messages"][1]["content"].startswith("INPUT_JSON:")
    assert rec.response
    req = fake_backend.requests[0]
    assert req.schema_name == "ProfileAssessment"
    assert "JSON Schema" not in req.messages[0].content  # json_schema mode: server enforces


async def test_wrapped_json_from_local_models_is_accepted(make_runner, fake_backend):
    fake_backend.script("profile", f"Of course!\n```json\n{GOOD}\n```")
    out = await _run(make_runner())
    assert out.genre.value == "news"


async def test_invalid_output_is_repaired_once(make_runner, fake_backend):
    fake_backend.script("profile", "garbage")
    runner = make_runner()
    out = await _run(runner)
    assert out.source_lang == "fr"
    assert len(runner.log.records) == 2
    repair = fake_backend.requests[1].messages
    assert [m.role for m in repair] == ["system", "user", "assistant", "user"]
    assert repair[2].content == "garbage"
    assert "invalid" in repair[3].content


async def test_fails_after_repair(make_runner, fake_backend):
    fake_backend.script("profile", "garbage", '{"source_lang": "fr"}')
    with pytest.raises(StructuredOutputError, match="profiler: invalid output") as exc:
        await _run(make_runner())
    assert exc.value.raw == '{"source_lang": "fr"}'


async def test_custom_validator_triggers_repair(make_runner, fake_backend):
    seen = []

    def validate(obj):
        seen.append(obj)
        return ["source_lang must not be fr"] if len(seen) == 1 else []

    out = await _run(make_runner(), validate=validate)
    assert len(seen) == 2 and out.source_lang == "fr"
    assert "source_lang must not be fr" in fake_backend.requests[1].messages[3].content


async def test_schema_in_prompt_when_server_cannot_enforce(make_config, make_runner, fake_backend):
    models = {**BASE_CONFIG["models"],
              "model-b": {"backend": "fake", "context_window": 8192,
                          "structured_output": "json_object", "name": "served-name",
                          "max_output_tokens": 512, "temperature": 0.0}}
    await _run(make_runner(make_config(models=models)))
    req = fake_backend.requests[0]
    assert "JSON Schema" in req.messages[0].content
    assert (req.model, req.max_tokens, req.temperature) == ("served-name", 512, 0.0)


async def test_transient_errors_are_retried_and_logged(make_runner, fake_backend):
    fake_backend.script("profile", TransientBackendError("busy"))
    runner = make_runner()
    await _run(runner)
    assert [(r.attempt, r.ok) for r in runner.log.records] == [(1, False), (2, True)]
    assert runner.log.records[0].error == "busy"


async def test_permanent_errors_propagate(make_runner, fake_backend):
    fake_backend.script("profile", PermanentBackendError("no such model"))
    runner = make_runner()
    with pytest.raises(PermanentBackendError):
        await _run(runner)
    assert len(runner.log.records) == 1


async def test_store_io_false_omits_payloads(make_runner):
    runner = make_runner(store_io=False)
    await _run(runner)
    assert runner.log.records[0].request is None
    assert runner.log.records[0].response is None


async def test_usage_aggregates_by_role(make_runner, fake_backend):
    runner = make_runner()
    await _run(runner)
    await _run(runner, role="other")
    usage = runner.log.usage()
    assert (usage.calls, usage.tokens_in, usage.tokens_out) == (2, 20, 20)
    assert usage.by_role["other"] == {"calls": 1, "tokens_in": 10, "tokens_out": 10}
```

- [ ] **Step 3: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/llm/test_agent.py -v --no-cov`
Expected: FAIL (`coglossa.llm.agent` is missing).

- [ ] **Step 4: Implement `engine/coglossa/llm/agent.py`**

```python
"""One agent call: prompt + JSON input -> validated Pydantic output (spec §4.5).

Flow: render prompt -> call backend (transient errors retried with backoff) -> extract JSON ->
validate (schema + optional semantic validator) -> on failure, one repair turn -> else
StructuredOutputError. Every backend attempt is recorded in the CallLog."""

import asyncio
import json
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from coglossa.config import CoglossaConfig, StructuredOutput
from coglossa.llm.payload import render_input
from coglossa.llm.retry import RetryPolicy, call_with_retry
from coglossa.llm.types import Backend, BackendError, ChatMessage, ChatRequest
from coglossa.prompts.library import PromptLibrary, RenderedPrompt
from coglossa.schema import LLMCallRecord, Usage


class StructuredOutputError(Exception):
    def __init__(self, message: str, *, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


AGENT_FAILURES: tuple[type[Exception], ...] = (StructuredOutputError, BackendError)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(content: str) -> str:
    """Pull the JSON object out of replies that wrap it in prose or Markdown fences."""
    text = content.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        return fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        return text[start : end + 1]
    return text


def _repair_message(errors: list[str]) -> str:
    bullets = "\n".join(f"- {e}" for e in errors[:20])
    return f"Your previous response was invalid:\n{bullets}\nReturn the corrected JSON object only."


@dataclass
class CallLog:
    store_io: bool = True
    records: list[LLMCallRecord] = field(default_factory=list)

    def add(self, record: LLMCallRecord) -> None:
        self.records.append(record)

    def usage(self) -> Usage:
        by_role: dict[str, dict[str, int]] = {}
        for r in self.records:
            d = by_role.setdefault(r.role, {"calls": 0, "tokens_in": 0, "tokens_out": 0})
            d["calls"] += 1
            d["tokens_in"] += r.tokens_in or 0
            d["tokens_out"] += r.tokens_out or 0
        return Usage(
            calls=len(self.records),
            tokens_in=sum(d["tokens_in"] for d in by_role.values()),
            tokens_out=sum(d["tokens_out"] for d in by_role.values()),
            by_role=by_role,
        )


class AgentRunner:
    def __init__(
        self,
        cfg: CoglossaConfig,
        backends: dict[str, Backend],
        prompts: PromptLibrary,
        log: CallLog,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.cfg = cfg
        self.log = log
        self.prompts = prompts
        self._backends = backends
        self._policy = RetryPolicy.from_config(cfg.retry)
        self._sleep = sleep
        self._rng = rng or random.Random()

    async def run[M: BaseModel](
        self,
        *,
        role: str,
        model_key: str,
        prompt_id: str,
        prompt_vars: dict[str, Any],
        payload: dict[str, Any],
        output_type: type[M],
        validate: Callable[[M], list[str]] | None = None,
    ) -> M:
        model = self.cfg.models[model_key]
        backend = self._backends[model.backend]
        prompt = self.prompts.render(prompt_id, **prompt_vars)
        schema = output_type.model_json_schema()
        system = prompt.text
        if model.structured_output is not StructuredOutput.JSON_SCHEMA:
            system += (
                "\n\nRespond with one JSON object that matches this JSON Schema:\n"
                + json.dumps(schema, ensure_ascii=False)
            )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=render_input(payload)),
        ]
        content = ""
        errors: list[str] = []
        for _ in range(2):  # first attempt + one repair turn
            if errors:
                messages = [
                    *messages,
                    ChatMessage(role="assistant", content=content),
                    ChatMessage(role="user", content=_repair_message(errors)),
                ]
            request = ChatRequest(
                model=model.name or model_key,
                messages=messages,
                schema_name=output_type.__name__,
                json_schema=schema,
                structured_output=model.structured_output,
                temperature=model.temperature,
                max_tokens=model.max_output_tokens,
            )
            content = await self._call(role, backend, request, prompt)
            result, errors = _parse(content, output_type, validate)
            if result is not None and not errors:
                return result
        raise StructuredOutputError(
            f"{role}: invalid output after repair: {'; '.join(errors[:5])}", raw=content
        )

    async def _call(
        self, role: str, backend: Backend, request: ChatRequest, prompt: RenderedPrompt
    ) -> str:
        attempt = 0

        async def once() -> str:
            nonlocal attempt
            attempt += 1
            try:
                resp = await backend.chat(request)
            except BackendError as e:
                self._record(role, backend, request, prompt, attempt, ok=False, error=str(e))
                raise
            self._record(
                role, backend, request, prompt, attempt, ok=True,
                latency_ms=resp.latency_ms, tokens_in=resp.tokens_in,
                tokens_out=resp.tokens_out, response=resp.content,
            )
            return resp.content

        return await call_with_retry(once, self._policy, sleep=self._sleep, rng=self._rng)

    def _record(
        self,
        role: str,
        backend: Backend,
        request: ChatRequest,
        prompt: RenderedPrompt,
        attempt: int,
        *,
        ok: bool,
        error: str | None = None,
        latency_ms: int = 0,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        response: str | None = None,
    ) -> None:
        store = self.log.store_io
        self.log.add(
            LLMCallRecord(
                role=role,
                backend=backend.name,
                model=request.model,
                prompt_id=prompt.id,
                prompt_version=prompt.version,
                attempt=attempt,
                ok=ok,
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                error=error,
                request={"messages": [m.model_dump() for m in request.messages]} if store else None,
                response=response if store else None,
            )
        )


def _parse[M: BaseModel](
    content: str, output_type: type[M], validate: Callable[[M], list[str]] | None
) -> tuple[M | None, list[str]]:
    try:
        obj = output_type.model_validate_json(extract_json(content))
    except ValidationError as e:
        return None, [
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in e.errors()
        ]
    return obj, (validate(obj) if validate else [])
```

- [ ] **Step 5: Run the agent tests, the full suite and lint**

Run: `cd engine && uv run pytest tests/llm/test_agent.py -v --no-cov && uv run pytest && cd .. && make lint`
Expected: pass; coverage ≥95%.

- [ ] **Step 6: Commit**

```bash
git add engine/coglossa/llm/agent.py engine/tests/conftest.py engine/tests/llm/test_agent.py
git commit -m "feat(llm): AgentRunner with schema validation, one repair turn, retries and call log"
```

### Task 8: Ingestion for TXT, Markdown and DOCX

**Files:**
- Create: `engine/coglossa/ingest/__init__.py` (empty), `engine/coglossa/ingest/base.py`, `engine/coglossa/ingest/txt.py`, `engine/coglossa/ingest/markdown.py`, `engine/coglossa/ingest/docx.py`, `engine/coglossa/ingest/registry.py`
- Test: `engine/tests/ingest/__init__.py` (empty), `engine/tests/ingest/test_txt_markdown.py`, `engine/tests/ingest/test_docx.py`, `engine/tests/ingest/test_registry.py`

**Interfaces:**
- Consumes: `Block`, `BlockKind`, `Provenance`, `SourceDocument` (Task 2).
- Produces:
  - `IngestError(ValueError)` and `UnsupportedFormatError(IngestError)`
  - `file_sha256(path) -> str`, `read_utf8(path) -> str`, `split_paragraphs(text) -> list[str]`, `is_scene_break(text) -> bool`
  - `make_blocks(source_path: str, items: list[tuple[BlockKind, str, str | None]]) -> list[Block]`
  - The `Ingestor` protocol: attributes `format` and `extensions`, method `ingest(path) -> SourceDocument`
  - `TextIngestor`, `MarkdownIngestor`, `DocxIngestor`
  - `ingest_file(path: Path) -> SourceDocument`, which raises `IngestError` for unreadable or empty documents
- Line breaks *inside* a paragraph are preserved, because poetry depends on them.

- [ ] **Step 1: Write the failing TXT and Markdown tests**

`engine/tests/ingest/test_txt_markdown.py`:
```python
import pytest

from coglossa.ingest.base import IngestError, is_scene_break, split_paragraphs
from coglossa.ingest.markdown import MarkdownIngestor
from coglossa.ingest.txt import TextIngestor
from coglossa.schema import BlockKind


@pytest.mark.parametrize("line", ["***", "* * *", "---", "___", "#", "~~~", "§", "  * * *  "])
def test_scene_breaks(line):
    assert is_scene_break(line)


@pytest.mark.parametrize("line", ["# Title", "Hello", "**bold**", "--", "- item"])
def test_not_scene_breaks(line):
    assert not is_scene_break(line)


def test_split_paragraphs_keeps_inner_line_breaks():
    text = "Roses are red,\r\nviolets are blue.\r\n\r\n\n  Second para.  \n\n\n"
    assert split_paragraphs(text) == ["Roses are red,\nviolets are blue.", "Second para."]


def test_txt_ingest(tmp_path):
    path = tmp_path / "story.txt"
    path.write_text("﻿First paragraph.\n\n* * *\n\nSecond, after a break.\n", encoding="utf-8")
    doc = TextIngestor().ingest(path)
    assert doc.format == "txt"
    assert len(doc.sha256) == 64
    assert [(b.id, b.kind, b.text) for b in doc.blocks] == [
        ("b1", BlockKind.PARAGRAPH, "First paragraph."),
        ("b2", BlockKind.BREAK, "* * *"),
        ("b3", BlockKind.PARAGRAPH, "Second, after a break."),
    ]
    assert doc.blocks[2].provenance.index == 2
    assert doc.blocks[0].provenance.source_path == str(path)


def test_txt_rejects_non_utf8(tmp_path):
    path = tmp_path / "latin1.txt"
    path.write_bytes("café".encode("latin-1"))
    with pytest.raises(IngestError, match=r"latin1\.txt is not valid UTF-8"):
        TextIngestor().ingest(path)


def test_markdown_ingest(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(
        "# Chapter One\n\nIt was night.\n\n- milk\n- 2. bread\n\n1. first\n2. second\n\n"
        "---\n\n## Part Two\nThe heading has a body.\n\n* * *\n",
        encoding="utf-8",
    )
    doc = MarkdownIngestor().ingest(path)
    assert [(b.kind, b.text) for b in doc.blocks] == [
        (BlockKind.HEADING, "Chapter One"),
        (BlockKind.PARAGRAPH, "It was night."),
        (BlockKind.LIST_ITEM, "milk"),
        (BlockKind.LIST_ITEM, "2. bread"),
        (BlockKind.LIST_ITEM, "first"),
        (BlockKind.LIST_ITEM, "second"),
        (BlockKind.BREAK, "---"),
        (BlockKind.HEADING, "Part Two"),
        (BlockKind.PARAGRAPH, "The heading has a body."),
        (BlockKind.BREAK, "* * *"),
    ]
    assert doc.format == "md"
```

- [ ] **Step 2: Run them and confirm they fail, then implement the base, TXT and Markdown modules**

Run: `cd engine && uv run pytest tests/ingest/test_txt_markdown.py -v --no-cov` → FAIL (modules missing).

`engine/coglossa/ingest/base.py`:
```python
"""Shared ingestion helpers and the Ingestor protocol."""

import hashlib
import re
from pathlib import Path
from typing import Protocol

from coglossa.schema import Block, BlockKind, Provenance, SourceDocument


class IngestError(ValueError):
    """The file cannot be turned into translatable text; message is user-facing."""


class UnsupportedFormatError(IngestError):
    pass


class Ingestor(Protocol):
    format: str
    extensions: tuple[str, ...]

    def ingest(self, path: Path) -> SourceDocument: ...


_SCENE_BREAK_RE = re.compile(r"^\s*(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,}|~{3,}|#|§)\s*$")
_BLANK_LINES_RE = re.compile(r"\n\s*\n")


def is_scene_break(text: str) -> bool:
    return bool(_SCENE_BREAK_RE.match(text))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_utf8(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise IngestError(
            f"{path.name} is not valid UTF-8 (byte {e.start}); re-save it as UTF-8 and retry"
        ) from e


def split_paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [p.strip() for p in _BLANK_LINES_RE.split(normalized) if p.strip()]


def make_blocks(source_path: str, items: list[tuple[BlockKind, str, str | None]]) -> list[Block]:
    return [
        Block(
            id=f"b{i + 1}",
            kind=kind,
            text=text,
            provenance=Provenance(source_path=source_path, index=i, locator=locator),
        )
        for i, (kind, text, locator) in enumerate(items)
    ]
```

`engine/coglossa/ingest/txt.py`:
```python
from pathlib import Path

from coglossa.ingest.base import file_sha256, is_scene_break, make_blocks, read_utf8, split_paragraphs
from coglossa.schema import BlockKind, SourceDocument


class TextIngestor:
    format = "txt"
    extensions = (".txt",)

    def ingest(self, path: Path) -> SourceDocument:
        items = [
            (BlockKind.BREAK if is_scene_break(p) else BlockKind.PARAGRAPH, p, None)
            for p in split_paragraphs(read_utf8(path))
        ]
        return SourceDocument(
            source_path=str(path),
            format=self.format,
            sha256=file_sha256(path),
            blocks=make_blocks(str(path), items),
        )
```

`engine/coglossa/ingest/markdown.py`:
```python
import re
from pathlib import Path

from coglossa.ingest.base import file_sha256, is_scene_break, make_blocks, read_utf8, split_paragraphs
from coglossa.schema import BlockKind, SourceDocument

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")

Item = tuple[BlockKind, str, str | None]


def _paragraph_items(para: str) -> list[Item]:
    if is_scene_break(para):
        return [(BlockKind.BREAK, para, None)]
    lines = para.split("\n")
    heading = _HEADING_RE.match(lines[0])
    if heading:
        rest = "\n".join(lines[1:]).strip()
        items: list[Item] = [(BlockKind.HEADING, heading.group(1), None)]
        return items + (_paragraph_items(rest) if rest else [])
    list_matches = [_LIST_RE.match(line) for line in lines]
    if all(list_matches):
        return [(BlockKind.LIST_ITEM, m.group(1), None) for m in list_matches if m]
    return [(BlockKind.PARAGRAPH, para, None)]


class MarkdownIngestor:
    format = "md"
    extensions = (".md", ".markdown")

    def ingest(self, path: Path) -> SourceDocument:
        items = [item for p in split_paragraphs(read_utf8(path)) for item in _paragraph_items(p)]
        return SourceDocument(
            source_path=str(path),
            format=self.format,
            sha256=file_sha256(path),
            blocks=make_blocks(str(path), items),
        )
```

Run the tests again. Expected: all pass.

- [ ] **Step 3: Write the failing DOCX tests**

`engine/tests/ingest/test_docx.py`:
```python
import docx
import pytest

from coglossa.ingest.base import IngestError
from coglossa.ingest.docx import DocxIngestor
from coglossa.schema import BlockKind


def test_docx_in_document_order(tmp_path):
    d = docx.Document()
    d.add_heading("Report", level=1)
    d.add_paragraph("Intro paragraph.")
    d.add_paragraph("")  # empty paragraphs are skipped
    d.add_paragraph("A bullet", style="List Bullet")
    table = d.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Left"
    table.cell(0, 1).text = ""
    d.add_paragraph("* * *")
    d.add_paragraph("After the table.")
    path = tmp_path / "r.docx"
    d.save(path)

    doc = DocxIngestor().ingest(path)
    assert doc.format == "docx"
    assert [(b.kind, b.text) for b in doc.blocks] == [
        (BlockKind.HEADING, "Report"),
        (BlockKind.PARAGRAPH, "Intro paragraph."),
        (BlockKind.LIST_ITEM, "A bullet"),
        (BlockKind.TABLE_CELL, "Left"),
        (BlockKind.BREAK, "* * *"),
        (BlockKind.PARAGRAPH, "After the table."),
    ]
    assert doc.blocks[3].provenance.locator == "table 1 row 1 col 1"


def test_merged_cells_are_not_duplicated(tmp_path):
    d = docx.Document()
    table = d.add_table(rows=1, cols=2)
    merged = table.cell(0, 0).merge(table.cell(0, 1))
    merged.text = "Spans both"
    path = tmp_path / "m.docx"
    d.save(path)
    assert [b.text for b in DocxIngestor().ingest(path).blocks] == ["Spans both"]


def test_corrupt_docx_is_ingest_error(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a zip")
    with pytest.raises(IngestError, match="broken.docx is not a readable DOCX"):
        DocxIngestor().ingest(path)
```

- [ ] **Step 4: Implement `engine/coglossa/ingest/docx.py`**

```python
"""DOCX ingestion in true document order (paragraphs and tables interleaved).
Footnotes, headers and text boxes are not read in M1."""

from pathlib import Path

import docx
from docx.table import Table

from coglossa.ingest.base import IngestError, file_sha256, is_scene_break, make_blocks
from coglossa.schema import BlockKind, SourceDocument

Item = tuple[BlockKind, str, str | None]


def _paragraph_kind(style_name: str, text: str) -> BlockKind:
    style = style_name.lower()
    if style.startswith(("heading", "title")):
        return BlockKind.HEADING
    if is_scene_break(text):
        return BlockKind.BREAK
    if style.startswith("list"):
        return BlockKind.LIST_ITEM
    return BlockKind.PARAGRAPH


def _table_items(table: Table, table_no: int) -> list[Item]:
    items: list[Item] = []
    seen: list[object] = []  # keep references: lxml proxies are only stable while referenced
    for r, row in enumerate(table.rows, start=1):
        for c, cell in enumerate(row.cells, start=1):
            tc = cell._tc
            if any(tc is s for s in seen):  # merged cells repeat the same underlying element
                continue
            seen.append(tc)
            text = cell.text.strip()
            if text:
                items.append((BlockKind.TABLE_CELL, text, f"table {table_no} row {r} col {c}"))
    return items


class DocxIngestor:
    format = "docx"
    extensions = (".docx",)

    def ingest(self, path: Path) -> SourceDocument:
        try:
            document = docx.Document(str(path))
        except Exception as e:  # python-docx raises several unrelated types for bad files
            raise IngestError(f"{path.name} is not a readable DOCX file ({e})") from e
        items: list[Item] = []
        table_no = 0
        for part in document.iter_inner_content():
            if isinstance(part, Table):
                table_no += 1
                items.extend(_table_items(part, table_no))
                continue
            text = part.text.strip()
            if text:
                style = part.style.name if part.style is not None else ""
                items.append((_paragraph_kind(style, text), text, None))
        return SourceDocument(
            source_path=str(path),
            format=self.format,
            sha256=file_sha256(path),
            blocks=make_blocks(str(path), items),
        )
```

Run: `cd engine && uv run pytest tests/ingest/test_docx.py -v --no-cov`
Expected: 3 passed.

- [ ] **Step 5: Write the failing registry tests**

`engine/tests/ingest/test_registry.py`:
```python
import pytest

from coglossa.ingest import registry
from coglossa.ingest.base import IngestError, UnsupportedFormatError
from coglossa.schema import SourceDocument


def test_dispatches_by_extension_case_insensitively(tmp_path):
    path = tmp_path / "A.TXT"
    path.write_text("Hello.", encoding="utf-8")
    assert registry.ingest_file(path).format == "txt"


def test_unsupported_extension(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF")
    with pytest.raises(UnsupportedFormatError, match=r"\.pdf.*\.docx, \.markdown, \.md, \.txt"):
        registry.ingest_file(path)


def test_missing_file(tmp_path):
    with pytest.raises(IngestError, match="not found"):
        registry.ingest_file(tmp_path / "nope.txt")


def test_document_without_text_is_rejected(tmp_path):
    path = tmp_path / "breaks.txt"
    path.write_text("* * *\n\n---\n", encoding="utf-8")
    with pytest.raises(IngestError, match="no translatable text"):
        registry.ingest_file(path)


def test_plugin_ingestors(tmp_path, monkeypatch):
    class OdtIngestor:
        format = "odt"
        extensions = (".odt",)

        def ingest(self, path):
            return SourceDocument(source_path=str(path), format="odt", sha256="0",
                                  blocks=[])

    class EP:
        name = "odt"

        def load(self):
            return OdtIngestor

    monkeypatch.setattr(registry, "entry_points", lambda group: [EP()])
    path = tmp_path / "x.odt"
    path.write_bytes(b"")
    with pytest.raises(IngestError, match="no translatable text"):
        registry.ingest_file(path)
```

- [ ] **Step 6: Implement `engine/coglossa/ingest/registry.py`**

```python
"""Pick an ingestor by file extension; third parties add formats via coglossa.ingestors
(entry point value: an Ingestor class). OCR and speech-to-text arrive this way later."""

from importlib.metadata import entry_points
from pathlib import Path

from coglossa.ingest.base import IngestError, Ingestor, UnsupportedFormatError
from coglossa.ingest.docx import DocxIngestor
from coglossa.ingest.markdown import MarkdownIngestor
from coglossa.ingest.txt import TextIngestor
from coglossa.schema import BlockKind, SourceDocument


def available_ingestors() -> list[Ingestor]:
    ingestors: list[Ingestor] = [TextIngestor(), MarkdownIngestor(), DocxIngestor()]
    ingestors += [ep.load()() for ep in entry_points(group="coglossa.ingestors")]
    return ingestors


def ingest_file(path: Path) -> SourceDocument:
    if not path.is_file():
        raise IngestError(f"input file not found: {path}")
    ext = path.suffix.lower()
    ingestors = available_ingestors()
    for ingestor in ingestors:
        if ext in ingestor.extensions:
            doc = ingestor.ingest(path)
            if not any(b.kind is not BlockKind.BREAK and b.text.strip() for b in doc.blocks):
                raise IngestError(f"{path.name} contains no translatable text")
            return doc
    supported = sorted({e for i in ingestors for e in i.extensions})
    raise UnsupportedFormatError(
        f"unsupported file type '{ext or path.name}'; supported: {', '.join(supported)}"
    )
```

- [ ] **Step 7: Run the ingest tests, the full suite and lint**

Run: `cd engine && uv run pytest tests/ingest -v --no-cov && uv run pytest && cd .. && make lint`
Expected: pass; coverage ≥95%.

- [ ] **Step 8: Commit**

```bash
git add engine/coglossa/ingest engine/tests/ingest
git commit -m "feat(ingest): TXT, Markdown and DOCX ingestion with plugin registry"
```

### Task 9: Text metrics and sentence segmentation

**Files:**
- Create: `engine/coglossa/segment/__init__.py` (empty), `engine/coglossa/segment/text_metrics.py`, `engine/coglossa/segment/sentences.py`, `engine/coglossa/segment/segmenter.py`
- Test: `engine/tests/segment/__init__.py` (empty), `engine/tests/segment/test_text_metrics.py`, `engine/tests/segment/test_sentences.py`, `engine/tests/segment/test_segmenter.py`

**Interfaces:**
- Consumes: `SourceDocument`, `Segment`, `BlockKind` (Task 2).
- Produces:
  - `count_words(text) -> int`. Words are separated by whitespace, plus one word per two characters in scripts written without spaces (CJK, Thai, Lao, Khmer, Myanmar).
  - `estimate_tokens(text, bytes_per_token=2.5) -> int`, a conservative estimate from UTF-8 bytes.
  - `MAX_SEGMENT_CHARS = 600`
  - `split_sentences(text, max_chars=MAX_SEGMENT_CHARS) -> list[tuple[int, int]]`
  - `segment_document(doc) -> list[Segment]`, with ids `s1…sN` across the whole document and `BREAK` blocks skipped
- **Guarantee for `split_sentences`:** the spans are ordered, don't overlap, are non-empty and trimmed of whitespace, are at most `max_chars` long, and together cover every non-whitespace character exactly once.

- [ ] **Step 1: Write the failing metrics tests**

`engine/tests/segment/test_text_metrics.py`:
```python
from coglossa.segment.text_metrics import count_words, estimate_tokens


def test_count_words_spaced_scripts():
    assert count_words("Hello brave  new\nworld") == 4
    assert count_words("مرحبا بكم في البيت") == 4
    assert count_words("") == 0


def test_count_words_spaceless_scripts():
    assert count_words("今日は晴れです") == 4  # 7 chars -> ceil(7/2)
    assert count_words("Tokyo 東京") == 2  # 1 word + ceil(2/2)


def test_estimate_tokens_is_byte_based():
    assert estimate_tokens("abcde", 2.5) == 2
    assert estimate_tokens("日本", 2.5) == 3  # 6 bytes
    assert estimate_tokens("", 2.5) == 0
```

- [ ] **Step 2: Implement `engine/coglossa/segment/text_metrics.py`**

```python
"""Cheap, offline text measurements. Deliberately conservative: no tokenizer downloads."""

import math
import re

_SPACELESS = re.compile(
    "[぀-ヿ㐀-䶿一-鿿豈-﫿"  # Japanese kana, CJK
    "฀-๿຀-໿က-႟ក-៿]"  # Thai, Lao, Myanmar, Khmer
)


def count_words(text: str) -> int:
    spaceless = len(_SPACELESS.findall(text))
    return len(_SPACELESS.sub(" ", text).split()) + math.ceil(spaceless / 2)


def estimate_tokens(text: str, bytes_per_token: float = 2.5) -> int:
    return math.ceil(len(text.encode("utf-8")) / bytes_per_token)
```

Run: `cd engine && uv run pytest tests/segment/test_text_metrics.py -v --no-cov`
Expected: 3 passed.

- [ ] **Step 3: Write the failing sentence tests, including property tests**

`engine/tests/segment/test_sentences.py`:
```python
from hypothesis import given, settings
from hypothesis import strategies as st

from coglossa.segment.sentences import MAX_SEGMENT_CHARS, split_sentences


def _split(text: str, **kw) -> list[str]:
    return [text[s:e] for s, e in split_sentences(text, **kw)]


def test_english_abbreviations_initials_and_dialogue():
    text = ('Dr. Smith met J. K. Rowling. They talked, e.g. about books! '
            '"Stop!" she said. Did it work? Yes…')
    assert _split(text) == [
        "Dr. Smith met J. K. Rowling.",
        "They talked, e.g. about books!",
        '"Stop!" she said.',
        "Did it work?",
        "Yes…",
    ]


def test_decimals_are_not_boundaries():
    assert _split("Pi is 3.14 today. Ok.") == ["Pi is 3.14 today.", "Ok."]


def test_japanese_without_spaces():
    assert _split("今日は晴れです。明日は雨でしょう！本当？") == [
        "今日は晴れです。", "明日は雨でしょう！", "本当？"
    ]


def test_arabic_right_to_left():
    assert _split("مرحبا بكم. كيف حالكم؟ أنا بخير.") == [
        "مرحبا بكم.", "كيف حالكم؟", "أنا بخير."
    ]


def test_hindi_danda_and_amharic():
    assert _split("मैं घर जा रहा हूँ। तुम कहाँ हो?") == ["मैं घर जा रहा हूँ।", "तुम कहाँ हो?"]
    assert _split("ሰላም ነው። እንዴት ነህ፧") == ["ሰላም ነው።", "እንዴት ነህ፧"]


def test_poetry_line_breaks_stay_in_one_segment():
    assert _split("Roses are red,\nviolets are blue") == ["Roses are red,\nviolets are blue"]


def test_unpunctuated_thai_is_split_at_spaces():
    text = "สวัสดีครับ " * 120
    parts = _split(text)
    assert len(parts) > 1
    assert all(len(p) <= MAX_SEGMENT_CHARS for p in parts)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")


def test_long_sentence_prefers_comma_then_space_then_hard_cut():
    text = ("word " * 30).strip() + ", " + ("more " * 30).strip()
    parts = _split(text, max_chars=160)
    assert parts[0].endswith(",")
    assert _split("x" * 50, max_chars=20) == ["x" * 20, "x" * 20, "x" * 10]


def test_whitespace_only():
    assert split_sentences("  \n\t ") == []


@settings(max_examples=300)
@given(st.text(max_size=2500), st.integers(min_value=20, max_value=700))
def test_spans_cover_every_non_space_character_exactly_once(text, max_chars):
    spans = split_sentences(text, max_chars=max_chars)
    covered = [0] * len(text)
    prev_end = 0
    for s, e in spans:
        assert prev_end <= s < e <= len(text)
        assert e - s <= max_chars
        assert not text[s].isspace() and not text[e - 1].isspace()
        for i in range(s, e):
            covered[i] += 1
        prev_end = e
    for i, ch in enumerate(text):
        assert covered[i] <= 1
        if not ch.isspace():
            assert covered[i] == 1


@given(st.lists(st.sampled_from(["Hola.", "¿Qué?", "東京。", "नमस्ते।", "word", "Mr.", "\n", " "]),
                max_size=200))
def test_sentence_like_inputs_never_crash(pieces):
    text = " ".join(pieces)
    assert all(e - s <= MAX_SEGMENT_CHARS for s, e in split_sentences(text))
```

- [ ] **Step 4: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/segment/test_sentences.py -v --no-cov`
Expected: FAIL (module missing).

- [ ] **Step 5: Implement `engine/coglossa/segment/sentences.py`**

```python
"""Language-aware sentence segmentation with no external models (spec §4.1).

Guarantee: spans are ordered, non-overlapping, non-empty, trimmed of whitespace, at most
max_chars long, and together cover every non-whitespace character of the input."""

from collections.abc import Iterator

MAX_SEGMENT_CHARS = 600

# End a sentence only when followed by whitespace (or end of text) and not by a lowercase word.
_SPACED_TERMINATORS = ".!?…‼⁇⁈⁉"
# End a sentence even without a following space: CJK, Indic danda, Arabic/Urdu, Ethiopic,
# Armenian, Myanmar, Georgian.
_SPACELESS_TERMINATORS = "。！？｡।॥؟۔።፧፨։။჻"
_TERMINATORS = _SPACED_TERMINATORS + _SPACELESS_TERMINATORS
_CLOSERS = "\"'”’»)]}」』）】〉》"
_ABBREVIATIONS = frozenset(
    {
        "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "no",
        "fig", "vol", "p", "pp", "cf", "al", "approx", "dept", "inc", "ltd", "co", "mt", "ft",
        "gen", "col", "capt", "lt", "sgt", "rev", "hon", "a.m", "p.m", "jan", "feb", "mar",
        "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    }
)
# Preferred cut points for over-long segments, strongest first.
_SOFT_BREAKS = (
    ("\n",),
    (",", ";", ":", "、", "，", "；", "،", "؛"),
    (" ", "\t", "　"),
)


def split_sentences(text: str, max_chars: int = MAX_SEGMENT_CHARS) -> list[tuple[int, int]]:
    raw: list[tuple[int, int]] = []
    start = 0
    for end in _boundaries(text):
        raw.append((start, end))
        start = end
    if start < len(text):
        raw.append((start, len(text)))
    spans: list[tuple[int, int]] = []
    for s, e in raw:
        for s2, e2 in _split_long(text, s, e, max_chars):
            s3, e3 = _trim(text, s2, e2)
            if s3 < e3:
                spans.append((s3, e3))
    return spans


def _boundaries(text: str) -> Iterator[int]:
    i, n = 0, len(text)
    while i < n:
        if text[i] in _TERMINATORS:
            j = i + 1
            while j < n and (text[j] in _TERMINATORS or text[j] in _CLOSERS):
                j += 1
            if _is_boundary(text, i, j):
                yield j
                i = j
                continue
        i += 1


def _is_boundary(text: str, i: int, j: int) -> bool:
    if text[i] in _SPACELESS_TERMINATORS:
        return True
    if j < len(text) and not text[j].isspace():
        return False  # e.g. 3.14, example.com, "a.b"
    nxt = _next_non_space(text, j)
    if nxt is not None and nxt.islower():
        return False  # '"Stop!" she said', 'e.g. about'
    if text[i] == ".":
        word = _word_before(text, i)
        if word.lower() in _ABBREVIATIONS:
            return False
        if len(word) == 1 and word.isalpha() and word.isupper():
            return False  # initials: J. K. Rowling
    return True


def _word_before(text: str, i: int) -> str:
    k = i
    while k > 0 and (text[k - 1].isalpha() or text[k - 1] == "."):
        k -= 1
    return text[k:i].strip(".")


def _next_non_space(text: str, j: int) -> str | None:
    while j < len(text) and text[j].isspace():
        j += 1
    while j < len(text) and text[j] in _CLOSERS:  # skip opening quotes etc.
        j += 1
    return text[j] if j < len(text) else None


def _split_long(text: str, s: int, e: int, max_chars: int) -> Iterator[tuple[int, int]]:
    while e - s > max_chars:
        cut = _best_cut(text, s, s + max_chars)
        yield s, cut
        s = cut
    yield s, e


def _best_cut(text: str, start: int, limit: int) -> int:
    window = text[start:limit]
    floor = len(window) // 4  # avoid tiny fragments
    for group in _SOFT_BREAKS:
        k = max(window.rfind(ch) for ch in group)
        if k > floor:
            return start + k + 1
    return limit


def _trim(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e
```

Check the trim and length guarantee: a raw span longer than `max_chars` is cut into pieces of at most `max_chars`, and trimming only shrinks spans, so the guarantee holds.

- [ ] **Step 6: Run the sentence tests**

Run: `cd engine && uv run pytest tests/segment/test_sentences.py -v --no-cov`
Expected: all pass. If an example test fails, fix the rule in `_is_boundary`. Do not weaken the property test.

- [ ] **Step 7: Write the failing segmenter test and implement it**

`engine/tests/segment/test_segmenter.py`:
```python
from coglossa.schema import Block, BlockKind, Provenance, SourceDocument
from coglossa.segment.segmenter import segment_document


def _block(i, kind, text):
    return Block(id=f"b{i}", kind=kind, text=text,
                 provenance=Provenance(source_path="x", index=i - 1))


def test_segments_are_global_and_point_back_to_blocks():
    doc = SourceDocument(source_path="x", format="txt", sha256="0", blocks=[
        _block(1, BlockKind.PARAGRAPH, "  One. Two.  "),
        _block(2, BlockKind.BREAK, "* * *"),
        _block(3, BlockKind.HEADING, "Title"),
    ])
    segs = segment_document(doc)
    assert [(s.id, s.block_id, s.text) for s in segs] == [
        ("s1", "b1", "One."), ("s2", "b1", "Two."), ("s3", "b3", "Title")
    ]
    blocks = {b.id: b for b in doc.blocks}
    assert all(blocks[s.block_id].text[s.start:s.end] == s.text for s in segs)
```

`engine/coglossa/segment/segmenter.py`:
```python
from coglossa.schema import BlockKind, Segment, SourceDocument
from coglossa.segment.sentences import split_sentences


def segment_document(doc: SourceDocument) -> list[Segment]:
    segments: list[Segment] = []
    for block in doc.blocks:
        if block.kind is BlockKind.BREAK:
            continue
        for start, end in split_sentences(block.text):
            segments.append(
                Segment(
                    id=f"s{len(segments) + 1}",
                    block_id=block.id,
                    start=start,
                    end=end,
                    text=block.text[start:end],
                )
            )
    return segments
```

- [ ] **Step 8: Run the segment tests, the full suite and lint**

Run: `cd engine && uv run pytest tests/segment -v --no-cov && uv run pytest && cd .. && make lint`
Expected: pass; coverage ≥95%.

- [ ] **Step 9: Commit**

```bash
git add engine/coglossa/segment engine/tests/segment
git commit -m "feat(segment): script-aware sentence splitting with coverage guarantees"
```

### Task 10: Chunk budget and chunking

**Files:**
- Create: `engine/coglossa/segment/chunking.py`
- Test: `engine/tests/segment/test_chunking.py`

**Interfaces:**
- Consumes: `CoglossaConfig` and `ConfigError` (Task 3), `count_words`, `estimate_tokens`, `MAX_SEGMENT_CHARS` and `segment_document` (Task 9), schema types (Task 2).
- Produces:
  - `ChunkBudget(max_words: int, target_words: int, max_tokens: int, bytes_per_token: float)`, frozen
  - `compute_budget(cfg) -> ChunkBudget`, which raises `ConfigError` when the smallest context window in the roster can't hold the largest possible segment
  - `chunk_segments(doc, segments, budget) -> list[Chunk]`, with ids `c1…cN`
- **Sizing rule:** the reconcile call is the largest. It holds the source plus one candidate per translator as input and about 1.5× the source as output. So `max_tokens = (smallest_context_window − reserve_tokens) / (num_translators + 2.5)`. The roster is every translator, the reviewer and `profiler_bible`.
- **Chunking rule:** fill chunks block by block. Close a chunk after a block once it reaches `target_words`. At a section start (a heading, or the first block after a scene break), close the current chunk early if it already has at least `target_words // 2`. Split inside a block only when the next segment would exceed `max_words` or `max_tokens`.

- [ ] **Step 1: Write the failing tests**

`engine/tests/segment/test_chunking.py`:
```python
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from coglossa.config import ConfigError
from coglossa.schema import Block, BlockKind, Provenance, SourceDocument
from coglossa.segment.chunking import ChunkBudget, chunk_segments, compute_budget
from coglossa.segment.segmenter import segment_document
from coglossa.segment.text_metrics import count_words, estimate_tokens
from tests.conftest import BASE_CONFIG

SENTENCE = "Lorem ipsum dolor sit amet."  # 5 words


def _doc(*blocks: tuple[BlockKind, str]) -> SourceDocument:
    return SourceDocument(source_path="x", format="txt", sha256="0", blocks=[
        Block(id=f"b{i + 1}", kind=k, text=t, provenance=Provenance(source_path="x", index=i))
        for i, (k, t) in enumerate(blocks)
    ])


def _para(sentences: int) -> tuple[BlockKind, str]:
    return BlockKind.PARAGRAPH, " ".join([SENTENCE] * sentences)


def _chunks(doc, budget):
    segs = segment_document(doc)
    by_id = {s.id: s for s in segs}
    return segs, [[by_id[i] for i in c.segment_ids] for c in chunk_segments(doc, segs, budget)]


BIG = ChunkBudget(max_words=400, target_words=250, max_tokens=100_000, bytes_per_token=2.5)


def test_compute_budget_default_roster(make_config):
    budget = compute_budget(make_config())
    assert budget == ChunkBudget(max_words=2000, target_words=1500,
                                 max_tokens=int((32768 - 3000) / 4.5), bytes_per_token=2.5)


def test_extra_translators_and_small_models_shrink_budget(make_config):
    models = {**BASE_CONFIG["models"],
              "small": {"backend": "fake", "context_window": 16384, "bytes_per_token": 2.0}}
    roles = {**BASE_CONFIG["roles"],
             "extra_translators": [{"name": "x", "prompt": "contextual", "model": "small"}]}
    budget = compute_budget(make_config(models=models, roles=roles))
    assert budget.max_tokens == int((16384 - 3000) / 5.5)
    assert budget.bytes_per_token == 2.0


def test_too_small_context_is_a_clear_config_error(make_config):
    models = {**BASE_CONFIG["models"], "model-a": {"backend": "fake", "context_window": 4096}}
    with pytest.raises(ConfigError, match=r"model-a.*4096.*reserve_tokens"):
        compute_budget(make_config(models=models))


def test_chunks_close_at_paragraph_boundaries_after_target():
    doc = _doc(*[_para(20) for _ in range(5)])  # 100 words each
    _, chunks = _chunks(doc, BIG)
    assert [sum(count_words(s.text) for s in c) for c in chunks] == [300, 200]


def test_section_starts_close_half_full_chunks():
    # 150w | heading: 150 >= 125 -> close | "Chapter Two" + 150w | break: 152 >= 125 -> close
    # | 150w + 10w -> closed at end. Three chunks.
    doc = _doc(_para(30), (BlockKind.HEADING, "Chapter Two"), _para(30),
               (BlockKind.BREAK, "* * *"), _para(30), _para(2))
    _, chunks = _chunks(doc, BIG)
    assert [c[0].text for c in chunks] == [SENTENCE, "Chapter Two", SENTENCE]
    assert len(chunks) == 3


def test_huge_paragraph_is_split_at_sentence_boundaries():
    doc = _doc(_para(200))  # 1000 words in one block
    _, chunks = _chunks(doc, BIG)
    assert all(sum(count_words(s.text) for s in c) <= 400 for c in chunks)
    assert len(chunks) == 3


def test_token_budget_limits_cjk_chunks():
    doc = _doc((BlockKind.PARAGRAPH, "今日は晴れです。" * 200))
    budget = ChunkBudget(max_words=10_000, target_words=5000, max_tokens=500, bytes_per_token=2.5)
    _, chunks = _chunks(doc, budget)
    assert len(chunks) > 1
    assert all(sum(estimate_tokens(s.text, 2.5) for s in c) <= 500 for c in chunks)


@settings(max_examples=150, deadline=None)
@given(
    st.lists(st.tuples(st.sampled_from(["p", "h", "b"]), st.integers(1, 60)), min_size=1,
             max_size=40),
    st.integers(50, 400),
)
def test_every_segment_lands_in_exactly_one_chunk_in_order(spec, target):
    blocks = []
    for kind, n in spec:
        if kind == "p":
            blocks.append(_para(n))
        elif kind == "h":
            blocks.append((BlockKind.HEADING, "Heading"))
        else:
            blocks.append((BlockKind.BREAK, "***"))
    doc = _doc(*blocks)
    budget = ChunkBudget(max_words=max(target, 200), target_words=target, max_tokens=2000,
                         bytes_per_token=2.5)
    segs = segment_document(doc)
    chunks = chunk_segments(doc, segs, budget)
    assert [i for c in chunks for i in c.segment_ids] == [s.id for s in segs]
    assert [c.id for c in chunks] == [f"c{i + 1}" for i in range(len(chunks))]
    by_id = {s.id: s for s in segs}
    for c in chunks:
        texts = [by_id[i].text for i in c.segment_ids]
        assert c.segment_ids
        assert sum(estimate_tokens(t, 2.5) for t in texts) <= budget.max_tokens
        if len(texts) > 1:
            assert sum(count_words(t) for t in texts) <= budget.max_words
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/segment/test_chunking.py -v --no-cov`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `engine/coglossa/segment/chunking.py`**

```python
"""Group segments into chunks that fit every model in the agent roster (spec §4.1, §7.2)."""

import math
from dataclasses import dataclass

from coglossa.config import CoglossaConfig, ConfigError
from coglossa.schema import BlockKind, Chunk, Segment, SourceDocument
from coglossa.segment.sentences import MAX_SEGMENT_CHARS
from coglossa.segment.text_metrics import count_words, estimate_tokens


@dataclass(frozen=True)
class ChunkBudget:
    max_words: int
    target_words: int
    max_tokens: int
    bytes_per_token: float


def compute_budget(cfg: CoglossaConfig) -> ChunkBudget:
    translators = cfg.roles.translators()
    roster = [t.model for t in translators]
    roster += [cfg.roles.reviewer.model, cfg.roles.profiler_bible.model]
    models = {key: cfg.models[key] for key in roster}
    smallest = min(models, key=lambda key: models[key].context_window)
    window = models[smallest].context_window
    bytes_per_token = min(m.bytes_per_token for m in models.values())
    reserve = cfg.chunking.reserve_tokens
    # The reconcile call is the largest: source + one candidate per translator in, ~1.5x out.
    max_tokens = int((window - reserve) / (len(translators) + 2.5))
    needed = math.ceil(MAX_SEGMENT_CHARS * 4 / bytes_per_token)  # worst-case single segment
    if max_tokens < needed:
        raise ConfigError(
            f"model '{smallest}' has a {window}-token context window, leaving "
            f"{max(max_tokens, 0)} tokens per chunk for {len(translators)} translators after "
            f"chunking.reserve_tokens={reserve}; at least {needed} are needed. Use a model "
            "with a larger context_window, lower chunking.reserve_tokens, or remove "
            "extra_translators."
        )
    return ChunkBudget(
        max_words=cfg.chunking.max_words,
        target_words=cfg.chunking.target_words,
        max_tokens=max_tokens,
        bytes_per_token=bytes_per_token,
    )


def chunk_segments(
    doc: SourceDocument, segments: list[Segment], budget: ChunkBudget
) -> list[Chunk]:
    by_block: dict[str, list[Segment]] = {}
    for seg in segments:
        by_block.setdefault(seg.block_id, []).append(seg)

    chunks: list[Chunk] = []
    current: list[Segment] = []
    words = tokens = 0

    def flush() -> None:
        nonlocal current, words, tokens
        if current:
            chunks.append(Chunk(id=f"c{len(chunks) + 1}", segment_ids=[s.id for s in current]))
        current, words, tokens = [], 0, 0

    pending_break = False
    for block in doc.blocks:
        if block.kind is BlockKind.BREAK:
            pending_break = True
            continue
        block_segments = by_block.get(block.id, [])
        if not block_segments:
            continue
        section_start = pending_break or block.kind is BlockKind.HEADING
        pending_break = False
        if section_start and words >= budget.target_words // 2:
            flush()
        for seg in block_segments:
            w = count_words(seg.text)
            t = estimate_tokens(seg.text, budget.bytes_per_token)
            if current and (words + w > budget.max_words or tokens + t > budget.max_tokens):
                flush()
            current.append(seg)
            words += w
            tokens += t
        if words >= budget.target_words:
            flush()
    flush()
    return chunks
```

- [ ] **Step 4: Run the chunking tests**

Run: `cd engine && uv run pytest tests/segment/test_chunking.py -v --no-cov`
Expected: all pass.

If `test_section_starts_close_half_full_chunks` fails, compare the result with the walk-through comment in the test. That comment applies the documented chunking rule, so fix the code to match it.

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `cd engine && uv run pytest && cd .. && make lint`

```bash
git add engine/coglossa/segment/chunking.py engine/tests/segment/test_chunking.py
git commit -m "feat(segment): context-aware chunk budget and section-preferring chunking"
```

### Task 11: Stage context and profile stage

**Files:**
- Create: `engine/coglossa/stages/__init__.py` (empty), `engine/coglossa/stages/context.py`, `engine/coglossa/stages/profile.py`
- Modify: `engine/tests/conftest.py` (add the `make_segments` helper)
- Test: `engine/tests/stages/__init__.py` (empty), `engine/tests/stages/test_profile.py`

**Interfaces:**
- Consumes: `AgentRunner`, `AGENT_FAILURES` (Task 7), `ProfilesConfig` (Task 3), `count_words` (Task 9), schema types.
- Produces:
  - `StageContext(cfg: CoglossaConfig, runner: AgentRunner, target_lang: str)`, a frozen dataclass
  - `StageError(Exception)`, raised when a *required* stage fails and the run can't continue
  - `classify_length(word_count: int, profiles: ProfilesConfig) -> PipelineProfile`
  - `build_sample(segments) -> str`, which uses the first ~1,000 words and the last ~500, joined by `"\n[…]\n"`
  - `async run_profile(ctx, segments, *, source_lang: str | None = None, pipeline_profile: PipelineProfile | None = None) -> DocumentProfile`. Its model calls are recorded with role label `"profiler"`.
  - Test helper `make_segments(*texts) -> list[Segment]` in `tests/conftest.py`, giving ids `s1…`, all in block `b1`

- [ ] **Step 1: Add the helper to `engine/tests/conftest.py`**

Add `from coglossa.schema import Segment` to the imports, then append:
```python
def make_segments(*texts: str) -> list[Segment]:
    return [
        Segment(id=f"s{i + 1}", block_id="b1", start=0, end=len(t), text=t)
        for i, t in enumerate(texts)
    ]
```

- [ ] **Step 2: Write the failing tests**

`engine/tests/stages/test_profile.py`:
```python
import pytest

from coglossa.config import ProfilesConfig
from coglossa.schema import PipelineProfile
from coglossa.stages.context import StageContext, StageError
from coglossa.stages.profile import build_sample, classify_length, run_profile
from tests.conftest import make_segments


@pytest.mark.parametrize(
    ("words", "expected"),
    [(1, "short"), (1499, "short"), (1500, "standard"), (20000, "standard"),
     (20001, "long-form")],
)
def test_classify_length(words, expected):
    assert classify_length(words, ProfilesConfig()).value == expected


def test_build_sample_short_document_is_whole_text():
    assert build_sample(make_segments("One.", "Two.")) == "One.\nTwo."


def test_build_sample_long_document_keeps_head_and_tail():
    segs = make_segments(*[f"Sentence number {i} here." for i in range(1000)])  # 4,000 words
    sample = build_sample(segs)
    assert sample.startswith("Sentence number 0 here.")
    assert sample.endswith("Sentence number 999 here.")
    assert "\n[…]\n" in sample
    assert "Sentence number 500 here." not in sample


def _ctx(make_runner, cfg=None):
    runner = make_runner(cfg)
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


async def test_run_profile_uses_hint_and_counts_words(make_runner, fake_backend):
    ctx = _ctx(make_runner)
    profile = await run_profile(ctx, make_segments("Bonjour tout le monde."), source_lang="fr")
    assert (profile.source_lang, profile.target_lang, profile.word_count) == ("fr", "en", 4)
    assert profile.pipeline_profile is PipelineProfile.SHORT
    assert ctx.runner.log.records[0].role == "profiler"


async def test_run_profile_detects_language_and_honours_override(make_runner):
    profile = await run_profile(_ctx(make_runner), make_segments("x"),
                                pipeline_profile=PipelineProfile.LONG_FORM)
    assert profile.source_lang == "und"  # synthetic model's "detected" language
    assert profile.pipeline_profile is PipelineProfile.LONG_FORM


async def test_run_profile_failure_is_stage_error(make_runner, fake_backend):
    fake_backend.script("profile", "garbage", "garbage")
    with pytest.raises(StageError, match="profiling failed"):
        await run_profile(_ctx(make_runner), make_segments("x"))
```

- [ ] **Step 3: Run them and confirm they fail, then implement**

Run: `cd engine && uv run pytest tests/stages/test_profile.py -v --no-cov` → FAIL (modules missing).

`engine/coglossa/stages/context.py`:
```python
from dataclasses import dataclass

from coglossa.config import CoglossaConfig
from coglossa.llm.agent import AgentRunner


class StageError(Exception):
    """A required stage failed; the run cannot continue. Message is user-facing."""


@dataclass(frozen=True)
class StageContext:
    cfg: CoglossaConfig
    runner: AgentRunner
    target_lang: str
```

`engine/coglossa/stages/profile.py`:
```python
"""Profile stage: language, genre, register and pipeline profile (spec §4.1, §4.2)."""

from coglossa.config import ProfilesConfig
from coglossa.llm.agent import AGENT_FAILURES
from coglossa.schema import DocumentProfile, PipelineProfile, ProfileAssessment, Segment
from coglossa.segment.text_metrics import count_words
from coglossa.stages.context import StageContext, StageError

SAMPLE_HEAD_WORDS = 1000
SAMPLE_TAIL_WORDS = 500


def classify_length(word_count: int, profiles: ProfilesConfig) -> PipelineProfile:
    if word_count < profiles.short_max_words:
        return PipelineProfile.SHORT
    if word_count <= profiles.standard_max_words:
        return PipelineProfile.STANDARD
    return PipelineProfile.LONG_FORM


def _take(segments: list[Segment], max_words: int) -> list[Segment]:
    taken, words = [], 0
    for seg in segments:
        if words >= max_words:
            break
        taken.append(seg)
        words += count_words(seg.text)
    return taken


def build_sample(segments: list[Segment]) -> str:
    total = sum(count_words(s.text) for s in segments)
    if total <= SAMPLE_HEAD_WORDS + SAMPLE_TAIL_WORDS:
        return "\n".join(s.text for s in segments)
    head = _take(segments, SAMPLE_HEAD_WORDS)
    tail = list(reversed(_take(list(reversed(segments)), SAMPLE_TAIL_WORDS)))
    return "\n".join(s.text for s in head) + "\n[…]\n" + "\n".join(s.text for s in tail)


async def run_profile(
    ctx: StageContext,
    segments: list[Segment],
    *,
    source_lang: str | None = None,
    pipeline_profile: PipelineProfile | None = None,
) -> DocumentProfile:
    model_key = ctx.cfg.roles.profiler_bible.model
    try:
        assessment = await ctx.runner.run(
            role="profiler",
            model_key=model_key,
            prompt_id="profile",
            prompt_vars={"target_lang": ctx.target_lang},
            payload={
                "task": "profile",
                "sample": build_sample(segments),
                "source_lang_hint": source_lang,
                "target_lang": ctx.target_lang,
            },
            output_type=ProfileAssessment,
        )
    except AGENT_FAILURES as e:
        raise StageError(f"profiling failed: {e}") from e
    words = sum(count_words(s.text) for s in segments)
    return DocumentProfile(
        source_lang=source_lang or assessment.source_lang,
        target_lang=ctx.target_lang,
        mixed_language=assessment.mixed_language,
        genre=assessment.genre,
        tone_register=assessment.tone_register,
        word_count=words,
        pipeline_profile=pipeline_profile or classify_length(words, ctx.cfg.profiles),
    )
```

Run the tests again. Expected: all pass.

- [ ] **Step 4: Run the full suite and lint, then commit**

Run: `cd engine && uv run pytest && cd .. && make lint`

```bash
git add engine/coglossa/stages engine/tests/stages engine/tests/conftest.py
git commit -m "feat(stages): stage context and document profile stage"
```

### Task 12: Bible stage (single pass, map-reduce, locked terms, slicing)

**Files:**
- Create: `engine/coglossa/stages/bible.py`
- Test: `engine/tests/stages/test_bible.py`

**Interfaces:**
- Consumes: `StageContext`, `StageError` (Task 11), `AGENT_FAILURES` (Task 7), `estimate_tokens` (Task 9), schema types.
- Produces:
  - `async run_bible(ctx, profile, segments, chunks, *, prior: Bible | None) -> tuple[Bible, list[RunError]]`. Its model calls use role label `"bible"`.
  - `apply_locked(prior: Bible | None, bible: Bible) -> Bible`
  - `bible_slice(bible: Bible, text: str) -> Bible`, which keeps only the glossary entries and entities whose term or name occurs in `text` (case-insensitive), plus the summary, style guide and cultural references
- **Rules:**
  - `short` profile: one call, `depth="light"`.
  - `standard`: one call, `depth="full"`, if `estimate_tokens(text) * 2 <= profiler window − reserve_tokens`; otherwise map-reduce.
  - `long-form`: always map-reduce. There is one `depth="partial"` call per chunk. A failed partial is recorded as a `RunError` and skipped, but if every partial fails, raise `StageError`.
  - Merging is recursive: if the partials don't fit the budget, they're split in half and merged hierarchically.
  - Engine output always has `locked=False`. Locked entries from `prior` are put back first, and they replace any engine entry with the same term (case-insensitive).

- [ ] **Step 1: Write the failing tests**

`engine/tests/stages/test_bible.py`:
```python
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coglossa.llm.types import PermanentBackendError
from coglossa.schema import (
    Bible,
    Chunk,
    DocumentProfile,
    Entity,
    Genre,
    GlossaryEntry,
    PipelineProfile,
)
from coglossa.stages.bible import apply_locked, bible_slice, run_bible
from coglossa.stages.context import StageContext, StageError
from tests.conftest import make_segments


def _profile(kind: PipelineProfile) -> DocumentProfile:
    return DocumentProfile(source_lang="fr", target_lang="en", mixed_language=False,
                           genre=Genre.LITERARY_PROSE, tone_register="wry", word_count=0,
                           pipeline_profile=kind)


def _ctx(runner):
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


def _chunks(n_chunks: int, per_chunk: int = 1):
    segs = make_segments(*[f"Phrase {i} ici." for i in range(n_chunks * per_chunk)])
    chunks = [Chunk(id=f"c{c + 1}", segment_ids=[s.id for s in
                                                 segs[c * per_chunk:(c + 1) * per_chunk]])
              for c in range(n_chunks)]
    return segs, chunks


def _tasks(fake_backend) -> list[str]:
    return [json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["task"]
            for r in fake_backend.requests]


@pytest.mark.parametrize(("kind", "depth"), [(PipelineProfile.SHORT, "light"),
                                             (PipelineProfile.STANDARD, "full")])
async def test_single_pass_depths(make_runner, fake_backend, kind, depth):
    segs, chunks = _chunks(2)
    bible, errors = await run_bible(_ctx(make_runner()), _profile(kind), segs, chunks, prior=None)
    assert errors == []
    assert bible.summary == "Synthetic summary of 6 words."
    payload = json.loads(fake_backend.requests[0].messages[1].content.split("INPUT_JSON:", 1)[1])
    assert (payload["task"], payload["depth"]) == ("bible", depth)


async def test_standard_too_large_for_profiler_falls_back_to_map_reduce(make_runner, fake_backend):
    segs = make_segments(*["mot " * 5000 for _ in range(4)])  # ~100 KB of text
    chunks = [Chunk(id=f"c{i + 1}", segment_ids=[s.id]) for i, s in enumerate(segs)]
    await run_bible(_ctx(make_runner()), _profile(PipelineProfile.STANDARD), segs, chunks,
                    prior=None)
    assert _tasks(fake_backend) == ["bible"] * 4 + ["bible_merge"]


async def test_long_form_map_reduce(make_runner, fake_backend):
    segs, chunks = _chunks(3)
    runner = make_runner()
    bible, _ = await run_bible(_ctx(runner), _profile(PipelineProfile.LONG_FORM), segs,
                               chunks, prior=None)
    assert _tasks(fake_backend) == ["bible", "bible", "bible", "bible_merge"]
    assert bible.summary.count("Synthetic summary") == 3
    assert {r.role for r in runner.log.records} == {"bible"}


async def test_merge_is_hierarchical_when_partials_do_not_fit(make_config, make_runner,
                                                              fake_backend):
    cfg = make_config(chunking={"reserve_tokens": 32000})  # profiler budget: 768 tokens
    segs, chunks = _chunks(8)
    bible, _ = await run_bible(_ctx(make_runner(cfg)), _profile(PipelineProfile.LONG_FORM),
                               segs, chunks, prior=None)
    assert _tasks(fake_backend).count("bible_merge") >= 3
    assert bible.summary.count("Synthetic summary") == 8


async def test_failed_partials_are_recorded_and_skipped(make_runner, fake_backend):
    fake_backend.script("bible", PermanentBackendError("boom"))
    segs, chunks = _chunks(3)
    bible, errors = await run_bible(_ctx(make_runner()), _profile(PipelineProfile.LONG_FORM),
                                    segs, chunks, prior=None)
    assert len(errors) == 1 and errors[0].stage == "bible" and "boom" in errors[0].message
    assert bible.summary.count("Synthetic summary") == 2


async def test_all_partials_failing_is_stage_error(make_runner, fake_backend):
    fake_backend.script("bible", *[PermanentBackendError("boom")] * 2)
    segs, chunks = _chunks(2)
    with pytest.raises(StageError, match="bible failed.*boom"):
        await run_bible(_ctx(make_runner()), _profile(PipelineProfile.LONG_FORM), segs, chunks,
                        prior=None)


async def test_single_pass_failure_is_stage_error(make_runner, fake_backend):
    fake_backend.script("bible", "garbage", "garbage")
    segs, chunks = _chunks(1)
    with pytest.raises(StageError, match="bible failed"):
        await run_bible(_ctx(make_runner()), _profile(PipelineProfile.SHORT), segs, chunks,
                        prior=None)


async def test_merge_failure_is_stage_error(make_runner, fake_backend):
    fake_backend.script("bible_merge", "garbage", "garbage")
    segs, chunks = _chunks(2)
    with pytest.raises(StageError, match="bible failed"):
        await run_bible(_ctx(make_runner()), _profile(PipelineProfile.LONG_FORM), segs, chunks,
                        prior=None)


async def test_locked_prior_terms_win_and_engine_cannot_lock(make_runner, fake_backend):
    engine_bible = {"summary": "s", "style_guide": "g", "cultural_references": [],
                    "entities": [],
                    "glossary": [{"term": "GESTALT", "rendering": "shape", "locked": True},
                                 {"term": "Zeitgeist", "rendering": "spirit", "locked": True}]}
    fake_backend.script("bible", json.dumps(engine_bible))
    prior = Bible(summary="old", style_guide="old", glossary=[
        GlossaryEntry(term="Gestalt", rendering="gestalt", locked=True),
        GlossaryEntry(term="Angst", rendering="dread"),
    ])
    segs, chunks = _chunks(1)
    bible, _ = await run_bible(_ctx(make_runner()), _profile(PipelineProfile.SHORT), segs, chunks,
                               prior=prior)
    assert [(g.term, g.rendering, g.locked) for g in bible.glossary] == [
        ("Gestalt", "gestalt", True), ("Zeitgeist", "spirit", False)
    ]
    payload = json.loads(fake_backend.requests[0].messages[1].content.split("INPUT_JSON:", 1)[1])
    assert payload["prior_bible"]["summary"] == "old"


def test_bible_slice_keeps_only_relevant_entries():
    bible = Bible(summary="s", style_guide="g", cultural_references=["c"],
                  glossary=[GlossaryEntry(term="Gestalt", rendering="g"),
                            GlossaryEntry(term="Angst", rendering="a")],
                  entities=[Entity(name="Anna", rendering="Anna", kind="person"),
                            Entity(name="Berlin", rendering="Berlin", kind="place")])
    sliced = bible_slice(bible, "Anna felt a strange gestalt.")
    assert [g.term for g in sliced.glossary] == ["Gestalt"]
    assert [e.name for e in sliced.entities] == ["Anna"]
    assert (sliced.summary, sliced.style_guide, sliced.cultural_references) == ("s", "g", ["c"])


terms = st.text(alphabet="abcABC", min_size=1, max_size=3)


@given(st.lists(st.tuples(terms, st.booleans()), max_size=8), st.lists(terms, max_size=8))
def test_apply_locked_never_drops_or_duplicates_locked_terms(prior_terms, engine_terms):
    prior = Bible(summary="", style_guide="", glossary=[
        GlossaryEntry(term=t, rendering="p", locked=locked) for t, locked in prior_terms])
    engine = Bible(summary="", style_guide="",
                   glossary=[GlossaryEntry(term=t, rendering="e") for t in engine_terms])
    merged = apply_locked(prior, engine)
    locked = [g for g in prior.glossary if g.locked]
    for entry in locked:
        assert entry in merged.glossary
    locked_keys = {g.term.casefold() for g in locked}
    for g in merged.glossary:
        if g.term.casefold() in locked_keys:
            assert g.locked
    assert apply_locked(None, engine) == engine
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/stages/test_bible.py -v --no-cov`
Expected: FAIL (`coglossa.stages.bible` is missing).

- [ ] **Step 3: Implement `engine/coglossa/stages/bible.py`**

```python
"""Bible stage (spec §4.1): build the translation bible, single pass or map-reduce."""

import asyncio
import json
from typing import Any

from coglossa.llm.agent import AGENT_FAILURES
from coglossa.schema import Bible, Chunk, DocumentProfile, PipelineProfile, RunError, Segment
from coglossa.segment.text_metrics import estimate_tokens
from coglossa.stages.context import StageContext, StageError


async def run_bible(
    ctx: StageContext,
    profile: DocumentProfile,
    segments: list[Segment],
    chunks: list[Chunk],
    *,
    prior: Bible | None,
) -> tuple[Bible, list[RunError]]:
    model_key = ctx.cfg.roles.profiler_bible.model
    model = ctx.cfg.models[model_key]
    budget = model.context_window - ctx.cfg.chunking.reserve_tokens
    text = "\n".join(s.text for s in segments)
    errors: list[RunError] = []

    single_pass = (
        profile.pipeline_profile is not PipelineProfile.LONG_FORM
        and estimate_tokens(text, model.bytes_per_token) * 2 <= budget
    )
    try:
        if single_pass:
            depth = "light" if profile.pipeline_profile is PipelineProfile.SHORT else "full"
            bible = await _bible_call(ctx, profile, text, depth, prior)
        else:
            by_id = {s.id: s for s in segments}
            texts = ["\n".join(by_id[i].text for i in c.segment_ids) for c in chunks]
            results = await asyncio.gather(
                *(_bible_call(ctx, profile, t, "partial", prior) for t in texts),
                return_exceptions=True,
            )
            partials: list[Bible] = []
            for chunk, result in zip(chunks, results, strict=True):
                if isinstance(result, Bible):
                    partials.append(result)
                elif isinstance(result, AGENT_FAILURES):
                    errors.append(RunError(stage="bible", chunk_id=chunk.id, role="bible",
                                           message=str(result)))
                else:
                    raise result
            if not partials:
                raise StageError(f"bible failed: every excerpt failed ({errors[0].message})")
            bible = await _merge(ctx, profile, partials, prior, budget, model.bytes_per_token)
    except AGENT_FAILURES as e:
        raise StageError(f"bible failed: {e}") from e
    return apply_locked(prior, _unlocked(bible)), errors


async def _bible_call(
    ctx: StageContext, profile: DocumentProfile, text: str, depth: str, prior: Bible | None
) -> Bible:
    return await ctx.runner.run(
        role="bible",
        model_key=ctx.cfg.roles.profiler_bible.model,
        prompt_id="bible",
        prompt_vars={"source_lang": profile.source_lang, "target_lang": profile.target_lang,
                     "depth": depth},
        payload={
            "task": "bible",
            "depth": depth,
            "source_lang": profile.source_lang,
            "target_lang": profile.target_lang,
            "genre": profile.genre.value,
            "register": profile.tone_register,
            "text": text,
            "prior_bible": _dump(prior),
        },
        output_type=Bible,
    )


async def _merge(
    ctx: StageContext,
    profile: DocumentProfile,
    partials: list[Bible],
    prior: Bible | None,
    budget: int,
    bytes_per_token: float,
) -> Bible:
    if len(partials) == 1:
        return partials[0]
    size = estimate_tokens(
        json.dumps([p.model_dump() for p in partials], ensure_ascii=False), bytes_per_token
    )
    if size * 2 > budget and len(partials) > 2:
        mid = len(partials) // 2
        left, right = await asyncio.gather(
            _merge(ctx, profile, partials[:mid], prior, budget, bytes_per_token),
            _merge(ctx, profile, partials[mid:], prior, budget, bytes_per_token),
        )
        partials = [left, right]
    return await ctx.runner.run(
        role="bible",
        model_key=ctx.cfg.roles.profiler_bible.model,
        prompt_id="bible_merge",
        prompt_vars={"source_lang": profile.source_lang, "target_lang": profile.target_lang},
        payload={
            "task": "bible_merge",
            "source_lang": profile.source_lang,
            "target_lang": profile.target_lang,
            "partials": [p.model_dump() for p in partials],
            "prior_bible": _dump(prior),
        },
        output_type=Bible,
    )


def _dump(bible: Bible | None) -> dict[str, Any] | None:
    return bible.model_dump() if bible is not None else None


def _unlocked(bible: Bible) -> Bible:
    glossary = [g.model_copy(update={"locked": False}) for g in bible.glossary]
    return bible.model_copy(update={"glossary": glossary})


def apply_locked(prior: Bible | None, bible: Bible) -> Bible:
    """Human-locked entries from the prior bible are kept verbatim and override engine entries."""
    if prior is None:
        return bible
    locked = [g for g in prior.glossary if g.locked]
    keys = {g.term.casefold() for g in locked}
    rest = [g for g in bible.glossary if g.term.casefold() not in keys]
    return bible.model_copy(update={"glossary": [*locked, *rest]})


def bible_slice(bible: Bible, text: str) -> Bible:
    """The part of the bible relevant to `text`, keeping prompts small for long books."""
    folded = text.casefold()
    return bible.model_copy(
        update={
            "glossary": [g for g in bible.glossary if g.term.casefold() in folded],
            "entities": [e for e in bible.entities if e.name.casefold() in folded],
        }
    )
```

Note: a `StageError` raised inside the `try` block isn't caught by `except AGENT_FAILURES`, because `StageError` isn't one of them, so it propagates unchanged.

- [ ] **Step 4: Run the bible tests, the full suite and lint**

Run: `cd engine && uv run pytest tests/stages/test_bible.py -v --no-cov && uv run pytest && cd .. && make lint`
Expected: pass; coverage ≥95%.

- [ ] **Step 5: Commit**

```bash
git add engine/coglossa/stages/bible.py engine/tests/stages/test_bible.py
git commit -m "feat(stages): translation bible with map-reduce merge and locked-term preservation"
```

### Task 13: Translate stage (coverage validation and parallel translators)

**Files:**
- Create: `engine/coglossa/stages/translate.py`
- Test: `engine/tests/stages/test_translate.py`

**Interfaces:**
- Consumes: `StageContext` (Task 11), `bible_slice` (Task 12), `AGENT_FAILURES` (Task 7), `TranslatorConfig` (Task 3), schema types.
- Produces:
  - `check_coverage(spans, segment_ids: list[str]) -> list[str]`. It returns human-readable errors, and an empty list means valid. `spans` is any sequence of objects with `.start` and `.end`.
  - `CONTEXT_CHARS = 800`
  - `ChunkInput(chunk: Chunk, segments: list[Segment], context_before: str, context_after: str)`, frozen
  - `build_chunk_inputs(chunks, segments) -> list[ChunkInput]`
  - `async translate_chunk(ctx, profile, bible, ci) -> tuple[list[Candidate], list[RunError]]`. Model calls use role label `f"translator:{name}"`.
- **Payload rules:**
  - The `literal` prompt gets `bible = {"glossary": [...]}` only, and empty `context_before` and `context_after`.
  - Every other prompt gets the full `bible_slice` and the neighboring source text.
  - Candidate `.model` is the served model name (`ModelConfig.name or key`).

- [ ] **Step 1: Write the failing tests**

`engine/tests/stages/test_translate.py`:
```python
import json

import pytest

from coglossa.llm.types import PermanentBackendError
from coglossa.schema import (
    Bible,
    Chunk,
    DocumentProfile,
    Genre,
    GlossaryEntry,
    PipelineProfile,
    SpanText,
)
from coglossa.stages.context import StageContext
from coglossa.stages.translate import (
    CONTEXT_CHARS,
    build_chunk_inputs,
    check_coverage,
    translate_chunk,
)
from tests.conftest import BASE_CONFIG, make_segments

PROFILE = DocumentProfile(source_lang="de", target_lang="en", mixed_language=False,
                          genre=Genre.TECHNICAL, tone_register="dry", word_count=4,
                          pipeline_profile=PipelineProfile.SHORT)
BIBLE = Bible(summary="s", style_guide="g",
              glossary=[GlossaryEntry(term="Gestalt", rendering="form"),
                        GlossaryEntry(term="Angst", rendering="dread")])


def _spans(*ranges):
    return [SpanText(start=a, end=b, text="t") for a, b in ranges]


IDS = ["s1", "s2", "s3"]


@pytest.mark.parametrize(
    ("ranges", "error"),
    [
        ([("s1", "s1"), ("s2", "s3")], None),
        ([("s1", "s3")], None),
        ([("s1", "s1"), ("s9", "s9")], "unknown segment id"),
        ([("s2", "s1")], "before start"),
        ([("s1", "s1"), ("s3", "s3")], "expected a span starting at s2"),
        ([("s1", "s2"), ("s2", "s3")], "expected a span starting at s3"),
        ([("s1", "s2")], "s3..s3 not covered"),
        ([("s1", "s3"), ("s3", "s3")], "after the last segment"),
        ([], "s1..s3 not covered"),
    ],
)
def test_check_coverage(ranges, error):
    errors = check_coverage(_spans(*ranges), IDS)
    if error is None:
        assert errors == []
    else:
        assert len(errors) == 1 and error in errors[0]


def test_build_chunk_inputs_adds_neighbour_context():
    segs = make_segments("A" * 1000, "B", "C" * 1000)
    chunks = [Chunk(id="c1", segment_ids=["s1"]), Chunk(id="c2", segment_ids=["s2"]),
              Chunk(id="c3", segment_ids=["s3"])]
    inputs = build_chunk_inputs(chunks, segs)
    assert inputs[0].context_before == "" and inputs[0].context_after == "B"
    assert inputs[1].context_before == "A" * CONTEXT_CHARS
    assert inputs[1].context_after == "C" * CONTEXT_CHARS
    assert inputs[2].context_after == ""
    assert [s.id for s in inputs[1].segments] == ["s2"]


def _ci(*texts):
    segs = make_segments(*texts)
    return build_chunk_inputs([Chunk(id="c1", segment_ids=[s.id for s in segs])], segs)[0]


def _payloads(fake_backend):
    return [json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])
            for r in fake_backend.requests]


def _ctx(runner):
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


async def test_runs_every_translator_with_role_specific_payloads(make_config, make_runner,
                                                                 fake_backend):
    models = {**BASE_CONFIG["models"],
              "model-b": {"backend": "fake", "context_window": 32768, "name": "served-b"}}
    runner = make_runner(make_config(models=models))
    ci = _ci("Die Gestalt ist klar.", "Gut.")
    candidates, errors = await translate_chunk(_ctx(runner), PROFILE, BIBLE, ci)
    assert errors == []
    assert [(c.translator, c.model, c.start, c.text) for c in candidates] == [
        ("literal", "model-a", "s1", "[en] Die Gestalt ist klar."),
        ("literal", "model-a", "s2", "[en] Gut."),
        ("contextual", "served-b", "s1", "[en] Die Gestalt ist klar."),
        ("contextual", "served-b", "s2", "[en] Gut."),
    ]
    by_name = {p["translator"]: p for p in _payloads(fake_backend)}
    literal, contextual = by_name["literal"], by_name["contextual"]
    assert literal["context_before"] == "" and literal["context_after"] == ""
    assert literal["bible"] == {"glossary": [{"term": "Gestalt", "rendering": "form",
                                              "notes": "", "locked": False}]}
    assert contextual["bible"]["style_guide"] == "g"
    assert [g["term"] for g in contextual["bible"]["glossary"]] == ["Gestalt"]
    assert {r.role for r in runner.log.records} == {"translator:literal", "translator:contextual"}


async def test_extra_translators_run_too(make_config, make_runner):
    roles = {**BASE_CONFIG["roles"],
             "extra_translators": [{"name": "second", "prompt": "contextual", "model": "model-a"}]}
    runner = make_runner(make_config(roles=roles))
    candidates, _ = await translate_chunk(_ctx(runner), PROFILE, BIBLE, _ci("Eins."))
    assert sorted({c.translator for c in candidates}) == ["contextual", "literal", "second"]


async def test_bad_coverage_is_repaired(make_runner, fake_backend):
    bad = json.dumps({"spans": [{"start": "s1", "end": "s1", "text": "only one"}]})
    fake_backend.script("translate", bad)
    runner = make_runner()
    candidates, errors = await translate_chunk(_ctx(runner), PROFILE, BIBLE, _ci("A.", "B."))
    assert errors == []
    assert len([c for c in candidates if c.translator == "literal"]) == 2
    assert len(runner.log.records) == 3  # literal twice (repair) + contextual once


async def test_failed_translator_is_recorded_not_fatal(make_runner, fake_backend):
    fake_backend.script("translate", PermanentBackendError("model gone"))
    candidates, errors = await translate_chunk(_ctx(make_runner()), PROFILE, BIBLE, _ci("A."))
    assert {c.translator for c in candidates} == {"contextual"}
    assert [(e.stage, e.chunk_id, e.role) for e in errors] == [
        ("translate", "c1", "translator:literal")]
    assert "model gone" in errors[0].message


async def test_programming_errors_propagate(make_runner, fake_backend):
    fake_backend.script("translate", RuntimeError("bug"))
    with pytest.raises(RuntimeError, match="bug"):
        await translate_chunk(_ctx(make_runner()), PROFILE, BIBLE, _ci("A."))
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/stages/test_translate.py -v --no-cov`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `engine/coglossa/stages/translate.py`**

```python
"""Translate stage (spec §4.1, §4.3, §4.4): every translator renders every chunk, keyed by spans."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from coglossa.config import TranslatorConfig
from coglossa.llm.agent import AGENT_FAILURES
from coglossa.schema import (
    Bible,
    Candidate,
    Chunk,
    DocumentProfile,
    RunError,
    Segment,
    TranslationOutput,
)
from coglossa.stages.bible import bible_slice
from coglossa.stages.context import StageContext

CONTEXT_CHARS = 800


class _SpanLike(Protocol):
    start: str
    end: str


def check_coverage(spans: Sequence[_SpanLike], segment_ids: list[str]) -> list[str]:
    """Spans must cover segment_ids exactly once, in order, each a contiguous range."""
    pos = {sid: i for i, sid in enumerate(segment_ids)}
    expected = 0
    for k, span in enumerate(spans):
        if span.start not in pos or span.end not in pos:
            return [f"spans[{k}]: unknown segment id in {span.start}..{span.end}"]
        a, b = pos[span.start], pos[span.end]
        if b < a:
            return [f"spans[{k}]: end {span.end} is before start {span.start}"]
        if expected >= len(segment_ids):
            return [f"spans[{k}]: span {span.start}..{span.end} is after the last segment"]
        if a != expected:
            return [
                f"spans[{k}]: expected a span starting at {segment_ids[expected]}, "
                f"got {span.start}"
            ]
        expected = b + 1
    if expected < len(segment_ids):
        return [f"segments {segment_ids[expected]}..{segment_ids[-1]} not covered by any span"]
    return []


@dataclass(frozen=True)
class ChunkInput:
    chunk: Chunk
    segments: list[Segment]
    context_before: str
    context_after: str


def build_chunk_inputs(chunks: list[Chunk], segments: list[Segment]) -> list[ChunkInput]:
    by_id = {s.id: s for s in segments}
    texts = ["\n".join(by_id[i].text for i in c.segment_ids) for c in chunks]
    return [
        ChunkInput(
            chunk=chunk,
            segments=[by_id[i] for i in chunk.segment_ids],
            context_before=texts[k - 1][-CONTEXT_CHARS:] if k > 0 else "",
            context_after=texts[k + 1][:CONTEXT_CHARS] if k + 1 < len(texts) else "",
        )
        for k, chunk in enumerate(chunks)
    ]


async def translate_chunk(
    ctx: StageContext, profile: DocumentProfile, bible: Bible, ci: ChunkInput
) -> tuple[list[Candidate], list[RunError]]:
    segment_ids = [s.id for s in ci.segments]
    sliced = bible_slice(bible, "\n".join(s.text for s in ci.segments))
    translators = ctx.cfg.roles.translators()

    async def one(t: TranslatorConfig) -> list[Candidate]:
        literal = t.prompt == "literal"
        payload = {
            "task": "translate",
            "translator": t.name,
            "source_lang": profile.source_lang,
            "target_lang": profile.target_lang,
            "genre": profile.genre.value,
            "register": profile.tone_register,
            "bible": (
                {"glossary": [g.model_dump() for g in sliced.glossary]}
                if literal
                else sliced.model_dump()
            ),
            "context_before": "" if literal else ci.context_before,
            "context_after": "" if literal else ci.context_after,
            "segments": [{"id": s.id, "text": s.text} for s in ci.segments],
        }
        out = await ctx.runner.run(
            role=f"translator:{t.name}",
            model_key=t.model,
            prompt_id=t.prompt,
            prompt_vars={
                "source_lang": profile.source_lang,
                "target_lang": profile.target_lang,
                "genre": profile.genre.value,
                "register": profile.tone_register,
            },
            payload=payload,
            output_type=TranslationOutput,
            validate=lambda o: check_coverage(o.spans, segment_ids),
        )
        served = ctx.cfg.models[t.model].name or t.model
        return [
            Candidate(chunk_id=ci.chunk.id, translator=t.name, model=served, start=sp.start,
                      end=sp.end, text=sp.text, notes=sp.notes)
            for sp in out.spans
        ]

    results = await asyncio.gather(*(one(t) for t in translators), return_exceptions=True)
    candidates: list[Candidate] = []
    errors: list[RunError] = []
    for t, result in zip(translators, results, strict=True):
        if isinstance(result, list):
            candidates.extend(result)
        elif isinstance(result, AGENT_FAILURES):
            errors.append(RunError(stage="translate", chunk_id=ci.chunk.id,
                                   role=f"translator:{t.name}", message=str(result)))
        else:
            raise result
    return candidates, errors
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd engine && uv run pytest tests/stages/test_translate.py -v --no-cov`
Expected: all pass.

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `cd engine && uv run pytest && cd .. && make lint`

```bash
git add engine/coglossa/stages/translate.py engine/tests/stages/test_translate.py
git commit -m "feat(stages): parallel translators with span coverage validation"
```

### Task 14: Reconcile stage, M1 scoring stub and flags

**Files:**
- Create: `engine/coglossa/stages/reconcile.py`, `engine/coglossa/stages/score.py`
- Test: `engine/tests/stages/test_reconcile.py`, `engine/tests/stages/test_score.py`

**Interfaces:**
- Consumes: `check_coverage`, `ChunkInput` (Task 13), `bible_slice` (Task 12), `AGENT_FAILURES` (Task 7), schema types.
- Produces:
  - `ReconcileResult(spans: list[ReconciledSpan], engine_error: str | None, errors: list[RunError])`
  - `async reconcile_chunk(ctx, profile, bible, ci, candidates) -> ReconcileResult`. Model calls use role label `"reviewer"`.
  - `SIGNALS = ("qe", "xsim", "roundtrip", "judge", "agreement", "bible_check")`
  - `unscored() -> SpanScore`, with every signal `None` and reliability `unscored`
  - `competing_candidates(candidates, start: str, end: str, positions: dict[str, int]) -> list[Candidate]`, the candidates whose ranges overlap the span
  - `initial_flags(span: ReconciledSpan, *, engine_error: str | None, competing: list[Candidate]) -> list[Flag]`
- **Fallback (spec §4.5):**
  - If the reviewer fails, the final spans are copied from the `contextual` candidates, or from the first translator that produced output. They get agreement `unknown`, `engine_error` set, and a `RunError(stage="reconcile")`.
  - If no translator produced output, there's one empty span per segment, with `chosen_from="none"` and `engine_error` set. No reviewer call is made.

- [ ] **Step 1: Write the failing score tests**

`engine/tests/stages/test_score.py`:
```python
from coglossa.schema import Agreement, Candidate, FlagCode, Reliability, ReconciledSpan
from coglossa.stages.score import SIGNALS, competing_candidates, initial_flags, unscored

POS = {"s1": 0, "s2": 1, "s3": 2}


def _cand(name, start, end):
    return Candidate(chunk_id="c1", translator=name, model="m", start=start, end=end, text=name)


def _span(**kw):
    base = dict(start="s1", end="s1", text="t", agreement=Agreement.AGREE, chosen_from="literal")
    return ReconciledSpan(**{**base, **kw})


def test_unscored_has_every_signal_null():
    score = unscored()
    assert score.signals == dict.fromkeys(SIGNALS)
    assert score.meaning is None and score.reliability is Reliability.UNSCORED


def test_competing_candidates_overlap():
    cands = [_cand("a", "s1", "s1"), _cand("b", "s2", "s3"), _cand("c", "s3", "s3")]
    assert [c.translator for c in competing_candidates(cands, "s2", "s2", POS)] == ["b"]
    assert [c.translator for c in competing_candidates(cands, "s1", "s2", POS)] == ["a", "b"]


def test_no_flags_for_clean_agreement():
    assert initial_flags(_span(), engine_error=None, competing=[]) == []


def test_all_flags():
    span = _span(agreement=Agreement.MAJOR, rationale="literal drops the negation",
                 cultural_or_wordplay=True)
    flags = initial_flags(span, engine_error="review failed: boom",
                          competing=[_cand("literal", "s1", "s1")])
    assert [f.code for f in flags] == [FlagCode.ENGINE_ERROR, FlagCode.AGENT_DISAGREEMENT,
                                       FlagCode.CULTURAL_OR_WORDPLAY]
    assert flags[0].evidence["error"] == "review failed: boom"
    assert flags[1].explanation == "literal drops the negation"
    assert flags[1].evidence["candidates"] == [{"translator": "literal", "model": "m",
                                                "text": "literal"}]


def test_flag_explanations_have_plain_language_defaults():
    flags = initial_flags(_span(agreement=Agreement.MAJOR, cultural_or_wordplay=True),
                          engine_error=None, competing=[])
    assert all(f.explanation for f in flags)
```

- [ ] **Step 2: Implement `engine/coglossa/stages/score.py`**

```python
"""M1 scoring: every signal is null (never invented, spec §1.2) plus flags derived from review.
M2 replaces unscored() with real signals and aggregation (spec §6)."""

from coglossa.schema import Agreement, Candidate, Flag, FlagCode, ReconciledSpan, SpanScore

SIGNALS = ("qe", "xsim", "roundtrip", "judge", "agreement", "bible_check")


def unscored() -> SpanScore:
    return SpanScore(signals=dict.fromkeys(SIGNALS))


def competing_candidates(
    candidates: list[Candidate], start: str, end: str, positions: dict[str, int]
) -> list[Candidate]:
    a, b = positions[start], positions[end]
    return [c for c in candidates if positions[c.start] <= b and positions[c.end] >= a]


def initial_flags(
    span: ReconciledSpan, *, engine_error: str | None, competing: list[Candidate]
) -> list[Flag]:
    alternatives = [{"translator": c.translator, "model": c.model, "text": c.text}
                    for c in competing]
    flags: list[Flag] = []
    if engine_error is not None:
        flags.append(Flag(
            code=FlagCode.ENGINE_ERROR,
            explanation=("This passage was not reviewed because an engine step failed. The text "
                         "shown is an unreviewed translator draft, or empty if none was produced."),
            evidence={"error": engine_error, "candidates": alternatives},
        ))
    if span.agreement is Agreement.MAJOR:
        flags.append(Flag(
            code=FlagCode.AGENT_DISAGREEMENT,
            explanation=span.rationale or "The translators disagreed on the meaning of this passage.",
            evidence={"chosen_from": span.chosen_from, "candidates": alternatives},
        ))
    if span.cultural_or_wordplay:
        flags.append(Flag(
            code=FlagCode.CULTURAL_OR_WORDPLAY,
            explanation=span.rationale or ("This passage contains a cultural reference, idiom or "
                                           "wordplay that may not fully carry over."),
            evidence={"candidates": alternatives},
        ))
    return flags
```

Run: `cd engine && uv run pytest tests/stages/test_score.py -v --no-cov`
Expected: all pass.

- [ ] **Step 3: Write the failing reconcile tests**

`engine/tests/stages/test_reconcile.py`:
```python
import json

from coglossa.schema import (
    Agreement,
    Bible,
    Candidate,
    Chunk,
    DocumentProfile,
    Genre,
    PipelineProfile,
)
from coglossa.stages.context import StageContext
from coglossa.stages.reconcile import reconcile_chunk
from coglossa.stages.translate import build_chunk_inputs
from tests.conftest import make_segments

PROFILE = DocumentProfile(source_lang="es", target_lang="en", mixed_language=False,
                          genre=Genre.NEWS, tone_register="neutral", word_count=2,
                          pipeline_profile=PipelineProfile.SHORT)
BIBLE = Bible(summary="s", style_guide="g")


def _ci():
    segs = make_segments("Hola.", "Adiós.")
    return build_chunk_inputs([Chunk(id="c1", segment_ids=["s1", "s2"])], segs)[0]


def _cands(*names):
    return [Candidate(chunk_id="c1", translator=n, model="m", start=s, end=s, text=f"{n}-{s}")
            for n in names for s in ("s1", "s2")]


def _ctx(runner):
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


async def test_reviewer_output_is_used(make_runner, fake_backend):
    runner = make_runner()
    result = await reconcile_chunk(_ctx(runner), PROFILE, BIBLE, _ci(),
                                   _cands("literal", "contextual"))
    assert result.engine_error is None and result.errors == []
    assert [(s.start, s.text, s.chosen_from) for s in result.spans] == [
        ("s1", "literal-s1", "literal"), ("s2", "literal-s2", "literal")]
    payload = json.loads(fake_backend.requests[0].messages[1].content.split("INPUT_JSON:", 1)[1])
    assert [c["translator"] for c in payload["candidates"]] == ["literal", "contextual"]
    assert payload["candidates"][1]["spans"][0] == {"start": "s1", "end": "s1",
                                                    "text": "contextual-s1", "notes": ""}
    assert runner.log.records[0].role == "reviewer"
    assert "literal, contextual" in fake_backend.requests[0].messages[0].content


async def test_reviewer_failure_falls_back_to_contextual(make_runner, fake_backend):
    fake_backend.script("reconcile", "garbage", "garbage")
    result = await reconcile_chunk(_ctx(make_runner()), PROFILE, BIBLE, _ci(),
                                   _cands("literal", "contextual"))
    assert [(s.text, s.agreement, s.chosen_from) for s in result.spans] == [
        ("contextual-s1", Agreement.UNKNOWN, "contextual"),
        ("contextual-s2", Agreement.UNKNOWN, "contextual")]
    assert result.engine_error.startswith("review failed")
    assert [(e.stage, e.role, e.chunk_id) for e in result.errors] == [
        ("reconcile", "reviewer", "c1")]


async def test_fallback_uses_first_available_when_no_contextual(make_runner, fake_backend):
    fake_backend.script("reconcile", "garbage", "garbage")
    result = await reconcile_chunk(_ctx(make_runner()), PROFILE, BIBLE, _ci(), _cands("literal"))
    assert {s.chosen_from for s in result.spans} == {"literal"}


async def test_no_candidates_yields_empty_flagged_spans(make_runner, fake_backend):
    result = await reconcile_chunk(_ctx(make_runner()), PROFILE, BIBLE, _ci(), [])
    assert [(s.start, s.text, s.chosen_from) for s in result.spans] == [
        ("s1", "", "none"), ("s2", "", "none")]
    assert result.engine_error == "no translator produced output for this passage"
    assert fake_backend.requests == []


async def test_reviewer_coverage_errors_are_repaired(make_runner, fake_backend):
    bad = {"spans": [{"start": "s1", "end": "s1", "text": "x", "agreement": "agree",
                      "chosen_from": "literal"}]}
    fake_backend.script("reconcile", json.dumps(bad))
    result = await reconcile_chunk(_ctx(make_runner()), PROFILE, BIBLE, _ci(),
                                   _cands("literal", "contextual"))
    assert result.engine_error is None
    assert len(result.spans) == 2
```

- [ ] **Step 4: Implement `engine/coglossa/stages/reconcile.py`**

```python
"""Reconcile stage (spec §4.3, §4.5): the reviewer turns candidates into final spans."""

from dataclasses import dataclass

from coglossa.llm.agent import AGENT_FAILURES
from coglossa.schema import (
    Agreement,
    Bible,
    Candidate,
    DocumentProfile,
    ReconciledSpan,
    ReconcileOutput,
    RunError,
)
from coglossa.stages.bible import bible_slice
from coglossa.stages.context import StageContext
from coglossa.stages.translate import ChunkInput, check_coverage

NO_OUTPUT = "no translator produced output for this passage"


@dataclass
class ReconcileResult:
    spans: list[ReconciledSpan]
    engine_error: str | None
    errors: list[RunError]


async def reconcile_chunk(
    ctx: StageContext,
    profile: DocumentProfile,
    bible: Bible,
    ci: ChunkInput,
    candidates: list[Candidate],
) -> ReconcileResult:
    names = [t.name for t in ctx.cfg.roles.translators()]
    by_translator = {n: [c for c in candidates if c.translator == n] for n in names}
    available = [n for n in names if by_translator[n]]
    if not available:
        spans = [ReconciledSpan(start=s.id, end=s.id, text="", agreement=Agreement.UNKNOWN,
                                chosen_from="none") for s in ci.segments]
        return ReconcileResult(spans=spans, engine_error=NO_OUTPUT, errors=[])

    segment_ids = [s.id for s in ci.segments]
    sliced = bible_slice(bible, "\n".join(s.text for s in ci.segments))
    try:
        out = await ctx.runner.run(
            role="reviewer",
            model_key=ctx.cfg.roles.reviewer.model,
            prompt_id="reconcile",
            prompt_vars={"source_lang": profile.source_lang, "target_lang": profile.target_lang,
                         "translators": available},
            payload={
                "task": "reconcile",
                "source_lang": profile.source_lang,
                "target_lang": profile.target_lang,
                "bible": sliced.model_dump(),
                "segments": [{"id": s.id, "text": s.text} for s in ci.segments],
                "candidates": [
                    {
                        "translator": n,
                        "spans": [{"start": c.start, "end": c.end, "text": c.text,
                                   "notes": c.notes} for c in by_translator[n]],
                    }
                    for n in available
                ],
            },
            output_type=ReconcileOutput,
            validate=lambda o: check_coverage(o.spans, segment_ids),
        )
    except AGENT_FAILURES as e:
        best = "contextual" if "contextual" in available else available[0]
        spans = [ReconciledSpan(start=c.start, end=c.end, text=c.text,
                                agreement=Agreement.UNKNOWN, chosen_from=best)
                 for c in by_translator[best]]
        return ReconcileResult(
            spans=spans,
            engine_error=f"review failed: {e}",
            errors=[RunError(stage="reconcile", chunk_id=ci.chunk.id, role="reviewer",
                             message=str(e))],
        )
    return ReconcileResult(spans=out.spans, engine_error=None, errors=[])
```

- [ ] **Step 5: Run the stage tests, the full suite and lint**

Run: `cd engine && uv run pytest tests/stages -v --no-cov && uv run pytest && cd .. && make lint`
Expected: pass; coverage ≥95%.

- [ ] **Step 6: Commit**

```bash
git add engine/coglossa/stages/reconcile.py engine/coglossa/stages/score.py engine/tests/stages/test_reconcile.py engine/tests/stages/test_score.py
git commit -m "feat(stages): reviewer reconciliation with graceful fallback, M1 null scores and flags"
```

### Task 15: Pipeline orchestration and report assembly

**Files:**
- Create: `engine/coglossa/pipeline.py`
- Test: `engine/tests/test_pipeline.py`

**Interfaces:**
- Consumes everything above:
  - `ingest_file` (Task 8)
  - `segment_document`, `compute_budget`, `chunk_segments` (Tasks 9–10)
  - `AgentRunner`, `CallLog` (Task 7), `PromptLibrary` (Task 5)
  - `run_profile`, `run_bible`, `translate_chunk`, `build_chunk_inputs`, `reconcile_chunk`, `unscored`, `initial_flags`, `competing_candidates` (Tasks 11–14)
  - `config_hash` (Task 3)
- Produces:
  - `RunOptions(target_lang, source_lang=None, pipeline_profile=None, bible=None, bible_approved=False, checkpoint=None)`
  - `BibleCheckpoint(profile, bible, errors)`
  - `async run_document(path: Path, cfg, backends: dict[str, Backend], opts: RunOptions, *, prompts: PromptLibrary | None = None, sleep=asyncio.sleep, now=lambda: datetime.now(UTC)) -> Report | BibleCheckpoint`
- **Behavior:**
  - `ConfigError` (context too small), `IngestError` and `StageError` (profile or bible failed) propagate to the caller.
  - With `bible_approved=True` and a bible supplied, bible generation **and** the checkpoint are skipped. Otherwise the supplied bible is the `prior`.
  - The checkpoint is `opts.checkpoint` if set, else `cfg.profiles.checkpoint.get(profile, False)`.
  - Chunks run concurrently, each translate → reconcile.
  - Span ids are `p1…` in reading order. `bible_proposals` are deduplicated case-insensitively and exclude terms already in the bible.
  - Status is `complete_with_errors` if there's any `RunError` or any `engine_error` span.

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_pipeline.py`:
```python
import json
from datetime import UTC, datetime

import pytest

from coglossa.config import ConfigError
from coglossa.ingest.base import IngestError
from coglossa.llm.types import PermanentBackendError
from coglossa.pipeline import BibleCheckpoint, RunOptions, run_document
from coglossa.schema import Bible, FlagCode, GlossaryEntry, PipelineProfile, Report, RunStatus
from coglossa.stages.context import StageError
from tests.conftest import BASE_CONFIG

TEXT = "Il pleut. Le chat dort.\n\n* * *\n\nDemain, le soleil."
FIXED = datetime(2026, 10, 9, tzinfo=UTC)


async def _no_sleep(_):
    return None


@pytest.fixture
def doc_path(tmp_path):
    path = tmp_path / "story.txt"
    path.write_text(TEXT, encoding="utf-8")
    return path


async def _run(path, cfg, fake_backend, **opts):
    return await run_document(path, cfg, {"fake": fake_backend},
                              RunOptions(target_lang="en", **opts), sleep=_no_sleep,
                              now=lambda: FIXED)


def _tasks(fake_backend):
    return [json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["task"]
            for r in fake_backend.requests]


async def test_short_document_end_to_end(doc_path, make_config, fake_backend):
    report = await _run(doc_path, make_config(), fake_backend, source_lang="fr")
    assert isinstance(report, Report)
    assert report.status is RunStatus.COMPLETE
    assert report.created_at == FIXED
    assert report.profile.source_lang == "fr"
    assert report.profile.pipeline_profile is PipelineProfile.SHORT
    assert [s.text for s in report.segments] == ["Il pleut.", "Le chat dort.", "Demain, le soleil."]
    assert [(s.id, s.start, s.text) for s in report.spans] == [
        ("p1", "s1", "[en] Il pleut."), ("p2", "s2", "[en] Le chat dort."),
        ("p3", "s3", "[en] Demain, le soleil.")]
    assert all(s.score.meaning is None and s.flags == [] for s in report.spans)
    assert len(report.candidates) == 6
    assert _tasks(fake_backend) == ["profile", "bible", "translate", "translate", "reconcile"]
    assert report.usage.calls == 5
    assert report.config.roles["reviewer"] == "model-a"
    assert report.config.prompt_versions == {"profile": 1, "bible": 1, "bible_merge": 1,
                                             "reconcile": 1, "literal": 1, "contextual": 1}
    assert {c.role for c in report.llm_calls} == {
        "profiler", "bible", "translator:literal", "translator:contextual", "reviewer"}
    assert Report.model_validate_json(report.model_dump_json()) == report


async def test_every_segment_is_covered_once_by_spans(tmp_path, make_config, fake_backend):
    path = tmp_path / "long.txt"
    path.write_text("\n\n".join(f"Paragraph {i}. It has two sentences." for i in range(400)),
                    encoding="utf-8")
    cfg = make_config(chunking={"target_words": 200, "max_words": 300})
    report = await _run(path, cfg, fake_backend, checkpoint=False)
    positions = {s.id: i for i, s in enumerate(report.segments)}
    covered = [i for s in report.spans for i in range(positions[s.start], positions[s.end] + 1)]
    assert covered == list(range(len(report.segments)))
    assert len(report.chunks) > 1


async def test_long_form_stops_at_checkpoint(doc_path, make_config, fake_backend):
    result = await _run(doc_path, make_config(), fake_backend,
                        pipeline_profile=PipelineProfile.LONG_FORM)
    assert isinstance(result, BibleCheckpoint)
    assert "translate" not in _tasks(fake_backend)
    assert result.profile.pipeline_profile is PipelineProfile.LONG_FORM


async def test_checkpoint_config_and_override(doc_path, make_config, fake_backend):
    cfg = make_config(profiles={"checkpoint": {"short": True}})
    assert isinstance(await _run(doc_path, cfg, fake_backend), BibleCheckpoint)
    report = await _run(doc_path, cfg, fake_backend, checkpoint=False)
    assert isinstance(report, Report)


async def test_approved_bible_skips_generation_and_is_used(doc_path, make_config, fake_backend):
    approved = Bible(summary="approved", style_guide="terse",
                     glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)])
    report = await _run(doc_path, make_config(), fake_backend, bible=approved,
                        bible_approved=True, pipeline_profile=PipelineProfile.LONG_FORM)
    assert isinstance(report, Report)
    assert "bible" not in _tasks(fake_backend)
    assert report.bible == approved
    contextual = [json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])
                  for r in fake_backend.requests]
    assert any(p.get("bible", {}).get("glossary") == [approved.glossary[0].model_dump()]
               for p in contextual if p["task"] == "translate")


async def test_prior_bible_locked_terms_survive_regeneration(doc_path, make_config, fake_backend):
    prior = Bible(summary="old", style_guide="old",
                  glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)])
    report = await _run(doc_path, make_config(), fake_backend, bible=prior)
    assert report.bible.glossary == prior.glossary
    assert report.bible.summary.startswith("Synthetic summary")


async def test_reviewer_failure_marks_spans_and_status(doc_path, make_config, fake_backend):
    fake_backend.script("reconcile", PermanentBackendError("reviewer down"))
    report = await _run(doc_path, make_config(), fake_backend)
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    assert all(s.flags[0].code is FlagCode.ENGINE_ERROR for s in report.spans)
    assert report.spans[0].flags[0].evidence["candidates"]  # alternatives shown to the user
    assert [e.stage for e in report.errors] == ["reconcile"]


async def test_translator_failure_is_reported(doc_path, make_config, fake_backend):
    fake_backend.script("translate", PermanentBackendError("literal down"))
    report = await _run(doc_path, make_config(), fake_backend)
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    assert [e.role for e in report.errors] == ["translator:literal"]
    assert all(s.flags == [] for s in report.spans)


async def test_disagreement_and_proposals(doc_path, make_config, fake_backend):
    spans = [
        {"start": "s1", "end": "s2", "text": "It rains; the cat sleeps.", "agreement": "major",
         "chosen_from": "merged", "rationale": "literal misreads 'dort'",
         "cultural_or_wordplay": False,
         "proposed_terms": [{"term": "Pluie", "rendering": "rain", "locked": True},
                            {"term": "pluie", "rendering": "rain"}]},
        {"start": "s3", "end": "s3", "text": "Tomorrow, sun.", "agreement": "agree",
         "chosen_from": "literal"},
    ]
    fake_backend.script("reconcile", json.dumps({"spans": spans}))
    report = await _run(doc_path, make_config(), fake_backend)
    assert [s.flags[0].code for s in report.spans if s.flags] == [FlagCode.AGENT_DISAGREEMENT]
    assert len(report.spans[0].flags[0].evidence["candidates"]) == 4  # 2 translators x s1,s2
    assert [(g.term, g.locked) for g in report.bible_proposals] == [("Pluie", False)]


async def test_failures_before_translation_propagate(tmp_path, doc_path, make_config,
                                                     fake_backend):
    fake_backend.script("profile", "garbage", "garbage")
    with pytest.raises(StageError):
        await _run(doc_path, make_config(), fake_backend)
    empty = tmp_path / "empty.txt"
    empty.write_text("* * *", encoding="utf-8")
    with pytest.raises(IngestError):
        await _run(empty, make_config(), fake_backend)
    small = {**BASE_CONFIG["models"], "model-a": {"backend": "fake", "context_window": 4096}}
    with pytest.raises(ConfigError):
        await _run(doc_path, make_config(models=small), fake_backend)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/test_pipeline.py -v --no-cov`
Expected: FAIL (`coglossa.pipeline` is missing).

- [ ] **Step 3: Implement `engine/coglossa/pipeline.py`**

```python
"""run_document: ingest → segment → profile → bible → [checkpoint] → per-chunk
(translate → reconcile) in parallel → report (spec §4). Scoring signals are null in M1."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import coglossa
from coglossa.config import CoglossaConfig, config_hash
from coglossa.ingest.registry import ingest_file
from coglossa.llm.agent import AgentRunner, CallLog
from coglossa.llm.types import Backend
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import (
    Bible,
    Candidate,
    Chunk,
    ConfigSnapshot,
    DocumentInfo,
    DocumentProfile,
    GlossaryEntry,
    PipelineProfile,
    Report,
    RunError,
    RunStatus,
    Segment,
    SourceDocument,
    Span,
)
from coglossa.segment.chunking import chunk_segments, compute_budget
from coglossa.segment.segmenter import segment_document
from coglossa.stages.bible import run_bible
from coglossa.stages.context import StageContext
from coglossa.stages.profile import run_profile
from coglossa.stages.reconcile import ReconcileResult, reconcile_chunk
from coglossa.stages.score import competing_candidates, initial_flags, unscored
from coglossa.stages.translate import ChunkInput, build_chunk_inputs, translate_chunk


@dataclass
class RunOptions:
    target_lang: str
    source_lang: str | None = None
    pipeline_profile: PipelineProfile | None = None
    bible: Bible | None = None
    bible_approved: bool = False  # use `bible` verbatim; skip generation and checkpoint
    checkpoint: bool | None = None  # None = per-profile default from config


@dataclass
class BibleCheckpoint:
    profile: DocumentProfile
    bible: Bible
    errors: list[RunError]


@dataclass
class _ChunkOutcome:
    ci: ChunkInput
    candidates: list[Candidate]
    reconciled: ReconcileResult
    errors: list[RunError]


async def run_document(
    path: Path,
    cfg: CoglossaConfig,
    backends: dict[str, Backend],
    opts: RunOptions,
    *,
    prompts: PromptLibrary | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Report | BibleCheckpoint:
    budget = compute_budget(cfg)
    doc = ingest_file(path)
    segments = segment_document(doc)
    chunks = chunk_segments(doc, segments, budget)
    prompts = prompts or PromptLibrary(cfg.prompts_dir)
    log = CallLog(store_io=cfg.audit.store_llm_io)
    runner = AgentRunner(cfg, backends, prompts, log, sleep=sleep)
    ctx = StageContext(cfg=cfg, runner=runner, target_lang=opts.target_lang)

    profile = await run_profile(
        ctx, segments, source_lang=opts.source_lang, pipeline_profile=opts.pipeline_profile
    )
    errors: list[RunError] = []
    if opts.bible is not None and opts.bible_approved:
        bible = opts.bible
    else:
        bible, bible_errors = await run_bible(ctx, profile, segments, chunks, prior=opts.bible)
        errors.extend(bible_errors)
        checkpoint = (
            opts.checkpoint
            if opts.checkpoint is not None
            else cfg.profiles.checkpoint.get(profile.pipeline_profile, False)
        )
        if checkpoint:
            return BibleCheckpoint(profile=profile, bible=bible, errors=errors)

    outcomes = await asyncio.gather(
        *(_process_chunk(ctx, profile, bible, ci) for ci in build_chunk_inputs(chunks, segments))
    )
    return _assemble(doc, segments, chunks, profile, bible, list(outcomes), errors, cfg,
                     prompts, log, now())


async def _process_chunk(
    ctx: StageContext, profile: DocumentProfile, bible: Bible, ci: ChunkInput
) -> _ChunkOutcome:
    candidates, translate_errors = await translate_chunk(ctx, profile, bible, ci)
    reconciled = await reconcile_chunk(ctx, profile, bible, ci, candidates)
    return _ChunkOutcome(ci, candidates, reconciled, translate_errors + reconciled.errors)


def _assemble(
    doc: SourceDocument,
    segments: list[Segment],
    chunks: list[Chunk],
    profile: DocumentProfile,
    bible: Bible,
    outcomes: list[_ChunkOutcome],
    errors: list[RunError],
    cfg: CoglossaConfig,
    prompts: PromptLibrary,
    log: CallLog,
    created_at: datetime,
) -> Report:
    positions = {s.id: i for i, s in enumerate(segments)}
    known_terms = {g.term.casefold() for g in bible.glossary}
    spans: list[Span] = []
    candidates: list[Candidate] = []
    proposals: list[GlossaryEntry] = []
    any_engine_error = False
    for outcome in outcomes:
        candidates.extend(outcome.candidates)
        errors.extend(outcome.errors)
        engine_error = outcome.reconciled.engine_error
        any_engine_error = any_engine_error or engine_error is not None
        for rs in outcome.reconciled.spans:
            competing = competing_candidates(outcome.candidates, rs.start, rs.end, positions)
            spans.append(Span(
                id=f"p{len(spans) + 1}",
                chunk_id=outcome.ci.chunk.id,
                start=rs.start,
                end=rs.end,
                text=rs.text,
                agreement=rs.agreement,
                chosen_from=rs.chosen_from,
                rationale=rs.rationale,
                issues=rs.issues,
                score=unscored(),
                flags=initial_flags(rs, engine_error=engine_error, competing=competing),
            ))
            for term in rs.proposed_terms:
                key = term.term.casefold()
                if key not in known_terms:
                    known_terms.add(key)
                    proposals.append(term.model_copy(update={"locked": False}))

    translator_prompts = list(dict.fromkeys(t.prompt for t in cfg.roles.translators()))
    prompt_ids = ["profile", "bible", "bible_merge", "reconcile", *translator_prompts]
    return Report(
        coglossa_version=coglossa.__version__,
        created_at=created_at,
        status=(
            RunStatus.COMPLETE_WITH_ERRORS if errors or any_engine_error else RunStatus.COMPLETE
        ),
        document=DocumentInfo(source_path=doc.source_path, format=doc.format, sha256=doc.sha256),
        profile=profile,
        bible=bible,
        bible_proposals=proposals,
        blocks=doc.blocks,
        segments=segments,
        chunks=chunks,
        candidates=candidates,
        spans=spans,
        errors=errors,
        usage=log.usage(),
        config=ConfigSnapshot(
            hash=config_hash(cfg),
            roles=dict(cfg.roles.all_roles()),
            prompt_versions=prompts.versions(prompt_ids),
        ),
        llm_calls=log.records,
    )
```

`compute_budget` runs first so a config that is too small fails before any file I/O or model call.

- [ ] **Step 4: Run the pipeline tests**

Run: `cd engine && uv run pytest tests/test_pipeline.py -v --no-cov`
Expected: all pass.

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `cd engine && uv run pytest && cd .. && make lint`
Expected: pass; coverage ≥95%.

```bash
git add engine/coglossa/pipeline.py engine/tests/test_pipeline.py
git commit -m "feat: run_document pipeline with bible checkpoint and report assembly"
```

### Task 16: CLI, end-to-end test over HTTP, and README

**Files:**
- Create: `engine/coglossa/cli.py`, `engine/coglossa.example.yaml`
- Modify: `README.md`
- Test: `engine/tests/test_cli.py`

**Interfaces:**
- Consumes: `load_config`, `ConfigError` (Task 3), `build_backends` (Task 4), `FakeOpenAIServer` (Task 6), `IngestError` (Task 8), `StageError` (Task 11), `run_document`, `RunOptions`, `BibleCheckpoint` (Task 15), schema types.
- Produces: `coglossa.cli.main(argv: list[str] | None = None) -> int`.
  - Exit codes: **0** complete (or stopped at the bible checkpoint), **1** failed, **2** usage error, **3** complete with errors.
  - `coglossa run INPUT --target-lang LANG [--config coglossa.yaml] [--source-lang L] [--profile short|standard|long-form] [--bible FILE [--approved-bible]] [--no-checkpoint] [--out DIR]`
    - It writes `DIR/report.json`, or `DIR/bible.json` and `DIR/profile.json` at a checkpoint. The default `DIR` is `coglossa-out/<input stem>`.
  - `coglossa fake-openai [--host 127.0.0.1] [--port 8000] [--model NAME ...]`
  - `coglossa --version`

- [ ] **Step 1: Write the failing CLI tests**

`engine/tests/test_cli.py`:
```python
import json

import pytest
import yaml

from coglossa import __version__
from coglossa.cli import main
from coglossa.schema import Report
from coglossa.testing.fake_openai import FakeOpenAIServer

TEXT = "Il pleut. Le chat dort.\n\nDemain, le soleil."


@pytest.fixture
def server():
    with FakeOpenAIServer(models=("model-a", "model-b")) as s:
        yield s


def _config(tmp_path, base_url, **extra):
    data = {
        "backends": {"local": {"base_url": base_url, "max_concurrency": 4}},
        "models": {"model-a": {"backend": "local", "context_window": 32768},
                   "model-b": {"backend": "local", "context_window": 32768}},
        "roles": {"literal_translator": {"model": "model-a"},
                  "contextual_translator": {"model": "model-b"},
                  "reviewer": {"model": "model-a"},
                  "profiler_bible": {"model": "model-b"}},
        "retry": {"max_attempts": 1, "base_delay_s": 0, "max_delay_s": 0},
        **extra,
    }
    path = tmp_path / "coglossa.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "story.txt"
    path.write_text(TEXT, encoding="utf-8")
    return path


def _tasks(server):
    return [json.loads(r["messages"][1]["content"].split("INPUT_JSON:", 1)[1])["task"]
            for r in server.requests]


def test_run_end_to_end_over_http(tmp_path, server, doc, capsys):
    out = tmp_path / "out"
    code = main(["run", str(doc), "--config", str(_config(tmp_path, server.base_url)),
                 "--target-lang", "fr", "--source-lang", "fr", "--out", str(out)])
    assert code == 0
    report = Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))
    assert [s.text for s in report.spans] == ["[fr] Il pleut.", "[fr] Le chat dort.",
                                              "[fr] Demain, le soleil."]
    printed = capsys.readouterr().out
    assert "complete: 3 spans, 0 flags, 0 errors" in printed
    assert str(out / "report.json") in printed


def test_default_out_dir(tmp_path, server, doc, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["run", str(doc), "--config", str(_config(tmp_path, server.base_url)),
                 "--target-lang", "en"]) == 0
    assert (tmp_path / "coglossa-out" / "story" / "report.json").exists()


def test_checkpoint_then_approved_bible(tmp_path, server, doc, capsys):
    cfg = str(_config(tmp_path, server.base_url))
    out = tmp_path / "out"
    assert main(["run", str(doc), "--config", cfg, "--target-lang", "en",
                 "--profile", "long-form", "--out", str(out)]) == 0
    bible_path = out / "bible.json"
    assert bible_path.exists() and (out / "profile.json").exists()
    assert "--approved-bible" in capsys.readouterr().out
    bible = json.loads(bible_path.read_text(encoding="utf-8"))
    bible["glossary"] = [{"term": "chat", "rendering": "cat", "locked": True}]
    bible_path.write_text(json.dumps(bible), encoding="utf-8")
    server.requests.clear()
    assert main(["run", str(doc), "--config", cfg, "--target-lang", "en", "--profile",
                 "long-form", "--bible", str(bible_path), "--approved-bible",
                 "--out", str(out)]) == 0
    assert "bible" not in _tasks(server)
    report = Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))
    assert report.bible.glossary[0].locked is True


def test_no_checkpoint_flag(tmp_path, server, doc):
    out = tmp_path / "out"
    assert main(["run", str(doc), "--config", str(_config(tmp_path, server.base_url)),
                 "--target-lang", "en", "--profile", "long-form", "--no-checkpoint",
                 "--out", str(out)]) == 0
    assert (out / "report.json").exists()


def test_complete_with_errors_exit_code(tmp_path, server, doc):
    cfg = _config(
        tmp_path, server.base_url,
        backends={"local": {"base_url": server.base_url},
                  "dead": {"base_url": "http://127.0.0.1:9/v1", "timeout_s": 2}},
        models={"model-a": {"backend": "local", "context_window": 32768},
                "model-b": {"backend": "local", "context_window": 32768},
                "gone": {"backend": "dead", "context_window": 32768}},
        roles={"literal_translator": {"model": "model-a"},
               "contextual_translator": {"model": "model-b"},
               "reviewer": {"model": "gone"},
               "profiler_bible": {"model": "model-b"}},
    )
    out = tmp_path / "out"
    assert main(["run", str(doc), "--config", str(cfg), "--target-lang", "en",
                 "--out", str(out)]) == 3
    report = Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))
    assert report.status.value == "complete_with_errors"


def test_warnings_are_printed(tmp_path, server, doc, capsys):
    cfg = _config(
        tmp_path, server.base_url,
        models={"m": {"backend": "local", "context_window": 32768}},
        roles={k: {"model": "m"} for k in ("literal_translator", "contextual_translator",
                                           "reviewer", "profiler_bible", "judge")},
    )
    assert main(["run", str(doc), "--config", str(cfg), "--target-lang", "en",
                 "--out", str(tmp_path / "o")]) == 0
    assert "warning: judge must use a different model" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--config", "missing.yaml"], "config file not found"),
        (["--bible", "nope.json"], "bible file not found"),
    ],
)
def test_failures_exit_1(tmp_path, server, doc, capsys, args, message, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _config(tmp_path, server.base_url)
    assert main(["run", str(doc), "--target-lang", "en", *args]) == 1
    assert message in capsys.readouterr().err


def test_invalid_bible_and_unsupported_input(tmp_path, server, doc, capsys):
    cfg = str(_config(tmp_path, server.base_url))
    bad = tmp_path / "bad.json"
    bad.write_text('{"summary": 1}', encoding="utf-8")
    assert main(["run", str(doc), "--config", cfg, "--target-lang", "en",
                 "--bible", str(bad)]) == 1
    assert "not a valid bible" in capsys.readouterr().err
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF")
    assert main(["run", str(pdf), "--config", cfg, "--target-lang", "en"]) == 1
    assert "unsupported file type" in capsys.readouterr().err


def test_usage_errors_exit_2(doc):
    with pytest.raises(SystemExit) as exc:
        main(["run", str(doc), "--target-lang", "en", "--approved-bible"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_fake_openai_command(monkeypatch, capsys):
    def interrupt(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeOpenAIServer, "serve_forever", interrupt)
    assert main(["fake-openai", "--port", "0", "--model", "m1"]) == 0
    assert "fake OpenAI-compatible server at http://127.0.0.1:" in capsys.readouterr().out
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd engine && uv run pytest tests/test_cli.py -v --no-cov`
Expected: FAIL (`coglossa.cli` is missing).

- [ ] **Step 3: Implement `engine/coglossa/cli.py`**

```python
"""Command-line interface.

Exit codes: 0 complete (or stopped at the bible checkpoint), 1 failed, 2 usage error,
3 complete with errors."""

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import ValidationError

from coglossa import __version__
from coglossa.config import CoglossaConfig, ConfigError, load_config
from coglossa.ingest.base import IngestError
from coglossa.llm.registry import build_backends
from coglossa.pipeline import BibleCheckpoint, RunOptions, run_document
from coglossa.schema import Bible, PipelineProfile, Report, RunStatus
from coglossa.stages.context import StageError
from coglossa.testing.fake_openai import FakeOpenAIServer

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_WITH_ERRORS = 0, 1, 2, 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coglossa", description="Multi-agent translation with verifiable, scored output."
    )
    parser.add_argument("--version", action="version", version=f"coglossa {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="translate a document")
    run.add_argument("input", type=Path, help="TXT, MD or DOCX file")
    run.add_argument("--config", type=Path, default=Path("coglossa.yaml"))
    run.add_argument("--target-lang", required=True, help="BCP-47 code, e.g. en, pt-BR")
    run.add_argument("--source-lang", help="override language detection")
    run.add_argument("--profile", choices=[p.value for p in PipelineProfile])
    run.add_argument("--bible", type=Path, help="bible JSON from a previous run")
    run.add_argument("--approved-bible", action="store_true",
                     help="use --bible as-is: skip bible generation and the checkpoint")
    run.add_argument("--no-checkpoint", action="store_true",
                     help="do not stop for bible review, even for long-form documents")
    run.add_argument("--out", type=Path, help="output directory (default: coglossa-out/<name>)")

    fake = sub.add_parser("fake-openai",
                          help="serve a synthetic OpenAI-compatible endpoint (demos, tests)")
    fake.add_argument("--host", default="127.0.0.1")
    fake.add_argument("--port", type=int, default=8000)
    fake.add_argument("--model", action="append", dest="models", help="model id to advertise")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "fake-openai":
        return _fake_openai(args)
    if args.approved_bible and args.bible is None:
        parser.error("--approved-bible requires --bible")
    return _run(args)


def _fake_openai(args: argparse.Namespace) -> int:
    server = FakeOpenAIServer(args.host, args.port, tuple(args.models or ["fake-model"]))
    print(f"fake OpenAI-compatible server at {server.base_url} (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def _run(args: argparse.Namespace) -> int:
    try:
        cfg, warnings = load_config(args.config)
        for warning in warnings:
            print(f"warning: {warning.message}", file=sys.stderr)
        opts = RunOptions(
            target_lang=args.target_lang,
            source_lang=args.source_lang,
            pipeline_profile=PipelineProfile(args.profile) if args.profile else None,
            bible=_load_bible(args.bible) if args.bible else None,
            bible_approved=args.approved_bible,
            checkpoint=False if args.no_checkpoint else None,
        )
        result = asyncio.run(_execute(args.input, cfg, opts))
    except (ConfigError, IngestError, StageError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_FAILED

    out_dir = args.out or Path("coglossa-out") / args.input.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(result, BibleCheckpoint):
        return _write_checkpoint(result, out_dir, args)
    return _write_report(result, out_dir)


async def _execute(path: Path, cfg: CoglossaConfig, opts: RunOptions) -> Report | BibleCheckpoint:
    backends = build_backends(cfg)
    try:
        return await run_document(path, cfg, backends, opts)
    finally:
        for backend in backends.values():
            await backend.aclose()


def _load_bible(path: Path) -> Bible:
    if not path.is_file():
        raise ConfigError(f"bible file not found: {path}")
    try:
        return Bible.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(f"{path} is not a valid bible: {e}") from e


def _write_checkpoint(result: BibleCheckpoint, out_dir: Path, args: argparse.Namespace) -> int:
    bible_path = out_dir / "bible.json"
    bible_path.write_text(result.bible.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "profile.json").write_text(result.profile.model_dump_json(indent=2),
                                          encoding="utf-8")
    profile_flag = f" --profile {args.profile}" if args.profile else ""
    print(
        f"Bible ready for review: {bible_path}\n"
        'Edit it (set "locked": true on glossary entries you want enforced), then run:\n'
        f"  coglossa run {args.input} --target-lang {args.target_lang}{profile_flag} "
        f"--bible {bible_path} --approved-bible"
    )
    return EXIT_OK


def _write_report(report: Report, out_dir: Path) -> int:
    report_path = out_dir / "report.json"
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    flags = sum(len(s.flags) for s in report.spans)
    tokens = report.usage.tokens_in + report.usage.tokens_out
    print(
        f"{report.status.value}: {len(report.spans)} spans, {flags} flags, "
        f"{len(report.errors)} errors, {tokens} tokens\nreport: {report_path}"
    )
    return EXIT_OK if report.status is RunStatus.COMPLETE else EXIT_WITH_ERRORS
```

`main([])` exits with code 2 because the subparser is `required=True`. Unsupported input raises `UnsupportedFormatError`, a subclass of `IngestError`, so it exits 1.

- [ ] **Step 4: Run the CLI tests**

Run: `cd engine && uv run pytest tests/test_cli.py -v --no-cov`
Expected: all pass. `test_complete_with_errors_exit_code` relies on port 9 refusing connections quickly. If your machine has something listening on port 9, change the dead backend URL to another closed port.

- [ ] **Step 5: Add `engine/coglossa.example.yaml`**

```yaml
# Coglossa configuration — copy to coglossa.yaml and point it at your model servers.
# Any OpenAI-compatible server works: vLLM, Ollama, TGI, llama.cpp server, LM Studio.
backends:
  gpu-a:
    base_url: http://127.0.0.1:8000/v1   # e.g. vLLM; Ollama is http://host:11434/v1
    # api_key_env: GPU_A_KEY             # name of an environment variable, if the server needs a key
    max_concurrency: 4                   # never send more simultaneous requests than this
    timeout_s: 120

models:
  fake-model:                  # the key is how roles refer to the model
    backend: gpu-a
    # name: Qwen/Qwen3-72B     # model id the server expects, if different from the key
    context_window: 32768
    structured_output: json_schema   # json_schema | json_object | none

roles:
  literal_translator:    { model: fake-model }
  contextual_translator: { model: fake-model }   # use a different model family when you can
  reviewer:              { model: fake-model }
  profiler_bible:        { model: fake-model }
  # extra_translators:
  #   - { name: second-opinion, prompt: contextual, model: other-model }

# chunking: { target_words: 1500, max_words: 2000, reserve_tokens: 3000 }
# profiles: { short_max_words: 1500, standard_max_words: 20000 }
# retry: { max_attempts: 4, base_delay_s: 2, max_delay_s: 60 }
# audit: { store_llm_io: true }
# prompts_dir: ./prompts       # override templates: prompts/<id>/v<N>.j2
```

Test it by hand. This needs two terminals, or run the server in the background:
```bash
cd engine
uv run coglossa fake-openai --port 8000 &
printf 'Il pleut. Le chat dort.\n' > /tmp/demo.txt
uv run coglossa run /tmp/demo.txt --config coglossa.example.yaml --target-lang en --out /tmp/demo-out
kill %1
```
Expected: `complete: 2 spans, 0 flags, 0 errors, … tokens`, and `/tmp/demo-out/report.json` exists. With only one model configured, no warnings are printed, because judge and roundtrip aren't set.

- [ ] **Step 6: Replace `README.md`**

````markdown
# Coglossa

Multi-agent translation that translates for meaning and shows its work. Several translator
agents render each passage, a reviewer reconciles them, and every span of the result carries
its alternatives, the reviewer's rationale and plain-language flags. It runs entirely on your
own OpenAI-compatible model servers, including inside air-gapped networks.

Status: **M1** (engine + CLI). Scores are not computed yet (all signals are `null`); see
`docs/superpowers/specs/2026-10-09-coglossa-v1-design.md` for the roadmap.

## Quick start

```bash
cd engine
uv sync --all-groups
cp coglossa.example.yaml coglossa.yaml        # point backends at your model servers
uv run coglossa run my-chapter.docx --target-lang en
```

There's no model server yet? Try it with the built-in synthetic server:

```bash
uv run coglossa fake-openai --port 8000 &
uv run coglossa run my-chapter.docx --config coglossa.example.yaml --target-lang en
```

Long documents stop after the translation bible is built so you can review it:

```bash
uv run coglossa run novel.docx --target-lang en              # writes coglossa-out/novel/bible.json
# edit bible.json — set "locked": true on terms you want enforced
uv run coglossa run novel.docx --target-lang en --bible coglossa-out/novel/bible.json --approved-bible
```

Exit codes: `0` complete, `1` failed, `2` usage error, `3` complete with errors (see `errors`
and `engine_error` flags in the report).

## Development

```bash
make test     # pytest with a 95% branch-coverage gate; no GPU or network needed
make lint
make schema   # regenerate schema/*.schema.json after changing coglossa.schema models
```
````

- [ ] **Step 7: Final verification: full suite, lint, schema drift and coverage**

Run: `make schema && git status --short schema && cd engine && uv run pytest && cd .. && make lint`
Expected:
- `git status` shows no change under `schema/` (no drift)
- every test passes
- the coverage report shows a TOTAL of at least 95% (branch)
- lint is clean

If coverage is under 95%, the term-missing report lists the uncovered lines. Add tests for them rather than `pragma: no cover`.

- [ ] **Step 8: Commit**

```bash
git add engine/coglossa/cli.py engine/coglossa.example.yaml engine/tests/test_cli.py README.md
git commit -m "feat(cli): coglossa run / fake-openai with checkpoint flow and documented exit codes"
```
