# M3 follow-ups

These came out of the M3 per-task reviews and the final review. They are open, not fixed,
unless listed under "Resolved".

## Resolved before the M3 merge (final fix wave)
- A non-database exception while saving a stage, chunk or sweep (for example Postgres rejecting
  a NUL character) left the run stuck in `ingesting`/`translating` with no error. Saves now fall
  back to the failure policy in a fresh transaction, and NUL characters are stripped during
  ingest and by the store's text/jsonb column types.
- The config hash depended on how `--config` was spelled (relative paths were not resolved), so
  queued runs got false config notes.
- The project guide was shared only when runs were checkpointed (engine guides are drafts). A run
  now builds on the latest approved version, else on the latest finished run's guide.
- No connect timeout: a firewalled database host hung the CLI, doctor and worker startup for the
  OS TCP timeout. Every connection now has a 10 s `connect_timeout`.
- `retry` of a `complete_with_errors` run whose errors all come from earlier stages said
  "re-running the sweep" and could not clear them; it now refuses with an explanation and
  `status` no longer suggests it.
- A non-numeric port in the database URL gave "internal error: ValueError"; the cancel message
  and the README's `complete_with_errors` description were inaccurate.

## Resolved in M4a
- Stored files are `0640` and fsynced.
- A failed submit removes its stored file.
- `open_app` is opened once per API process, with any other opening nested inside it (documented
  in `coglossa_api.app.procrastinate_queue`).
- The library-boundary test discovers modules.
- `LocalStorage.put` closes its file descriptor when `fchmod`/`fdopen` fails.

## Should fix soon (M4/M5)
- **Stalled job copies accumulate** (T11): copies that `requeue_stalled` closes as failed stay
  in `procrastinate_jobs`; nothing cleans them up.
- **No `server_default` on status/state columns** (T6): defaults are Python-side only, so M4/M5
  raw SQL inserts must set them.
- **Cancelled runs cannot produce a report**: `build_report` needs `complete` or
  `complete_with_errors`, so a cancel that arrives during the sweep loses a fully translated
  run's report.
- **`delete-project` deletes rows before files**: if file deletion then fails, confidential
  files stay on disk with no row pointing to them.
- **`_DB_RETRY` covers well under a minute of database outage** (5 attempts): a longer outage
  fails the job and the run stays in its working state until someone runs `coglossa retry`.
- **The config check runs only in `start_run`**: a worker restarted with a different config in
  the middle of a run's chunks is not noted.
- **Scorer health is recorded at worker startup**, not per run: a scorer that goes down later
  is still reported healthy in the run's report.
- **`coglossa run` and the queued path disagree on a sweep crash**: the in-memory CLI aborts the
  run, the worker ends it `complete_with_errors` (ruling F8). Align them.
- **`_job` treats any `NotFoundError` as "run deleted"** (T10): a missing guide version would
  silently strand a run in `translating`.
- **`approve_guide` commits `translating` before queueing `translate_run`** (T10): a crash in
  between needs a manual `retry`.
- **The finish job's sweep save ignores `cancel_requested`** (T10): the sweep is applied, then
  the run is cancelled.
- **`db upgrade` reports every database error as "cannot connect"** (T12): an error in the
  middle of a migration is mislabelled.

