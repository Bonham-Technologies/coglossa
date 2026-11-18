# M4a follow-ups

Known limits of the M4a server side, decided during planning. Items found during execution are
added below them.

## Should fix soon (M4b/M5)
- **Upload size is checked after Starlette spools the body.** `api.max_upload_mb` stops a large
  file from being stored, but the request body has already reached a temporary file. Put a body
  size limit on the reverse proxy in front of the API (M5 operator guide).
- **The progress stream polls.** Each open stream reads the run's progress every
  `api.progress_poll_s`; many open review tabs mean many small queries. LISTEN/NOTIFY can replace
  polling later.
- **A retry of a run with failed chunks undoes every sweep revision of the run.** M3
  `retry_run` calls `undo_sweep` and deletes the sweep stage, which also removes revisions from
  a re-run sweep (even under reviewer edits) and reopens resolved flags. A sweep-only retry no
  longer does this.
- **A finished run that is being reworked cannot be cancelled.** A re-run sweep, a sweep-only
  retry and a retry of failed chunks keep `finished_at`; cancel refuses every non-terminal run
  whose `finished_at` is set, because a cancelled run has no report or exports. The rework
  finishes on its own.
- **`service.submit_bytes` writes and fsyncs the upload on the event loop**, inside the submit
  transaction. Move the write to a thread (or before the transaction) when uploads get large.
- **Review actions take the run row lock.** Heavy concurrent reviewing of one run serializes.
  They work only on `complete` and `complete_with_errors` runs (whose report shows them) and
  are refused while a re-run sweep runs.
- **Exports rebuild the full report on every download.**
- **An image-only page makes a whole PDF `needs_ocr`.** This follows spec §4.1 literally, but it
  refuses ordinary illustrated books (a cover or an illustration page), and the message ("use a
  PDF with a text layer") is wrong for them. Needs a spec ruling, e.g. a threshold, or skipping
  image-only pages when other pages have text.
- **Exports show the engine's scores next to user-edited text** with no "edited" mark. The HTML
  and DOCX exports leave the UI, so a reader may attribute the old score to the new text. Mark
  edited passages, or show a dash for their scores.

## Later / polish
- ~~Concurrent edits of one passage: the later edit wins; both stay in the revision history.~~
  Done in CGL-36: edits and version picks send the text they started from, a stale one is
  refused with 409 `passage_changed` and the current text, and the reviewer keeps theirs or
  takes the other.
- ~~The clean DOCX does not rebuild tables (cells become paragraphs) and flattens heading levels to
  Heading 1.~~ Done in CGL-87 for Word sources: tables (merged and nested cells too), heading
  levels, the page header and footer, and footnotes and endnotes as a marked notes section are
  rebuilt. Other formats record no table structure or levels yet, so their cells still become
  paragraphs and their headings Heading 1; a cited note's mark ends its passage rather than
  sitting where the source cited it.
- PDF: no heading detection; running headers and footers are translated (bare page numbers are
  dropped).
- EPUB: the navigation document is not translated; image alt text is not translated.
- HTML: `alt` and `title` attributes are not translated.
- `storage.display_name` collapses control characters in an uploaded file name to a space.
- **After a re-sweep, the run's summary comes from the project's latest approved guide
  version.** `rerun_sweep` repoints the run at it, so in a multi-document project
  `RunDetail.summary`, the report's `guide.summary` and the HTML export's summary may describe
  another document. Single-document projects are unaffected.
- **`RunSummary.finished_at` is set during a re-sweep or a chunk retry** of a finished run, so
  the M4b UI must read `state`, not "`finished_at` is set", to know a run is done.
- **CSRF (M5 operator guide):** with `trusted_header` behind a cookie-based SSO proxy, the
  bodyless POSTs (`/cancel`, `/retry`, `/sweep`, `/accept`, `/guide/approve`) can be triggered
  cross-site by anyone who knows a run's id. Use `SameSite` cookies on the proxy, or add an
  `Origin` check to the API.

## Spec deviations in M4a
- **A guide version a person saves is `approved`.** Editing the project guide or adding an entry
  from the review view creates an approved version authored by `user`.
- **Re-running the sweep keeps earlier sweep revisions** and points the run at the project's
  latest approved guide.
- **`review_event` payloads carry `"user"`** (spec §5 has no user column; v1 has no user
  management).
- **No Swagger or ReDoc page.** FastAPI is created with `openapi_url`, `docs_url` and
  `redoc_url` set to `None`; the OpenAPI document is served at `/api/openapi.json` from the
  protected router (behind auth). Those pages load CDN assets, unusable air-gapped.
- **The sweep on/off switch is read from the run's config snapshot**, not the live config: the
  worker's `finish_run` follows what the run was started with.
- **Postgres test fixtures live in the pytest plugin `coglossa.testing.pytest_db`**, which is a
  database module for the library-boundary test.

## Resolved in M4b
- **The project guide included drafts of unfinished runs.** It is now `runs.prior_guide`
  everywhere in Python, and the projects list mirrors the same rule in SQL
  (`catalog._projects_query`); checkpoint approval is measured against it (Task 1), using the
  version each draft was built from (final review I4).
- **The SSE client must close the stream on a terminal state.** The web client closes it
  (Task 6).
- **Guide bodies were not cleaned.** They are now cleaned with `clean_guide` (Task 1).
- **The README lacked the `base_version` rule.** It is documented (Task 2).
- **The every-route auth test enumerated the OpenAPI document.** It now walks the real routes
  (Task 2).
