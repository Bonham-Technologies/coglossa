# Coglossa

Multi-agent translation that translates for meaning and shows its work. Several translator
agents render each passage, a reviewer reconciles them, and every span of the result carries
its alternatives, the reviewer's rationale and plain-language flags. It runs entirely on your
own OpenAI-compatible model servers, including inside air-gapped networks.

Status: **M5** (engine + CLI + scoring + queued runs + REST API + web app + offline bundle). See
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

Long documents stop after the translation guide is built so you can review it:

```bash
uv run coglossa run novel.docx --target-lang en              # writes coglossa-out/novel/guide.json
# edit guide.json — set "locked": true on terms and names that must be used as written
# (the command to resume is printed; it also passes the reviewed profile.json via --profile-file)
uv run coglossa run novel.docx --target-lang en --guide coglossa-out/novel/guide.json --approved-guide
```

`"locked": true` entries are kept verbatim whenever a guide is regenerated and are given to every
translator and the reviewer as mandatory renderings. The sweep enforces them and a locked entry
that is still missing is flagged. Edit `profile.json` to correct the detected genre or
register and pass it back with `--profile-file`.

Exit codes: `0` complete, `1` failed, `2` usage error, `3` complete with errors, `130` interrupted (see `errors`
and `engine_error` flags in the report).

Supported input formats: plain text and Markdown (`.txt`, `.md`, `.markdown`), Word (`.docx`),
HTML (`.html`, `.htm`, `.xhtml`), EPUB and PDF with a text layer. A scanned PDF stops with
`needs_ocr` and the pages that have no text.

## Scoring

Every span gets signals; a signal that cannot be computed is `null`, never guessed.

- `qe`: MetricX-24 reference-free quality estimate (scorer service)
- `xsim`: LaBSE cross-lingual similarity between source and translation (scorer service)
- `roundtrip`: similarity of a back-translation (`roundtrip` role) to the source
- `judge`: MQM-style errors found by the `judge` role, ideally a model no translator or the
  reviewer uses
- `agreement`: how closely the reviewer agreed with the translators

`meaning` (0-100) is a weighted mean of the available signals, renormalised over those present;
it is `null` unless at least one model signal exists. `style` (0-100) comes from the judge alone,
so without a `judge` role spans have no style score. Reliability tiers (A, B, C) come from the
language pair, and drop one step when two or more model signals are missing, or unless the judge
model differs from the reviewer's and the translators' and the roundtrip model differs from the
translators'.

A run where no span got a meaning or style score (no scorer and no judge, or neither reachable)
is reported as unscored: spans have reliability `unscored`, the report carries an `unscored`
notice ("Review every passage") and the summary line ends in `(unscored)` instead of `(tier X)`.

Sweep: after all chunks are done, the sweep rewrites passages that miss a locked guide entry,
records each change as a revision with a reason, and marks the fixed flag `resolved`. It is on for
`standard` and `long-form` (`profiles.sweep`). Scores describe the reviewed draft.

Flags (per span, each with a plain-language explanation and the evidence behind it):

- `low_meaning`: meaning below the threshold (defaults A 86, B 84, C 70), or a critical
  accuracy error from the judge
- `style_drift`: style below the threshold (default 100: any style error the judge finds), or a
  critical style error
- `terminology_violation`: a locked glossary term was not rendered as written
- `possible_omission`: length ratio outside 0.5-2.0 together with a judge omission
- `number_mismatch`: the numbers written in digits differ between the source and the
  translation (one changed, dropped or added); the evidence lists the unmatched values on each
  side. It never changes the scores. Digit scripts (١٠٠٠, १०००), grouping and decimal marks
  (1,000.5, 1.000,5, 1 000,5), dates in another order (2026-11-17, 17.11.2026), percent signs and
  ordinal endings (21st, 21.) count as the same number. A scale word right next to a number is
  applied, so 1.5 million, 150万, 15 लाख and 1,500,000 all match; without one, 5 against 50 is
  flagged. Numbers written as words are not read: a number found on one side only is let
  through when it is one commonly spelled out (0 to 20, the tens to 100, 1000), so "three"
  against 3 is not flagged, but neither is "three" against 4, nor a dropped "3 items";
  "twenty-five" against 25 is. The same digits with another scale word (2 million against 2
  billion) are not seen. 12- and 24-hour times (1200 GMT against 12:00) and unit conversions
  (1,000 pounds against 450 kg) are flagged, as a person should check them anyway. The full
  rules are in `engine/coglossa/scoring/numbers.py`
