# Coglossa web app: UI/UX redesign and dark mode — Design Spec

- **Date:** 2026-10-16
- **Status:** Draft for review
- **Builds on:** `2026-10-09-coglossa-v1-design.md` (v1, M1–M6 merged) and `2026-10-14-coglossa-hosted-preview-design.md` (merged in PR #10)
- **Mockups:** made during brainstorming in the visual companion (`.superpowers/brainstorm/`, not committed). Layout B (history sidebar) and review layout A (side-by-side document) were chosen.

## 1. Purpose

All of the web app's features exist and are reachable, but the screens speak the engine's language and expect a technical user. Most customers are not technical. This redesign keeps every capability but rebuilds the screens and wording around what a typical user does. It also adds dark mode.

### 1.1 Who it is for (decided 2026-10-16)

- **The main user** gets a translation and checks it properly. Most look over the flagged passages, and some adjust names and terms before downloading.
- **Their documents** are mostly one-off single files, or small groups of related files. Each group is distinct from the others.
- **Never pause.** A translation starts straight away and runs to the end, with no guide-approval stop. Names and terms are adjusted afterwards from the review screen and pushed to every matching passage. The app is set and forget until results arrive, not a tool someone has to remember to come back to.
- **Desktop first.** Design for a wide screen and use all of it. Phones are not a target.

### 1.2 Success criteria

1. A first-time user translates a document without reading anything. They drop a file, pick a language by name and press Translate.
2. No internal word appears in the main flow: project, run, chunk, span, tier, pipeline profile, flag code, or a raw score.
3. A finished translation with usable output never looks like a failure. Red is reserved for "nothing usable came out".
4. Every current capability is still reachable within one click of where it is used (§6).
5. Dark mode covers every screen. It follows the computer's setting until the user picks a theme, and loads without a light flash.
6. Text meets WCAG AA contrast in both themes, and that is checked by a test.
7. Both deployments, the air-gapped install and the hosted preview, get the new app with no new runtime dependency. The CI gates are unchanged and green, including the coverage gates and the air-gap job.

### 1.3 Out of scope (each becomes a Jira follow-up)

- **Keeping names consistent across files while they translate in parallel.** That was an engine change, since made (CGL-23, `2026-10-17-set-guide-consistency-design.md`): each file's guide is merged onto the set's guide as it grows, so files translated together share names. Apply to all (§5.4) and the recheck still cover a person's later changes.
- ~~**Translating an existing set into a second language.**~~ Done in CGL-24: see `2026-10-18-set-languages-design.md`.
- **Layouts for phone-sized screens.**
- ~~**User roles,** e.g. hiding Technical details from non-admins.~~ Done in CGL-26: see `2026-10-18-user-roles-design.md`.
- **Lifting the 4 MB hosted upload cap** (CGL-20, already filed).
- **New translation features of any kind.**

## 2. Approach

Rework the existing React app in place, with a small design system of our own.

- The current stack stays: React 19, React Router 7, TanStack Query, openapi-fetch and the bundled Noto fonts.
- **No new runtime packages.** The air-gapped customer vets and bundles every package.
- Controls use built-in browser elements: `<dialog>` for pop-ups, the `popover` attribute for menus, `<details>` for folded sections and `<select>` for dropdowns.
- The existing API hooks in `web/src/api/` are reused, and the screens above them are rebuilt.

**Rejected:**
- *A headless component library (React Aria or Radix).* It would add packages to vet, and the controls we need are covered by the browser. It can be revisited for the language picker alone if search becomes necessary.
- *A full UI kit (Mantine, MUI, or Tailwind with shadcn).* It brings many packages and a larger bundle, the app would look like every other app built with it, and we would be fighting it for the side-by-side document view.

## 3. Structure and wording

### 3.1 The frame

Every screen has three parts:

- **Header:**
  - On the left, the Coglossa name, which links to `/`.
  - On the right:
    - the theme toggle (☾ / ☀)
    - Help, which opens the help overlay (§7.4)
    - Settings
    - the signed-in name with Sign out (the logout URL from `/me`, as today)
- **Sidebar** (left, always visible, about 260 px wide):
  - A **+ New translation** button.
  - The user's sets, newest activity first, grouped under Today, This week and Earlier.
  - **Each row** shows the set's name and a status in words or a symbol:
    - ✓ ready
    - "2 to check"
    - a percentage while translating
    - "needs attention" when a file failed
  - **Search:** a box filters the list by name once there are more than 15 sets.
  - **The ⋯ menu** on each row has **Rename** and **Delete…**. Delete asks for confirmation in a `<dialog>` and names the set and its number of files. This replaces the Delete button on today's project page.
- **Main area:** the rest of the width.

### 3.2 Addresses

| Address | Shows |
|---|---|
| `/` | New translation (§4.1) |
| `/t/:projectId` | A set. With one file it shows that file's latest translation. With several, it opens the first file's tab. |
| `/t/:projectId/:runId` | One file's translation in a set |
| `/t/:projectId?lang=…&file=…` | A set in one language; a file of it that has no translation in that language yet (CGL-24) |
| `/settings` | Settings (§6.2) |

**The current language** (CGL-24): the run in the address decides it; else `?lang=`; else the
set's first language. Each file shows its newest translation in that language.

**Redirects** keep existing bookmarks and documents working:
- `/projects` → `/`
- `/projects/:id` and `/projects/:id/guide` → `/t/:id`
- `/runs/:runId` and its tabs → `/t/<project of run>/:runId`
- `/system` → `/settings`

An unknown address shows the existing Not Found page inside the frame.

### 3.3 Wording

| Engine word | On screen |
|---|---|
| project | a "translation"; a multi-file one is "a set of 3 files" |
| run | not shown. **Translate again** makes a fresh version. |
| span | "passage" |
| chunk | not shown |
| flag (open) | "worth a look"; in counts, "N to check" |
| accepted / dismissed / resolved flag | "checked" / "not a problem" / "fixed by the recheck" |
| guide | **Translation guide**: names, terms and style. "Bible" stays retired. |
| glossary entry / entity | "term" / "name" |
| locked | "always" |
| sweep | "recheck" |
| language tier | a plain note, e.g. "Coglossa has less practice with Tongan, so check carefully" |
| Meaning / Style score | under More details only: "Meaning match 97 / 100" |
| pipeline profile | "Length: Automatic (recommended) / Short / Standard / Long document" |

## 4. Starting a translation

### 4.1 The New translation screen (`/`)

1. **Drop area.** A large area that takes one or more files by drag and drop or by browsing.
   - It accepts the same types as today: `.txt .md .markdown .docx .html .htm .xhtml .epub .pdf`.
   - Each file becomes a chip with its name, its size and a remove button.
   - **Size check.** A file over the upload limit is marked at once, e.g. "Notice.pdf is 6 MB, and the limit is 4 MB". The limit is `max_upload_mb` from `GET /me` (CGL-26; it used to come from `/system/config`, now admin-only). If that request fails, the check is skipped and the server's refusal is shown instead.
2. **Paste text** tab, beside the drop area. The text is sent as a `.txt` file named after its first five words, e.g. `Welcome to the Riverside.txt`. Characters that are not allowed in file names are removed.
3. **From** dropdown. "Detect automatically" is the default and sends no `source_lang`.
4. **To** dropdown. It is required, and Translate stays disabled until a language is chosen.
   - **Team** (CGL-26): a dropdown of the person's teams and **Only me**, shown only when they
     are in two or more teams; the last choice is remembered. One team is used without asking;
     no team makes the set personal.
   - **Names:** language names come from `Intl.DisplayNames` in the browser's language, so they work offline. The code is shown only when the browser has no name for it.
   - **Order:**
     1. The user's five most recent target languages, kept in `localStorage`.
     2. The languages in the install's tier table (`GET /system/tiers`), alphabetical by name.
     3. **Other language…**, which reveals the existing `LanguageInput` code box with its check.
   - **Practice note:** if the chosen language is one Coglossa has less practice with, a one-line note appears under the dropdown. "Less practice" means reliability tier C, the tier at which the engine already adds its low-reliability flag. The tier is looked up from the pair entry when a source language is chosen, then the target's entry, then the table's default. Tier letters are not shown.
5. **More options** (a `<details>` fold, closed by default): the Length setting from §3.3, defaulting to Automatic.
6. **Translate.**
   1. `POST /projects` with the set's name, `target_lang` and `source_lang` (if chosen).
      - **The name** is the file's name without its extension. For several files it is "<first name> and N more", and for pasted text it is the first five words.
   2. For each file, `POST /projects/{id}/runs` with `checkpoint=false`, and `pipeline_profile` when one is chosen. Uploads go one after another.
   3. Navigate to `/t/:projectId` as soon as the set exists. Upload progress and failures show on that page's file tabs.
   4. **If an upload fails,** that file's tab shows the reason in words (413 → "too large for this server"; other errors → the API's one-line message) with a **Try again** button. The other files carry on.
   5. **If every upload fails,** the set stays and shows the reasons. The user can delete it from the ⋯ menu; it is not deleted automatically.

