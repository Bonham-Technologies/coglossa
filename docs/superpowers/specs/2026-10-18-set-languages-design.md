# Coglossa: one set, several languages (CGL-24)

Status: written design for review. The user left the choice of approach to me ("Your call",
2026-10-18); §3 records the decision and the alternative I rejected.

## 1. Problem

A set (the engine's *project*) is translated into one language. To get a second language, the
user drops the same files again on **New translation**. That creates a second set, with its own
copy of every file and its own guide, and nothing in the app says the two sets hold the same
files.

The customer translates the same material into many languages, including low-resource ones. One
set per language means ten sidebar entries and ten uploads for ten languages, and a new file has
to be added to each set by hand.

There is also a latent engine bug. The API already accepts a run of a set's file in a different
language (`POST /documents/{id}/runs` with `target_lang`). But a guide version has no language:
`prior_guide(project)` would hand that run the other language's renderings, and since CGL-23 the
merge at save keeps every set guide entry. So the run's guide could be in the wrong language.

## 2. Goal and success criteria

- From a set's page, the user starts every file of the set in another language without
  uploading anything again.
- The set's languages show on its page; the user switches between them. Each language shows the
  same files, with their own translations, status, guide and downloads.
- Each language has its own translation guide. Renderings, locks and the style guide never cross
  from one language to another.
- Files added to a set with several languages are translated into all of them.
- Everything that works on a one-language set today works the same, and looks the same.
  A set with one language shows no language controls.

Out of scope:
- Removing a language from a set.
- Seeding a new language's guide from another language's guide, such as copying its list of
  names without their renderings. Each language builds its guide from the files, as a new set
  does today.
- A different source language per target.
- Phone layouts (CGL-25) and user roles (CGL-26).

## 3. Approach

**Chosen: languages inside one set.** A set keeps one copy of each file. Each run already has
its own `target_lang`. The guide becomes per set *and* language. The web groups a set's runs by
language.

**Rejected: a linked set per language.** "Translate into another language" would create a new
project that copies the files and records a link to the first one. It needs almost no engine
change, because one project would still mean one language. But it keeps one sidebar entry and
one copy of the files per language. It needs a link table, and adding files would have to fan
out across linked projects. It also leaves the engine bug in §1 in place. With many languages
per set, the cost is paid on every screen.

## 4. Design

### 4.1 Engine: the guide has a language

- `guide_version` gains `target_lang` (text, not null). Migration `0004` adds the column and
  fills it, then sets it not null. The uniqueness rule becomes
  `(project_id, target_lang, version)`. Each row is filled with the first of these that exists:
  1. the target language of its `source_run_id`'s run
  2. the project's `target_lang`
  3. the target language of the project's earliest run
  4. `und`

  `und` happens only for a guide saved by hand in a CLI project that has no runs and no default
  language. No run ever reads such a guide (§8).
- Version numbers count per set and language: a new version is
  `max(version for project and language) + 1`, still under the project row lock. Existing
  numbers stay as they are. They are unique per project, so they stay unique per project and
  language too.
- Every guide reader and writer takes the language:
  - `runs.prior_guide(s, project_id, target_lang)`
  - `latest_guide(s, project_id, target_lang, approved_only=…)`
  - `add_guide_version(s, project_id, target_lang, …)`
  - `catalog.project_guide`, `guide_versions` and `save_project_guide`

  The callers pass `run.target_lang`. The callers are:
  - the worker's guide stage and the `finish_run` sweep (`worker/execute.py`)
  - `service.approve_guide`, and the approved-guide check before a run
  - `review.add_guide_entry`, which **Always translate ▸ Apply to all** uses
- The catalog's `usable_guide_version()` SQL mirror filters by language the same way.

### 4.2 Engine: a set's languages

- A set's languages are the project's `target_lang` (its first language) followed by every
  other `target_lang` among its runs, in the order each language's first run was created. No new
  table is needed. A language exists once it has a run.
- `ProjectInfo` gains `languages: list[SetLanguage]`. Each entry has `target_lang` and
  `guide_version`, the version number `prior_guide` would give, or null.
  - `ProjectInfo.guide_version` stays and means the first language's version, so existing
    clients keep working.
  - The set-level counts (`files_translating`, `files_ready`, `files_failed`, `open_flags`) now
    count each file's newest run *in each language*. Today they count each file's newest run
    overall. For a one-language set the numbers are unchanged.
- New: `catalog.add_language(db, queue, project_id, target_lang) -> list[RunSummary]`. In one
  transaction under the project lock:
  - It refuses a language the set already has (conflict), and a set with no files (invalid
    request).
  - It creates one run per file with `checkpoint=false` and `use_project_guide=false`. The
    language starts from an empty guide.
  - It queues every run after the commit.

  The language code is checked with the same rules as an upload's.
- Upload into a set (`POST /projects/{id}/runs`) gains a form field `all_languages`
  (default false). When it is true, the new file gets one run per set language. The response is
  the run in the requested `target_lang`, or the set's first language when none is given.

### 4.3 API

- `POST /projects/{project_id}/languages`, body `{target_lang}`.
  - Returns 202 with the list of new runs.
  - Returns 409 if the set already has the language, and 422 (the API's code for an invalid
    request) if the set has no files.
- `GET /projects/{id}/guide`, `GET /projects/{id}/guide/versions` and `PUT /projects/{id}/guide`
  take an optional `target_lang` query parameter, which defaults to the set's first language.
- `ProjectInfo.languages` and the upload's `all_languages` field, as in §4.2.
- `make schema` regenerates `openapi.json` and the web types.

### 4.4 Web

**The current language.** The run in the address decides the language. Where the address names
no run (`/t/:projectId`, or `?file=` for a file with no run), `?lang=` decides it. Otherwise the
set's first language is used. `pickRun` and `FileTabs` use each file's newest run *in the current
language*, not its newest run overall.

**Language tabs.** When the set has two or more languages, a row of language tabs appears
between the header and the file tabs (`<nav aria-label="Languages">`).
- Each tab shows the language name from `Intl.DisplayNames` and the status of that language's
  files. The status is worked out by the same rule as the sidebar's set status (`setStatus`):
  translating, has failed files, needs review, or ready.
- A tab links to the same file in that language if it has a run there, else to the first file
  in that language.
- A one-language set shows no row, so it looks exactly as it does today.

**Translate into another language.** A new item in the set's **⋯** menu:
**Translate into another language…**
- It opens a dialog with the existing language picker. The set's own languages appear in the
  list but are disabled, with "already in this set".
- The dialog says: "All N files will be translated into <language>. It gets its own translation
  guide."
- **Translate** calls `POST /projects/{id}/languages` and goes to the first file's new run.
- Errors show in the dialog, the way **+ Add files** shows them.
- The item is hidden while the set has no files.

**Things that already exist follow the current language:**
- The header's language pair names the current language.
- **Translate again** passes the current language. Today it sends no language, so the run would
  take the set's first language.
- **Earlier versions** in the **⋯** menu lists the file's earlier runs in the current language
  only.
- **+ Add files** sends `all_languages=true` when the set has several languages, and the dialog
  says "Translated into Spanish, German and French."
- The **Translation guide** panel reads and saves the current language's guide.
  **Always translate ▸ Apply to all** and the recheck of all files that follows it act only on
  the current language's files.
- **Download** stays per run.
- **Delete** removes every language. Its confirmation says "This deletes all N files in every
  language" when the set has several.

**A file with no run in the current language.** For example, a file added through the API with
one language, or one whose run was never created. Its tab says "not started", and the page says
"This file hasn't been translated into <language> yet", with a **Translate** button that starts
the run (`POST /documents/{id}/runs` with `target_lang`).

**The sidebar** is unchanged. Its set status uses the counts that now span every language
(§4.2).

## 5. Error handling

- Adding a language the set already has gets a 409. The dialog shows "This set is already
  translated into <language>", and refreshes the set so the new language tab shows.
- Adding a language is one transaction, so either every file gets a run or none does. Queueing
  happens after the commit, as it does for uploads and **Translate again** today. If the process
  stops between the commit and the queueing, a file is left with a run that never starts. That
  is the same gap uploads have now, and **Translate again** on that file recovers it. The gap is
  not widened here.
- An upload with `all_languages` creates every language's run in the upload's transaction.
- Version conflicts on a guide save are per language: a save in Spanish is never refused
  because the French guide changed.

## 6. Testing

- **Migration:**
  - each backfill rule, including `und`
  - the new uniqueness rule
  - a project with versions from runs in two languages keeps each version under its run's
    language
- **Store:**
  - `prior_guide` and the catalog query agree, per language
  - a draft of language B never becomes the guide of language A
  - CGL-23's merge at save only merges within one language
  - version numbers count per language
- **Worker:** two runs of one file in two languages finish with guides in their own language.
  The sweep enforces only its own language's locks.
- **Catalog and API:**
  - `languages` order and `guide_version` per language
  - counts per file per language
  - `add_language`: runs for every file, 409 for a language the set has, 400 for an empty set,
    and nothing created when the transaction fails
  - upload with `all_languages`
  - guide endpoints with and without `target_lang`
- **Web:**
  - language tabs hidden for one language and shown for two, with a status for each language
  - switching language keeps the file
  - the dialog: disabled languages, the 409 message, going to the new run
  - Translate again, Earlier versions, Add files, guide panel and recheck all use the current
    language
  - a file with no run in the current language shows its **Translate** button
- **e2e:** one Playwright test translates a set, adds a second language, switches between the
  two and opens each guide.
- Every existing test keeps passing unchanged. One-language sets must look and behave as today.

## 7. Documents to update

- `README.md`: a short paragraph on languages in a set.
- The UI spec `docs/superpowers/specs/2026-10-16-coglossa-ui-ux-design.md`:
  - §1.3: a second language is no longer out of scope
  - §3.2: the current language
  - §5.1: the menu item and language tabs
- The operator guide, if it describes set guides: the guide is per language.

## 8. Known limits

- A language can't be removed. A language whose runs all failed keeps its tab, and its files can
  be translated again from it.
- A guide that the backfill marks `und` stays in the database but is never used. It can be
  saved again under a real language through the API.
- Ten languages in a large set queue ten times the work. The dialog states the file count, and
  the queue runs the work as it already does.
