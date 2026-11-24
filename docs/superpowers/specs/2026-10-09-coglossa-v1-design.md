# Coglossa v1 — Design Spec

- **Date:** 2026-10-09
- **Status:** Draft for review
- **Repo:** https://github.com/Bonham-Technologies/coglossa

## 1. Purpose

Coglossa is a multi-agent translation system that translates for meaning, not word-for-word. It shows its work so a human can check it. Several agents translate the source independently. A reviewer reconciles their versions, and independent signals score every span of the result. The user gets:

- a summary of the document
- a "translation guide" (the terms, names, voice and style decisions the agents applied; documents written before 2026-10-11 call it the "bible")
- a side-by-side view of source and translation, with a meaning score, a style score and explained flags for each span.

### 1.1 Who v1 is for

v1 targets a known customer that runs on an **air-gapped network** with **local models served over OpenAI-compatible APIs**. The customer needs to:

- move Coglossa into their network as an offline bundle and run it there
- point it at their own model servers by editing config
- translate any kind of material: long-form prose, technical reports and papers, short mixed-source items, and (in a later sub-project) scanned and audio sources
- work in any language pair, **including low-resource languages**.

A hosted SaaS version (accounts, billing, bundled or bring-your-own model keys, cost estimation) is planned. It reuses this same core as a different way of deploying it, and is **out of scope for v1**.

### 1.2 Success criteria

1. The system installs from the offline bundle on a host with no internet access and runs end to end against the customer's OpenAI-compatible endpoints. The only setup step is editing one config file.
2. `coglossa doctor` validates the installation and reports every misconfiguration in plain language.
3. For documents from a single message up to a full book, a run produces: a guide, a final translation aligned to the source, candidate translations, scores, explained flags and exports.
4. Scores are never invented. When a signal is unavailable, the span records that and its reliability is downgraded.
5. Automated tests reach **at least 95% branch coverage** in each Python package and **at least 95% line and branch coverage** in the web app. CI enforces this, and all tests run locally without GPUs or network access.
6. `coglossa bench` compares any pipeline configuration against a single-model, one-pass baseline on the customer's own models. It reports quality against tokens used and time taken, and measures how often flags catch injected errors.

### 1.3 Out of scope for v1

- User management, multi-tenancy, billing, cost estimation, bundled model keys (SaaS layer)
- OCR and speech-to-text ingestion. This is the next sub-project; v1 defines the ingestion plugin interface it will implement.
- Editing config in the UI; real-time multi-user collaboration; comments; per-user permissions
- Kubernetes/Helm packaging. v1 is container-first, so Helm can be added later.
- Learned score calibration. v1 records the feedback data this will need.

## 2. Architecture

```
                 customer network (air-gapped)
┌───────────────────────────────────────────────────────────────────┐
│  docker compose                                                   │
│  ┌─────────────┐   HTTP   ┌──────────────────────────────┐        │
│  │ web (React  │◀───────▶│ api (FastAPI)                 │        │
│  │ SPA, static)│          │ projects · docs · runs · guide│        │
│  └─────────────┘          │ review edits · export · SSE   │        │
│                           └──────┬───────────────▲────────┘        │
│                           ┌──────▼───────────────┴────────┐        │
│                           │ postgres (state + job queue)  │        │
│                           └──────┬───────────────▲────────┘        │
│  ┌───────────────┐  HTTP  ┌──────▼───────────────┴────────┐        │
│  │ scorer        │◀──────│ worker (coglossa.engine)       │        │
│  │ MetricX-24,   │        │ ingest → segment → profile →   │        │
│  │ LaBSE         │        │ guide → translate → reconcile →│        │
│  └───────────────┘        │ score → sweep → report         │        │
│     files: local volume   └──────┬────────────────────────┘        │
└──────────────────────────────────┼────────────────────────────────┘
                                   ▼  OpenAI-compatible HTTP
                 customer model servers (vLLM / Ollama / TGI / llama.cpp)
```

### 2.1 Components

