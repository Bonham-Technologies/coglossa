# M4b follow-ups

## Should fix soon (carried to M6)
Triaged on 2026-10-30 (CGL-13). Each open item has its own ticket.
- **Error alerts can outlive the problem** (CGL-45). In the review panel, each action keeps its
  own error alert, so an alert from a refused action stays after another action succeeds, until
  that action is tried again or the panel is closed.

## Fixed after the CGL-13 triage
- **Candidates are joined differently from the server** (CGL-44): fixed. The panel now joins a
  translator's pieces as `coglossa.review.use_candidate` does (reading order, cleaned and trimmed,
  single spaces) and offers Use only when the pieces cover the passage exactly and leave some
  text; a version split differently is shown with Use turned off and a note
  (`web/src/review/byTranslator.ts`). Both sides are checked against the same cases,
  `engine/tests/fixtures/translator_choices.json`.

## Resolved or dropped in the CGL-13 triage
- **A failed "Reload the current guide" leaves a bare error:** resolved by the redesign. The
  guide panel's form keeps its fields and shows the reload's error beside them
  (`web/src/guide/GuidePanel.tsx`, `reloadError`).
- **A refetched project guide replaces unsaved edits:** resolved by the redesign. The guide form
  is keyed by a reload counter, not by the guide's version, so a refetch no longer remounts it.
- **Clicking another row ends the pin:** dropped. The redesigned review view shows the whole
  document and does not filter rows, so there is no pin to lose.
- **A reused project guide shows its first document's summary:** dropped. The redesigned set
  view shows no document summary; the guide panel shows the guide's own summary, which is right
  for a reused guide.
- **Drafts from before migration 0003 have no built-from version:** dropped. Only drafts made
  before migration 0003 are affected, and "Start from the current project guide" approves one.

## Resolved in M5
- **Font coverage:** a Noto font for every living-language script @fontsource publishes (checked 2026-10-12), plus a few liturgical and revival scripts and Noto's symbol, math and SignWriting fonts, is bundled (Task 12); a script outside that set falls back to the system's fonts.
- **The API image must build the web app:** `deploy/docker/app.Dockerfile` builds it with `pnpm build` in a stage and serves it from `/opt/coglossa/web` (`coglossa-api --web-dir`).
- **Approval refused after another run's draft became the project guide:** only a newer approved version refuses "Approve as it is" now (`service.py`, cbc23aa).
- **The guide-conflict 409 is matched by its text:** guide conflicts carry `"code": "guide_changed"` beside `detail`, and the web app matches the code (`isGuideChanged`, cbc23aa).
- **The re-run-the-sweep offer ignores the profile:** `RunDetail.sweep_enabled` says whether the run's config runs the sweep, and the offer shows only then (58af992).

## Later
- **UI language:** the UI is English only (spec §8). The strings are inline; extracting them is a prerequisite for translating the UI.
- **Keyboard shortcuts** cover moving between flags and opening passages. Shortcuts for the actions themselves (accept, dismiss) are not there yet.
- **Unsaved-changes guard:** only the guide editor has one. The passage editor keeps its text across a refused save, but leaving the page drops an unsaved edit.
- **Many runs in a project:** the projects list and a project's documents load in full. Paging can wait until a project has hundreds of documents.
- **Run comparison:** side-by-side comparison of runs is a `bench` concern in v1 (spec §8.2).

## Accepted as is (final review)
- **Two seeds of server data in component state.** `ReviewPage` keeps the last listed row of the
  selected passage in a ref, as the pin's fallback, and `CheckpointGuide` keeps the project guide
  it fetched after a conflict in state, as the editor's starting point. Both are the starting
  point of UI state rather than a second cache; the global constraint is read that way (M4).
- **The second journey reuses the first's data.** "a reload keeps the review's place" opens the
  run the upload journey finished; the journeys run serially, and a failure of the first fails
  the second (M6).

## Spec deviations in M4b
- **§8.3, where the sweep is offered.** "Editing a locked term after translation offers to re-run
  the sweep": the offer lives on each finished run's Guide tab. It is derived from the data, so
  it shows whenever the project guide's locked terms or names differ from those of the guide the
  run used, whether they were changed there, on the project guide page, or from the review
  panel. The project guide page, which belongs to no run, offers nothing itself; the review
  panel's add-to-guide shows a one-line link to the run's Guide tab after a locked entry.
- **Unknown non-GET `/api/*` paths answer 405 when `api.web_dir` is set.** The app's catch-all
  route serves `index.html` for GET, so a POST, PUT or DELETE to an unknown `/api` path gets
  FastAPI's 405 instead of the JSON 404 the API gives without the web app. GET still answers a
  JSON 404.

## M4 acceptance
Spec §10, M4 "API and web": REST API and SSE, the SPA screens (§8), exports, the remaining
ingestors.
- **REST API and SSE, exports, ingestors:** M4a (`docs/m4a-followups.md`), with the M4a
  carry-over items resolved in M4b (listed there).
- **The SPA screens (§8):** projects, documents and runs (upload, live progress, cancel, retry),
  the guide editor (checkpoint approval, project guide versions, the sweep offer above), the
  review view (filters, keyboard moves between flags, the passage panel and every review
  action), export and system. Types are generated from `schema/openapi.json` and CI fails on
  drift; Vitest covers at least 95% of lines, branches, functions and statements; the Playwright
  journey runs upload, guide approval, review and export against a real local stack and checks
  that nothing is requested from another origin.
- **Final review:** no critical issue; the important ones (I1 to I5) and the minors chosen for
  the fix wave are fixed; what remains is listed above.