## Later / polish
- T1: `_unlocked` stripping entity locks has no direct assertion; `Entity.locked` ("set only by
  humans") is enforced only by `_unlocked` at engine boundaries.
- T2: reviewer overflow fires the translate hook three times (whole chunk and each half); no
  test pins the reconcile-overflow hook sequence.
- T2: `split_chunk_input` drops the original context when the sibling half is shorter than
  `CONTEXT_CHARS`; `half()` has an untyped `segments` parameter; `gather` leaves the sibling half
  running on a non-oversize exception; the truncated-review split test lacks a split count.
- T3: the synthetic sweep handler uses a substring test while the validator uses word
  boundaries (spurious synthetic sweep errors for short terms).
- T3: no test that accepted/dismissed terminology flags and other flags are left alone by the
  sweep; `SpanChange.resolved` claims `terminology_violation` even without such a flag (harmless:
  the update filters open flags); `_fix` keeps an unused `violations` parameter.
- T3: `resolve_proposals` skips glossary terms but not entity names; losing proposals' notes are
  dropped; the chosen-has-notes conflict branch is untested; the unexpected-exception re-raise
  in `run_sweep` is untested.
- T4: the CLI summary test calls the private `_write_report`; no test that pipeline
  `guide_proposals` come from `resolve_proposals`.
- T5: unsafe-key tests cover only `put`; get-on-directory is untested; a NUL byte in a key
  raises `ValueError`, not `StorageError`; validation is lexical only (no symlink check inside
  the root).
- T6: the `pg_url` fixture leaks the throwaway database if `upgrade` raises; `script.py.mako`
  generates annotations ruff rejects; the cascade test covers 6 of 15 tables.
- T7: `get_run(lock=True)` blocking and `add_guide_version`'s project lock have no concurrency
  test; `chunks_with_errors` has no `ORDER BY`; `LLMCallRecord` save and load are asymmetric.
- T8: no store-level test of `delete_chunk_outputs` plus re-save with flags and sweep
  revisions in one transaction; no negative case for the `apply_sweep` open-flag filter;
  candidate rows use `c.chunk_id`, span rows `outcome.chunk_id`.
- T9: the delete test doesn't assert that `llm_call`, document and guide-version rows are gone;
  no test cancelling `awaiting_guide_approval` then approving or retrying; `available_ingestors()`
  runs twice per `ingest_file`; `run_progress` queries the score stage twice.
- T10: translate/reconcile rows stay `running` after a chunk failure (retry deletes them; M4's
  UI may show running rows in a terminal run); missing stage rows hit an `assert`.
- T10: every chunk job builds the inputs of all chunks (O(n²) for long documents); a
  re-delivered `start_run` re-queues running chunk jobs (duplicate model calls, result
  discarded); a re-delivered mid-chunk job doesn't reopen a completed reconcile row; no test of a
  cancel during the sweep.
- T11: `_DB_RETRY` duplicates `execute.DB_ERRORS`; the SIGTERM test can kill pytest if the
  handler isn't installed; two concurrent `requeue_stalled` calls can race on one stalled job;
  `build_services` without a scorer and stop-between-passes are uncovered.
- T12: `report` creates `coglossa-out/<id>` before the database lookup; an `OSError` writing
  `guide --out` or the report reads "internal error"; a bad `--guide` file's error embeds a
  multi-line validation error; function-local imports from `coglossa.cli` in `cli_ops`.
- T13: a schema ahead of the build still suggests `coglossa db upgrade`; the context probe runs
  after a failed plain completion; an empty served-model list reads "one of: "; some branches
  are untested and `--json` isn't schema-validated; the storage check creates directories;
  one raising backend `aclose()` skips the rest.
- T14: the boundary test's `check=True` hides subprocess stderr; the e2e lower bound on
  in-flight requests could flake on a very slow runner.

## Spec deviations in M3
- **Chunks belong to runs**, not documents: chunk sizes depend on the run's context windows.
- **`guide_version.source_run_id` has no foreign key**: runs reference guide versions, so a key
  the other way would be circular.
- **`stage_result.error_count`** stores the number of errors in a stage's output (1 for a failed
  stage), so progress and the final state are cheap queries.
- **`span.proposed_terms`** stores the reviewer's proposals per span, so reports can re-resolve
  them.
- **Flag status `resolved`**: set by the sweep when its revision fixes the problem; spec §5 lists
  only open, accepted and dismissed.
- **The sweep enforces locked guide entries only.** Spec §4.1 also mentions checking names and
  forms of address across chunks; that is deferred.
- **An unexpected sweep exception ends the run `complete_with_errors`**, not `failed` (ruling F8):
  the sweep only refines finished output.
- **`config_hash` excludes the `database`, `storage` and `worker` sections**, so tuning the
  deployment never reads as a config change.
- **Project guide fallback**: without an approved version, a run builds on the guide of the
  project's latest finished run (`complete` or `complete_with_errors`), which is a draft.
  `--use-project-guide` still reuses approved versions only.
