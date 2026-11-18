# M6 calibration prep: bench fixes before the calibration run

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the five bench defects listed under "Fix before the calibration run (Task 12)" in `docs/m6-followups.md` (Jira CGL-8, CGL-9, CGL-10), so that the calibration run on real models (CGL-7) can resume safely and report numbers that mean one thing.

**Architecture:** All changes are in `engine/coglossa/bench/` and their tests in `engine/tests/bench/`, plus wording in `README.md`, `docs/operator-guide.md` and `docs/m6-followups.md`. A bench result gets two separate identities:
- an **output fingerprint**, covering only what shapes the translated text. A mismatch means translating again.
- a **MetricX identity**, the scorer's QE model plus `qe_max_chars`. A mismatch means scoring the stored outputs again, without translating.

`bench ab` reads exactly the results the last summary used.

**Tech Stack:** Python 3.12, Pydantic v2, pytest (asyncio), uv. Run the engine tests with `make test-engine`, or `cd engine && uv run pytest tests/bench -q`. Lint with `make lint`.

**Spec:** `docs/superpowers/specs/2026-10-09-coglossa-v1-design.md` (§9.2, `coglossa bench`). Background: `docs/superpowers/plans/2026-10-12-m6-bench.md` and `docs/m6-followups.md`.

## Global Constraints
- Coverage gate: ≥95% branch coverage for the engine package (currently 99.17%). Never lower it.
- Tests run offline: no network, no GPU. Use the existing fakes in `engine/tests/bench/conftest.py` (fake backends and fake scorers).
- Docs and log lines are plain English, short sentences, and match the voice of the surrounding text. Never use the word "bible"; the per-project record is the "translation guide".
- Keep existing public CLI flags and file names (`summary.json`, `summary.md`, `bench-meta.json`, `calibration.yaml`, `flag-summary.md`, `ab-sheet.csv`, `ab-key.csv`) unchanged.
- Results written by older versions must load without error. A missing new field means "unknown", so the result is redone or re-scored, never trusted.

## Review Focus
1. **Following the guide's apply steps does not re-translate.** After `bench run` finishes, an operator edits `scoring.thresholds`, `scoring.language_tiers_file`, a backend's `timeout_s`, `max_concurrency` or `api_key_env`, `retry`, `audit`, `scorer` or `worker`, then runs `bench run` again. Expected: every pair is skipped as finished, and nothing is translated.
2. **A prompt edit does re-translate.** Changing the text of a template under `prompts_dir`, without renaming it, makes the affected systems run again ("settings changed").
3. **A baseline ignores the rest of the config.** Changing the reviewer's model or the judge's backend URL does not re-run a baseline that uses another model. Changing the baseline model's own backend `base_url` or `temperature` does re-run it.
4. **A scorer without QE, or a scorer that is down, never marks MetricX as done.** Scores arrive on the next run with a working QE scorer, without translating again.
5. **`bench ab` after removing a system or a pair** never offers results the summary left out.

---

### Task 1: Output fingerprint covers only what shapes the text (CGL-8)

**Files:**
- Create: `engine/coglossa/bench/fingerprint.py`
- Modify: `engine/coglossa/bench/run.py` (`_fingerprint` and its use in `run_bench`)
- Modify: `engine/coglossa/bench/metrics.py` (the `Fingerprint` model)
- Test: create `engine/tests/bench/test_fingerprint.py`; modify `engine/tests/bench/test_run.py`

**Interfaces:**
- Produces: `output_hash(cfg: CoglossaConfig, spec: SystemConfig) -> str`, a 16-hex-character SHA-256 prefix. `Fingerprint.config` is replaced by `Fingerprint.output: str`, which holds that hash. The other `Fingerprint` fields (`dataset`, `split`, `sentences`, `seed`, `sample`, `model`) stay. Since the field changes, a result with the old `config` field fails validation as a `Fingerprint`. Make `SystemPairResult` loading tolerate this: an old result whose fingerprint cannot be parsed loads with `fingerprint=None`, which means "redo". The simplest way is a `field_validator("fingerprint", mode="before")` that returns None for a dict without `output`.
- Consumes: `SystemConfig` from `coglossa.bench.config`, which has `name`, `kind` ("pipeline" or "baseline"), `config` and `model`. Also `CoglossaConfig` from `coglossa.config`, and `PromptLibrary` and `BUILTIN_DIR` from `coglossa.prompts.library`.