- `agent_disagreement`: the reviewer rated the translators' disagreement `major`
- `cultural_or_wordplay`: cultural reference, idiom or wordplay that may not carry over
- `engine_error`: an engine step failed; the text is an unreviewed draft (or empty)

Document-level notices: `low_reliability_language` (tier C pairs) and `unscored`. Thresholds
are set with `scoring.thresholds` (`meaning: 70` for every tier, or `meaning: {A: 70, C: 75}`).

The default thresholds were calibrated in October 2026 on FLORES-200 with Gemma 4 as judge and
back-translator (`docs/bench-results-2026-10.md`): each flags about one clean passage in ten. The
numbers differ by tier because each tier weighs the signals differently. The tiers and weights
are unchanged defaults; calibrate them for your own models with `coglossa bench`. Override tiers
with `scoring.language_tiers_file`.

Run the real scorer (local model directories only, no downloads). The QE model is the MetricX-24
checkpoint `google/metricx-24-hybrid-xl-v2p6` (`google/metricx-24-hybrid-large-v2p6` is the
smaller choice on CPU); its tokenizer is `google/mt5-xl`; embeddings use
`sentence-transformers/LaBSE`. Download them once (e.g. `huggingface-cli download`) and pass the
directories:

```bash
coglossa-scorer --qe-model DIR --qe-tokenizer MT5_XL_DIR --embed-model LABSE_DIR --device cpu
```

Inputs longer than `scoring.qe_max_chars` (1500) or `scoring.sim_max_chars` (800 per side) are
not sent to the scorer, so those signals stay `null` instead of being scored on a truncated text.

Then set `scorer.url` in the config. To try the pipeline without models:

```bash
uv run coglossa fake-scorer --port 8080 &     # set scorer.url: http://127.0.0.1:8080
```

## Queued runs (worker and database)

`coglossa run` translates one document in the foreground. For long documents use the queue: a
run is stored in Postgres, split into chunk jobs that a worker processes, survives a crash or
restart, can be resumed, and shows reviewable passages as chunks finish.

```bash
export COGLOSSA_DATABASE_URL=postgresql://user:password@host:5432/coglossa
uv run coglossa db upgrade      # create or migrate the schema; run it before worker or submit
uv run coglossa worker          # long-running; --burst drains the queue and exits
```

The commands refuse to run against a database that has not been migrated. The password is
never printed.

```bash
coglossa submit book.docx --target-lang en --project novel   # prints the run id; --checkpoint / --no-checkpoint, --use-project-guide
coglossa status RUN_ID                  # state and per-stage chunk counts
coglossa guide RUN_ID --out guide.json  # checkpointed runs: review the guide ...
coglossa approve-guide RUN_ID --guide guide.json   # ... then approve it (omit --guide to keep it as generated)
coglossa import-glossary terms.csv --project novel --lock-all   # add a CSV, TSV or TBX glossary to the project guide (--dry-run to preview)
coglossa cancel RUN_ID                  # finished passages are kept
coglossa retry RUN_ID                   # retries failed chunks and the sweep; also resumes a run whose job was lost
coglossa report RUN_ID --out out/       # writes report.json for a finished run
coglossa delete-project novel --yes     # removes the project's documents, runs and files
```

Documents in one project share a guide in each language. The project guide is its newest
version that is approved or is the draft of a run that has built it and has not failed, been cancelled or stopped for
approval, so a run still translating counts. A new run builds its guide on the project guide,
and when the draft is saved it is merged onto the project guide as it is then: every term and
name already there keeps its entry, and the draft adds the ones that are new (it keeps its own
summary and style guide). Files of one project therefore agree on names and terms even when they
translate at the same time. `--use-project-guide` reuses the project guide as-is
instead of building a new one, so it shares names with the files translating alongside it. Only people lock entries: set `"locked": true` in the guide you pass to `approve-guide`.