| Component | Responsibility | Depends on |
|---|---|---|
| `engine/` (`coglossa.engine`) | A pure Python library. Each pipeline stage is a function with typed input and output. It also contains the model backend adapters and the scorer client. It has **no knowledge of the database or the web.** It ships a CLI: `coglossa run`, `coglossa doctor`, `coglossa bench`. | `schema/` types, backends via the protocol |
| `engine/` worker | A thin layer that takes stage tasks from the queue, calls the library, saves outputs, publishes progress and enforces concurrency limits. | engine, Postgres |
| `api/` | A FastAPI REST API plus SSE for progress. It serves the SPA's static files. It creates runs and stores review actions. It never calls models. | Postgres, storage |
| `web/` | A TypeScript React single-page app built with Vite: projects, runs, guide editor, review view, export, system status. | api |
| `scorer/` | An HTTP service for the model-based signals: MetricX-24 hybrid in QE mode, LaBSE embeddings. It runs on GPU or CPU. | model weights in the bundle |
| `schema/` | Exported JSON Schema, the published contract. The Pydantic models in `coglossa.schema` are the source; `make schema` regenerates these files, TypeScript types are generated from them (M4), and CI fails on drift. | `coglossa.schema` |
| `deploy/` | The compose file, config template, bundle build script, SBOM generation and the air-gap install test. | all images |

Postgres is the single source of truth and also the job queue (`procrastinate`). Files live on a local volume behind a `Storage` interface; S3/MinIO is a future implementation of that interface.

### 2.2 Repository layout

```
engine/      Python package coglossa (library, worker, CLI)   — uv
api/         FastAPI service                                    — uv
scorer/      scoring service                                    — uv
web/         React + Vite SPA                                   — pnpm
schema/      exported JSON Schema (generated by make schema)
deploy/      compose, config template, bundle tooling, fake OpenAI server
bench/       benchmark datasets manifest + error-injection tooling
docs/        specs, plans, operator guide
```

## 3. Configuration

All deployment-specific behavior lives in one YAML file, `coglossa.yaml`, plus an optional `prompts/` override directory. The file is validated at startup and by `doctor`.

```yaml
backends:
  gpu-a:
    kind: openai_compat          # built-in adapter; plugins can register other kinds
    base_url: http://10.0.4.12:8000/v1
    api_key_env: GPU_A_KEY       # optional
    max_concurrency: 8
    timeout_s: 120
  gpu-b:
    kind: openai_compat
    base_url: http://10.0.4.13:11434/v1
    max_concurrency: 4

models:
  qwen3-72b:   { backend: gpu-a, context_window: 32768, structured_output: json_schema }
  llama-4-mav: { backend: gpu-b, context_window: 131072, structured_output: json_schema }
  gemma-3-27b: { backend: gpu-b, context_window: 131072, structured_output: json_schema }

roles:
  literal_translator:    { model: qwen3-72b }
  contextual_translator: { model: llama-4-mav }
  extra_translators: []          # optional additional { name, prompt, model }
  reviewer:              { model: qwen3-72b }
  judge:                 { model: gemma-3-27b }   # must differ from reviewer (validated)
  roundtrip:             { model: gemma-3-27b }   # must differ from every translator (validated)
  profiler_guide:        { model: llama-4-mav }

scorer:
  url: http://scorer:8080        # the device is chosen when starting the scorer:
                                 #   coglossa-scorer --device cuda (or cpu) ...

scoring:
  thresholds: { meaning: { A: 70, B: 72, C: 75 }, style: 60 }   # or meaning: 70 for every tier
  # weights_by_tier: { C: { qe: 0.1, xsim: 0.05, roundtrip: 0.3, judge: 0.4, agreement: 0.15 } }
  # literary_roundtrip_factor: 0.5    # roundtrip weight multiplier for literary prose and poetry
  language_tiers_file: tiers.yaml

retry: { max_attempts: 4, base_delay_s: 2, max_delay_s: 60 }
audit: { store_llm_io: true }
storage: { kind: local, path: /data/files }
auth:  { mode: none }            # none | trusted_header (header name configurable)
```