**What the hash covers.** Build a dict, dump it with `json.dumps(..., sort_keys=True, ensure_ascii=False, default=str)`, and hash it.

A **pipeline** system covers:
- `backends`: per backend, only `kind` and `base_url`.
- `models`: everything (`backend`, `name`, `context_window`, `structured_output`, `bytes_per_token`, `max_output_tokens`, `temperature`).
- `roles`: all of it. The judge and roundtrip roles are covered because they spend tokens and time, which the result reports.
- `chunking` and `profiles`: all of them.
- `prompts`: for every prompt id the library can resolve (the names of the subdirectories of `BUILTIN_DIR` and, if set, `cfg.prompts_dir`), the pair `(version, sha256 of the resolved template file's bytes)`, taken from `PromptLibrary(cfg.prompts_dir).resolve(id)`.

A **baseline** system covers:
- `models[spec.model]`, all of it
- `backends[that model's backend]`, only `kind` and `base_url`
- `chunking`
- `prompts`, for the `contextual` prompt id only
- the model key itself.

Nothing else is covered: not `retry`, `audit`, `scoring`, `scorer`, `database`, `storage`, `worker`, `api` or `auth`, not the path `prompts_dir` itself, and not a backend's `api_key_env`, `timeout_s` or `max_concurrency`.

If a baseline's model is not in `cfg.models`, `output_hash` raises `ConfigError`. `load_systems` already refuses such a config, so this is a guard.

- [ ] **Step 1: Write the failing tests** in `engine/tests/bench/test_fingerprint.py`. Build a pipeline `CoglossaConfig` and a baseline `SystemConfig` with the helpers the existing bench tests use (look in `conftest.py` and `test_run.py`). Each case changes one field with `model_copy(update=...)`, or by writing the config file again, and asserts whether the hash changed.
  - **Unchanged, pipeline and baseline:** `scoring.thresholds.style`, `scoring.language_tiers_file`, `backends[x].timeout_s`, `backends[x].max_concurrency`, `backends[x].api_key_env`, `retry.max_attempts`, `audit.store_llm_io`, `worker.concurrency`, and `scorer` (set versus None).
  - **Changed, pipeline:** `models[x].temperature`, `backends[x].base_url`, `roles.reviewer.model`, `roles.judge`, `chunking.target_words`, `profiles.short_max_words`.
  - **Prompt edits, pipeline:** write a `prompts_dir` holding `contextual/v1.j2`, hash, then rewrite the file's text and hash again: changed. Moving the same files to another directory and pointing `prompts_dir` there: unchanged.
  - **Baseline:**
    - Changing `roles.reviewer.model`, or the `base_url` of a backend the baseline's model does not use: unchanged.
    - Changing the baseline model's `temperature` or its backend's `base_url`: changed.
    - Editing `literal/v1.j2` in `prompts_dir`: unchanged.
    - Editing `contextual/v1.j2`: changed.
- [ ] **Step 2:** Run `cd engine && uv run pytest tests/bench/test_fingerprint.py -q`. Expected: it fails, because the module does not exist yet.
- [ ] **Step 3:** Implement `fingerprint.py`. Then switch `_fingerprint` in `run.py` to `output=output_hash(cfg, spec)`, and change `Fingerprint` in `metrics.py` (`config` becomes `output`, with the tolerant loader described above).
- [ ] **Step 4:** Add the integration tests to `engine/tests/bench/test_run.py`, using the fake backends that count calls:
  - Run once, change `scoring.thresholds` and `backends[x].timeout_s` in the system's config file, then run again. The log says "skipping N finished", and the fake backend gets no new calls.
  - Change a prompt template's text in `prompts_dir`, then run again. The log says "re-running … settings changed".
  - A result file written with the old fingerprint shape (`"fingerprint": {"dataset": …, "config": "abc"}`) loads, and the pair is redone.
- [ ] **Step 5:** Run `cd engine && uv run pytest tests/bench -q && cd .. && make lint`. Expected: all pass, and lint is clean.
- [ ] **Step 6:** Commit: `git commit -m "bench: fingerprint only what shapes the translated text (CGL-8)"`