An existing glossary does not have to be retyped or written as JSON. `coglossa import-glossary`,
**Terms ▸ Import terms…** in the web app's guide panel, and the API's
`POST /projects/{id}/guide/import/preview` and `POST /projects/{id}/guide/import` read a CSV or
TSV spreadsheet (the term in the first column, its translation in the second; a header row and
further columns are fine; UTF-8 with or without a byte-order mark) or a TBX termbase (v2
`termEntry` or v3 `conceptEntry`) into one language's project guide. The preview counts the terms
that are new, already there, or there with another translation, and the terms the file repeats;
nothing is written until the import, which adds a guide version by the user, all or nothing. A
term the guide has with another translation keeps it unless you choose to replace it
(`--overwrite-conflicts`, `overwrite_conflicts`), locked ones too, and **Lock all imported terms**
(`--lock-all`, `lock_all`) locks every imported term, so the sweep enforces it like one locked by
hand. A file that cannot be read is refused with the line at fault. TBX languages match
case-insensitively, with `-` and `_` alike: a guide in `pt-BR` takes `pt-BR`, else plain `pt`; a
guide in `pt` takes `pt`, else a regional `pt-…`; `pt-PT` never serves `pt-BR`. The terms are
read from the project's source language, else the file's one other language (or its main
`xml:lang`); `--source-lang` or `source_lang` chooses it. Glossary files are capped at 20 MB and
20,000 terms (and at `api.max_upload_mb` through the API), and the XML is read without DTDs,
entities or network access.