**Validation rules:**

- every role names a model that is defined
- every model names a backend that is defined
- `judge` ≠ `reviewer`
- `roundtrip` is not the model of any translator role

If the roundtrip rule can't be met because only one model is configured, the config still validates. The system then warns and marks reliability lower (§6.4).

**Plugins:** these are Python entry points in the groups `coglossa.backends`, `coglossa.ingestors` and `coglossa.scorers`. This is how non-OpenAI backends, OCR/STT and licensed scorers such as CometKiwi plug in without changing the core.

## 4. Engine pipeline

```
ingest → segment → profile → guide ─(checkpoint?)→ [translate → reconcile → score] per chunk ∥ → sweep → report
```

### 4.1 Stages

| Stage | Input → Output | Behavior |
|---|---|---|
| ingest | file → `SourceDocument` | Produces structured `Block`s (heading, paragraph, footnote, table cell, list item) with provenance (file offsets, page, or EPUB spine item). v1 ingestors: TXT, MD, HTML, DOCX, EPUB, and PDFs that contain text. If a PDF page has no text layer, ingest reports `needs_ocr` and stops; it does not attempt OCR. |
| segment | blocks → `Segment[]`, `Chunk[]` | Sentence segmentation is language-aware: ICU/`pysbd`-style rules plus a fallback for scripts without sentence punctuation. Chunks are about 1–2k words, split at section or scene breaks, and sized to fit the **smallest context window in the agent roster** after reserving room for the prompt, guide and output. |
| profile | document → `DocumentProfile` | Records the source language (with a per-block override for mixed-language text), genre, register, length class and chosen pipeline profile. The user can override any of these. |
| guide | document (+ previous project guide) → `Guide` | Contents: summary, glossary (`term`, `rendering`, `notes`, `locked`), entities (name, gender, forms of address, speech style), style guide, cultural references. For `long-form`, the guide is built map-reduce: notes per chunk, then a merge. The merge never drops locked entries from an earlier guide. |
| checkpoint | — | If enabled for the profile, the run waits in `awaiting_guide_approval` until the user approves the guide version. |
| translate | chunk + guide + neighboring source chunks → `Candidate[]` | Every translator role runs on the chunk. Translators run in parallel across chunks. |
| reconcile | source + guide + candidates → `Span[]` | Produces the final translation for each span, an agreement level, a rationale for each divergence, notes on errors, and proposed new guide terms. |
| score | spans → `SpanScore[]`, `Flag[]` | See §6. |
| sweep | whole run → `Revision[]` | Enforces locked terms and checks names and forms of address for consistency across chunks. Resolves the proposed guide terms. Every change is stored as a revision with a reason. |
| report | run → `Report` | The canonical JSON report, plus exports (§7.5). |

### 4.2 Pipeline profiles

| Profile | Chosen when (default) | Guide | Checkpoint | Sweep |
|---|---|---|---|---|
| `short` | under 1,500 words | light brief | off | off |
| `standard` | 1,500–20,000 words | full, single pass | off | on |
| `long-form` | over 20,000 words | map-reduce | on | on |

The word thresholds and per-profile toggles can be changed in config.

### 4.3 Agents

| Role | Receives | Instruction focus |
|---|---|---|
| literal_translator | chunk + glossary | Faithfulness to meaning; no stylistic liberties; preserve every proposition. |
| contextual_translator | chunk + full guide + neighboring source chunks | Natural, genre-appropriate rendering that preserves voice, register and cultural intent. |
| extra_translators | as configured | Optional, to add diversity (e.g., a second contextual translator on a different model). |
| reviewer | source + guide + all candidates | Picks, merges or rewrites the final translation; rates agreement as `agree`, `minor` or `major`; explains divergences; flags cultural references and wordplay; proposes guide terms. |
| judge | source + final translation (+ guide) | Marks MQM-style error spans (§6.1). Must use a different model from the reviewer. |
| roundtrip | final translation | Translates back into the source language. Must use a model no translator used. |
| profiler_guide | document | Runs the profile and guide stages. |