### Task 2: MetricX identity, and `bench ab` reads what the summary read (CGL-10)

**Files:**
- Modify: `engine/coglossa/bench/metrics.py` (`SystemPairResult` and `score_units`)
- Modify: `engine/coglossa/bench/run.py` (`run_bench`, `_run_one` and `_rescore`)
- Modify: `engine/coglossa/bench/absheet.py`
- Modify: `docs/operator-guide.md`, section 12, the paragraph in "Run it" about the scorer being down
- Test: `engine/tests/bench/test_run.py` and `engine/tests/bench/test_absheet.py`

**Interfaces:**
- Consumes: `Scorer.health() -> ScorerHealth`, whose `qe_model: str | None` is the QE model id, or None when the scorer has no QE model. It sets `scorer.supports_qe`, and raises `ScorerUnavailableError` when the scorer cannot be reached.
- Produces: a new field `SystemPairResult.metricx_by: str | None = None`. It holds the identity of the MetricX scoring that produced the result's numbers, in the form `f"{qe_model}@{qe_max_chars}"`, and None when the result was never scored. `metricx_pending: bool` stays, with a new meaning: True whenever the result has outputs but `metricx_by` is None, either because the scorer failed, had no QE model, or was not configured. `summary.md` and `summary.json` keep reading `metricx_pending` as before.

**Behavior:**
1. **At the start of `run_bench`, with a scorer:** call `await scorer.health()`.
   - On `ScorerUnavailableError`, log one line, `scorer unavailable (<message>): MetricX stays pending; the next run with a scorer scores it`, and carry on as if there were no scorer.
   - If `health.qe_model` is None, log `scorer has no QE model: MetricX stays pending`, and carry on the same way.
   - Otherwise, each system's expected identity is `f"{health.qe_model}@{configs[name].scoring.qe_max_chars}"`.
2. **Resume.** A complete result whose output fingerprint matches is:
   - finished, when there is no usable scorer, or when its `metricx_by` equals the expected identity;
   - otherwise re-scored with `_rescore`, which translates nothing. The log line is `re-scoring <system> <pair>: MetricX pending` when `metricx_by` is None, and `re-scoring <system> <pair>: scorer changed` otherwise.
3. **`_run_one` and `_rescore` set the fields:** `metricx_by` is the identity when scoring succeeded; otherwise it is None, with `metricx_pending=True`.
   - Also count it as succeeded when there were no outputs to score: no unit fitted, or every output was empty.
   - When the scorer raised partway, `metricx_by` is None and pending is True, as today.
   - A failed pair, with no units, has `metricx_by=None` and `metricx_pending=False`.
4. **`bench ab`** (`absheet.py`) picks its system pairs from `out/summary.json`: the rows with `status == "complete"` for the two systems asked for. It maps each pair label to its file slug with the same `Pair` parsing that `run.py` uses.
   - If `summary.json` is missing or unreadable, it stops with one line: `no summary in <out> (run coglossa bench run first)`.
   - A result file whose pair or system is not in the summary is never read.
   - `--pair` still filters within the summary's pairs.
5. **Operator guide, section 12, "Run it":** replace the sentence beginning "If the scorer was down" with: "If the scorer was down, or has no MetricX model loaded, the pairs are saved without MetricX, and the next run with a working scorer scores them from the saved translations without translating again. The same happens when you switch the scorer to another MetricX model."

- [ ] **Step 1: Write the failing tests** in `test_run.py`, using the fake scorers in `conftest.py`. Add a QE-less fake whose `health()` returns `qe_model=None`, and one whose `health()` raises, if they do not exist yet.
  - First run with the QE-less scorer, second with a QE scorer: the second re-scores ("MetricX pending") and makes no backend calls, and the summary has MetricX values.
  - First run with the QE scorer `model-a`, second with `model-b`: re-scores, with the log "scorer changed".
  - First and second runs with the same QE scorer: skipped as finished.
  - First run with the QE scorer, then `qe_max_chars` changed in the system config: re-scores without translating. This needs Task 1's fingerprint to exclude `scoring`, which it does.
  - `health()` raising: the run completes, the results are pending, and the log has the one line.
  - A result file from before this change (no `metricx_by`, `metricx_pending: false`, with MetricX values) is re-scored once when a scorer is present.