A project can be translated into several languages. Its files are stored once and each language
has its own guide: renderings, locks and the style guide never cross from one language to
another. In the web app, **⋯ ▸ Translate into another language…** starts every file of a set in a
new language, tabs switch between the languages, and files added later are translated into all of
them. Through the API: `POST /api/projects/{id}/languages` with `{"target_lang": …}` (409
`language_exists` for a language the set has), `all_languages=true` on an upload, and
`target_lang` on the project-guide endpoints (the set's first language when left out).

Behind a sign-in proxy, sets belong to teams. People see the sets of their teams and the
personal sets they made; admins see every set and the operator screens (Settings ▸ Status and
Technical details). Teams and admins come from the proxy's group header and from a people file
in the config folder (the operator guide, §6). An install without sign-in works as before:
everyone sees everything.

Run states:

- `created`, `ingesting`, `profiling`, `building_guide`: preparing the document
- `awaiting_guide_approval`: a checkpointed run waits for `approve-guide`
- `translating`: chunk jobs are running (up to `worker.concurrency` at once)
- `sweeping`: the sweep is fixing missed locked terms
- `complete`, `complete_with_errors`: finished; the latter has errors listed in the report's
  `errors`: chunks marked `engine_error`, judge or scorer errors, guide or sweep errors, or a
  config note (the configuration changed between `submit` and execution)
- `failed`: the run could not continue; fix the cause and `retry`
- `cancelled`: stopped by `cancel`

A worker never sends a backend more simultaneous requests than its `max_concurrency`, however
many chunk jobs it has in flight.

`coglossa doctor` checks an installation end to end: the config, prompts, language tiers, each
backend and model (reachable, a test completion, structured output, context window), the scorer,
the database (reachable and migrated) and the storage path. Each problem comes with a fix.
`--no-probe` skips the context-window probe; `--json` prints the result as JSON. The exit code is
`0` when nothing failed and `1` otherwise.

Config sections:

- `database.url_env`: the environment variable holding the URL (default `COGLOSSA_DATABASE_URL`)
- `storage`: `path` of the local file store (default `/data/files`)
- `worker`: `concurrency` (chunk jobs at once, default 8), `stalled_after_s` (re-queue the jobs
  of a worker that has been silent this long; default 60, minimum 30) and `scorer_wait_s` (at
  startup, wait this long for a configured scorer to become healthy, then start without it;
  default 900, 0 does not wait); `burst_scorer_wait_s` is the same for `--burst` (default 0)
- `profiles.sweep`: whether the sweep runs for each pipeline profile

Good to know:

- Changing `database`, `storage` or `worker` does not change a run's config hash. Any other config
  change between `submit` and execution marks the run `complete_with_errors` with a config note.
- The worker refuses to start on an invalid config.
- `retry` re-runs chunks with errors and the sweep. Errors from earlier stages (a config note,
  guide errors) need a new run: `submit` the document again. `status` only suggests `retry`
  when it can help.
- `cancel` is refused while a finished run is reworked (a retry of its failed chunks or a
  re-run sweep): cancelling would lose the finished translation and its review. The rework
  finishes on its own.

## REST API

The web app (M4b) and scripts use the REST API. It needs the same database and storage as the
worker, and never calls a model itself.

```bash
coglossa-api --config coglossa.yaml --host 0.0.0.0 --port 8080   # from api/: uv run coglossa-api ...
```

It refuses to start, with one line, on an invalid config or an unmigrated database. The
OpenAPI document is in `schema/openapi.json` and is served at `/api/openapi.json` (behind
authentication). There is no Swagger or ReDoc page: they load their assets from a CDN, which an
air-gapped network cannot reach.

| Area | Routes (under `/api`) |
|---|---|
| Projects | `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}` (`PATCH` renames; `DELETE` takes `?confirm=<name>`), `GET /projects/{id}/documents` |
| Runs | `POST /projects/{id}/runs` (upload), `POST /documents/{id}/runs`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/progress`, `GET /runs/{id}/events` (SSE), `POST /runs/{id}/cancel`, `/retry`, `/sweep` |
| Guide | `GET /runs/{id}/guide`, `POST /runs/{id}/guide/approve`, `GET/PUT /projects/{id}/guide`, `GET /projects/{id}/guide/versions`, `POST /projects/{id}/guide/import/preview` and `/import` (a CSV, TSV or TBX glossary, multipart) |
| Review | `GET /runs/{id}/spans` (filters), `GET /runs/{id}/spans/{span}`, `POST …/edit`, `…/use-candidate`, `…/accept`, `…/guide`, `POST /runs/{id}/flags/{flag}` |
| Exports | `GET /runs/{id}/report`, `GET /runs/{id}/exports/{html,docx,bilingual-docx,json,tmx}` (`tmx?reviewed_only=true`: only passages a person accepted or edited) |
| System | `GET /health`, `GET /me`, `GET /system/doctor`, `GET /system/config`, `GET /system/tiers` |

The API also serves the web app when `api.web_dir` points at the built files (`web/dist`); every
path outside `/api` returns the app.

A new run of an uploaded document (`POST /documents/{id}/runs`) takes the project's defaults
for the fields it leaves out, as an upload does. A checkpoint draft records the project guide
version it was built from. Approving it is refused (409) when the project guide is no longer that
version (a save or another approval landed while the guide was being built), or, with a
`base_version` of the project guide, no longer that version: reload the guide and approve the
current one. Approve with the edited guide and the `base_version` it started from (the run's
draft or the project guide); `base_version` without a `guide`, or one that is neither of those
two versions, is refused (422). Review actions work on
`complete` and `complete_with_errors` runs, and are refused (409) while a re-run sweep runs.
A deadlock or lock timeout in the database answers 409 "try again".

Every review action is recorded in the feedback log with the signed-in user. With
`auth: { mode: trusted_header }`, a reverse proxy authenticates people and passes the user name
in `X-Remote-User` (or the configured header); requests without it get 401.

## Web app

The web app (`web/`, React + Vite) is how reviewers use Coglossa:
- **New translation:** pick a language, drop in one or more files and start. A translation is a set of files.
- **A translation's page:** one tab per file, with live steps while it works, then a plain summary of how it went and the original beside the translation.
- **Review:** open any passage to see why it was flagged, edit it, choose a better version, mark it checked, or fix a name or term everywhere.
- **Translation guide:** the names, terms and style a translation follows; edit it, and update the translations to match.
- **Download:** the translation as a web page, Word file or side-by-side Word file, or as a translation memory (TMX) for other translation tools, of every passage or only the ones a person checked.
- **Settings:** installation checks and, under Technical details, the scores and settings behind the results.

It comes in a light and a dark theme (it follows the system setting, and can be switched by hand).

The API serves it when `api.web_dir` points at the built files.

    cd web && pnpm install && pnpm build      # web/dist
    # engine/coglossa.yaml:  api: { web_dir: ../web/dist }   (relative to the config file)

It works offline: a Noto font for every living-language script @fontsource publishes (plus a few liturgical and revival scripts and the symbol fonts) is bundled and nothing is loaded from the internet. Every passage is shown in its own direction (`dir="auto"`), so right-to-left text displays correctly.

**Development:**
- `make dev-stack` runs a complete local Coglossa API on port 8080: a throwaway database, fake models, a worker and the API. It serves no web app there (it would need `--web-dir`).
- `cd web && pnpm dev` serves the app with live reloading and forwards `/api` to that stack: open the address `pnpm dev` prints.
- `make test-web` runs the component tests (Vitest, 95% coverage gate).
- `make e2e` runs the Playwright journeys against a fresh stack.
- `make schema` regenerates `web/src/api/schema.d.ts` from `schema/openapi.json`; CI's web job regenerates it (`pnpm gen:api`) and fails when it differs.

## Benchmarking (`coglossa bench`)

`coglossa bench` measures translation quality on FLORES-200 (spec §9.2). It compares any number
of Coglossa configurations and single-model baselines, tests how often the flags catch known
errors, and suggests thresholds and language tiers from the results. It reads local files only;
only `fetch-flores` downloads.

```bash
uv run coglossa bench fetch-flores --out flores              # once, with internet (CC BY-SA 4.0)
uv run coglossa bench run bench.yaml --out bench-out           # MetricX, chrF, tokens, time
uv run coglossa bench flags bench.yaml --scoring-config coglossa.yaml --out bench-out
uv run coglossa bench calibrate --out bench-out --scoring-config coglossa.yaml
uv run coglossa bench ab --out bench-out --a coglossa --b baseline   # a blind A/B sheet
```

A bench config names the dataset and the systems (paths are relative to the bench file):

```yaml
dataset:
  kind: flores200                 # or parallel: source_file and reference_file, line by line
  path: flores                    # where fetch-flores put it
  split: devtest                  # or dev
  pairs: ["en>es", "en>zh-Hans", "en>yo"]   # BCP-47 tags
  sentences: 100                  # per pair, sampled with seed
  seed: 1
systems:
  - { name: coglossa, kind: pipeline, config: coglossa.yaml }
  - { name: baseline, kind: baseline, config: coglossa.yaml, model: m1 }   # one model, one pass
scorer_url: http://127.0.0.1:8080 # MetricX-24 reference mode; without it, chrF only
injector: { config: coglossa.yaml, model: m1 }   # optional: negation and register errors for flags
tiers: { a_max: 5.0, b_max: 10.0 }                # MetricX cut-offs for the suggested tiers
```

Everything lands under `--out`:
- `run` writes `runs/<system>/<pair>.json`, `summary.json`, `summary.md` and `bench-meta.json`. A
  system-pair finished with the same settings is skipped when the command runs again (`--force`
  redoes it), so an interrupted run resumes. These settings translate it again: the sentences,
  seed, split or a pair's data; a model, its backend's URL, the roles, chunking or profiles; or
  the text of a prompt template. A baseline only cares about its own model and anything that
  changes its chunk size. Thresholds, tiers, timeouts, concurrency, retry, audit and the scorer
  do not translate again. A pair whose MetricX failed for want of the scorer is scored again from
  its stored outputs. So is one scored with no QE model, with another MetricX model, or with a
  changed `qe_max_chars`. A pair's MetricX in `summary.md` is over the units every system scored.
- `flags` writes `flags/<pair>.jsonl`, `flag-summary.json` and `flag-summary.md`: recall per error
  kind and tier, the clean false-positive rate for all flags together, precision at the bench's mix, and sample
  injections to check by eye. `--pairs N` scores N pairs at the same time. Each pair's
  `flags/<pair>.meta.json` records what made it, so a later run keeps the pairs whose settings
  (not timeouts, concurrency or retries) are unchanged; `--force` scores them all again.
- `calibrate` writes `calibration.yaml` (a `scoring:` section to paste into the config) and
  `tiers.calibrated.yaml` (a `tiers.yaml`). Each threshold is the highest that flags at most 10%
  of clean sentences with its own flag (`low_meaning` or `style_drift`, plus critical errors)
  (`--max-false-positive-rate`). `flag-summary.md` gives the rate of all flags together.
- `ab` writes `ab-sheet.csv` for the judges and `ab-key.csv`, which says which system wrote which
  translation.

With FLORES-200 data, `run`, `flags` and `ab` also write FLORES's `LICENSE.txt` and
`ATTRIBUTION.txt` (CC BY-SA 4.0) under `--out`; after `flags`, the attribution says which
references carry injected errors. Share them with any output you hand on.

File names write a pair `en-es`; the summaries write `en>es`. The offline bundle ships FLORES-200
and runs bench in the worker container (operator guide, "Benchmark your models").

## Installing (offline bundle)

Coglossa ships as one offline bundle, `coglossa-<version>-linux-amd64.tar`, with its `.sha256`.
It holds the app, scorer and Postgres images, the scorer weights, FLORES-200 for `coglossa bench`,
the compose files, the example configs, the operator scripts (`install.sh`, `backup.sh`, `restore.sh`), the operator
guide, SBOMs and a manifest. Nothing in it reaches the internet, so it installs on an
air-gapped host that has Docker with the Compose plugin 2.24 or newer.

```bash
./install.sh                                # checks the bundle, loads the images, creates config/coglossa.yaml, config/tiers.yaml and .env
# edit config/coglossa.yaml                 # your model servers, models and roles
docker compose up -d --wait
docker compose exec api coglossa doctor
```

`docs/operator-guide.md` (also `operator-guide.md` in the bundle) covers the requirements,
configuration, the reverse proxy, backups, restore, upgrades and troubleshooting.

Building the bundle needs Docker and internet access:

```bash
make weights        # once: fetch the scorer weights into ~/coglossa-weights (about 25 GB)
make flores         # once: fetch FLORES-200 into ~/coglossa-flores (FLORES_DIR=...)
make bundle         # dist/coglossa-<version>-linux-amd64.tar; SCORER=cpu for the CPU-only scorer;
                    # ships FLORES-200 when FLORES_DIR exists; needs about 70 GB free in dist/
                    # (the weights are copied into a staging directory, then into the tarball)
make demo           # the stack from the images with demo models, on http://127.0.0.1:8080
make demo-down      # stop the demo and delete its data
make airgap-test    # build a bundle without weights and install, run, bench, back up, upgrade
                    # and restore it on a Docker network with no route out
make deploy-check   # shellcheck and the tests of the operator scripts and compose files
```

## Development

```bash
make test     # engine, scorer, api and web suites; 95% branch-coverage gate each; no GPU or internet
              # needed; needs Docker: it starts Postgres on 127.0.0.1:55432 via `make db-up`
make db-down  # stop that Postgres
make lint     # ruff check and format check for the engine, scorer and api packages, and
              # `pnpm lint` for the web app (eslint, prettier check and tsc)
make schema   # regenerate schema/*.schema.json, schema/openapi.json and the web app's
              # generated API types (web/src/api/schema.d.ts) after changing the
              # coglossa.schema models or the API
make test-web # the web app's Vitest suite; 95% coverage gate (lines, branches, functions, statements)
make e2e        # build the web app and run the Playwright journeys against a fresh local stack
make dev-stack  # a complete local Coglossa API on port 8080: throwaway database, fake models, worker;
                # run `pnpm dev` in web/ for the app
make images     # build the app and scorer images (coglossa/app:dev, coglossa/scorer:dev, CPU torch;
                # make image-scorer-cuda builds coglossa/scorer:dev-cuda)
make demo       # run those images with demo models on http://127.0.0.1:8080 (`make demo-down` stops it)
make deploy-check # shellcheck and the tests of install.sh, restore.sh and the compose files
```

The web app (`web/`, React + Vite, pnpm) needs Node 22 or newer and pnpm 10 (`corepack enable`).
Run `cd web && pnpm install --frozen-lockfile` once before `make test`, `make lint` or
`make schema`: they now run the web project too. CI does not run `make schema`; the drift checks
are the engine and api tests (the JSON schemas and `openapi.json`) and the web job's
`pnpm gen:api` diff. A job that does run it must set up Node 22 and pnpm and run that install
first.

The scorer installs CPU-only torch on its first `uv sync` (several hundred MB). Real-model checks
are opt-in and excluded from CI: set `COGLOSSA_REAL_MODELS=1` plus `COGLOSSA_METRICX_DIR`,
`COGLOSSA_MT5_TOKENIZER_DIR` and `COGLOSSA_LABSE_DIR` (local directories) as described in
`scorer/tests/test_real_models.py`. The checks passed on 2026-10-10 against
`google/metricx-24-hybrid-xl-v2p6`, the `google/mt5-xl` tokenizer and `sentence-transformers/LaBSE`.
MetricX publishes only `pytorch_model-*.bin` shards; transformers refuses to load `.bin` files
with torch older than 2.6 (CVE-2025-32434), which includes the Intel-Mac pin. On such a machine,
convert the checkpoint to safetensors first (load each shard with `torch.load(...,
weights_only=True)` and save it with `safetensors.torch.save_file`). On CPU, MetricX-24 XL scores
about one item per second, so use a GPU for book-length runs.

## License

Coglossa is licensed under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE).
Contributions are welcome under the same terms: see [CONTRIBUTING.md](CONTRIBUTING.md), which
asks for a DCO sign-off on each commit, and [SECURITY.md](SECURITY.md) for reporting a
vulnerability privately.