### 4.4 Alignment: spans

Agents receive segments numbered with IDs and return translations keyed by **span**: a contiguous range of segment IDs (`s12`, or `s12–s13` when sentences are merged). For every chunk, the reconciled spans must cover each segment **exactly once**. The engine checks this; if the check fails, it triggers the repair path (§4.5). Candidates may use different span boundaries from the final version; the review UI matches them up by overlapping segment ranges.

### 4.5 Structured output

Every agent's output is checked against a JSON Schema from `schema/`. The adapter asks the server for schema-constrained output when the model declares `structured_output: json_schema`. For `json_object` or `none`, the schema goes into the prompt and the output is parsed. If parsing or validation fails, the agent gets **one repair retry** with the validation errors. If that fails too, the affected spans are produced as `engine_error` spans: the final translation is copied from the best-available candidate, or left empty if there is none, and a flag is raised. The run does not crash.

### 4.6 Prompts

Prompts are versioned Jinja templates in `engine/coglossa/prompts/<role>/v<N>.j2`. The customer can override them through the `prompts/` directory. Every stage result and model-call record stores the prompt ID and version.

## 5. Data model (Postgres)

```
project ─┬─ guide_version            (append-only; status draft|approved; author engine|user)
         └─ document ─┬─ block ── segment     (immutable after ingest)
                      ├─ chunk                (segment range)
                      └─ run ─┬─ stage_result (run, stage, chunk?) — idempotency key; status, attempt, output JSONB, error
                              ├─ llm_call     (role, backend, model, prompt_version, tokens in/out, latency, request/response if audit on)
                              ├─ candidate    (seg_start, seg_end, role, model, text, notes)
                              └─ span ─┬─ span_score   (signals JSONB, meaning, style, reliability)
                                       ├─ flag         (code, explanation, evidence JSONB, status open|accepted|dismissed, dismiss_reason)
                                       ├─ revision     (append-only; source engine|sweep|user; before, after, reason)
                                       └─ review_event (action, payload, at) — feedback log
```

- The guide belongs to the **project**. Documents in the same project share it and add to it over time.
- A **run** is one pipeline execution with a frozen config snapshot: the config hash, the snapshot JSON and the prompt versions. Re-running creates a new run, which makes runs comparable and supports the bench (§9).
- The current text of a span is its latest `revision`. Earlier versions are never deleted.
- **Deleting a project** cascades to every row, every stored file and every `llm_call` payload.
- Migrations use Alembic. The shapes of JSONB fields are defined in `schema/`.

## 6. Scoring and flags

### 6.1 Signals (per span)

| Signal | Method | Notes |
|---|---|---|
| `qe` | MetricX-24 hybrid (`google/metricx-24-hybrid-*-v2p6`, Apache-2.0) in QE mode on (source, final). Its error score (0–25, lower is better) is mapped to 0–1. | Scorer service. The XL size by default; the large size is the fallback when running on CPU. |
| `xsim` | Cosine similarity of LaBSE embeddings (source, final) | Scorer service. Runs fine on CPU. |
| `roundtrip` | Back-translation by the `roundtrip` role, then MetricX in reference mode and LaBSE (source vs back-translation) | Less weight in `long-form` literary text. |
| `judge` | LLM judge returns error spans: category (accuracy/mistranslation, accuracy/omission, accuracy/addition, terminology, fluency, style/register, locale) and severity (minor, major, critical). Turned into an MQM-style penalty. | Supplies explanations and highlighted spans. |
| `agreement` | The reviewer's agreement rating, plus the mean pairwise LaBSE similarity between candidates | |
| `guide_check` | Rule-based: locked glossary terms, name spellings, forms of address | |