### 4.2 While a translation runs

**The file's view** shows plain steps. A finished step gets a tick and the current step is highlighted:

| Run state | Step shown |
|---|---|
| `created` | Waiting to start |
| `ingesting`, `profiling` | Reading your document |
| `building_guide` | Building the translation guide |
| `translating` | Translating, with a percentage from `chunks_done` / `chunks_total` |
| `sweeping` | Checking consistency |
| `complete`, `complete_with_errors` | Ready (§5) |

**Below the steps:**
- The line "This usually takes a few minutes. You can leave this page; it keeps going."
- **Stop translating**, a secondary button that calls cancel after a confirmation. A cancelled run shows "Stopped" with a **Translate again** button.

**Live updates and progress:**
- Progress comes from the existing progress stream and endpoints, as today.
- While the open file is running, the browser tab title shows the percentage. When it finishes the title reads "✓ Ready – <name>".
- On a multi-file set, each file tab shows its own status, and a finished file can be reviewed while the others run.

### 4.3 Adding files and translating again

- **+ Add files** (in the header's ⋯ menu, §5.1) opens the drop area in a `<dialog>`, with the set's language fixed. It uploads into the same project with `checkpoint=false`. The engine starts the new file from the set's latest guide, so names carry over; no engine change is needed.
- **Translate again** (in the header's ⋯ menu, §5.1, and on a stopped or failed file) calls `POST /documents/{document_id}/runs` with `checkpoint=false`. The view then shows the newest run. Earlier runs are listed under **Earlier versions** in the same ⋯ menu. Each opens at its own `/t/:projectId/:runId` with a note, "An earlier version. See the latest", and stays editable as today.

### 4.4 Runs waiting for guide approval

The UI never asks for a pause. A run can still be in `awaiting_guide_approval` if it was started from the command line or before this change. Its view says "Waiting for you to check the translation guide", with two buttons:
- **Continue as is** calls `POST /runs/{id}/guide/approve` with no body.
- **Look at the guide first** opens the translation guide panel (§5.5) for this run's draft. Saving there approves it with the edited guide and its `base_version`, as `RunGuide` does today.

## 5. The open translation (review layout A)

### 5.1 Header row

- **The set's name.** Click it to rename (§8.2).
- The language pair as names, and when the translation finished.
- **Translation guide (N)**, where N is the number of names and terms. It opens §5.5.
- **Download ▾**, a `popover` menu:
  - Word document (`docx`)
  - Side-by-side Word file with reviewer notes (`bilingual-docx`, `comments=true`)
  - Web page (`html`)
  - **Other formats ▸**: Data file (JSON) (`json`) and Quality report (`/report`)
- **⋯ menu** for the file on screen: **Translate again**, **Earlier versions** (§4.3), **+ Add files** (§4.3), **Translate into another language…** (CGL-24). Translate again and Earlier versions are for the current language.
- On a set with two or more languages, language tabs sit below the header, each with the most pressing status of its files. They open the same file in that language.
- On a multi-file set, file tabs sit below the header. Each tab shows its status the way the sidebar does; a file with no translation in the current language says "not started".

### 5.2 Summary banner

The banner is one sentence chosen from the run's state and counts. It is neutral or green, and red only when there is no usable output.

| Condition | Banner | Action |
|---|---|---|
| `complete`, no open flags | Looks good. N passages translated, nothing flagged. | none |
| `complete`, open flags | Looks good. N are worth a quick look before you download. | **Next to check →** |
| the run's `language_tier` is C | Adds: Check carefully: Coglossa has less practice with <language>. | none |
| `complete_with_errors` | Ready. Some checking didn't finish on N passages; they're marked. | **Try those again** (retry) when `retryable` |
| `complete_with_errors` with `problems` (CGL-80: e.g. the guide couldn't be built) | Ready. <each problem's sentence>, never "Looks good" | **Try those again** when `retryable`; else **Next to check →** while flags are open (Translate again stays in the set's menu); else **Translate again** |
| `failed` | This translation couldn't finish. <reason in plain words> | **Try again** (retry if `retryable`, else Translate again); the raw error is under Details |
| `cancelled` | Stopped before it finished. | **Translate again** |

When every flag has been dealt with, the banner reads "All checked. Ready to download."

### 5.3 The document

- **Layout.** A two-column grid with Original on the left and the translation, headed with the language name, on the right. Each row is one passage, so the two sides line up.
- **Scripts.** `dir="auto"` and the `lang` attributes are kept, so right-to-left scripts and their fonts work as they do today.
- **Marks:**
  - A passage with open flags is tinted with a warning colour and an edge bar, and carries a ⚠ symbol, so the state is not shown by colour alone.
  - The selected passage has a stronger tint.
  - An edited passage carries a small "edited" mark.
- **Loading.** Passages load in pages from `GET /runs/{id}/spans`. The next page is fetched as the user nears the end, through an `IntersectionObserver`. Today's Previous and Next buttons go away.
- **Show:** menu: Everything / To check (the existing `flagged=true` filter). The numeric score filters move to Technical details (§6.1). Filtering by edited or checked passages would need new API filters and is left out.
- **Next to check →** and the `n` / `p` keys jump to the next or previous passage with open flags, loading pages as needed. Enter opens or closes the panel, and Esc closes it. The shortcuts are listed in the help overlay.

### 5.4 The passage panel

The panel opens on the right, about 380 px wide. Between 1024 and 1280 px it overlays the document.

1. **Why it's flagged.** One block per open flag: a plain title, with the engine's `explanation` below it in smaller text.

   | Code | Title |
   |---|---|
   | `possible_omission` | Something may be missing |
   | `low_meaning` | The meaning may have shifted |
   | `style_drift` | The style doesn't match the rest |
   | `terminology_violation` | Doesn't follow your translation guide |
   | `number_mismatch` | A number doesn't match the original |
   | `agent_disagreement` | The translators disagreed |
   | `cultural_or_wordplay` | Wordplay or a cultural reference |
   | `low_reliability_language` | Less-practised language: check carefully |
   | `unscored` | Couldn't be checked |
   | `engine_error` | Some checking didn't finish |

   A code missing from this table shows its explanation with the title "Worth a look". That way a new engine code never breaks the panel.

2. **Better versions.** Each candidate gets a **Use** button, and **Edit** turns the translation into a text box with Save and Cancel.
   - Using a candidate or saving an edit calls the existing endpoint and then `POST …/accept`. The panel then says "Changed and marked as checked" and offers **Next to check →**.
   - If the accept call fails, the change still stands and the panel says the passage is still open.
3. **It's fine** calls `POST …/accept` and moves to the next passage to check.
4. **Always translate.** Two fields: *Always translate* [X] *as* [Y].
   - **Filling X:** selecting text in the Original column fills X when the panel is open.
   - **The tick box** "This is a name (person, place, organisation…)" chooses a name entry (kind `person`, editable later in the guide) over a term.
   - **Apply to all:**
     1. Calls `POST /runs/{id}/spans/{span_id}/guide` with a locked entry.
     2. Calls `POST /runs/{run}/sweep` for every run in the set that is the newest for its file and is `complete` or `complete_with_errors`.
     3. The panel reports "Added to the translation guide. Rechecking 3 files…". Each file tab shows "Rechecking" until its sweep finishes.
     4. A sweep that cannot start, e.g. because that run is still translating, is skipped. Its file picks the rule up when it finishes, because the sweep that follows its translation uses the latest guide.
5. **More details ▸** (a `<details>` fold, closed):
   - the scores as "Meaning match 97 / 100" and "Style match 100 / 100"
   - the signals and the revision history (today's `Signals` and `Revisions`)
   - every flag with its status in §3.3 wording
   - **Reopen** on checked flags
   - **Not a problem…**, which dismisses a flag after asking for a reason, as today

### 5.5 The translation guide panel

A wide panel that slides in from the right, about 640 px, as a `<dialog>`. It works on the set's project guide; for a run waiting for approval (§4.4) it works on that run's draft.

- **Names** (entities), **Terms** (glossary) and **Style** tabs.
- **Each name or term is one readable row,** e.g. "Riverside Community Centre → Centro Comunitario Riverside · always".
  - Clicking a row turns it into a form: source, translation, the Always tick box and, for names, the kind.
  - The rarely used fields (gender, forms of address, notes) sit in a **More** fold inside the row.
  - This replaces today's wide input grid, which overflows the page.
- **+ Add a name** / **+ Add a term**, and a remove button on each row.
- **Style** shows the guide's style settings as labelled form fields, one per line.
- **Saving:**
  - Save calls `PUT /projects/{id}/guide` with the `base_version` the edit started from.
  - A conflict (409) says "Someone else changed the guide. Reload to see their changes", with a Reload button. The user's edits are not thrown away until they choose Reload.
  - After a save, the panel asks **Update the translations to match?** Yes runs the recheck exactly as in §5.4 step 4.
- **Unsaved changes:** closing the panel with unsaved changes asks for confirmation, using the existing `UnsavedChanges` logic.

## 6. Expert controls and Settings

### 6.1 Where each expert control lives

| Control | Location |
|---|---|
| Length profile | More options on New translation (§4.1) |
| Meaning and Style scores, signals, revisions | More details in the passage panel |
| Numeric score filters | **Technical details** on Settings, as a "Find passages by score" form. It asks for a translation and the score ranges, and opens that translation with the filter applied. |
| JSON export and quality report | Download ▸ Other formats |
| Retry failed parts | The summary banner (§5.2) |
| Raw config, config hash, language tier table, raw check results | Settings ▸ Technical details |
| Earlier runs of a file | The header's ⋯ menu ▸ Earlier versions |

### 6.2 Settings (`/settings`)

Since CGL-26, everyone sees **Appearance**; **Status** and **Technical details** are for admins
only.

- **Appearance:** Theme, with the choices Match my computer (default), Light and Dark (§7.1).
- **Status:** one sentence from `GET /system/doctor` (without `probe`), loaded when the page opens:
  - All checks pass: "Everything is working."
  - The scorer check fails: "The quality checker is offline. Translations still finish but aren't double-checked."
  - A model or database check fails: "Coglossa can't reach <the failing part>. Translations can't start until an administrator fixes it."
  - Any other failure: "Something needs an administrator's attention", plus the check's name.

  **Run a full check** calls the doctor with `probe=true`.
- **Technical details ▸** (folded): today's System page content, unchanged in substance. That is the config path and hash, the raw config, the tier table, the raw doctor results, and the score search from §6.1.

## 7. The look and dark mode

### 7.1 Theme

- **Setting.** The `data-theme` attribute on `<html>` is `light` or `dark`.
- **Choice.** The user's choice (`system`, `light` or `dark`) is stored in `localStorage` under `coglossa.theme`. "system" follows `prefers-color-scheme` and updates live when the computer's setting changes.
- **No flash.** A small script, `web/public/theme-init.js`, is loaded with a plain blocking `<script src>` in `index.html`'s `<head>`. It sets `data-theme` before the first paint. It is a separate file, not inline, so a future Content-Security-Policy does not have to allow inline scripts.
- **Toggle.** The header toggle switches between light and dark and stores that choice. Settings offers all three choices.
- **Storage failures.** If `localStorage` is unavailable, e.g. blocked or in a private window, the app follows the computer's setting and the toggle still works for the session.

### 7.2 Design values

`web/src/styles/tokens.css` defines every colour, space, radius, font size and shadow as a CSS custom property, with a light set and a dark set. No other stylesheet uses a raw colour value.

| Value | Light | Dark |
|---|---|---|
| `--bg` page | warm off-white, #faf8f5 | charcoal, #15171c |
| `--surface` panels and cards | #ffffff | #1b1e25 |
| `--surface-2` headers, hover | #f1eee9 | #20242d |
| `--border` | #ddd8cf | #2f3542 |
| `--text` | #1f2328 | #e6e8ee |
| `--text-muted` | #5c6370 | #9aa3b5 |
| `--accent` buttons and links | #3554d1 | #7d97ff |
| `--ok` | #1a6f40 | #7fd49b |
| `--warn` text | #8a5a00 | #f0c36a |
| `--warn-tint` flagged passage | #fff4dc | #2b2716 |
| `--danger` | #b42318 | #ff8a80 |

The values are a starting point. The contrast test in §9.2 binds them: every text-on-background pair the app uses must reach 4.5:1, and 3:1 for large text and for borders that carry meaning. A value that fails is changed.

The app's own stylesheet, `web/src/styles/app.css`, is rewritten on top of the tokens.

### 7.3 Layout and sizes

- **Widths:**
  - Designed for 1280 px and wider, using the full width with no fixed maximum.
  - At 1024–1280 px, the passage panel and the guide panel overlay the document instead of narrowing it.
  - Below 900 px, the sidebar becomes a menu button that opens it as an overlay.
  - Below 900 px nothing else is tuned (§1.3).
- **Sizes** are in `rem`, so browser zoom scales everything.
- The bundled Noto font stack stays as it is.

### 7.4 Accessibility

- **Keyboard:** everything works from the keyboard. Every interactive element has a visible `:focus-visible` outline in `--accent`.
- **Pop-ups:** `<dialog>` handles focus trapping and Esc.
- **Menus:** they use `popover` and are closed by Esc or a click outside.
- **Status:** status is always a word or a symbol plus a colour, never colour alone.
- **Help overlay:** a `<dialog>` that lists the keyboard shortcuts and explains the statuses and flag titles in one short page.

## 8. Backend changes

Both changes are additive. The command line, other API clients and the database schema are unchanged, and no migration is needed.

### 8.1 Set summary on `ProjectInfo`

`GET /projects` and `GET /projects/{id}` gain these fields:

| Field | Type | Meaning |
|---|---|---|
| `last_activity_at` | datetime | The latest of the project's `created_at` and its runs' `created_at` and `finished_at`. The sidebar sorts by it. |
| `files_translating` | int | Files whose newest run is in `created`, `ingesting`, `profiling`, `building_guide`, `awaiting_guide_approval`, `translating` or `sweeping` |
| `files_ready` | int | Files whose newest run is `complete` or `complete_with_errors` |
| `files_failed` | int | Files whose newest run is `failed` or `cancelled` |
| `open_flags` | int | Open flags on the newest run of each file |

They are computed in `catalog.list_projects` and `catalog.get_project`, with grouped queries for the whole list, not one query per project.

### 8.2 Rename

`PATCH /projects/{id}` with `{"name": str}` (1–200 characters after trimming) returns the updated `ProjectInfo`. A blank name returns 422. An unknown project returns 404, as the other project routes do.

### 8.3 Regeneration

`schema/openapi.json` and `web/src/api/schema.d.ts` are regenerated with the existing commands.

## 9. Testing

The current tools stay: Vitest with Testing Library and MSW for components, pytest for the API and engine, and Playwright end to end. The coverage gates stay as they are.

### 9.1 Component tests

| Component | Tests |
|---|---|
| Drop area | multiple files, the type filter, over-limit marking, Paste text naming |
| Language dropdown | names from `Intl.DisplayNames`, the recent-first order, Other language with the code check, the practice note (pair, then language, then default lookup) |
| Translate sequence | create, then the uploads one after another, with `checkpoint=false` on every request; one upload failing while the others continue |
| Progress steps | every run state from §4.2 |
| Summary banner | every row of §5.2 |
| Passage panel | every flag title in §5.4 and the unknown-code fallback; Use, Edit, It's fine (each followed by accept); Apply to all (add-to-guide, then a sweep for exactly the newest finished run of each file); More details |
| Guide panel | row editing, the More fold, save with `base_version`, the 409 conflict message, the recheck prompt, unsaved changes |
| Sidebar | grouping by date, status per row, search, rename, delete with confirmation |
| Redirects | every old address in §3.2 |
| Theme | stored choice, the system default, live system changes, `localStorage` throwing |
| Settings | the status sentence for each doctor outcome; Technical details content |

### 9.2 Contrast test

A unit test parses `tokens.css` and checks every text and background pair the app uses against WCAG AA, in both themes. The pairs are listed in the test, next to the tokens.

### 9.3 End to end

The existing `web/e2e/journey.spec.ts` and `system.spec.ts` are rewritten for the new flow: drop a file, pick Spanish, wait for Ready, open a flagged passage, apply a term with Apply to all, download a Word file. A dark-mode run checks that `data-theme="dark"` is set before the first paint and that the main screens render.

### 9.4 Backend tests

The §8.1 counts are checked across mixed states, several runs per file (only the newest counts) and an empty project. Rename is checked for success, a blank name and an unknown project.

## 10. Rollout

- **One branch and one PR,** merged when every CI check passes. That includes the air-gap job and the coverage gates.
- **The hosted preview** is redeployed by hand until the GitHub deploy tokens arrive (CGL-22), and `smoke.py` is run.
  - Smoke check 3 already uses `/api/health`, so it is unaffected.
  - The login page in `deploy/hosted/vercel` gets the same tokens and dark mode, so the very first screen matches the app.
- **The air-gapped bundle** picks up the new `web/dist` with no other change.
- **The operator guide's** web-app section (`docs/operator-guide.md`) is updated to the new wording and screens.