- [ ] **Step 2:** Run the tests. Expected: they fail.
- [ ] **Step 3:** Implement behaviors 1–3.
- [ ] **Step 4: Write the failing `absheet` tests:**
  - A `runs/<system>/<slug>.json` for a pair that is not in `summary.json` is left out of the sheet.
  - A missing `summary.json` gives the one-line error and exit code 1 through the CLI.
  - A failed row in the summary is left out.
- [ ] **Step 5:** Implement behavior 4, update the operator guide (behavior 5), run `cd engine && uv run pytest tests/bench -q && cd .. && make lint`, and commit: `git commit -m "bench: re-score by MetricX identity; ab reads the summary's results (CGL-10)"`

### Task 3: Say which flags the false-positive rate counts, and print recall counts (CGL-9)

**Files:**
- Modify: `engine/coglossa/bench/calibrate.py`
- Modify: `engine/coglossa/bench/flagtest.py` (`flag-summary.md` wording only)
- Modify: `README.md` (around lines 288–295), and `docs/operator-guide.md`, section 12 ("Test the flags, then calibrate", the `calibration.yaml` bullet)
- Test: `engine/tests/bench/test_calibrate.py` and `engine/tests/bench/test_flagtest.py`

**Interfaces:**
- Consumes: `Fit` in `calibrate.py` (`threshold`, `false_positive_rate`, `recall`, `kinds`) and `_fit(positives, negatives, max_fpr)`.
- Produces:
  - `Fit.counts: dict[str, tuple[int, int]]` maps each kind to (caught, total) at the chosen threshold.
  - `Fit.kinds` keeps the recall per kind.
  - `Fit.recall` becomes the mean over the kinds with at least `MIN_KIND_SAMPLES = 10` injected spans.
  - `Fit.recall` is `None` when no kind has that many. `_fit` still returns a Fit as long as a threshold meets the false-positive target.
  - The `recall == 0` refusal ("catches none of the errors") now applies when every kind's caught count is 0.

**Behavior:**
1. **Per-kind output.** Every per-kind recall printed, in the console summary and in the `calibration.yaml` comments, shows its counts, e.g. `omission 60% (18/30)`. A kind below `MIN_KIND_SAMPLES` shows `(4/6, too few to average)`, and is left out of the average.
2. **The average** is printed as `recall averaged over K kinds: 55%`, or as `recall: too few examples of each kind to average` when it is None.
3. **The `HEADER` in `calibrate.py`.** Replace "flags at most {target} of clean spans (the clean false-positive rate, which carries over to real use)" with: "flags at most {target} of clean spans with its own flag (low_meaning for meaning, style_drift for style) or a critical error found by the judge: the clean false-positive rate, which carries over to real use. Other flags (terminology, omission) also fire on some clean spans; flag-summary.md gives the rate for all flags together." Replace "averaged over the kinds of error so that each kind weighs the same" with "averaged over the kinds of error with at least {min_kind} examples, so that each kind weighs the same".
4. **`flag-summary.md`.** The line `Clean false-positive rate: …` becomes `Clean false-positive rate (any flag): …`. In the introduction text near `flagtest.py` line 71, add one sentence: "This rate counts every flag; `bench calibrate` fits each threshold to the rate of its own flag, so this one is higher."
5. **`README.md` and the operator guide.** Wherever they say a threshold "flags at most 10% of (the) clean sentences", say instead that it flags at most 10% of clean sentences "with its own flag (`low_meaning` or `style_drift`, plus critical errors)", and point to `flag-summary.md` for the rate of all flags together. In the operator guide, also mention the counts and the 10-example floor in the sentence that describes the comments.

- [ ] **Step 1: Write the failing tests** in `test_calibrate.py`:
  - A tier where one kind has 3 spans (all caught) and two kinds have 20 each (10 caught): the average is 50%, not 66.7%, and the output contains `(3/3, too few to average)` and `(10/20)`.
  - All kinds below 10: the threshold is still fitted, and the text says "too few examples of each kind to average".
  - The header text of `calibration.yaml` contains "with its own flag".

  In `test_flagtest.py`: `flag-summary.md` contains "Clean false-positive rate (any flag)".