The CometKiwi and xCOMET models are licensed CC BY-NC-SA 4.0. They are **not shipped**; they may be added as a `coglossa.scorers` plugin by a customer who has their own license.

### 6.2 Outputs (per span)

- `meaning` (0–100): combines `qe`, `xsim`, `roundtrip` and the judge's accuracy penalty.
- `style` (0–100): based on the judge's fluency, style/register and locale penalties.
- `reliability`: tier A, B or C, adjusted down for missing signals or a shared-model fallback.
- `flags[]`: each has `code`, a plain-language `explanation`, and `evidence` (the judge's error spans, competing candidates, the violated glossary entry, or the values of the signals involved).

Flag codes:

- `low_meaning`
- `style_drift`
- `agent_disagreement`
- `terminology_violation`
- `possible_omission`
- `number_mismatch` (CGL-86)
- `untranslated` (CGL-107)
- `cultural_or_wordplay`
- `engine_error`
- `low_reliability_language`
- `unscored` (document-level notice: no scorer or judge produced any score, so every passage needs review; replaces `low_reliability_language`)

### 6.3 Aggregation (v1)

- Scores are a weighted mean of the normalized signals that are available. Each profile and tier has its own weights, shipped as defaults and changeable in config. If a signal is `null`, the remaining weights are renormalized.
- A span is flagged when its meaning score is below the meaning threshold or its style score is below the style threshold. The defaults are 70 and 60, configurable per language pair.
- **Hard triggers** raise a flag regardless of the scores:
  - any `critical` judge error
  - any locked-term violation
  - reviewer agreement of `major`
  - a target/source length ratio outside the band for that language pair, combined with a judge omission (`possible_omission`).
  - numbers written in digits that differ between source and target (`number_mismatch`, CGL-86): an exact multiset check that reads every digit script and separator style and never changes the scores (`coglossa/scoring/numbers.py`).
  - a translation that is the source copied through, or is mostly in a script the target language is not written in (`untranslated`, CGL-107): two text rules with no language model, which leave out names, numbers, addresses and code and never change the scores (`coglossa/scoring/untranslated.py`).
- The UI and the report always include the raw signals.

### 6.4 Language reliability tiers

`tiers.yaml` assigns each language pair a tier:

- **A**: high-resource
- **B**: mid-resource
- **C**: low-resource.

Tier C changes the defaults:

- lower weight on `qe` and `xsim`, higher weight on `judge`, `roundtrip` and `agreement`
- more conservative thresholds
- a banner across the whole document, "Scores for this pair are less reliable", and the `low_reliability_language` flag in the document metadata.

A span's reliability drops one tier when two or more signals are `null`, or when the judge or roundtrip model is the same as a translator's. The customer can edit `tiers.yaml`. Pair overrides are written `"src>tgt": A`. The default tiers are derived from bench results on FLORES-200 (§9.2).

### 6.5 Feedback capture

Every accept, edit, candidate selection, flag dismissal (with a reason) and guide addition is written to `review_event`. v1 stores this data and does not use it yet.

## 7. Jobs and operations

### 7.1 Run lifecycle

```
created → ingesting → profiling → building_guide → [awaiting_guide_approval] → translating
        → sweeping → complete | complete_with_errors | failed | cancelled
```

- During `translating`, each chunk moves through translate → reconcile → score on its own. The sweep waits until every chunk has finished.
- The API publishes progress over SSE: counts of chunks in each stage. A polling endpoint is the fallback.
- Every task is idempotent, keyed by `(run, stage, chunk)`.

### 7.2 Concurrency and context

- The worker never sends a backend more simultaneous requests than its `max_concurrency`.
- Chunk sizing respects context windows (§4.1). If a model still reports an overflow, the chunk is split in two and retried.
- v1 runs one worker process with async concurrency. The design allows more workers without changes.

### 7.3 Failure handling

| Failure | Response |
|---|---|
| Backend timeout, 5xx response, connection error | Exponential backoff with jitter, up to `retry.max_attempts`. Then the chunk fails, the other chunks continue, the run ends `complete_with_errors`, its spans are flagged `engine_error`, and the user can retry the failed chunks. |
| Invalid structured output | One repair retry, then `engine_error` (§4.5). |
| Context overflow | Split the chunk and retry. |
| Worker crash | The queue detects stalled jobs and re-queues them; idempotency prevents duplicate work. |
| Scorer unavailable | The signal is `null` and reliability drops; the run continues. |
| Cancel | Checked between tasks; completed results are kept. |
| Config invalid at startup | The service refuses to start and prints `doctor`-style errors. |

### 7.4 `coglossa doctor`

Available as a CLI command and on the UI's System page. It checks:

- config validity and the role constraints
- for each backend: reachable; models listed by `/v1/models`; a test completion succeeds; schema-constrained output works when declared; the declared context window is plausible (a probe prompt)
- scorer health and which models are loaded
- database connectivity and migration state
- that the storage location is writable.

Each check reports pass, warn or fail, with a fix-it message.

### 7.5 Exports

- the canonical JSON `Report`
- a bilingual side-by-side DOCX, with flags optionally added as Word comments
- a clean DOCX in the target language
- HTML.

All exports reflect the current revisions.

### 7.6 Observability

- structured JSON logs
- the `llm_call` table (tokens, latency, and full request and response when `audit.store_llm_io` is on)
- run timing in each `stage_result`.

## 8. Web app

The app works fully offline. Noto fonts are bundled (no CDN). Every text cell uses `dir="auto"` so right-to-left scripts display correctly. The UI language is English in v1.

1. **Projects:** create a project (source and target language, auto-detected with override; profile auto or override).
2. **Documents and runs:** upload, run status, live progress by stage, retry failed chunks, list of past runs per document (side-by-side run comparison is a `bench` concern in v1).
3. **Guide editor:** summary, glossary (edit and lock), entities, style guide; Approve resumes the run. Editing a locked term after translation offers to re-run the sweep.
4. **Review view:** source | translation (editable) | meaning | style | flags, with:
   - filters by flagged status, flag code and score range
   - keyboard navigation between flags
   - an expandable panel per span: the candidates with their role and model, the reviewer's rationale, the signal breakdown, the judge's error spans highlighted inline, and revision history
   - actions: accept, use candidate, edit, dismiss flag (with a reason), add to guide
   - a document header with the summary, a reliability banner and statistics.
5. **Export:** the formats in §7.5.
6. **System:** `doctor` results, the active config (read-only), the language tier table.

The REST API is the only interface the SPA uses, so it also serves customers who want to script Coglossa themselves.

## 9. Testing and evaluation

### 9.1 Tests (run locally with `make test`; no GPU or network needed)

| Layer | Scope | Tools |
|---|---|---|
| Unit | Each stage, adapter, aggregation rule and API handler. `FakeBackend` returns scripted, deterministic responses and injects faults: timeouts, 5xx responses, invalid JSON, context overflow, slow responses. `FakeScorer` does the same for scoring. | pytest, pytest-asyncio |
| Property | Every segment is covered exactly once by spans; offsets map back to the source; chunks stay within the context budget; guide merges never drop locked terms; aggregation output stays in [0, 100]. | Hypothesis |
| Contract | Generated Pydantic and TS types match `schema/` (CI fails on a diff). API responses validate against the schema. | CI codegen check |
| Integration | API + worker + Postgres + a **fake OpenAI-compatible server** (in `deploy/`), so the real `openai_compat` adapter is tested over HTTP. | pytest, docker compose |
| Web + E2E | Component tests; Playwright journeys: upload → approve guide → review and resolve a flag → export. | Vitest, Testing Library, Playwright |

**Coverage gate:** at least 95% branch coverage for each Python package (`--cov-branch --cov-fail-under=95`), and at least 95% line and branch coverage for `web/` (Vitest thresholds). CI blocks merges below that. Any `pragma: no cover` needs a justification comment.

**Outside the gate:**

- **Real-model smoke test** (nightly or on demand): Ollama with a small instruct model, to confirm the prompts produce valid structured output on real local models.
- **Air-gap install test** (CI): build the bundle, install it on a Docker network created with `--internal` (no egress), then run `doctor` and an E2E smoke test.

### 9.2 Quality evaluation: `coglossa bench`

- Runs any number of pipeline configurations, plus a **single-model one-pass baseline**, over a dataset. It reports MetricX (reference mode), chrF, tokens used and time taken per configuration and language pair, and optionally exports a blind A/B sheet for human preference judgments.
- Datasets:
  - **FLORES-200** (CC BY-SA 4.0; shipped in the bundle with attribution), covering 200 languages
  - public-domain literary works with published translations, for long-form text.
- **Testing the flags:** inject known errors into reference translations (omissions, flipped negations, swapped names or numbers, register shifts, violations of locked terms). Then measure flag recall and precision per tier. These results set the default thresholds and `tiers.yaml`.

## 10. Packaging and deployment

- Images: `api` (which also serves `web` static files), `worker`, `scorer`, `postgres`, plus the `fake-openai` image for tests and demos.
- `make bundle` produces a versioned tarball containing:
  - the `docker save` image archives
  - the scorer weights (MetricX-24 XL and large, LaBSE)
  - `compose.yaml`
  - `coglossa.example.yaml` and `tiers.yaml`
  - the FLORES-200 data for bench
  - SHA-256 checksums
  - an SBOM (CycloneDX)
  - an operator guide.
- Install steps: verify the checksums → `docker load` → copy and edit the config → `docker compose up -d` → `coglossa doctor`.
- Upgrades: load the new bundle; database migrations run automatically at startup, after a backup step described in the operator guide.

## 11. Build sequence (milestones)

Each milestone gets its own implementation plan and keeps coverage at or above 95%.

1. **M1: Schema and engine core with the CLI.** Schema and codegen; ingest (TXT, MD, DOCX); segment; profile; guide; translate; reconcile; the `openai_compat` adapter, `FakeBackend` and the fake OpenAI server; `coglossa run` producing a JSON report from files. Scoring stubbed to `null` signals.
2. **M2: Scoring.** The scorer service (MetricX-24, LaBSE), the judge, roundtrip, agreement, guide_check, aggregation, tiers, flags.
3. **M3: Persistence and orchestration.** Postgres schema, the worker on procrastinate, run lifecycle, concurrency limits, retries, sweep, `doctor`.
4. **M4: API and web.** REST API and SSE, the SPA screens (§8), exports, remaining ingestors (HTML, EPUB, text PDFs).
5. **M5: Packaging.** Bundle build, SBOM, air-gap install test, operator guide.
6. **M6: Bench.** `coglossa bench`, FLORES-200 integration, error injection, default threshold and tier calibration.

M1 alone produces a useful CLI tool that can be demonstrated to the customer early.

## 12. Risks and assumptions

| Risk or assumption | Mitigation |
|---|---|
| The customer's local models are weaker at schema-constrained JSON than frontier APIs. | Guided decoding, the repair retry and `engine_error` degradation (§4.5); the real-model smoke test (§9.1). |
| MetricX-24 quality on low-resource pairs is unverified (it is mT5-based, and its published evaluations cover few pairs). | Tier C reduces `qe`'s weight; tiers are derived from FLORES-200 bench results (§6.4, §9.2). |
| The multi-agent pipeline may not beat a strong single-model baseline on some content. | `bench` measures this per customer and language pair; profiles let simple content use the light path. |
| The customer may have only one usable model, which defeats model diversity. | The config validates with a warning; reliability is downgraded; the UI says why. |
| A long-form run on shared GPUs may take hours. | Chunks complete independently and are reviewable as they finish; runs resume after interruption; per-backend concurrency limits. |
| The assumption that the customer's servers are OpenAI-compatible is wrong for some endpoint. | The `coglossa.backends` plugin interface. |