- [ ] **Step 2:** Run the tests. Expected: they fail.
- [ ] **Step 3:** Implement behaviors 1–4 and update the docs (behavior 5).
- [ ] **Step 4:** Run `cd engine && uv run pytest tests/bench -q && cd .. && make lint && make test-engine`. Expected: all pass, with coverage at or above 95%.
- [ ] **Step 5:** Commit: `git commit -m "bench: name the flags the false-positive rate counts; recall counts and a floor (CGL-9)"`

### Task 4: Close the follow-ups

**Files:**
- Modify: `docs/m6-followups.md`

- [ ] **Step 1:** Rename the heading "Fix before the calibration run (Task 12)" to "Fixed before the calibration run". Rewrite each of its five items as one line saying how it was fixed, with its Jira key (CGL-8, CGL-9 or CGL-10). In the "Left out on purpose, or open" section, update the bullet "Pending MetricX without a scorer stays pending" to the new rule: a result is pending until a QE-capable scorer scores it, and a change of MetricX model re-scores it.
- [ ] **Step 2:** Commit: `git commit -m "docs: m6 follow-ups fixed before the calibration run"`

### Task 5: Per-model `extra_body`, to turn off thinking and set server options

Added on 2026-10-13, before the calibration run. Qwen3.6, served through OpenRouter, reasons by default. It spent 339 hidden completion tokens translating one sentence, and 8 with reasoning off. vLLM serving Qwen3 does the same unless the request carries `chat_template_kwargs: {"enable_thinking": false}`. Neither server can be told this today, so the calibration run, and any customer running a thinking model, pays several times the tokens and time, and risks truncated output.

**Files:**
- Modify: `engine/coglossa/config.py` (`ModelConfig`)
- Modify: `engine/coglossa/llm/types.py` (`ChatRequest`)
- Modify: `engine/coglossa/llm/agent.py` (where `ChatRequest` is built), and every other place that builds a `ChatRequest` (search for `ChatRequest(`)
- Modify: `engine/coglossa/llm/openai_compat.py` (`_payload`)
- Modify: `deploy/` config template (the commented model example in `coglossa.example.yaml`, wherever it lives) and `docs/operator-guide.md` (section 5, Configuration: a short subsection "Model server options")
- Test: `engine/tests/` (the existing tests for config, payload and agent)

**Interfaces:**
- Produces:
  - `ModelConfig.extra_body: dict[str, Any] = Field(default_factory=dict)`, with JSON-compatible values.
  - `ChatRequest.extra_body: dict[str, Any] = Field(default_factory=dict)`.
  - `_payload(request)` merges `extra_body` into the payload first, then sets `model`, `messages`, `temperature`, `max_tokens` and `response_format` over it, so those keys can never be overridden.
- Config validation: an `extra_body` key named `model`, `messages`, `temperature`, `max_tokens`, `response_format` or `stream` is a config error. The message is one line: `models.<key>.extra_body: '<name>' is set by Coglossa; use the model's own setting` (or `cannot be set` for `messages`, `stream` and `response_format`). Use the same validator style as the other config checks.
- Bench's output fingerprint already covers all of `models`, so a change of `extra_body` re-runs the systems that use that model. Add one assertion for this to `engine/tests/bench/test_fingerprint.py`.

- [ ] **Step 1: Write the failing tests:**
  - `_payload` with `extra_body={"reasoning": {"enabled": False}, "temperature": 9}` contains `reasoning` and keeps the request's own temperature.
  - An agent call with a model whose `extra_body` is `{"chat_template_kwargs": {"enable_thinking": False}}` sends it. Use the fake backend or the fake OpenAI server, whichever the existing agent tests use.
  - `extra_body: {stream: true}` in a config gives the one-line error.
  - The fingerprint changes when `extra_body` changes.
- [ ] **Step 2:** Run the tests. Expected: they fail.
- [ ] **Step 3:** Implement it. Then add the operator-guide subsection: one paragraph, plus a YAML example showing both forms (vLLM `chat_template_kwargs: {enable_thinking: false}`, and OpenRouter-style `reasoning: {enabled: false}`). Say that the server must understand the options. Add a commented `extra_body` line to the model example in the config template.
- [ ] **Step 4:** Run `make lint && make test-engine` (and `make deploy-check` if the template changed). Expected: they pass.
- [ ] **Step 5:** Commit: `git commit -m "config: per-model extra_body for server options such as turning off thinking"`
