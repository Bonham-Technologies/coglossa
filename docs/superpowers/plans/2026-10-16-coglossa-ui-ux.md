# Web App UI/UX Redesign and Dark Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Coglossa web app's screens and wording for non-technical users (layout B sidebar + side-by-side review), add light/dark themes, and add the two small backend endpoints the new screens need.

**Architecture:** The React app is reworked in place. The API hooks in `web/src/api/` stay and grow; new screens are built bottom-up as tested components (shell, New translation, passage panel, document view, run view, guide panel, set page, Settings) and wired into the router in one switch task that also deletes the old pages. Colours, spacing and sizes come from one token stylesheet with a light and a dark set; a tiny script in `index.html` applies the saved theme before the first paint. The backend gains a status summary on `ProjectInfo` and `PATCH /projects/{id}` for rename.

**Tech Stack:** React 19, React Router 7, TanStack Query 5, openapi-fetch, Vite 7, Vitest 3 + Testing Library + MSW 2, Playwright; Python 3 / FastAPI / SQLAlchemy 2 (async, Postgres) / pytest for the backend.

**Spec:** `docs/superpowers/specs/2026-10-16-coglossa-ui-ux-design.md` (approved 2026-10-16). Read it before your task: its exact wording is binding.

## Global Constraints

- **No new packages.** `web/package.json` `dependencies` and `devDependencies` do not change. No new Python packages.
- **Words on screen** follow spec §3.3. The main flow never shows: project, run, chunk, span, tier, pipeline profile, a flag code, or a raw score (scores appear only under "More details" and Settings ▸ Technical details). The guide is always "Translation guide".
- **Every request the UI makes that starts a translation sends `checkpoint=false`** (uploads, Translate again).
- **Colours** appear only in `web/src/styles/tokens.css`. Every other stylesheet uses `var(--…)`. A test enforces it (Task 2).
- **Contrast:** text pairs ≥ 4.5:1, borders that carry meaning ≥ 3:1, in both themes (Task 2's test).
- **Breakpoints:** designed for ≥ 1280 px; panels overlay the document at 1024–1279 px; below 900 px the sidebar becomes a menu button.
- **Passage text** always carries `dir="auto"` and the passage's `lang`.
- **Status is never colour alone:** a word or symbol (✓ ⚠ ! …) always accompanies a status colour.
- **Exact user-facing strings** in this plan and the spec are binding. Code structure in a step may change where the type checker or linter demands it; behaviour and strings may not.
- **Gates for every task:** `cd web && pnpm fmt && pnpm lint && pnpm test` (coverage thresholds 95% lines/branches/functions/statements stay) and, for backend tasks, `make test-engine test-api` plus `cd engine && uv run ruff check . && uv run ruff format --check .` (same for `api`).
- **Commits** end with:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012TBMbjzsQyykrdRBR7Vuv5
  ```
- Work happens on branch `ui-ux-redesign` (the spec is already committed there).

## Plan decisions (where the spec left a choice open)

These are rulings made while writing the plan; reviewers should treat them as the intended behaviour.

1. **Unique set names.** Project names are unique in the database (`409` on a duplicate). Creating a set retries with `"<name> (2)"`, `"<name> (3)"`, … up to `(50)`. Rename shows "Another translation already has this name." on a `409`.
2. **Sidebar progress.** `ProjectInfo` carries counts, not progress, so a running set's sidebar row reads "translating…" with no percentage. The percentage shows on the step list and in the browser tab title (spec §4.2).
3. **"All checked" wording.** The API counts open flags only, so the banner cannot tell "never flagged" from "all checked". When nothing is open it reads "Looks good. N passages translated, nothing left to check." (merges spec §5.2's first row and its "All checked" line).
4. **Menus** (Download, ⋯) are a small state-driven `Menu` component (button with `aria-expanded`, a `role="menu"` list, closed by Esc or a click outside) instead of the `popover` attribute: same behaviour, and testable in jsdom, which has no popover support.
5. **Re-sweep while open.** During a recheck (`sweeping` with `finished_at` set) the open file shows "Rechecking against your translation guide…" in place of the document; the live update brings the document back.
6. **Failed runs** show "This translation couldn't finish. Something went wrong while translating." The engine's raw error stays under "Details".
7. **Pending uploads live in memory.** If the browser reloads mid-upload, files not yet uploaded are gone; the set shows only what reached the server.

## Review Focus

The five conditions most likely to bite a user that the spec implies but does not spell out. Each has a test in the owning task.

1. **The same file translated twice** (or two files with one name): the second set must get "welcome (2)", not an error. Test: Task 4 `createSet retries with a number on a 409`.
2. **Browser storage blocked** (private window, policy): theme and recent languages must still work for the session. Tests: Task 2 `falls back to memory when storage throws`, Task 3 `recent languages survive storage that throws`.
3. **Apply to all while another file in the set is still translating**: the rule must save and the finished files recheck; the running file is skipped without an error. Test: Task 4 `recheckSet skips runs that are not finished and 409s`.
4. **Renaming to a name another translation already uses**: a plain message, the dialog stays open. Test: Task 5 `rename conflict shows a plain message`.
5. **A set with no files** (every upload failed, or a project made by the command line): the page must offer + Add files, not crash or spin. Test: Task 12 `a set with no files offers Add files`.

## File map

| File | Responsibility | Task |
|---|---|---|
| `engine/coglossa/schema/views.py` | `ProjectInfo` summary fields | 1 |
| `engine/coglossa/catalog.py` | summaries query, `rename_project` | 1 |
| `api/coglossa_api/models.py`, `routes/projects.py` | `RenameProject`, `PATCH /projects/{id}` | 1 |
| `web/src/styles/tokens.css`, `base.css` | design values, element defaults | 2 |
| `web/src/theme/theme.ts`, `web/public/theme-init.js` | theme choice, no-flash init | 2 |
| `web/src/lib/flags.ts`, `languages.ts`, `status.ts`, `names.ts` | plain-language helpers | 3, 4 |
| `web/src/api/sets.ts`, `web/src/upload/queue.ts` | create/rename/recheck/translate again, upload queue | 4 |
| `web/src/components/Dialog.tsx`, `Menu.tsx`, `Layout.tsx`, `Sidebar.tsx`, `SetDialogs.tsx`, `HelpDialog.tsx` | the frame | 5 |
| `web/src/translate/LanguagePicker.tsx`, `DropArea.tsx`, `NewTranslationPage.tsx` | starting a translation | 6, 7 |
| `web/src/review/PassagePanel.tsx`, `AlwaysTranslate.tsx`, `MoreDetails.tsx`, `candidates.ts` | the passage panel | 8 |
| `web/src/review/DocumentView.tsx`, `useDocumentKeys.ts` | side-by-side document | 9 |
| `web/src/set/RunView.tsx`, `ProgressSteps.tsx`, `SummaryBanner.tsx` | one file's run | 10 |
| `web/src/guide/GuidePanel.tsx`, `GuideRows.tsx`, `entries.ts` | translation guide panel | 11 |
| `web/src/set/SetPage.tsx`, `SetHeader.tsx`, `FileTabs.tsx`, `AddFilesDialog.tsx` | a set | 12 |
| `web/src/settings/SettingsPage.tsx`, `statusSentence.ts`, `TechnicalDetails.tsx` | Settings | 13 |
| `web/src/routes.tsx`, `web/src/set/Redirects.tsx` | addresses, redirects; old pages deleted | 14 |
| `web/e2e/*.spec.ts`, `deploy/hosted/vercel/static/login.html`, `docs/operator-guide.md` | end to end, sign-in page, docs | 15 |

**Test helpers used throughout** (created in Task 4, `web/src/test/render.tsx`): `queryWrapper()` for hook tests and `renderAt(element, { path, pattern })` for component tests. MSW handlers come from `web/src/test/server.ts` (`server.use(...)`); fixtures from `web/src/test/fixtures.ts`.

---
### Task 1: Backend — set summary on `ProjectInfo` and rename

Spec §8. Both changes are additive; no migration.

**Files:**
- Modify: `engine/coglossa/schema/views.py` (`ProjectInfo`)
- Modify: `engine/coglossa/catalog.py` (summaries, `_clean_name`, `rename_project`)
- Modify: `api/coglossa_api/models.py` (`RenameProject`)
- Modify: `api/coglossa_api/routes/projects.py` (`PATCH /projects/{project_id}`)
- Test: `engine/tests/test_catalog.py`, `api/tests/test_projects.py`
- Regenerate: `schema/openapi.json`, `web/src/api/schema.d.ts` (and whatever else `make schema` rewrites)
- Modify: `web/src/test/fixtures.ts` (the `project` fixture gains the new fields)

**Interfaces:**
- Produces: `ProjectInfo` JSON gains `last_activity_at: str (ISO datetime)`, `files_translating: int`, `files_ready: int`, `files_failed: int`, `open_flags: int`. `catalog.rename_project(db, project_id: uuid.UUID, name: str) -> ProjectInfo`. `PATCH /api/projects/{project_id}` body `{"name": str}` → `ProjectInfo`; 404 unknown, 409 duplicate name, 422 blank.

- [ ] **Step 1: Write the failing engine tests** (append to `engine/tests/test_catalog.py`; it already imports `seed_run`, `service`, `PromptLibrary`, `RecordingQueue`, `build_config`, `RunState`, `NotFoundError`, `catalog`, `uuid`, `pytest`)

```python
async def test_set_summary_counts_the_newest_run_of_each_file(db):
    ready = await seed_run(db, project="mixed")
    failed = await seed_run(db, project="mixed", filename="b.txt", state=RunState.FAILED)
    redone = await seed_run(db, project="mixed", filename="c.txt")
    # a new run of c.txt: only the newest run of a file counts, so c.txt is now translating
    await service.submit_existing(
        db,
        RecordingQueue(),
        build_config(),
        PromptLibrary(None),
        redone.document_id,
        service.RunOptions(target_lang="en"),
    )
    expected_flags = 0
    for run_id in (ready.run_id, failed.run_id):  # c.txt's finished run is no longer the newest
        expected_flags += (await catalog.run_detail(db, run_id)).stats.open_flags
    info = await catalog.get_project(db, ready.project_id)
    assert (info.files_ready, info.files_failed, info.files_translating) == (1, 1, 1)
    assert info.open_flags == expected_flags
    assert info.last_activity_at >= info.created_at
    [listed] = await catalog.list_projects(db)
    assert listed == info


async def test_set_summary_of_an_empty_project(db):
    info = await catalog.create_project(db, "empty", target_lang="en")
    assert (info.files_ready, info.files_failed, info.files_translating, info.open_flags) == (
        0,
        0,
        0,
        0,
    )
    assert info.last_activity_at == info.created_at


async def test_rename_project(db):
    first = await catalog.create_project(db, "first", target_lang="en")
    await catalog.create_project(db, "second", target_lang="en")
    renamed = await catalog.rename_project(db, uuid.UUID(first.id), "  Welcome letter\x00 ")
    assert renamed.name == "Welcome letter"
    with pytest.raises(service.ConflictError, match="a project named 'second' already exists"):
        await catalog.rename_project(db, uuid.UUID(first.id), "second")
    with pytest.raises(service.InvalidRequestError, match="a project needs a name"):
        await catalog.rename_project(db, uuid.UUID(first.id), "   ")
    with pytest.raises(NotFoundError, match="not found"):
        await catalog.rename_project(db, uuid.uuid4(), "third")
```

Also update `test_create_and_read_projects` so its tuple check still holds (the new fields are additive; no change needed unless it compares whole objects — it compares `get_project(...) == info`, which still holds).

- [ ] **Step 2: Run them to see them fail**

Run: `cd engine && uv run pytest tests/test_catalog.py -k "set_summary or rename_project" -v`
Expected: FAIL — `AttributeError: 'ProjectInfo' object has no attribute 'files_ready'` and `module 'coglossa.catalog' has no attribute 'rename_project'`.

- [ ] **Step 3: Add the fields to `ProjectInfo`** (`engine/coglossa/schema/views.py`, after `guide_version`)

```python
    last_activity_at: datetime  # the latest of created_at and its runs' created and finished times
    files_translating: int  # files whose newest run is still under way (or waiting for approval)
    files_ready: int  # files whose newest run finished with output
    files_failed: int  # files whose newest run failed or was cancelled
    open_flags: int  # open flags on the newest run of each file
```

- [ ] **Step 4: Compute them in `engine/coglossa/catalog.py`**

Add imports: `from dataclasses import dataclass`, `from datetime import datetime`, `from sqlalchemy.ext.asyncio import AsyncSession`, `FlagStatus` to the `coglossa.schema` import, and `FlagRow, SpanRow` to the `coglossa.store.models` import. Then add, after `_projects_query`:

```python
_UNDER_WAY = frozenset(
    {
        RunState.CREATED,
        RunState.INGESTING,
        RunState.PROFILING,
        RunState.BUILDING_GUIDE,
        RunState.AWAITING_GUIDE_APPROVAL,
        RunState.TRANSLATING,
        RunState.SWEEPING,
    }
)
_STOPPED = frozenset({RunState.FAILED, RunState.CANCELLED})


@dataclass(frozen=True)
class _SetSummary:
    """What the web app's sidebar shows for a project (UI spec §8.1)."""

    last_activity_at: datetime | None = None
    translating: int = 0
    ready: int = 0
    failed: int = 0
    open_flags: int = 0


async def _set_summaries(
    s: AsyncSession, project_ids: list[uuid.UUID]
) -> dict[uuid.UUID, _SetSummary]:
    """Three grouped queries for the whole list, never one per project."""
    if not project_ids:
        return {}
    rank = func.row_number().over(
        partition_by=Run.document_id, order_by=(Run.created_at.desc(), Run.id.desc())
    )
    ranked = (
        select(Run.id, Run.state, Document.project_id, rank.label("rank"))
        .join(Document, Run.document_id == Document.id)
        .where(Document.project_id.in_(project_ids))
        .subquery()
    )
    newest = (
        await s.execute(
            select(ranked.c.id, ranked.c.state, ranked.c.project_id).where(ranked.c.rank == 1)
        )
    ).all()
    open_by_run: dict[uuid.UUID, int] = {}
    if newest:
        open_by_run = dict(
            (
                await s.execute(
                    select(SpanRow.run_id, func.count(FlagRow.id))
                    .join(FlagRow, FlagRow.span_id == SpanRow.id)
                    .where(
                        SpanRow.run_id.in_([row.id for row in newest]),
                        FlagRow.status == FlagStatus.OPEN.value,
                    )
                    .group_by(SpanRow.run_id)
                )
            ).all()
        )
    latest = func.greatest(Run.created_at, func.coalesce(Run.finished_at, Run.created_at))
    activity = dict(
        (
            await s.execute(
                select(Document.project_id, func.max(latest))
                .join(Run, Run.document_id == Document.id)
                .where(Document.project_id.in_(project_ids))
                .group_by(Document.project_id)
            )
        ).all()
    )
    counts: dict[uuid.UUID, dict[str, int]] = defaultdict(
        lambda: {"translating": 0, "ready": 0, "failed": 0, "open_flags": 0}
    )
    for row in newest:
        state = RunState(row.state)
        c = counts[row.project_id]
        if state in _UNDER_WAY:
            c["translating"] += 1
        elif state in _STOPPED:
            c["failed"] += 1
        else:
            c["ready"] += 1
        c["open_flags"] += open_by_run.get(row.id, 0)
    return {
        pid: _SetSummary(last_activity_at=activity.get(pid), **counts[pid]) for pid in project_ids
    }
```

Change `_project_info` to take the summary and fill the fields:

```python
def _project_info(
    project: Project, documents: int, run_count: int, guide: int | None, summary: _SetSummary
) -> ProjectInfo:
    activity = summary.last_activity_at
    return ProjectInfo(
        id=str(project.id),
        name=project.name,
        target_lang=project.target_lang,
        source_lang=project.source_lang,
        pipeline_profile=PipelineProfile(project.pipeline_profile)
        if project.pipeline_profile
        else None,
        created_at=project.created_at,
        documents=documents,
        runs=run_count,
        guide_version=guide,
        last_activity_at=max(project.created_at, activity) if activity else project.created_at,
        files_translating=summary.translating,
        files_ready=summary.ready,
        files_failed=summary.failed,
        open_flags=summary.open_flags,
    )
```

and update the two readers so the summaries are computed in the same session:

```python
async def list_projects(db: Database) -> list[ProjectInfo]:
    async with db.session() as s:
        rows = (await s.execute(_projects_query().order_by(Project.name))).all()
        summaries = await _set_summaries(s, [row[0].id for row in rows])
    return [_project_info(*row, summaries[row[0].id]) for row in rows]


async def get_project(db: Database, project_id: uuid.UUID) -> ProjectInfo:
    async with db.session() as s:
        row = (await s.execute(_projects_query().where(Project.id == project_id))).one_or_none()
        if row is None:
            raise runs.NotFoundError(f"project {project_id} not found")
        summaries = await _set_summaries(s, [project_id])
    return _project_info(*row, summaries[project_id])
```

- [ ] **Step 5: Add `rename_project`**, sharing the name rules with `create_project`

```python
def _clean_name(name: str) -> str:
    name = clean_text(name).strip()
    if not name:
        raise service.InvalidRequestError("a project needs a name")
    if len(name) > MAX_PROJECT_NAME:
        raise service.InvalidRequestError(
            f"project names are at most {MAX_PROJECT_NAME} characters"
        )
    return name
```

Replace the first five lines of `create_project`'s body with `name = _clean_name(name)`, then add:

```python
async def rename_project(db: Database, project_id: uuid.UUID, name: str) -> ProjectInfo:
    name = _clean_name(name)
    try:
        async with db.session() as s, s.begin():
            project = await s.get(Project, project_id, with_for_update=True)
            if project is None:
                raise runs.NotFoundError(f"project {project_id} not found")
            project.name = name
    except IntegrityError as e:  # the name is unique
        raise service.ConflictError(f"a project named '{name}' already exists") from e
    return await get_project(db, project_id)
```

- [ ] **Step 6: Run the engine tests**

Run: `cd engine && uv run pytest tests/test_catalog.py -v`
Expected: PASS (all, including the existing ones).

- [ ] **Step 7: Write the failing API test** (append to `api/tests/test_projects.py`)

```python
async def test_rename_a_project(api):
    project = await _project(api)
    await _project(api, name="taken")
    url = f"/api/projects/{project['id']}"
    renamed = await api.client.patch(url, json={"name": " Welcome letter "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Welcome letter"
    assert (await api.client.get(url)).json()["name"] == "Welcome letter"
    assert (await api.client.patch(url, json={"name": "taken"})).status_code == 409
    assert (await api.client.patch(url, json={"name": ""})).status_code == 422
    assert (await api.client.patch(url, json={"name": "   "})).status_code == 422
    unknown = await api.client.patch(f"/api/projects/{uuid.uuid4()}", json={"name": "x"})
    assert unknown.status_code == 404


async def test_project_summary_fields(api, db):
    seeded = await seed_run(db)
    info = (await api.client.get(f"/api/projects/{seeded.project_id}")).json()
    assert (info["files_ready"], info["files_failed"], info["files_translating"]) == (1, 0, 0)
    assert isinstance(info["open_flags"], int)
    assert info["last_activity_at"] >= info["created_at"]
```

- [ ] **Step 8: Add the model and route**

`api/coglossa_api/models.py`, after `CreateProject`:

```python
class RenameProject(BaseModel):
    name: str = Field(min_length=1, max_length=200)
```

`api/coglossa_api/routes/projects.py` (import `RenameProject` beside `CreateProject`), after `get_project`:

```python
@router.patch("/projects/{project_id}")
async def rename_project(project_id: uuid.UUID, body: RenameProject, ctx: Ctx) -> ProjectInfo:
    """Rename a project; names stay unique (409) and cannot be blank (422)."""
    return await catalog.rename_project(ctx.db, project_id, body.name)
```

- [ ] **Step 9: Regenerate the schema and web types; fix the web fixtures**

Run: `make schema`
Then in `web/src/test/fixtures.ts` add to `project` (and so to `otherProject`, which spreads it):

```ts
  last_activity_at: "2026-10-01T09:05:00Z",
  files_translating: 0,
  files_ready: 1,
  files_failed: 0,
  open_flags: 2,
```

and in `otherProject` override `files_ready: 0, open_flags: 0` (it has no documents).

- [ ] **Step 10: Run every gate**

Run: `make test-engine test-api && cd web && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS. `api/tests/test_openapi.py` passes because `schema/openapi.json` was regenerated.

- [ ] **Step 11: Commit**

```bash
git add engine api schema web/src/api/schema.d.ts web/src/test/fixtures.ts
git commit -m "feat(api): set summary on ProjectInfo and PATCH /projects/{id} rename"
```

---

### Task 2: Design values, theme, and the no-flash script

Spec §7.1, §7.2. Nothing on screen uses the new stylesheets' classes yet except element defaults; old pages keep `app.css` until Task 14.

**Files:**
- Create: `web/src/styles/tokens.css`, `web/src/styles/base.css`
- Create: `web/src/theme/theme.ts`, `web/src/theme/theme.test.ts`
- Create: `web/public/theme-init.js`, `web/src/theme/theme-init.test.ts`
- Create: `web/src/styles/contrast.test.ts`, `web/src/styles/no-raw-colours.test.ts`
- Modify: `web/index.html`, `web/src/main.tsx`, `web/eslint.config.js`

**Interfaces:**
- Produces: `type ThemeChoice = "system" | "light" | "dark"`, `type Theme = "light" | "dark"`, `THEME_KEY = "coglossa.theme"`, `storedChoice(): ThemeChoice`, `resolveTheme(choice: ThemeChoice, prefersDark?: boolean): Theme`, `applyTheme(choice?: ThemeChoice): void`, `setThemeChoice(choice: ThemeChoice): void`, `useTheme(): { choice: ThemeChoice; theme: Theme; setChoice: (c: ThemeChoice) => void }`, `resetThemeForTests(): void` — all from `web/src/theme/theme.ts`. CSS custom properties listed in `tokens.css` below; CSS classes in `base.css`: `.button`/`button` variants `.primary`, `.quiet`, `.danger`, `.link`; `.muted`, `.error`, `.notice`, `.visually-hidden`, `.row`, `.row.end`, `.row.between`, `.field`, `.dialog`, `.menu`, `.menu-list`, `.menu-heading`, `.tone-ok`, `.tone-warn`, `.tone-busy`, `.tone-bad`, `.tone-none`.

- [ ] **Step 1: Write the failing theme tests** — `web/src/theme/theme.test.ts`

```ts
import { act, renderHook } from "@testing-library/react";
import {
  applyTheme,
  resetThemeForTests,
  resolveTheme,
  setThemeChoice,
  storedChoice,
  THEME_KEY,
  useTheme,
} from "./theme";

function media(dark: boolean) {
  const listeners = new Set<() => void>();
  const query = {
    matches: dark,
    addEventListener: (_: string, l: () => void) => listeners.add(l),
    removeEventListener: (_: string, l: () => void) => listeners.delete(l),
  };
  vi.stubGlobal("matchMedia", () => query);
  return {
    flip(next: boolean) {
      query.matches = next;
      listeners.forEach((l) => l());
    },
  };
}

beforeEach(() => {
  localStorage.clear();
  resetThemeForTests();
  delete document.documentElement.dataset.theme;
});

it("follows the computer until a choice is stored", () => {
  media(true);
  expect(storedChoice()).toBe("system");
  applyTheme();
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(resolveTheme("light", true)).toBe("light");
  expect(resolveTheme("system", false)).toBe("light");
});

it("stores a choice and applies it", () => {
  media(false);
  setThemeChoice("dark");
  expect(localStorage.getItem(THEME_KEY)).toBe("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
  localStorage.setItem(THEME_KEY, "purple");
  expect(storedChoice()).toBe("system"); // an unknown value is ignored
});

it("falls back to memory when storage throws", () => {
  media(false);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  setThemeChoice("dark");
  expect(storedChoice()).toBe("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
});

it("updates live when the computer's setting changes", () => {
  const m = media(false);
  const { result } = renderHook(() => useTheme());
  expect(result.current).toMatchObject({ choice: "system", theme: "light" });
  act(() => m.flip(true));
  expect(result.current.theme).toBe("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
  act(() => result.current.setChoice("light"));
  expect(result.current).toMatchObject({ choice: "light", theme: "light" });
});

it("works without matchMedia", () => {
  vi.stubGlobal("matchMedia", undefined);
  expect(resolveTheme("system")).toBe("light");
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd web && pnpm vitest run src/theme/theme.test.ts`
Expected: FAIL — `Failed to resolve import "./theme"`.

- [ ] **Step 3: Write `web/src/theme/theme.ts`**

```ts
import { useSyncExternalStore } from "react";

/** Spec §7.1: the reader's theme. "system" follows the computer's setting, live. */
export type ThemeChoice = "system" | "light" | "dark";
export type Theme = "light" | "dark";

export const THEME_KEY = "coglossa.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

// When the browser refuses storage (a private window, a policy), the choice lasts the session.
let memory: ThemeChoice | null = null;
const listeners = new Set<() => void>();

function isChoice(value: unknown): value is ThemeChoice {
  return value === "system" || value === "light" || value === "dark";
}

function media(): MediaQueryList | undefined {
  return typeof globalThis.matchMedia === "function" ? globalThis.matchMedia(DARK_QUERY) : undefined;
}

export function storedChoice(): ThemeChoice {
  if (memory !== null) return memory;
  try {
    const value = localStorage.getItem(THEME_KEY);
    return isChoice(value) ? value : "system";
  } catch {
    return "system";
  }
}

export function resolveTheme(choice: ThemeChoice, prefersDark = media()?.matches ?? false): Theme {
  if (choice === "system") return prefersDark ? "dark" : "light";
  return choice;
}

export function applyTheme(choice: ThemeChoice = storedChoice()): void {
  document.documentElement.dataset.theme = resolveTheme(choice);
}

export function setThemeChoice(choice: ThemeChoice): void {
  try {
    localStorage.setItem(THEME_KEY, choice);
    memory = null;
  } catch {
    memory = choice;
  }
  applyTheme(choice);
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const query = media();
  const onChange = () => {
    if (storedChoice() === "system") applyTheme("system");
    listener();
  };
  query?.addEventListener("change", onChange);
  return () => {
    listeners.delete(listener);
    query?.removeEventListener("change", onChange);
  };
}

export function useTheme(): {
  choice: ThemeChoice;
  theme: Theme;
  setChoice: (choice: ThemeChoice) => void;
} {
  const choice = useSyncExternalStore(subscribe, storedChoice);
  const theme = useSyncExternalStore(subscribe, () => resolveTheme(storedChoice()));
  return { choice, theme, setChoice: setThemeChoice };
}

export function resetThemeForTests(): void {
  memory = null;
}
```

- [ ] **Step 4: Run the theme tests**

Run: `cd web && pnpm vitest run src/theme/theme.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the failing no-flash test** — `web/src/theme/theme-init.test.ts`

```ts
import source from "../../public/theme-init.js?raw";

function run(stored: string | null | "throw", prefersDark: boolean | null): string | undefined {
  vi.stubGlobal("localStorage", {
    getItem: () => {
      if (stored === "throw") throw new Error("blocked");
      return stored;
    },
  });
  vi.stubGlobal(
    "matchMedia",
    prefersDark === null ? undefined : (q: string) => ({ matches: prefersDark && q.includes("dark") }),
  );
  delete document.documentElement.dataset.theme;
  new Function(source)();
  return document.documentElement.dataset.theme;
}

it.each([
  ["dark", false, "dark"],
  ["light", true, "light"],
  ["system", true, "dark"],
  [null, false, "light"],
  ["throw", true, "dark"],
  [null, null, "light"],
] as const)("stored %s, computer dark %s: %s", (stored, dark, expected) => {
  expect(run(stored, dark)).toBe(expected);
});
```

- [ ] **Step 6: Run it to see it fail**

Run: `cd web && pnpm vitest run src/theme/theme-init.test.ts`
Expected: FAIL — cannot resolve `../../public/theme-init.js?raw`.

- [ ] **Step 7: Write `web/public/theme-init.js`** (a plain script, not a module: it runs before the first paint)

```js
// Spec §7.1: apply the saved theme before the page draws, so it never flashes light.
// Keep in step with src/theme/theme.ts (THEME_KEY and the "system" rule).
(function () {
  var choice = "system";
  try {
    choice = localStorage.getItem("coglossa.theme") || "system";
  } catch {
    choice = "system";
  }
  if (choice !== "light" && choice !== "dark") {
    var dark = typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
    choice = dark ? "dark" : "light";
  }
  document.documentElement.dataset.theme = choice;
})();
```

In `web/index.html`, inside `<head>` after the viewport meta:

```html
    <script src="/theme-init.js"></script>
```

In `web/eslint.config.js`, add a block before the last one:

```js
  { files: ["public/**/*.js"], languageOptions: { globals: globals.browser } },
```

- [ ] **Step 8: Run it**

Run: `cd web && pnpm vitest run src/theme/theme-init.test.ts`
Expected: PASS (6 cases).

- [ ] **Step 9: Write the failing stylesheet tests**

`web/src/styles/contrast.test.ts`:

```ts
import tokens from "./tokens.css?raw";

/** The colour values of one theme block of tokens.css. */
function block(selector: string): Record<string, string> {
  const start = tokens.indexOf(selector);
  if (start < 0) throw new Error(`${selector} is missing from tokens.css`);
  const body = tokens.slice(tokens.indexOf("{", start) + 1, tokens.indexOf("}", start));
  return Object.fromEntries(
    [...body.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map((m) => [m[1]!, m[2]!]),
  );
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

// Every foreground / background pair the stylesheets use. Add a row when you add a pair.
const PAIRS: [string, string, number][] = [
  ["text", "bg", 4.5],
  ["text", "surface", 4.5],
  ["text", "surface-2", 4.5],
  ["text", "warn-tint", 4.5],
  ["text", "warn-tint-strong", 4.5],
  ["text", "selected", 4.5],
  ["text-muted", "bg", 4.5],
  ["text-muted", "surface", 4.5],
  ["text-muted", "surface-2", 4.5],
  ["text-muted", "selected", 4.5],
  ["accent", "bg", 4.5],
  ["accent", "surface", 4.5],
  ["accent", "surface-2", 4.5],
  ["accent-text", "accent", 4.5],
  ["ok", "bg", 4.5],
  ["ok", "surface", 4.5],
  ["warn", "bg", 4.5],
  ["warn", "surface", 4.5],
  ["warn", "warn-tint", 4.5],
  ["danger", "bg", 4.5],
  ["danger", "surface", 4.5],
  // statuses and links on the selected sidebar row, on headers, and on tinted passages
  ["ok", "selected", 4.5],
  ["warn", "selected", 4.5],
  ["danger", "selected", 4.5],
  ["accent", "selected", 4.5],
  ["ok", "surface-2", 4.5],
  ["warn", "surface-2", 4.5],
  ["danger", "surface-2", 4.5],
  ["warn", "warn-tint-strong", 4.5],
  ["text-muted", "warn-tint", 4.5],
  ["accent", "warn-tint", 4.5],
  ["border-strong", "bg", 3],
  ["border-strong", "surface", 3],
];

describe.each([
  ["light", ':root[data-theme="light"]'],
  ["dark", ':root[data-theme="dark"]'],
])("the %s theme", (_name, selector) => {
  const colours = block(selector);
  it.each(PAIRS)("%s on %s reaches %s:1", (fg, bg, min) => {
    expect(colours[fg], `--${fg}`).toBeDefined();
    expect(colours[bg], `--${bg}`).toBeDefined();
    expect(contrast(colours[fg]!, colours[bg]!)).toBeGreaterThanOrEqual(min);
  });
});
```

`web/src/styles/no-raw-colours.test.ts`:

```ts
const sheets = import.meta.glob("./*.css", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

// tokens.css defines the colours; app.css is the old stylesheet, deleted in Task 14.
const ALLOWED = new Set(["./tokens.css", "./app.css"]);
const RAW = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/;

it.each(Object.keys(sheets).filter((path) => !ALLOWED.has(path)))(
  "%s uses design values, not raw colours",
  (path) => {
    expect(sheets[path]).not.toMatch(RAW);
  },
);
```

- [ ] **Step 10: Run them to see them fail**

Run: `cd web && pnpm vitest run src/styles`
Expected: FAIL — `tokens.css` cannot be resolved.

- [ ] **Step 11: Write `web/src/styles/tokens.css`**

```css
/* Spec §7.2: every colour, space, radius, text size and shadow the app uses. Other stylesheets
   use these names only (no-raw-colours.test.ts); contrast.test.ts checks the colour pairs.
   theme-init.js sets data-theme before the first paint; without it the light set applies. */
:root,
:root[data-theme="light"] {
  color-scheme: light;
  --bg: #faf8f5;
  --surface: #ffffff;
  --surface-2: #f1eee9;
  --border: #ddd8cf;
  --border-strong: #8a8378;
  --text: #1f2328;
  --text-muted: #5c6370;
  --accent: #3554d1;
  --accent-text: #ffffff;
  --ok: #1a6f40;
  --warn: #8a5a00;
  --warn-tint: #fff4dc;
  --warn-tint-strong: #ffe7b0;
  --danger: #b42318;
  --selected: #e3e8fb;
  --shadow: 0 0.5rem 1.5rem rgb(0 0 0 / 12%);
}

:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #15171c;
  --surface: #1b1e25;
  --surface-2: #20242d;
  --border: #2f3542;
  --border-strong: #6b7385;
  --text: #e6e8ee;
  --text-muted: #9aa3b5;
  --accent: #7d97ff;
  --accent-text: #0d1020;
  --ok: #7fd49b;
  --warn: #f0c36a;
  --warn-tint: #2b2716;
  --warn-tint-strong: #3d3518;
  --danger: #ff8a80;
  --selected: #26304a;
  --shadow: 0 0.5rem 1.5rem rgb(0 0 0 / 45%);
}

:root {
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.5rem;
  --space-6: 2rem;
  --radius-1: 0.375rem;
  --radius-2: 0.625rem;
  --text-xs: 0.75rem;
  --text-sm: 0.875rem;
  --text-md: 1rem;
  --text-lg: 1.25rem;
  --text-xl: 1.5rem;
  --topbar-height: 3.25rem;
  --sidebar-width: 16.25rem;
  --panel-width: 23.75rem;
  --guide-width: 40rem;
}
```

- [ ] **Step 12: Write `web/src/styles/base.css`** (element defaults and the shared classes listed under Interfaces)

```css
/* Element defaults and shared classes, on the design values only (spec §7). */
*,
*::before,
*::after {
  box-sizing: border-box;
}

html {
  font-size: 100%;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  /* font-family: app.css's --font-stack until Task 14 moves it here */
  font-size: var(--text-md);
  line-height: 1.5;
}

h1 {
  font-size: var(--text-xl);
  margin: 0 0 var(--space-4);
}

h2 {
  font-size: var(--text-lg);
  margin: 0 0 var(--space-3);
}

h3 {
  font-size: var(--text-md);
  margin: var(--space-4) 0 var(--space-2);
}

a {
  color: var(--accent);
}

:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

button,
.button {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-1);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  cursor: pointer;
  text-decoration: none;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

button.primary,
.button.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-text);
  font-weight: 600;
}

button.quiet {
  border-color: transparent;
  background: transparent;
}

button.danger {
  border-color: var(--danger);
  color: var(--danger);
}

button.link {
  border: 0;
  padding: 0;
  background: none;
  color: var(--accent);
  text-decoration: underline;
}

input,
select,
textarea {
  font: inherit;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-1);
  padding: var(--space-2);
}

textarea {
  width: 100%;
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin-bottom: var(--space-3);
}

.row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.row.end {
  justify-content: flex-end;
}

.row.between {
  justify-content: space-between;
}

.muted {
  color: var(--text-muted);
}

.error {
  color: var(--danger);
}

.notice {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-2);
  padding: var(--space-3) var(--space-4);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}

.tone-ok {
  color: var(--ok);
}

.tone-warn {
  color: var(--warn);
}

.tone-busy {
  color: var(--accent);
}

.tone-bad {
  color: var(--danger);
}

.tone-none {
  color: var(--text-muted);
}

dialog.dialog {
  max-width: min(36rem, 92vw);
  border: 1px solid var(--border);
  border-radius: var(--radius-2);
  background: var(--surface);
  color: var(--text);
  box-shadow: var(--shadow);
  padding: var(--space-5);
}

dialog::backdrop {
  background: var(--bg);
  opacity: 0.7;
}

.menu {
  position: relative;
  display: inline-block;
}

.menu-list {
  position: absolute;
  right: 0;
  z-index: 20;
  min-width: 14rem;
  display: flex;
  flex-direction: column;
  padding: var(--space-1);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-1);
  box-shadow: var(--shadow);
}

.menu-list > a,
.menu-list > button {
  justify-content: flex-start;
  border: 0;
  border-radius: var(--radius-1);
  padding: var(--space-2) var(--space-3);
  background: transparent;
  color: var(--text);
  text-align: start;
  text-decoration: none;
}

.menu-list > a:hover,
.menu-list > button:hover {
  background: var(--surface-2);
}

.menu-heading {
  margin: var(--space-2) var(--space-3) 0;
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
}

details > summary {
  cursor: pointer;
  color: var(--accent);
}
```

In `web/src/main.tsx`, import the new sheets before `app.css` and apply the theme (main.tsx is outside coverage; `theme-init.js` already did this in a browser):

```ts
import "./fonts";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/app.css";
import { applyTheme } from "./theme/theme";
// ...existing imports...

applyTheme();
```

- [ ] **Step 13: Run every web gate**

Run: `cd web && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS, including every contrast case in both themes.

- [ ] **Step 14: Commit**

```bash
git add web/src/styles web/src/theme web/public/theme-init.js web/index.html web/src/main.tsx web/eslint.config.js
git commit -m "feat(web): design values with light and dark themes, applied before first paint"
```

---

### Task 3: Plain-language helpers

Pure functions the screens share: flag titles (spec §5.4), language names, tiers and recent languages (spec §4.1), sidebar status, step list and the summary banner (spec §4.2, §5.2).

**Files:**
- Create: `web/src/lib/flags.ts`, `web/src/lib/flags.test.ts`
- Create: `web/src/lib/languages.ts`, `web/src/lib/languages.test.ts`
- Create: `web/src/lib/status.ts`, `web/src/lib/status.test.ts`

**Interfaces:**
- Consumes: types from `web/src/api/types.ts` (`FlagCode`, `FlagStatus`, `ProjectInfo`, `RunDetail`, `RunProgress`, `RunState`, `TierTable`, `Reliability`); `isLanguageCode` from `web/src/lib/format.ts`.
- Produces:
  - `flags.ts`: `FLAG_TITLES: Record<FlagCode, string>`, `flagTitle(code: string): string`, `FLAG_STATUS_WORDS: Record<FlagStatus, string>`, `plural(n: number, word: string): string`.
  - `languages.ts`: `languageName(code: string, locale?: string): string`, `tierFor(t: TierTable, target: string, source: string | null): Reliability`, `lessPractised(t: TierTable | undefined, target: string, source: string | null): boolean`, `knownLanguages(t: TierTable | undefined, locale?: string): string[]`, `RECENT_KEY`, `recentLanguages(): string[]`, `rememberLanguage(code: string): void`, `resetRecentForTests(): void`.
  - `status.ts`: `type Tone = "ok" | "warn" | "busy" | "bad" | "none"`, `type Status = { text: string; symbol: string; tone: Tone }`, `setStatus(p: ProjectInfo): Status`, `runStateStatus(state: RunState): Status`, `STEPS: readonly string[]`, `stepIndex(state: RunState): number`, `percentDone(p: RunProgress): number`, `type Banner = { tone: "ok" | "neutral" | "bad"; text: string; action: "next" | "retry" | "again" | null; careful: string | null }`, `bannerFor(detail: RunDetail, targetName: string): Banner`.

- [ ] **Step 1: Write the failing tests**

`web/src/lib/flags.test.ts`:

```ts
import { FLAG_CODES } from "../review/filters";
import { FLAG_STATUS_WORDS, FLAG_TITLES, flagTitle, plural } from "./flags";

it("has a plain title for every engine flag code", () => {
  for (const code of FLAG_CODES) expect(FLAG_TITLES[code]).toMatch(/^[A-Z]/);
  expect(flagTitle("possible_omission")).toBe("Something may be missing");
  expect(flagTitle("engine_error")).toBe("Some checking didn't finish");
});

it("falls back for a code the app does not know yet", () => {
  expect(flagTitle("brand_new_code")).toBe("Worth a look");
});

it("words flag statuses and counts", () => {
  expect(FLAG_STATUS_WORDS).toEqual({
    open: "to check",
    accepted: "checked",
    dismissed: "not a problem",
    resolved: "fixed by the recheck",
  });
  expect(plural(1, "passage")).toBe("1 passage");
  expect(plural(3, "file")).toBe("3 files");
});
```

`web/src/lib/languages.test.ts`:

```ts
import { tiers } from "../test/fixtures";
import {
  knownLanguages,
  languageName,
  lessPractised,
  RECENT_KEY,
  recentLanguages,
  rememberLanguage,
  resetRecentForTests,
  tierFor,
} from "./languages";

beforeEach(() => {
  localStorage.clear();
  resetRecentForTests();
});

it("names languages in the reader's language, falling back to the code", () => {
  expect(languageName("es", "en")).toBe("Spanish");
  expect(languageName("pt_BR", "en")).toBe("Brazilian Portuguese");
  expect(languageName("es", "fr")).toBe("espagnol");
  expect(languageName("zz", "en")).toBe("zz");
  expect(languageName("english!", "en")).toBe("english!");
});

it("finds the tier from the pair, then the language, then the default", () => {
  // fixture: default B; languages en A, fr A, ar B, am C; pairs fr>en A, en>am C
  expect(tierFor(tiers, "en", "fr")).toBe("A");
  expect(tierFor(tiers, "am", null)).toBe("C");
  expect(tierFor(tiers, "de", null)).toBe("B");
  expect(lessPractised(tiers, "am", "en")).toBe(true);
  expect(lessPractised(tiers, "fr", null)).toBe(false);
  expect(lessPractised(undefined, "am", null)).toBe(false);
  expect(lessPractised(tiers, "", null)).toBe(false);
});

it("lists the install's languages by name", () => {
  expect(knownLanguages(tiers, "en")).toEqual(["am", "ar", "en", "fr"]); // Amharic, Arabic, …
  expect(knownLanguages(undefined)).toEqual([]);
});

it("keeps the five most recent languages, newest first", () => {
  for (const code of ["es", "fr", "de", "it", "pt", "es"]) rememberLanguage(code);
  expect(recentLanguages()).toEqual(["es", "pt", "it", "de", "fr"]);
  localStorage.setItem(RECENT_KEY, JSON.stringify(["en", "not a code!", 7]));
  resetRecentForTests();
  expect(recentLanguages()).toEqual(["en"]);
  localStorage.setItem(RECENT_KEY, "{broken");
  expect(recentLanguages()).toEqual([]);
});

it("recent languages survive storage that throws", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  rememberLanguage("es");
  expect(recentLanguages()).toEqual(["es"]);
});
```

`web/src/lib/status.test.ts`:

```ts
import { progress, project, runDetail } from "../test/fixtures";
import type { RunDetail } from "../api/types";
import { bannerFor, percentDone, runStateStatus, setStatus, stepIndex, STEPS } from "./status";

it("words a set's status for the sidebar", () => {
  expect(setStatus({ ...project, files_failed: 1 })).toEqual({
    text: "needs attention",
    symbol: "!",
    tone: "bad",
  });
  expect(setStatus({ ...project, files_translating: 1 }).text).toBe("translating…");
  expect(setStatus({ ...project, open_flags: 2 })).toEqual({
    text: "2 to check",
    symbol: "⚠",
    tone: "warn",
  });
  expect(setStatus({ ...project, open_flags: 0 })).toEqual({
    text: "ready",
    symbol: "✓",
    tone: "ok",
  });
  expect(setStatus({ ...project, open_flags: 0, files_ready: 0 }).text).toBe("no files yet");
});

it("words a file's run state", () => {
  expect(runStateStatus("translating").text).toBe("translating…");
  expect(runStateStatus("awaiting_guide_approval").text).toBe("waiting for you");
  expect(runStateStatus("complete").text).toBe("ready");
  expect(runStateStatus("complete_with_errors").text).toBe("ready");
  expect(runStateStatus("failed").text).toBe("needs attention");
  expect(runStateStatus("cancelled").text).toBe("stopped");
});

it("maps every run state onto the step list", () => {
  expect(STEPS).toEqual([
    "Waiting to start",
    "Reading your document",
    "Building the translation guide",
    "Translating",
    "Checking consistency",
    "Ready",
  ]);
  expect(stepIndex("created")).toBe(0);
  expect(stepIndex("profiling")).toBe(1);
  expect(stepIndex("awaiting_guide_approval")).toBe(2);
  expect(stepIndex("translating")).toBe(3);
  expect(stepIndex("sweeping")).toBe(4);
  expect(stepIndex("complete_with_errors")).toBe(5);
  expect(stepIndex("failed")).toBe(-1);
});

it("averages the three stages into one percentage", () => {
  expect(percentDone({ ...progress, chunks_total: 4, chunks_done: { translate: 3, reconcile: 2, score: 1 } })).toBe(50);
  expect(percentDone({ ...progress, chunks_total: 0, chunks_done: {} })).toBe(0);
  expect(percentDone({ ...progress, chunks_total: 2, chunks_done: { translate: 9 } })).toBe(33);
});

const finished = (patch: Partial<RunDetail["run"]>, stats: Partial<RunDetail["stats"]> = {}, extra: Partial<RunDetail> = {}): RunDetail => ({
  ...runDetail,
  ...extra,
  run: { ...runDetail.run, state: "complete", ...patch },
  stats: { spans: 6, open_flags: 0, flags_by_code: {}, edited_spans: 0, ...stats },
  progress: { ...runDetail.progress, retryable: false, ...(extra.progress ?? {}) },
});

it("chooses the banner for each outcome", () => {
  expect(bannerFor(finished({}), "Spanish")).toEqual({
    tone: "ok",
    text: "Looks good. 6 passages translated, nothing left to check.",
    action: null,
    careful: null,
  });
  expect(bannerFor(finished({}, { open_flags: 2 }), "Spanish")).toMatchObject({
    text: "Looks good. 2 are worth a quick look before you download.",
    action: "next",
  });
  expect(bannerFor(finished({}, { open_flags: 1 }), "Spanish").text).toBe(
    "Looks good. 1 is worth a quick look before you download.",
  );
  expect(bannerFor(finished({}, {}, { language_tier: "C" }), "Tongan").careful).toBe(
    "Check carefully: Coglossa has less practice with Tongan.",
  );
  expect(
    bannerFor(
      finished({ state: "complete_with_errors" }, { open_flags: 2, flags_by_code: { engine_error: 2 } }, { progress: { ...runDetail.progress, retryable: true } }),
      "Spanish",
    ),
  ).toMatchObject({
    tone: "neutral",
    text: "Ready. Some checking didn't finish on 2 passages; they're marked.",
    action: "retry",
  });
  // every unfinished passage dealt with: worded like a complete run
  expect(bannerFor(finished({ state: "complete_with_errors" }), "Spanish").text).toMatch(/^Looks good/);
  expect(bannerFor(finished({ state: "failed" }, {}, { progress: { ...runDetail.progress, retryable: true } }), "Spanish")).toEqual({
    tone: "bad",
    text: "This translation couldn't finish. Something went wrong while translating.",
    action: "retry",
    careful: null,
  });
  expect(bannerFor(finished({ state: "failed" }), "Spanish").action).toBe("again");
  expect(bannerFor(finished({ state: "cancelled" }), "Spanish")).toMatchObject({
    tone: "neutral",
    text: "Stopped before it finished.",
    action: "again",
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && pnpm vitest run src/lib`
Expected: FAIL — cannot resolve `./flags`, `./languages`, `./status`.

- [ ] **Step 3: Write `web/src/lib/flags.ts`**

```ts
import type { FlagCode, FlagStatus } from "../api/types";

/** Spec §5.4: the plain title of each engine flag. */
export const FLAG_TITLES: Record<FlagCode, string> = {
  possible_omission: "Something may be missing",
  low_meaning: "The meaning may have shifted",
  style_drift: "The style doesn't match the rest",
  terminology_violation: "Doesn't follow your translation guide",
  agent_disagreement: "The translators disagreed",
  cultural_or_wordplay: "Wordplay or a cultural reference",
  low_reliability_language: "Less-practised language: check carefully",
  unscored: "Couldn't be checked",
  engine_error: "Some checking didn't finish",
};

/** A code a newer engine added falls back to a neutral title, never breaking the panel. */
export function flagTitle(code: string): string {
  return (FLAG_TITLES as Record<string, string>)[code] ?? "Worth a look";
}

/** Spec §3.3. */
export const FLAG_STATUS_WORDS: Record<FlagStatus, string> = {
  open: "to check",
  accepted: "checked",
  dismissed: "not a problem",
  resolved: "fixed by the recheck",
};

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
```

- [ ] **Step 4: Write `web/src/lib/languages.ts`**

```ts
import type { Reliability, TierTable } from "../api/types";
import { isLanguageCode } from "./format";

const normal = (code: string) => code.replace(/_/g, "-");

/** Spec §4.1: a language's name in the reader's language, offline (Intl). The code when the
 * browser has no name for it. */
export function languageName(code: string, locale: string = navigator.language): string {
  try {
    const name = new Intl.DisplayNames([locale], { type: "language" }).of(normal(code));
    return name && name !== normal(code) ? name : code;
  } catch {
    return code;
  }
}

/** The pair's tier when a source is known, else the target's, else the table's default. */
export function tierFor(tiers: TierTable, target: string, source: string | null): Reliability {
  const pair = source === null ? undefined : tiers.pairs[`${source}>${target}`];
  return pair ?? tiers.languages[target] ?? tiers.default;
}

/** Tier C: the tier at which the engine adds its low-reliability flag. */
export function lessPractised(
  tiers: TierTable | undefined,
  target: string,
  source: string | null,
): boolean {
  return tiers !== undefined && target !== "" && tierFor(tiers, target, source) === "C";
}

/** Every language the tier table names, sorted by name. */
export function knownLanguages(tiers: TierTable | undefined, locale?: string): string[] {
  if (tiers === undefined) return [];
  const codes = new Set(Object.keys(tiers.languages));
  for (const pair of Object.keys(tiers.pairs)) for (const code of pair.split(">")) codes.add(code);
  return [...codes].sort((a, b) => languageName(a, locale).localeCompare(languageName(b, locale)));
}

export const RECENT_KEY = "coglossa.recentLanguages";
const RECENT_MAX = 5;
let memory: string[] | null = null; // when the browser refuses storage

export function recentLanguages(): string[] {
  if (memory !== null) return memory;
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((c): c is string => typeof c === "string" && isLanguageCode(c));
  } catch {
    return [];
  }
}

export function rememberLanguage(code: string): void {
  const next = [code, ...recentLanguages().filter((c) => c !== code)].slice(0, RECENT_MAX);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    memory = null;
  } catch {
    memory = next;
  }
}

export function resetRecentForTests(): void {
  memory = null;
}
```

- [ ] **Step 5: Write `web/src/lib/status.ts`**

```ts
import type { ProjectInfo, RunDetail, RunProgress, RunState } from "../api/types";
import { plural } from "./flags";

export type Tone = "ok" | "warn" | "busy" | "bad" | "none";
/** A status always carries words and a symbol, never colour alone (spec §7.2). */
export type Status = { text: string; symbol: string; tone: Tone };

const NEEDS_ATTENTION: Status = { text: "needs attention", symbol: "!", tone: "bad" };
const TRANSLATING: Status = { text: "translating…", symbol: "…", tone: "busy" };
const READY: Status = { text: "ready", symbol: "✓", tone: "ok" };

/** Spec §3.1: a set's row in the sidebar. */
export function setStatus(p: ProjectInfo): Status {
  if (p.files_failed > 0) return NEEDS_ATTENTION;
  if (p.files_translating > 0) return TRANSLATING;
  if (p.open_flags > 0) return { text: `${p.open_flags} to check`, symbol: "⚠", tone: "warn" };
  if (p.files_ready > 0) return READY;
  return { text: "no files yet", symbol: "", tone: "none" };
}

/** A file tab's status (spec §5.1): the run summary has its state only. */
export function runStateStatus(state: RunState): Status {
  switch (state) {
    case "complete":
    case "complete_with_errors":
      return READY;
    case "failed":
      return NEEDS_ATTENTION;
    case "cancelled":
      return { text: "stopped", symbol: "■", tone: "none" };
    case "awaiting_guide_approval":
      return { text: "waiting for you", symbol: "⚠", tone: "warn" };
    default:
      return TRANSLATING;
  }
}

/** Spec §4.2. */
export const STEPS = [
  "Waiting to start",
  "Reading your document",
  "Building the translation guide",
  "Translating",
  "Checking consistency",
  "Ready",
] as const;

const STEP_OF: Record<RunState, number> = {
  created: 0,
  ingesting: 1,
  profiling: 1,
  building_guide: 2,
  awaiting_guide_approval: 2,
  translating: 3,
  sweeping: 4,
  complete: 5,
  complete_with_errors: 5,
  failed: -1,
  cancelled: -1,
};

export function stepIndex(state: RunState): number {
  return STEP_OF[state];
}

const STAGES = ["translate", "reconcile", "score"] as const;

/** One percentage over the three chunk stages. */
export function percentDone(p: RunProgress): number {
  if (p.chunks_total <= 0) return 0;
  const done = STAGES.reduce(
    (sum, stage) => sum + Math.min(p.chunks_done[stage] ?? 0, p.chunks_total),
    0,
  );
  return Math.floor((100 * done) / (STAGES.length * p.chunks_total));
}

export type Banner = {
  tone: "ok" | "neutral" | "bad";
  text: string;
  action: "next" | "retry" | "again" | null;
  careful: string | null;
};

/** Spec §5.2 (and plan decisions 3 and 6). */
export function bannerFor(detail: RunDetail, targetName: string): Banner {
  const { run, stats, progress } = detail;
  if (run.state === "failed") {
    return {
      tone: "bad",
      text: "This translation couldn't finish. Something went wrong while translating.",
      action: progress.retryable ? "retry" : "again",
      careful: null,
    };
  }
  if (run.state === "cancelled") {
    return { tone: "neutral", text: "Stopped before it finished.", action: "again", careful: null };
  }
  const careful =
    detail.language_tier === "C"
      ? `Check carefully: Coglossa has less practice with ${targetName}.`
      : null;
  const unfinished = stats.flags_by_code["engine_error"] ?? 0;
  if (run.state === "complete_with_errors" && unfinished > 0) {
    return {
      tone: "neutral",
      text: `Ready. Some checking didn't finish on ${plural(unfinished, "passage")}; they're marked.`,
      action: progress.retryable ? "retry" : null,
      careful,
    };
  }
  if (stats.open_flags > 0) {
    const verb = stats.open_flags === 1 ? "is" : "are";
    return {
      tone: "ok",
      text: `Looks good. ${stats.open_flags} ${verb} worth a quick look before you download.`,
      action: "next",
      careful,
    };
  }
  return {
    tone: "ok",
    text: `Looks good. ${plural(stats.spans, "passage")} translated, nothing left to check.`,
    action: null,
    careful,
  };
}
```

- [ ] **Step 6: Run the tests**

Run: `cd web && pnpm vitest run src/lib`
Expected: PASS.

- [ ] **Step 7: Run every web gate and commit**

Run: `cd web && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src/lib
git commit -m "feat(web): plain-language helpers for flags, languages, statuses and the summary banner"
```

---
### Task 4: Set API hooks, set naming, the upload queue, and test helpers

Spec §4.1 (create, upload one after another, failures per file), §4.3 (Translate again), §4.4 (Continue as is), §5.4 step 4 (recheck), §8.2 (rename).

**Files:**
- Create: `web/src/lib/names.ts`, `web/src/lib/names.test.ts`
- Create: `web/src/api/sets.ts`, `web/src/api/sets.test.ts`
- Create: `web/src/upload/queue.ts`, `web/src/upload/queue.test.ts`
- Modify: `web/src/api/client.ts` (add `reportUrl`)
- Modify: `web/src/test/render.tsx` (add `queryWrapper`, `renderAt`)

**Interfaces:**
- Consumes: `client`, `unwrap`, `ApiError` (`api/client.ts`); `projectKeys`, `uploadDocument` (`api/projects.ts`); `runKeys`, `POLL_MS` (`api/runs.ts`); `guideChanged` (`api/guide.ts`); `isTerminal` (`lib/format.ts`); `messageOf` (`components/ErrorMessage.tsx`).
- Produces:
  - `lib/names.ts`: `withoutExtension(name: string): string`, `firstWords(text: string, count?: number): string`, `pastedFile(text: string): File`, `setNameFor(files: File[]): string`, `numbered(name: string, n: number): string`.
  - `api/sets.ts`: `MAX_NAME_TRIES = 50`, `SET_LIST_POLL_MS = 5000`, `createSet(body: CreateProject): Promise<ProjectInfo>`, `useSetList()`, `useSetDocuments(projectId)`, `useRenameProject(projectId)` (mutation of `name: string`), `useTranslateAgain(projectId)` (mutation of `documentId: string` → `RunSummary`), `useApproveAsIs(runId, projectId)` (mutation, no variables), `type RecheckResult = { started: string[]; skipped: string[] }`, `recheckSet(projectId): Promise<RecheckResult>`, `useRecheckSet(projectId)` (mutation, no variables → `RecheckResult`).
  - `upload/queue.ts`: `type UploadOptions = { target_lang: string; source_lang: string | null; pipeline_profile: PipelineProfile | null }`, `type UploadItem = { key: string; projectId: string; name: string; size: number; status: "waiting" | "uploading" | "done" | "failed"; error: string | null; runId: string | null }`, `enqueueUploads(projectId, files: File[], options: UploadOptions, queryClient: QueryClient): void`, `retryUpload(key: string): void`, `dismissUpload(key: string): void`, `useUploads(projectId: string): UploadItem[]`, `uploadErrorText(error: unknown): string`, `resetUploads(): void`.
  - `api/client.ts`: `reportUrl(runId: string): string`.
  - `test/render.tsx`: `queryWrapper(): { queryClient: QueryClient; wrapper: (props: { children: ReactNode }) => ReactElement }`, `renderAt(element: ReactElement, opts?: { path?: string; pattern?: string })` → `{ router, user, queryClient, ...RenderResult }`.

- [ ] **Step 1: Add the test helpers** to `web/src/test/render.tsx` (keep `renderApp`)

```tsx
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { makeQueryClient, Providers } from "../Providers";
import { routes } from "../routes";

// renderApp stays as it is.

/** For hook tests: a fresh query client and a wrapper that provides it. */
export function queryWrapper() {
  const queryClient = makeQueryClient({ retry: false });
  function wrapper({ children }: { children: ReactNode }) {
    return <Providers client={queryClient}>{children}</Providers>;
  }
  return { queryClient, wrapper };
}

/** Render one element at `path`, matched by `pattern` so useParams works. Any other address
 * renders "Somewhere else", so tests can follow a navigation through router.state. */
export function renderAt(
  element: ReactElement,
  { path = "/", pattern = "*" }: { path?: string; pattern?: string } = {},
) {
  const elsewhere = { path: "*", element: <p>Somewhere else</p> };
  const router = createMemoryRouter(
    pattern === "*" ? [{ path: "*", element }] : [{ path: pattern, element }, elsewhere],
    { initialEntries: [path] },
  );
  const queryClient = makeQueryClient({ retry: false });
  const user = userEvent.setup();
  const view = render(
    <Providers client={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { router, user, queryClient, ...view };
}
```

- [ ] **Step 2: Write the failing tests**

`web/src/lib/names.test.ts`:

```ts
import { firstWords, numbered, pastedFile, setNameFor, withoutExtension } from "./names";

it("names a set after its files", () => {
  expect(setNameFor([new File(["x"], "Welcome letter.docx")])).toBe("Welcome letter");
  const three = ["a.txt", "b.txt", "c.txt"].map((n) => new File(["x"], n));
  expect(setNameFor(three)).toBe("a and 2 more");
  expect(setNameFor([new File(["x"], ".env")])).toBe(".env");
  expect(setNameFor([])).toBe("Translation");
  expect(setNameFor([new File(["x"], `${"x".repeat(300)}.txt`)])).toHaveLength(180);
});

it("makes a text file from pasted text, named after its first five words", () => {
  const file = pastedFile("  Welcome to the Riverside: Community Centre!\nMore text");
  expect(file.name).toBe("Welcome to the Riverside Community.txt");
  expect(file.type).toBe("text/plain");
  expect(pastedFile("??? ***").name).toBe("Pasted text.txt");
  expect(firstWords(" one  two\nthree ", 2)).toBe("one two");
  expect(withoutExtension("a.b.c")).toBe("a.b");
});

it("numbers repeated names", () => {
  expect(numbered("welcome", 1)).toBe("welcome");
  expect(numbered("welcome", 2)).toBe("welcome (2)");
});
```

`web/src/api/sets.test.ts`:

```ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { documentSummary, progress, project, runSummary } from "../test/fixtures";
import { queryWrapper } from "../test/render";
import { server } from "../test/server";
import { ApiError } from "./client";
import { projectKeys } from "./projects";
import {
  createSet,
  recheckSet,
  useApproveAsIs,
  useRenameProject,
  useTranslateAgain,
} from "./sets";

const body = { name: "welcome", target_lang: "es", source_lang: null, pipeline_profile: null };

it("createSet retries with a number on a 409", async () => {
  const names: string[] = [];
  server.use(
    http.post("*/api/projects", async ({ request }) => {
      const sent = (await request.json()) as { name: string };
      names.push(sent.name);
      if (names.length < 3) {
        return HttpResponse.json({ detail: "a project named it already exists" }, { status: 409 });
      }
      return HttpResponse.json({ ...project, name: sent.name }, { status: 201 });
    }),
  );
  const created = await createSet(body);
  expect(names).toEqual(["welcome", "welcome (2)", "welcome (3)"]);
  expect(created.name).toBe("welcome (3)");
});

it("createSet stops at any other error, and after 50 tries", async () => {
  server.use(
    http.post("*/api/projects", () =>
      HttpResponse.json({ detail: "target_lang: bad" }, { status: 422 }),
    ),
  );
  await expect(createSet(body)).rejects.toThrow("target_lang: bad");
  let calls = 0;
  server.use(
    http.post("*/api/projects", () => {
      calls += 1;
      return HttpResponse.json({ detail: "taken" }, { status: 409 });
    }),
  );
  await expect(createSet(body)).rejects.toBeInstanceOf(ApiError);
  expect(calls).toBe(50);
});

it("recheckSet skips runs that are not finished and 409s", async () => {
  const swept: string[] = [];
  const doc = (id: string, runs: (typeof runSummary)[]) => ({ ...documentSummary, id, runs });
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([
        doc("d-a", [{ ...runSummary, id: "r-a", state: "complete" }]),
        doc("d-b", [{ ...runSummary, id: "r-b", state: "translating" }]),
        doc("d-c", [
          { ...runSummary, id: "r-c", state: "complete_with_errors" },
          { ...runSummary, id: "r-old", state: "complete" },
        ]),
        doc("d-d", []),
      ]),
    ),
    http.post("*/api/runs/:runId/sweep", ({ params }) => {
      swept.push(params["runId"] as string);
      if (params["runId"] === "r-c") {
        return HttpResponse.json({ detail: "the sweep is off" }, { status: 409 });
      }
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  expect(await recheckSet("p-1")).toEqual({ started: ["r-a"], skipped: ["r-b", "r-c"] });
  expect(swept).toEqual(["r-a", "r-c"]); // never an older run of a file
});

it("recheckSet passes on a real failure", async () => {
  server.use(
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])),
    http.post("*/api/runs/:runId/sweep", () => HttpResponse.json({}, { status: 500 })),
  );
  await expect(recheckSet("p-1")).rejects.toThrow("The server answered 500");
});

it("renames a set and updates the cached copy", async () => {
  let sent: unknown;
  server.use(
    http.patch("*/api/projects/p-1", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...project, name: "Letters" });
    }),
  );
  const { queryClient, wrapper } = queryWrapper();
  const { result } = renderHook(() => useRenameProject("p-1"), { wrapper });
  await act(() => result.current.mutateAsync("Letters"));
  expect(sent).toEqual({ name: "Letters" });
  expect(queryClient.getQueryData<typeof project>(projectKeys.one("p-1"))?.name).toBe("Letters");
});

it("translates a file again without pausing", async () => {
  let sent: unknown;
  server.use(
    http.post("*/api/documents/d-1/runs", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...runSummary, id: "r-2", state: "created" }, { status: 202 });
    }),
  );
  const { wrapper } = queryWrapper();
  const { result } = renderHook(() => useTranslateAgain("p-1"), { wrapper });
  const run = await act(() => result.current.mutateAsync("d-1"));
  expect(sent).toEqual({ checkpoint: false });
  expect(run.id).toBe("r-2");
});

it("approves a waiting guide as it is", async () => {
  let sent: unknown;
  server.use(
    http.post("*/api/runs/r-1/guide/approve", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ message: "approved", progress }, { status: 202 });
    }),
  );
  const { wrapper } = queryWrapper();
  const { result } = renderHook(() => useApproveAsIs("r-1", "p-1"), { wrapper });
  await act(() => result.current.mutateAsync());
  await waitFor(() => expect(sent).toEqual({}));
});
```

`web/src/upload/queue.test.ts`:

```ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { ApiError } from "../api/client";
import { runSummary } from "../test/fixtures";
import { NodeFileClass, nodeFormData } from "../test/node-web";
import { queryWrapper } from "../test/render";
import { server } from "../test/server";
import {
  dismissUpload,
  enqueueUploads,
  resetUploads,
  retryUpload,
  uploadErrorText,
  useUploads,
} from "./queue";

const options = { target_lang: "es", source_lang: null, pipeline_profile: null };
const file = (name: string) => new NodeFileClass(["Il pleut."], name, { type: "text/plain" });

beforeEach(async () => {
  resetUploads();
  vi.stubGlobal("FormData", await nodeFormData());
  vi.stubGlobal("File", NodeFileClass);
});

it("uploads one file at a time with checkpoint=false and reports each", async () => {
  const seen: string[] = [];
  server.use(
    http.post("*/api/projects/p-1/runs", async ({ request }) => {
      const form = await request.formData();
      seen.push(`${(form.get("file") as File).name}:${form.get("checkpoint")}`);
      if (seen.length === 2) {
        return HttpResponse.json({ detail: "b.txt is larger than the limit" }, { status: 413 });
      }
      return HttpResponse.json({ ...runSummary, id: `r-${seen.length}` }, { status: 202 });
    }),
  );
  const { queryClient, wrapper } = queryWrapper();
  const { result } = renderHook(() => useUploads("p-1"), { wrapper });
  act(() => enqueueUploads("p-1", [file("a.txt"), file("b.txt"), file("c.txt")], options, queryClient));
  await waitFor(() =>
    expect(result.current.map((u) => u.status)).toEqual(["done", "failed", "done"]),
  );
  expect(seen).toEqual(["a.txt:false", "b.txt:false", "c.txt:false"]);
  expect(result.current[0]!.runId).toBe("r-1");
  expect(result.current[1]).toMatchObject({ name: "b.txt", error: "Too large for this server." });

  act(() => retryUpload(result.current[1]!.key));
  await waitFor(() => expect(result.current[1]!.status).toBe("done"));
  act(() => dismissUpload(result.current[1]!.key));
  expect(result.current.map((u) => u.name)).toEqual(["a.txt", "c.txt"]);
});

it("keeps each set's uploads apart", async () => {
  server.use(
    http.post("*/api/projects/:id/runs", () =>
      HttpResponse.json(runSummary, { status: 202 }),
    ),
  );
  const { queryClient, wrapper } = queryWrapper();
  const { result } = renderHook(() => useUploads("p-2"), { wrapper });
  act(() => enqueueUploads("p-1", [file("a.txt")], options, queryClient));
  act(() => enqueueUploads("p-2", [file("z.txt")], options, queryClient));
  await waitFor(() => expect(result.current.map((u) => [u.name, u.status])).toEqual([["z.txt", "done"]]));
});

it("words upload errors", () => {
  expect(uploadErrorText(new ApiError(413, "x"))).toBe("Too large for this server.");
  expect(uploadErrorText(new ApiError(422, "notes.rtf: unsupported file type '.rtf'"))).toBe(
    "notes.rtf: unsupported file type '.rtf'",
  );
});
```

- [ ] **Step 3: Run them to see them fail**

Run: `cd web && pnpm vitest run src/lib/names.test.ts src/api/sets.test.ts src/upload`
Expected: FAIL — cannot resolve `./names`, `./sets`, `./queue`.

- [ ] **Step 4: Write `web/src/lib/names.ts`**

```ts
/** Characters a file name cannot hold on common systems. */
const NOT_IN_NAMES = '\\/:*?"<>|';

function cleanName(text: string): string {
  return [...text].filter((ch) => ch >= " " && !NOT_IN_NAMES.includes(ch)).join("");
}

export function withoutExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

export function firstWords(text: string, count = 5): string {
  return text.trim().split(/\s+/).slice(0, count).join(" ");
}

/** Spec §4.1: pasted text travels as a .txt file named after its first five words. */
export function pastedFile(text: string): File {
  const base = cleanName(firstWords(text)).replace(/\s+/g, " ").trim().slice(0, 80);
  return new File([text], `${base || "Pasted text"}.txt`, { type: "text/plain" });
}

/** Spec §4.1: the set is named after its file, or "<first> and N more". */
export function setNameFor(files: File[]): string {
  const first = withoutExtension(files[0]?.name ?? "").trim() || "Translation";
  const name = files.length > 1 ? `${first} and ${files.length - 1} more` : first;
  return name.slice(0, 180); // room for " (50)" under the API's 200
}

/** Plan decision 1: "welcome", "welcome (2)", … */
export function numbered(name: string, n: number): string {
  return n <= 1 ? name : `${name} (${n})`;
}
```

- [ ] **Step 5: Write `web/src/api/sets.ts`**

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isTerminal } from "../lib/format";
import { numbered } from "../lib/names";
import { ApiError, client, unwrap } from "./client";
import { guideChanged } from "./guide";
import { projectKeys } from "./projects";
import { POLL_MS, runKeys } from "./runs";
import type { CreateProject, ProjectInfo, RunState } from "./types";

export const MAX_NAME_TRIES = 50;
export const SET_LIST_POLL_MS = 5000;

/** Create a set (a project) under the first free name: plan decision 1. */
export async function createSet(body: CreateProject): Promise<ProjectInfo> {
  for (let n = 1; n <= MAX_NAME_TRIES; n++) {
    try {
      return await unwrap(client.POST("/api/projects", { body: { ...body, name: numbered(body.name, n) } }));
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 409)) throw error;
    }
  }
  throw new ApiError(409, "Too many translations share this name. Rename one and try again.");
}

/** The sidebar's list: refreshed while any set is translating. */
export function useSetList() {
  return useQuery({
    queryKey: projectKeys.all,
    queryFn: () => unwrap(client.GET("/api/projects")),
    refetchInterval: (query) =>
      query.state.data?.some((p) => p.files_translating > 0) ? SET_LIST_POLL_MS : false,
  });
}

/** A set's files: refreshed while the newest run of any file is under way. */
export function useSetDocuments(projectId: string) {
  return useQuery({
    queryKey: projectKeys.documents(projectId),
    queryFn: () =>
      unwrap(
        client.GET("/api/projects/{project_id}/documents", {
          params: { path: { project_id: projectId } },
        }),
      ),
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.runs[0] !== undefined && !isTerminal(d.runs[0].state))
        ? POLL_MS
        : false,
  });
}

export function useRenameProject(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      unwrap(
        client.PATCH("/api/projects/{project_id}", {
          params: { path: { project_id: projectId } },
          body: { name },
        }),
      ),
    onSuccess: (info) => {
      queryClient.setQueryData(projectKeys.one(projectId), info);
      return queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
    },
  });
}

/** Spec §4.3: a fresh run of a file, never pausing for the guide. */
export function useTranslateAgain(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      unwrap(
        client.POST("/api/documents/{document_id}/runs", {
          params: { path: { document_id: documentId } },
          body: { checkpoint: false },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
      return queryClient.invalidateQueries({ queryKey: projectKeys.documents(projectId) });
    },
  });
}

/** Spec §4.4: Continue as is, approving the guide the engine drafted. */
export function useApproveAsIs(runId: string, projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        client.POST("/api/runs/{run_id}/guide/approve", {
          params: { path: { run_id: runId } },
          body: {},
        }),
      ),
    onSuccess: () => {
      guideChanged(queryClient, projectId);
      return queryClient.invalidateQueries({ queryKey: runKeys.one(runId) });
    },
  });
}

const RECHECKABLE: readonly RunState[] = ["complete", "complete_with_errors"];

export type RecheckResult = { started: string[]; skipped: string[] };

/** Spec §5.4 step 4: re-run the sweep on the newest finished run of every file in the set. A
 * file still running, or one whose sweep cannot start (409), is skipped: it picks the guide up
 * when its own translation finishes. */
export async function recheckSet(projectId: string): Promise<RecheckResult> {
  const documents = await unwrap(
    client.GET("/api/projects/{project_id}/documents", {
      params: { path: { project_id: projectId } },
    }),
  );
  const result: RecheckResult = { started: [], skipped: [] };
  for (const doc of documents) {
    const run = doc.runs[0];
    if (run === undefined) continue;
    if (!RECHECKABLE.includes(run.state)) {
      result.skipped.push(run.id);
      continue;
    }
    try {
      await unwrap(client.POST("/api/runs/{run_id}/sweep", { params: { path: { run_id: run.id } } }));
      result.started.push(run.id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) result.skipped.push(run.id);
      else throw error;
    }
  }
  return result;
}

export function useRecheckSet(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => recheckSet(projectId),
    onSuccess: (result) => {
      for (const runId of result.started) {
        void queryClient.invalidateQueries({ queryKey: runKeys.one(runId), exact: true });
      }
      void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
      return queryClient.invalidateQueries({ queryKey: projectKeys.documents(projectId) });
    },
  });
}
```

Add to `web/src/api/client.ts`, after `exportUrl`:

```ts
export function reportUrl(runId: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/report`;
}
```

- [ ] **Step 6: Write `web/src/upload/queue.ts`**

```ts
import type { QueryClient } from "@tanstack/react-query";
import { useMemo, useSyncExternalStore } from "react";
import { ApiError } from "../api/client";
import { projectKeys, uploadDocument } from "../api/projects";
import type { PipelineProfile } from "../api/types";
import { messageOf } from "../components/ErrorMessage";

export type UploadOptions = {
  target_lang: string;
  source_lang: string | null;
  pipeline_profile: PipelineProfile | null;
};

export type UploadItem = {
  key: string;
  projectId: string;
  name: string;
  size: number;
  status: "waiting" | "uploading" | "done" | "failed";
  error: string | null;
  runId: string | null;
};

type Entry = UploadItem & { file: File; options: UploadOptions; queryClient: QueryClient };

// Spec §4.1: uploads outlive the New translation page (it navigates away at once), so they live
// here, in memory, one at a time (plan decision 7).
let entries: Entry[] = [];
let snapshot: UploadItem[] = [];
let running = false;
let counter = 0;
const listeners = new Set<() => void>();

function publish(): void {
  snapshot = entries.map(({ key, projectId, name, size, status, error, runId }) => ({
    key,
    projectId,
    name,
    size,
    status,
    error,
    runId,
  }));
  listeners.forEach((listener) => listener());
}

function update(entry: Entry, patch: Partial<UploadItem>): void {
  Object.assign(entry, patch);
  publish();
}

export function uploadErrorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 413) return "Too large for this server.";
  return messageOf(error);
}

async function drain(): Promise<void> {
  if (running) return;
  running = true;
  try {
    for (
      let next = entries.find((e) => e.status === "waiting");
      next !== undefined;
      next = entries.find((e) => e.status === "waiting")
    ) {
      const entry = next;
      update(entry, { status: "uploading" });
      try {
        const run = await uploadDocument(entry.projectId, {
          file: entry.file,
          ...entry.options,
          checkpoint: false,
          use_project_guide: false,
        });
        update(entry, { status: "done", runId: run.id });
        void entry.queryClient.invalidateQueries({ queryKey: projectKeys.documents(entry.projectId) });
        void entry.queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
      } catch (error) {
        update(entry, { status: "failed", error: uploadErrorText(error) });
      }
    }
  } finally {
    running = false;
  }
}

export function enqueueUploads(
  projectId: string,
  files: File[],
  options: UploadOptions,
  queryClient: QueryClient,
): void {
  for (const file of files) {
    counter += 1;
    entries.push({
      key: `upload-${counter}`,
      projectId,
      name: file.name,
      size: file.size,
      status: "waiting",
      error: null,
      runId: null,
      file,
      options,
      queryClient,
    });
  }
  publish();
  void drain();
}

export function retryUpload(key: string): void {
  const entry = entries.find((e) => e.key === key);
  if (entry?.status !== "failed") return;
  update(entry, { status: "waiting", error: null });
  void drain();
}

export function dismissUpload(key: string): void {
  entries = entries.filter((e) => e.key !== key);
  publish();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useUploads(projectId: string): UploadItem[] {
  const all = useSyncExternalStore(subscribe, () => snapshot);
  return useMemo(() => all.filter((item) => item.projectId === projectId), [all, projectId]);
}

export function resetUploads(): void {
  entries = [];
  running = false;
  publish();
}
```

- [ ] **Step 7: Run the tests**

Run: `cd web && pnpm vitest run src/lib/names.test.ts src/api/sets.test.ts src/upload`
Expected: PASS.

- [ ] **Step 8: Run every web gate and commit**

Run: `cd web && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src/lib/names.ts web/src/lib/names.test.ts web/src/api/sets.ts web/src/api/sets.test.ts web/src/api/client.ts web/src/upload web/src/test/render.tsx
git commit -m "feat(web): set hooks, unique set names, recheck and an in-memory upload queue"
```

---

### Task 5: The frame — header, sidebar, menus, dialogs, help

Spec §3.1, §7.3 (narrow screens), §7.4 (dialogs, menus, help overlay, keyboard). The old pages still render inside the new frame until Task 14; sidebar links point at `/t/:id`, which Task 14 routes.

**Files:**
- Create: `web/src/components/Dialog.tsx`, `Menu.tsx`, `StatusText.tsx`, `Sidebar.tsx`, `SetDialogs.tsx`, `HelpDialog.tsx`
- Create: `web/src/lib/keys.ts` (moved from `review/useFlagKeys.ts`), `web/src/lib/dates.ts`
- Create: `web/src/styles/shell.css`
- Modify: `web/src/components/Layout.tsx` (rewrite), `web/src/review/useFlagKeys.ts` (import `keyBelongsToControl`), `web/src/main.tsx` (import `shell.css`)
- Modify: `web/src/test/setup.ts` (jsdom polyfills), `web/src/test/server.ts` (default `GET /api/projects`)
- Test: `web/src/components/Layout.test.tsx` (rewrite), `Menu.test.tsx`, `Sidebar.test.tsx`, `web/src/lib/dates.test.ts`

**Interfaces:**
- Consumes: `useSetList`, `useRenameProject` (Task 4); `useDeleteProject` (`api/projects.ts`); `useMe` (`api/system.ts`); `useTheme` (Task 2); `setStatus`, `Status` (Task 3); `FLAG_TITLES` (Task 3).
- Produces:
  - `Dialog({ open, label, onClose, className?, children })` — a modal `<dialog>`; children render only while open; Esc calls `onClose`.
  - `Menu({ label, button, className?, children: (close: () => void) => ReactNode })` — items are `<button role="menuitem">` or `<a role="menuitem">`.
  - `StatusText({ status }: { status: Status })`.
  - `RenameDialog({ project, open, onClose })`, `DeleteDialog({ project, open, onClose })`, `renameErrorText(error: unknown): string`.
  - `HelpDialog({ open, onClose })`.
  - `keyBelongsToControl(target: EventTarget | null, key: string): boolean` (`lib/keys.ts`).
  - `dateGroup(iso, now?)`, `groupByDate(items, dateOf, now?)`, `whenText(iso: string, now?: Date, locale?: string): string` (`lib/dates.ts`).

- [ ] **Step 1: Add jsdom polyfills and the default projects handler**

`web/src/test/setup.ts`, after the imports:

```ts
// jsdom has no modal dialogs and no scrolling; the app uses both (spec §7.4).
HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
  this.open = true;
};
HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
  this.open = false;
};
Element.prototype.scrollIntoView ??= function () {};
```

`web/src/test/server.ts`:

```ts
// Every page renders the frame: it asks who is signed in and lists the sets.
export const server = setupServer(
  http.get("*/api/me", () => HttpResponse.json({ user: null })),
  http.get("*/api/projects", () => HttpResponse.json([])),
);
```

- [ ] **Step 2: Write the failing tests**

`web/src/lib/dates.test.ts`:

```ts
import { dateGroup, groupByDate, whenText } from "./dates";

const now = new Date("2026-10-16T15:00:00");
const ago = (hours: number) => new Date(now.getTime() - hours * 3600_000).toISOString();

it("groups by Today, This week and Earlier", () => {
  expect(dateGroup(ago(1), now)).toBe("Today");
  expect(dateGroup(ago(30), now)).toBe("This week");
  expect(dateGroup(ago(24 * 10), now)).toBe("Earlier");
  const groups = groupByDate([ago(200), ago(1), ago(2)], (iso) => iso, now);
  expect(groups.map((g) => [g.label, g.items.length])).toEqual([
    ["Today", 2],
    ["Earlier", 1],
  ]);
});

it("words how long ago something happened", () => {
  expect(whenText(ago(0), now, "en")).toBe("just now");
  expect(whenText(ago(0.05), now, "en")).toBe("3 minutes ago");
  expect(whenText(ago(5), now, "en")).toBe("5 hours ago");
  expect(whenText(ago(24 * 3), now, "en")).toBe("3 days ago");
  expect(whenText(ago(24 * 40), now, "en")).toBe(new Date(ago(24 * 40)).toLocaleDateString("en"));
});
```

`web/src/components/Menu.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Menu } from "./Menu";

function Example({ onPick }: { onPick: () => void }) {
  return (
    <>
      <p>Outside</p>
      <Menu label="Download" button="Download ▾">
        {(close) => (
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              onPick();
              close();
            }}
          >
            Word document
          </button>
        )}
      </Menu>
    </>
  );
}

it("opens, picks, and closes by Esc or a click outside", async () => {
  const user = userEvent.setup();
  const onPick = vi.fn();
  render(<Example onPick={onPick} />);
  const button = screen.getByRole("button", { name: "Download" });
  expect(button).toHaveAttribute("aria-expanded", "false");
  await user.click(button);
  expect(button).toHaveAttribute("aria-expanded", "true");
  await user.click(screen.getByRole("menuitem", { name: "Word document" }));
  expect(onPick).toHaveBeenCalled();
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  await user.click(button);
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  await user.click(button);
  await user.click(screen.getByText("Outside"));
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});
```

`web/src/components/Sidebar.test.tsx`:

```tsx
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { project } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { Sidebar } from "./Sidebar";

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString();
const sets = [
  { ...project, id: "p-1", name: "Welcome letter", last_activity_at: hoursAgo(0.1), open_flags: 2 },
  { ...project, id: "p-2", name: "Notice", last_activity_at: hoursAgo(24 * 30), open_flags: 0 },
  { ...project, id: "p-3", name: "Volunteer guide", last_activity_at: hoursAgo(0.2), files_translating: 1 },
];

function list(projects: unknown[]) {
  server.use(http.get("*/api/projects", () => HttpResponse.json(projects)));
}

it("lists sets newest first, grouped by date, with a status in words", async () => {
  list(sets);
  renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  const today = await screen.findByRole("region", { name: "Today" });
  const links = within(today).getAllByRole("link");
  expect(links.map((l) => l.textContent)).toEqual([
    "Welcome letter⚠ 2 to check",
    "Volunteer guide… translating…",
  ]);
  expect(links[0]).toHaveAttribute("href", "/t/p-1");
  expect(within(screen.getByRole("region", { name: "Earlier" })).getByText("ready")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "+ New translation" })).toHaveAttribute("href", "/");
  expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
});

it("offers search once there are more than 15 sets", async () => {
  const many = Array.from({ length: 16 }, (_, i) => ({
    ...project,
    id: `p-${i}`,
    name: i === 7 ? "Bishop's letter" : `Set ${i}`,
  }));
  list(many);
  const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  await user.type(await screen.findByRole("searchbox", { name: "Search your translations" }), "bishop");
  expect(screen.getAllByRole("link").filter((l) => l.getAttribute("href")?.startsWith("/t/"))).toHaveLength(1);
});

it("says where translations will appear when there are none", async () => {
  renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  expect(await screen.findByText("Your translations will appear here.")).toBeInTheDocument();
});

it("renames a set from its menu", async () => {
  list(sets);
  let sent: unknown;
  server.use(
    http.patch("*/api/projects/p-1", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...sets[0], name: "Letters" });
    }),
  );
  const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "More actions for Welcome letter" }));
  await user.click(screen.getByRole("menuitem", { name: "Rename" }));
  const dialog = screen.getByRole("dialog", { name: "Rename" });
  const field = within(dialog).getByLabelText("Name");
  await user.clear(field);
  await user.type(field, "Letters");
  await user.click(within(dialog).getByRole("button", { name: "Rename" }));
  expect(sent).toEqual({ name: "Letters" });
  expect(screen.queryByRole("dialog", { name: "Rename" })).not.toBeInTheDocument();
});

it("rename conflict shows a plain message", async () => {
  list(sets);
  server.use(
    http.patch("*/api/projects/p-1", () =>
      HttpResponse.json({ detail: "a project named 'Notice' already exists" }, { status: 409 }),
    ),
  );
  const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "More actions for Welcome letter" }));
  await user.click(screen.getByRole("menuitem", { name: "Rename" }));
  const dialog = screen.getByRole("dialog", { name: "Rename" });
  await user.clear(within(dialog).getByLabelText("Name"));
  await user.type(within(dialog).getByLabelText("Name"), "Notice");
  await user.click(within(dialog).getByRole("button", { name: "Rename" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Another translation already has this name.",
  );
});

it("deletes a set after confirming, leaving its page", async () => {
  list(sets);
  let confirm: string | null = null;
  server.use(
    http.delete("*/api/projects/p-1", ({ request }) => {
      confirm = new URL(request.url).searchParams.get("confirm");
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const { user, router } = renderAt(<Sidebar open={false} onNavigate={() => {}} />, {
    path: "/t/p-1",
  });
  await user.click(await screen.findByRole("button", { name: "More actions for Welcome letter" }));
  await user.click(screen.getByRole("menuitem", { name: "Delete…" }));
  const dialog = screen.getByRole("dialog", { name: "Delete" });
  expect(dialog).toHaveTextContent("Delete “Welcome letter”?");
  expect(dialog).toHaveTextContent("its 1 file");
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/"));
  expect(confirm).toBe("Welcome letter");
});
```

`web/src/components/Layout.test.tsx` — replace the first two tests and the "sends the start page" test, keep the rest, and add the new ones. Replace every `screen.getByRole("navigation", { name: "Main" })` with `screen.getByRole("banner")` (the header) and every `findByText("Signed in as ana")` with `findByText("ana")`:

```tsx
it("shows the header with the signed-in user, Settings and Help", async () => {
  server.use(http.get("*/api/me", () => HttpResponse.json({ user: "ana" })));
  renderApp("/nowhere");
  const header = screen.getByRole("banner");
  expect(await within(header).findByText("ana")).toBeInTheDocument();
  expect(within(header).getByRole("link", { name: "Coglossa" })).toHaveAttribute("href", "/");
  expect(within(header).getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  expect(screen.getByRole("navigation", { name: "Your translations" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
});

it("switches the theme from the header", async () => {
  localStorage.clear();
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  const { user } = renderApp("/nowhere");
  await user.click(screen.getByRole("button", { name: "Switch to the dark theme" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  await user.click(screen.getByRole("button", { name: "Switch to the light theme" }));
  expect(document.documentElement.dataset.theme).toBe("light");
});

it("opens help from the header and with the ? key", async () => {
  const { user } = renderApp("/nowhere");
  await user.click(screen.getByRole("button", { name: "Help" }));
  const help = screen.getByRole("dialog", { name: "Help" });
  expect(help).toHaveTextContent("Keyboard shortcuts");
  expect(help).toHaveTextContent("Something may be missing");
  await user.click(within(help).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog", { name: "Help" })).not.toBeInTheDocument();
  await user.keyboard("?");
  expect(screen.getByRole("dialog", { name: "Help" })).toBeInTheDocument();
});

it("folds the sidebar behind a button on narrow screens", async () => {
  const { user } = renderApp("/nowhere");
  const toggle = screen.getByRole("button", { name: "Your translations" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("navigation", { name: "Your translations" })).toHaveClass("open");
});
```

Delete the old tests "shows the navigation and the signed-in user" and "sends the start page to the projects list" (Task 14 re-tests `/`). Import `within` from `@testing-library/react`.

- [ ] **Step 3: Run them to see them fail**

Run: `cd web && pnpm vitest run src/components src/lib/dates.test.ts`
Expected: FAIL — `./Menu`, `./Sidebar`, `./dates` do not exist; Layout assertions fail.

- [ ] **Step 4: Write the small pieces**

`web/src/lib/keys.ts` — move `ACTIVATABLE`, `NOT_TEXT_INPUT` and `keyBelongsToControl` here unchanged from `web/src/review/useFlagKeys.ts`, export `keyBelongsToControl`, and in `useFlagKeys.ts` replace them with `import { keyBelongsToControl } from "../lib/keys";`.

`web/src/lib/dates.ts`:

```ts
export type DateGroup = "Today" | "This week" | "Earlier";
const DAY_MS = 24 * 3600_000;
const ORDER: DateGroup[] = ["Today", "This week", "Earlier"];

export function dateGroup(iso: string, now: Date = new Date()): DateGroup {
  const then = new Date(iso);
  if (then.toDateString() === now.toDateString()) return "Today";
  return now.getTime() - then.getTime() < 7 * DAY_MS ? "This week" : "Earlier";
}

export function groupByDate<T>(
  items: T[],
  dateOf: (item: T) => string,
  now: Date = new Date(),
): { label: DateGroup; items: T[] }[] {
  return ORDER.map((label) => ({
    label,
    items: items.filter((item) => dateGroup(dateOf(item), now) === label),
  })).filter((group) => group.items.length > 0);
}

/** "just now", "3 minutes ago", "5 hours ago", "3 days ago", or the date after a month. */
export function whenText(iso: string, now: Date = new Date(), locale?: string): string {
  const then = new Date(iso);
  const minutes = Math.round((now.getTime() - then.getTime()) / 60_000);
  if (minutes < 1) return "just now";
  const words = new Intl.RelativeTimeFormat(locale, { numeric: "always" });
  if (minutes < 60) return words.format(-minutes, "minute");
  if (minutes < 24 * 60) return words.format(-Math.round(minutes / 60), "hour");
  if (minutes < 30 * 24 * 60) return words.format(-Math.round(minutes / (24 * 60)), "day");
  return then.toLocaleDateString(locale);
}
```

`web/src/components/Dialog.tsx`:

```tsx
import { useEffect, useRef, type ReactNode } from "react";

type Props = {
  open: boolean;
  label: string;
  onClose: () => void;
  className?: string;
  children: ReactNode;
};

/** Spec §7.4: a modal <dialog>. The browser traps focus; Esc asks onClose, so a parent can
 * refuse (unsaved changes). Children render only while it is open. */
export function Dialog({ open, label, onClose, className = "dialog", children }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-label={label}
      className={className}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      {open ? children : null}
    </dialog>
  );
}
```

`web/src/components/Menu.tsx`:

```tsx
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

type Props = {
  label: string;
  button: ReactNode;
  className?: string;
  children: (close: () => void) => ReactNode;
};

/** Plan decision 4: a button that opens a list of actions; Esc or a click outside closes it. */
export function Menu({ label, button, className, children }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onPointer = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);
  return (
    <div ref={ref} className={className ? `menu ${className}` : "menu"}>
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
      >
        {button}
      </button>
      {open ? (
        <div id={id} role="menu" aria-label={label} className="menu-list">
          {children(() => setOpen(false))}
        </div>
      ) : null}
    </div>
  );
}
```

`web/src/components/StatusText.tsx`:

```tsx
import type { Status } from "../lib/status";

/** A status in words, with its symbol, coloured by tone (never colour alone). */
export function StatusText({ status }: { status: Status }) {
  return (
    <span className={`status tone-${status.tone}`}>
      {status.symbol ? <span aria-hidden="true">{status.symbol} </span> : null}
      {status.text}
    </span>
  );
}
```

- [ ] **Step 5: Write `web/src/components/SetDialogs.tsx`**

```tsx
import { useId, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { useDeleteProject } from "../api/projects";
import { useRenameProject } from "../api/sets";
import type { ProjectInfo } from "../api/types";
import { plural } from "../lib/flags";
import { Dialog } from "./Dialog";
import { ErrorMessage, messageOf } from "./ErrorMessage";

export function renameErrorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "Another translation already has this name.";
  }
  return messageOf(error);
}

type Props = { project: ProjectInfo; open: boolean; onClose: () => void };

function RenameForm({ project, onClose }: Omit<Props, "open">) {
  const rename = useRenameProject(project.id);
  const [name, setName] = useState(project.name);
  const id = useId();
  const trimmed = name.trim();
  function submit(event: FormEvent) {
    event.preventDefault();
    if (trimmed && trimmed !== project.name) rename.mutate(trimmed, { onSuccess: onClose });
  }
  return (
    <form onSubmit={submit}>
      <h2>Rename</h2>
      <div className="field">
        <label htmlFor={id}>Name</label>
        <input
          id={id}
          dir="auto"
          maxLength={200}
          value={name}
          autoFocus
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      {rename.isError ? (
        <p role="alert" className="error">
          {renameErrorText(rename.error)}
        </p>
      ) : null}
      <div className="row end">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="submit"
          className="primary"
          disabled={!trimmed || trimmed === project.name || rename.isPending}
        >
          Rename
        </button>
      </div>
    </form>
  );
}

export function RenameDialog({ project, open, onClose }: Props) {
  return (
    <Dialog open={open} label="Rename" onClose={onClose}>
      <RenameForm project={project} onClose={onClose} />
    </Dialog>
  );
}

function DeleteForm({ project, onClose }: Omit<Props, "open">) {
  const remove = useDeleteProject(project.id);
  const navigate = useNavigate();
  const location = useLocation();
  const leaving = location.pathname === `/t/${project.id}` || location.pathname.startsWith(`/t/${project.id}/`);
  return (
    <div>
      <h2>
        Delete “<span dir="auto">{project.name}</span>”?
      </h2>
      <p>
        This deletes the translation, its {plural(project.documents, "file")}, its translation
        guide and every review decision. It can&apos;t be undone.
      </p>
      {remove.isError ? <ErrorMessage error={remove.error} /> : null}
      <div className="row end">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="danger"
          disabled={remove.isPending}
          onClick={() =>
            remove.mutate(project.name, {
              onSuccess: () => {
                onClose();
                if (leaving) navigate("/");
              },
            })
          }
        >
          Delete
        </button>
      </div>
    </div>
  );
}

export function DeleteDialog({ project, open, onClose }: Props) {
  return (
    <Dialog open={open} label="Delete" onClose={onClose}>
      <DeleteForm project={project} onClose={onClose} />
    </Dialog>
  );
}
```

- [ ] **Step 6: Write `web/src/components/Sidebar.tsx`**

```tsx
import { useState } from "react";
import { Link, NavLink } from "react-router";
import { useSetList } from "../api/sets";
import type { ProjectInfo } from "../api/types";
import { groupByDate } from "../lib/dates";
import { setStatus } from "../lib/status";
import { ErrorMessage } from "./ErrorMessage";
import { Loading } from "./Loading";
import { Menu } from "./Menu";
import { DeleteDialog, RenameDialog } from "./SetDialogs";
import { StatusText } from "./StatusText";

export const SEARCH_FROM = 15;

function SetRow({ project, onNavigate }: { project: ProjectInfo; onNavigate: () => void }) {
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  return (
    <li className="set-row">
      <NavLink to={`/t/${project.id}`} className="set-link" onClick={onNavigate}>
        <span className="set-name" dir="auto">
          {project.name}
        </span>
        <StatusText status={setStatus(project)} />
      </NavLink>
      <Menu label={`More actions for ${project.name}`} button="⋯" className="row-menu">
        {(close) => (
          <>
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                close();
                setRenaming(true);
              }}
            >
              Rename
            </button>
            <button
              role="menuitem"
              type="button"
              className="danger"
              onClick={() => {
                close();
                setDeleting(true);
              }}
            >
              Delete…
            </button>
          </>
        )}
      </Menu>
      <RenameDialog project={project} open={renaming} onClose={() => setRenaming(false)} />
      <DeleteDialog project={project} open={deleting} onClose={() => setDeleting(false)} />
    </li>
  );
}

/** Spec §3.1: + New translation, then the sets, newest activity first. */
export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const sets = useSetList();
  const [search, setSearch] = useState("");
  const all = [...(sets.data ?? [])].sort(
    (a, b) => Date.parse(b.last_activity_at) - Date.parse(a.last_activity_at),
  );
  const term = search.trim().toLocaleLowerCase();
  const shown = term ? all.filter((p) => p.name.toLocaleLowerCase().includes(term)) : all;
  return (
    <nav aria-label="Your translations" className={open ? "sidebar open" : "sidebar"}>
      <Link to="/" className="button primary new" onClick={onNavigate}>
        + New translation
      </Link>
      {all.length > SEARCH_FROM ? (
        <input
          type="search"
          aria-label="Search your translations"
          placeholder="Search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      ) : null}
      {sets.isPending ? <Loading /> : null}
      {sets.isError ? <ErrorMessage error={sets.error} /> : null}
      {sets.data && all.length === 0 ? (
        <p className="muted">Your translations will appear here.</p>
      ) : null}
      {groupByDate(shown, (p) => p.last_activity_at).map((group) => (
        <section key={group.label} aria-label={group.label}>
          <h2 className="group">{group.label}</h2>
          <ul>
            {group.items.map((p) => (
              <SetRow key={p.id} project={p} onNavigate={onNavigate} />
            ))}
          </ul>
        </section>
      ))}
    </nav>
  );
}
```

- [ ] **Step 7: Write `web/src/components/HelpDialog.tsx`**

```tsx
import { FLAG_TITLES } from "../lib/flags";
import { Dialog } from "./Dialog";

/** Spec §7.4: the shortcuts, statuses and flag titles on one short page. */
export function HelpDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} label="Help" onClose={onClose} className="dialog help">
      <h2>Help</h2>
      <h3>Keyboard shortcuts</h3>
      <dl className="shortcuts">
        <dt><kbd>n</kbd></dt>
        <dd>Next passage to check</dd>
        <dt><kbd>p</kbd></dt>
        <dd>Previous passage to check</dd>
        <dt><kbd>Enter</kbd></dt>
        <dd>Open or close the selected passage</dd>
        <dt><kbd>Esc</kbd></dt>
        <dd>Close the passage, a menu or this window</dd>
        <dt><kbd>?</kbd></dt>
        <dd>Open this help</dd>
      </dl>
      <h3>What the statuses mean</h3>
      <ul>
        <li>✓ ready: translated, nothing left to check.</li>
        <li>⚠ 2 to check: passages worth a look before you download.</li>
        <li>… translating: still working. You can leave the page.</li>
        <li>! needs attention: a file couldn&apos;t finish.</li>
      </ul>
      <h3>Why a passage is marked</h3>
      <ul>
        {Object.values(FLAG_TITLES).map((title) => (
          <li key={title}>{title}</li>
        ))}
      </ul>
      <div className="row end">
        <button type="button" className="primary" onClick={onClose}>
          Close
        </button>
      </div>
    </Dialog>
  );
}
```

- [ ] **Step 8: Rewrite `web/src/components/Layout.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet } from "react-router";
import { useMe } from "../api/system";
import { keyBelongsToControl } from "../lib/keys";
import { useTheme } from "../theme/theme";
import { ErrorMessage } from "./ErrorMessage";
import { HelpDialog } from "./HelpDialog";
import { Sidebar } from "./Sidebar";

/** Spec §3.1: the header, the sidebar and the main area. */
export function Layout() {
  const me = useMe();
  const { theme, setChoice } = useTheme();
  const [help, setHelp] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const dark = theme === "dark";
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "?" && !keyBelongsToControl(event.target, event.key)) setHelp(true);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="shell">
      <header className="topbar">
        <div className="row">
          <button
            type="button"
            className="quiet narrow-only"
            aria-expanded={navOpen}
            onClick={() => setNavOpen(!navOpen)}
          >
            Your translations
          </button>
          <Link to="/" className="brand">
            Coglossa
          </Link>
        </div>
        <div className="topbar-actions">
          <button
            type="button"
            className="quiet"
            aria-label={dark ? "Switch to the light theme" : "Switch to the dark theme"}
            onClick={() => setChoice(dark ? "light" : "dark")}
          >
            <span aria-hidden="true">{dark ? "☀" : "☾"}</span>
          </button>
          <button type="button" className="quiet" onClick={() => setHelp(true)}>
            Help
          </button>
          <NavLink to="/settings">Settings</NavLink>
          {me.data?.user ? <span className="user">{me.data.user}</span> : null}
          {me.data?.logout_url ? (
            <form method="post" action={me.data.logout_url} className="signout">
              <button type="submit" className="link">
                Sign out
              </button>
            </form>
          ) : null}
        </div>
      </header>
      {me.isError ? <ErrorMessage error={me.error} /> : null}
      <div className="workspace">
        <Sidebar open={navOpen} onNavigate={() => setNavOpen(false)} />
        <main className="main">
          <Outlet />
        </main>
      </div>
      <HelpDialog open={help} onClose={() => setHelp(false)} />
    </div>
  );
}
```

- [ ] **Step 9: Write `web/src/styles/shell.css`** and import it in `main.tsx` after `base.css`

```css
/* Spec §3.1, §7.3: the frame. */
.shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  height: var(--topbar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: 0 var(--space-4);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

.brand {
  font-weight: 700;
  color: var(--text);
  text-decoration: none;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.topbar-actions .user {
  color: var(--text-muted);
}

.workspace {
  flex: 1;
  display: flex;
  min-height: 0;
}

.sidebar {
  position: sticky;
  top: var(--topbar-height);
  width: var(--sidebar-width);
  height: calc(100vh - var(--topbar-height));
  flex-shrink: 0;
  overflow-y: auto;
  padding: var(--space-3);
  background: var(--surface);
  border-right: 1px solid var(--border);
}

.sidebar .new {
  width: 100%;
  justify-content: center;
  margin-bottom: var(--space-3);
}

.sidebar input[type="search"] {
  width: 100%;
  margin-bottom: var(--space-3);
}

.sidebar .group {
  margin: var(--space-3) 0 var(--space-1);
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
}

.sidebar ul {
  list-style: none;
  margin: 0;
  padding: 0;
}

.set-row {
  display: flex;
  align-items: center;
  border-radius: var(--radius-1);
}

.set-row:hover {
  background: var(--surface-2);
}

.set-link {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: var(--space-2);
  border-radius: var(--radius-1);
  color: var(--text);
  text-decoration: none;
}

.set-link.active {
  background: var(--selected);
}

.set-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status {
  font-size: var(--text-sm);
}

.row-menu > button {
  border-color: transparent;
  background: transparent;
}

.main {
  flex: 1;
  min-width: 0;
  padding: var(--space-5) var(--space-6);
}

.shortcuts {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--space-1) var(--space-4);
}

.shortcuts dd {
  margin: 0;
}

kbd {
  padding: 0 var(--space-1);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-1);
  background: var(--surface-2);
  font-size: var(--text-sm);
}

.narrow-only {
  display: none;
}

@media (max-width: 899px) {
  .narrow-only {
    display: inline-flex;
  }

  .sidebar {
    position: fixed;
    left: 0;
    z-index: 15;
    display: none;
    box-shadow: var(--shadow);
  }

  .sidebar.open {
    display: block;
  }

  .main {
    padding: var(--space-4);
  }
}
```

- [ ] **Step 10: Run the tests; fix old page tests the sidebar disturbs**

Run: `cd web && pnpm vitest run`
Expected: the new tests PASS. If an old page test (`ProjectsPage.test.tsx`, `ProjectPage.test.tsx`, …) now fails because the sidebar shows the same project name, scope that query to the page: `within(screen.getByRole("main")).getByRole(...)`. Change nothing else in old tests; Task 14 deletes them.

- [ ] **Step 11: Run every web gate and commit**

Run: `cd web && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src
git commit -m "feat(web): new frame with sidebar, theme toggle, help, menus and set dialogs"
```

---
### Task 6: Language picker and drop area

Spec §4.1 items 1, 3, 4. Two self-contained controls the New translation screen (Task 7) and Add files (Task 12) use.

**Files:**
- Create: `web/src/translate/LanguagePicker.tsx`, `web/src/translate/LanguagePicker.test.tsx`
- Create: `web/src/translate/DropArea.tsx`, `web/src/translate/DropArea.test.tsx`
- Create: `web/src/styles/translate.css` (import it in `main.tsx` after `shell.css`)

**Interfaces:**
- Consumes: `languageName`, `knownLanguages`, `recentLanguages` (Task 3); `LanguageInput` (`components/LanguageInput.tsx`, unchanged); `TierTable` type.
- Produces:
  - `LanguagePicker({ label, value, onChange, tiers, allowDetect? }: { label: string; value: string; onChange: (code: string) => void; tiers: TierTable | undefined; allowDetect?: boolean })`. `value === ""` means "Detect automatically" (with `allowDetect`) or "nothing chosen yet".
  - `ACCEPTED: string[]`, `sizeText(bytes: number): string`, `fileProblem(file: File, maxMb: number | null): string | null`, `DropArea({ files, onFiles, maxMb }: { files: File[]; onFiles: (files: File[]) => void; maxMb: number | null })`.

- [ ] **Step 1: Write the failing tests**

`web/src/translate/LanguagePicker.test.tsx`:

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { rememberLanguage, resetRecentForTests } from "../lib/languages";
import { tiers } from "../test/fixtures";
import { LanguagePicker } from "./LanguagePicker";

function Harness({ initial = "", detect = false }: { initial?: string; detect?: boolean }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <LanguagePicker label="To" value={value} onChange={setValue} tiers={tiers} allowDetect={detect} />
      <output>{value}</output>
    </>
  );
}

beforeEach(() => {
  localStorage.clear();
  resetRecentForTests();
});

it("offers recent languages, then the install's by name, then Other", async () => {
  rememberLanguage("es");
  const user = userEvent.setup();
  render(<Harness />);
  const select = screen.getByLabelText("To");
  const [recent, all] = within(select).getAllByRole("group");
  expect(recent).toHaveAttribute("label", "Recent");
  expect(within(recent!).getAllByRole("option").map((o) => o.textContent)).toEqual(["Spanish"]);
  expect(within(all!).getAllByRole("option").map((o) => o.textContent)).toEqual([
    "Amharic",
    "Arabic",
    "English",
    "French",
  ]);
  expect(within(select).getByRole("option", { name: "Choose a language" })).toBeDisabled();
  await user.selectOptions(select, "fr");
  expect(screen.getByRole("status")).toHaveTextContent("fr");
});

it("takes any code under Other language", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.selectOptions(screen.getByLabelText("To"), "Other language…");
  const code = screen.getByLabelText("To: language code");
  await user.type(code, "to");
  expect(screen.getByRole("status")).toHaveTextContent("to");
  await user.clear(code);
  await user.type(code, "english!");
  expect(screen.getByText(/Use a language code/)).toBeInTheDocument();
});

it("shows a code it does not list under Other", () => {
  render(<Harness initial="haw" />);
  expect(screen.getByLabelText("To")).toHaveValue("__other__");
  expect(screen.getByLabelText("To: language code")).toHaveValue("haw");
});

it("can offer automatic detection", () => {
  render(<Harness detect />);
  expect(screen.getByLabelText("To")).toHaveDisplayValue("Detect automatically");
});
```

(`<output>` has the implicit role `status`.)

`web/src/translate/DropArea.test.tsx`:

```tsx
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { DropArea, fileProblem, sizeText } from "./DropArea";

function Harness({ maxMb = 4 }: { maxMb?: number | null }) {
  const [files, setFiles] = useState<File[]>([]);
  return <DropArea files={files} onFiles={setFiles} maxMb={maxMb} />;
}

it("collects chosen files as chips that can be removed", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.upload(screen.getByLabelText("Choose files"), [
    new File(["Il pleut."], "Welcome letter.docx"),
    new File(["x"], "notice.pdf"),
  ]);
  const chips = screen.getByRole("list", { name: "Chosen files" });
  expect(within(chips).getAllByRole("listitem")).toHaveLength(2);
  expect(chips).toHaveTextContent("Welcome letter.docx");
  await user.click(screen.getByRole("button", { name: "Remove notice.pdf" }));
  expect(within(chips).getAllByRole("listitem")).toHaveLength(1);
  expect(screen.getByText(/up to 4 MB each/)).toBeInTheDocument();
});

it("takes dropped files and marks the ones it cannot use", () => {
  render(<Harness maxMb={1} />);
  const area = screen.getByText("Drop files here, or click to choose").closest("label")!;
  fireEvent.dragOver(area);
  expect(area).toHaveClass("over");
  fireEvent.drop(area, {
    dataTransfer: {
      files: [
        new File(["x"], "notes.rtf"),
        new File([new Uint8Array(2 * 1024 * 1024)], "big.pdf"),
        new File([], "empty.txt"),
      ],
    },
  });
  expect(area).not.toHaveClass("over");
  expect(screen.getByText(/notes\.rtf isn't a file type Coglossa reads/)).toBeInTheDocument();
  expect(screen.getByText("big.pdf is 2 MB, and the limit is 1 MB.")).toBeInTheDocument();
  expect(screen.getByText("empty.txt is empty.")).toBeInTheDocument();
});

it("words sizes and skips the limit when the server's is unknown", () => {
  expect(sizeText(10)).toBe("1 KB");
  expect(sizeText(300 * 1024)).toBe("300 KB");
  expect(sizeText(1.5 * 1024 * 1024)).toBe("1.5 MB");
  expect(fileProblem(new File([new Uint8Array(10)], "a.txt"), null)).toBeNull();
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && pnpm vitest run src/translate`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write `web/src/translate/LanguagePicker.tsx`**

```tsx
import { useId, useMemo, useState } from "react";
import type { TierTable } from "../api/types";
import { LanguageInput } from "../components/LanguageInput";
import { knownLanguages, languageName, recentLanguages } from "../lib/languages";

const OTHER = "__other__";

type Props = {
  label: string;
  value: string;
  onChange: (code: string) => void;
  tiers: TierTable | undefined;
  allowDetect?: boolean;
};

/** Spec §4.1: languages by name, recent first, then the install's; Other takes any code. */
export function LanguagePicker({ label, value, onChange, tiers, allowDetect = false }: Props) {
  const id = useId();
  const recent = useMemo(() => recentLanguages(), []);
  const known = useMemo(
    () => knownLanguages(tiers).filter((code) => !recent.includes(code)),
    [tiers, recent],
  );
  const listed = value === "" || recent.includes(value) || known.includes(value);
  const [other, setOther] = useState(!listed);
  return (
    <div className="field language-picker">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        value={other ? OTHER : value}
        onChange={(e) => {
          const next = e.target.value;
          setOther(next === OTHER);
          onChange(next === OTHER ? "" : next);
        }}
      >
        {allowDetect ? (
          <option value="">Detect automatically</option>
        ) : (
          <option value="" disabled>
            Choose a language
          </option>
        )}
        {recent.length > 0 ? (
          <optgroup label="Recent">
            {recent.map((code) => (
              <option key={code} value={code}>
                {languageName(code)}
              </option>
            ))}
          </optgroup>
        ) : null}
        {known.length > 0 ? (
          <optgroup label="All languages">
            {known.map((code) => (
              <option key={code} value={code}>
                {languageName(code)}
              </option>
            ))}
          </optgroup>
        ) : null}
        <option value={OTHER}>Other language…</option>
      </select>
      {other ? (
        <LanguageInput label={`${label}: language code`} value={value} onChange={onChange} />
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Write `web/src/translate/DropArea.tsx`**

```tsx
import { useId, useState } from "react";

export const ACCEPTED = [".txt", ".md", ".markdown", ".docx", ".html", ".htm", ".xhtml", ".epub", ".pdf"];

const MB = 1024 * 1024;

export function sizeText(bytes: number): string {
  if (bytes < MB) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / MB).toFixed(1).replace(/\.0$/, "")} MB`;
}

function accepted(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && ACCEPTED.includes(name.slice(dot).toLowerCase());
}

/** Spec §4.1: what is wrong with a file, before it is uploaded; null when nothing is. */
export function fileProblem(file: File, maxMb: number | null): string | null {
  if (!accepted(file.name)) {
    return `${file.name} isn't a file type Coglossa reads (Word, PDF, text, Markdown, HTML or EPUB).`;
  }
  if (file.size === 0) return `${file.name} is empty.`;
  if (maxMb !== null && file.size > maxMb * MB) {
    return `${file.name} is ${sizeText(file.size)}, and the limit is ${maxMb} MB.`;
  }
  return null;
}

type Props = { files: File[]; onFiles: (files: File[]) => void; maxMb: number | null };

export function DropArea({ files, onFiles, maxMb }: Props) {
  const [over, setOver] = useState(false);
  const inputId = useId();
  const add = (list: FileList | File[] | null) => {
    if (list && list.length > 0) onFiles([...files, ...Array.from(list)]);
  };
  return (
    <div className="drop">
      <label
        htmlFor={inputId}
        className={over ? "drop-area over" : "drop-area"}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          add(e.dataTransfer.files);
        }}
      >
        <strong>Drop files here, or click to choose</strong>
        <span className="muted">
          Word, PDF, text, Markdown, HTML or EPUB{maxMb !== null ? ` · up to ${maxMb} MB each` : ""}
        </span>
      </label>
      <input
        id={inputId}
        type="file"
        multiple
        accept={ACCEPTED.join(",")}
        aria-label="Choose files"
        className="visually-hidden"
        onChange={(e) => {
          add(e.target.files);
          e.target.value = "";
        }}
      />
      {files.length > 0 ? (
        <ul aria-label="Chosen files" className="chips">
          {files.map((file, i) => {
            const problem = fileProblem(file, maxMb);
            return (
              <li key={`${file.name}-${i}`} className={problem ? "chip bad" : "chip"}>
                <span dir="auto">{file.name}</span> <span className="muted">{sizeText(file.size)}</span>
                {problem ? <span className="error"> {problem}</span> : null}
                <button
                  type="button"
                  className="quiet"
                  aria-label={`Remove ${file.name}`}
                  onClick={() => onFiles(files.filter((_, k) => k !== i))}
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 5: Write `web/src/styles/translate.css`**

```css
/* Spec §4.1: starting a translation. */
.new-translation {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.drop-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  min-height: 14rem;
  padding: var(--space-6);
  border: 2px dashed var(--border-strong);
  border-radius: var(--radius-2);
  background: var(--surface);
  cursor: pointer;
  text-align: center;
}

.drop-area.over {
  border-color: var(--accent);
  background: var(--selected);
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  list-style: none;
  margin: var(--space-3) 0 0;
  padding: 0;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-1);
  background: var(--surface);
}

.chip.bad {
  border-color: var(--danger);
}

.tabs {
  display: flex;
  gap: var(--space-1);
  border-bottom: 1px solid var(--border);
}

.tabs [role="tab"] {
  border: 0;
  border-bottom: 3px solid transparent;
  border-radius: 0;
  background: transparent;
}

.tabs [role="tab"][aria-selected="true"] {
  border-bottom-color: var(--accent);
  font-weight: 600;
}

.languages {
  align-items: flex-end;
  gap: var(--space-4);
}

.language-picker select {
  min-width: 16rem;
}

.translate {
  align-self: flex-start;
  padding: var(--space-3) var(--space-6);
  font-size: var(--text-lg);
}
```

- [ ] **Step 6: Run the tests, every web gate, and commit**

Run: `cd web && pnpm vitest run src/translate && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src/translate web/src/styles/translate.css web/src/main.tsx
git commit -m "feat(web): language picker by name and a drop area that checks files first"
```

---

### Task 7: The New translation screen

Spec §4.1. Not routed yet (Task 14 puts it at `/`); its tests render it directly.

**Files:**
- Create: `web/src/translate/NewTranslationPage.tsx`, `web/src/translate/NewTranslationPage.test.tsx`

**Interfaces:**
- Consumes: `createSet` (Task 4), `enqueueUploads` (Task 4), `pastedFile`, `setNameFor` (Task 4), `LanguagePicker`, `DropArea`, `fileProblem` (Task 6), `lessPractised`, `languageName`, `rememberLanguage` (Task 3), `useTiers`, `useConfig` (`api/system.ts`), `languageOk` (`components/LanguageInput.tsx`).
- Produces: `NewTranslationPage()`, `LENGTHS: { value: PipelineProfile | ""; label: string }[]`, `uploadLimit(config: ConfigView | undefined): number | null`.

- [ ] **Step 1: Write the failing test** — `web/src/translate/NewTranslationPage.test.tsx`

```tsx
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { recentLanguages, resetRecentForTests } from "../lib/languages";
import { configView, project, tiers } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { enqueueUploads } from "../upload/queue";
import { NewTranslationPage, uploadLimit } from "./NewTranslationPage";

vi.mock("../upload/queue", () => ({ enqueueUploads: vi.fn() }));

let created: unknown[] = [];

beforeEach(() => {
  created = [];
  localStorage.clear();
  resetRecentForTests();
  vi.mocked(enqueueUploads).mockClear();
  server.use(
    http.get("*/api/system/tiers", () => HttpResponse.json(tiers)),
    http.get("*/api/system/config", () =>
      HttpResponse.json({ ...configView, config: { api: { max_upload_mb: 4 } } }),
    ),
    http.post("*/api/projects", async ({ request }) => {
      const body = (await request.json()) as { name: string };
      created.push(body);
      return HttpResponse.json({ ...project, id: "p-9", name: body.name }, { status: 201 });
    }),
  );
});

it("starts a translation from chosen files and opens it", async () => {
  const { user, router } = renderAt(<NewTranslationPage />);
  expect(screen.getByRole("heading", { name: "New translation" })).toBeInTheDocument();
  const translate = screen.getByRole("button", { name: "Translate" });
  expect(translate).toBeDisabled();
  await user.upload(screen.getByLabelText("Choose files"), [
    new File(["Il pleut."], "Welcome letter.docx"),
  ]);
  await screen.findByRole("option", { name: "English" });
  await user.selectOptions(screen.getByLabelText("To"), "en");
  await user.click(translate);
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-9"));
  expect(created).toEqual([
    { name: "Welcome letter", target_lang: "en", source_lang: null, pipeline_profile: null },
  ]);
  expect(enqueueUploads).toHaveBeenCalledWith(
    "p-9",
    [expect.objectContaining({ name: "Welcome letter.docx" })],
    { target_lang: "en", source_lang: null, pipeline_profile: null },
    expect.anything(),
  );
  expect(recentLanguages()).toEqual(["en"]);
});

it("translates pasted text with a chosen source and length", async () => {
  const { user, router } = renderAt(<NewTranslationPage />);
  await user.click(screen.getByRole("tab", { name: "Paste text" }));
  await user.type(screen.getByLabelText("Text to translate"), "Bienvenue au centre communautaire");
  await screen.findByRole("option", { name: "English" });
  await user.selectOptions(screen.getByLabelText("From"), "fr");
  await user.selectOptions(screen.getByLabelText("To"), "en");
  await user.click(screen.getByText("More options"));
  await user.selectOptions(screen.getByLabelText("Length"), "Long document");
  await user.click(screen.getByRole("button", { name: "Translate" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-9"));
  expect(created).toEqual([
    {
      name: "Bienvenue au centre communautaire",
      target_lang: "en",
      source_lang: "fr",
      pipeline_profile: "long-form",
    },
  ]);
  const [, files] = vi.mocked(enqueueUploads).mock.calls[0]!;
  expect(files[0]!.name).toBe("Bienvenue au centre communautaire.txt");
});

it("warns about a less-practised language and leaves unusable files out", async () => {
  const { user } = renderAt(<NewTranslationPage />);
  await user.upload(screen.getByLabelText("Choose files"), [
    new File(["x"], "a.txt"),
    new File([new Uint8Array(5 * 1024 * 1024)], "big.pdf"),
  ]);
  await screen.findByRole("option", { name: "Amharic" });
  await user.selectOptions(screen.getByLabelText("To"), "am");
  expect(screen.getByText("Coglossa has less practice with Amharic, so check carefully.")).toBeInTheDocument();
  expect(screen.getByText("Files marked in red will be left out.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Translate" }));
  await vi.waitFor(() => expect(enqueueUploads).toHaveBeenCalled());
  const [, files] = vi.mocked(enqueueUploads).mock.calls[0]!;
  expect(files.map((f) => f.name)).toEqual(["a.txt"]);
});

it("shows why the set could not be created", async () => {
  server.use(
    http.post("*/api/projects", () =>
      HttpResponse.json({ detail: "The server is busy." }, { status: 503 }),
    ),
  );
  const { user } = renderAt(<NewTranslationPage />);
  await user.upload(screen.getByLabelText("Choose files"), [new File(["x"], "a.txt")]);
  await screen.findByRole("option", { name: "English" });
  await user.selectOptions(screen.getByLabelText("To"), "en");
  await user.click(screen.getByRole("button", { name: "Translate" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The server is busy.");
  expect(enqueueUploads).not.toHaveBeenCalled();
});

it("reads the upload limit from the server's settings", () => {
  expect(uploadLimit({ ...configView, config: { api: { max_upload_mb: 4 } } })).toBe(4);
  expect(uploadLimit({ ...configView, config: {} })).toBeNull();
  expect(uploadLimit(undefined)).toBeNull();
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd web && pnpm vitest run src/translate/NewTranslationPage.test.tsx`
Expected: FAIL — `./NewTranslationPage` not found.

- [ ] **Step 3: Write `web/src/translate/NewTranslationPage.tsx`**

```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useNavigate } from "react-router";
import { projectKeys } from "../api/projects";
import { createSet } from "../api/sets";
import { useConfig, useTiers } from "../api/system";
import type { ConfigView, CreateProject, PipelineProfile } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { languageOk } from "../components/LanguageInput";
import { languageName, lessPractised, rememberLanguage } from "../lib/languages";
import { pastedFile, setNameFor } from "../lib/names";
import { enqueueUploads } from "../upload/queue";
import { DropArea, fileProblem } from "./DropArea";
import { LanguagePicker } from "./LanguagePicker";

/** Spec §3.3: the length setting in plain words. */
export const LENGTHS: { value: PipelineProfile | ""; label: string }[] = [
  { value: "", label: "Automatic (recommended)" },
  { value: "short", label: "Short" },
  { value: "standard", label: "Standard" },
  { value: "long-form", label: "Long document" },
];

/** api.max_upload_mb, when the server's settings could be read. */
export function uploadLimit(config: ConfigView | undefined): number | null {
  const api = (config?.config as { api?: { max_upload_mb?: unknown } } | undefined)?.api;
  return typeof api?.max_upload_mb === "number" ? api.max_upload_mb : null;
}

/** Spec §4.1: drop files or paste text, pick the languages, Translate. */
export function NewTranslationPage() {
  const tiers = useTiers();
  const config = useConfig();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"files" | "paste">("files");
  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState("");
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [length, setLength] = useState<PipelineProfile | "">("");
  const headingId = useId();
  const pasteId = useId();
  const lengthId = useId();
  const create = useMutation({ mutationFn: (body: CreateProject) => createSet(body) });

  const maxMb = uploadLimit(config.data);
  const chosen = mode === "files" ? files : text.trim() ? [pastedFile(text)] : [];
  const usable = chosen.filter((file) => fileProblem(file, maxMb) === null);
  const ready =
    usable.length > 0 && languageOk(target, true) && languageOk(source) && !create.isPending;
  const sourceOrNull = source || null;
  const options = { target_lang: target, source_lang: sourceOrNull, pipeline_profile: length || null };

  function translate() {
    create.mutate(
      { name: setNameFor(usable), ...options },
      {
        onSuccess: (set) => {
          rememberLanguage(target);
          void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
          enqueueUploads(set.id, usable, options, queryClient);
          navigate(`/t/${set.id}`);
        },
      },
    );
  }

  return (
    <section className="new-translation" aria-labelledby={headingId}>
      <h1 id={headingId}>New translation</h1>
      <div role="tablist" aria-label="What to translate" className="tabs">
        <button
          role="tab"
          type="button"
          aria-selected={mode === "files"}
          onClick={() => setMode("files")}
        >
          Files
        </button>
        <button
          role="tab"
          type="button"
          aria-selected={mode === "paste"}
          onClick={() => setMode("paste")}
        >
          Paste text
        </button>
      </div>
      {mode === "files" ? (
        <DropArea files={files} onFiles={setFiles} maxMb={maxMb} />
      ) : (
        <div className="field">
          <label htmlFor={pasteId}>Text to translate</label>
          <textarea
            id={pasteId}
            dir="auto"
            rows={12}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
      )}
      <div className="row languages">
        <LanguagePicker
          label="From"
          value={source}
          onChange={setSource}
          tiers={tiers.data}
          allowDetect
        />
        <span aria-hidden="true">→</span>
        <LanguagePicker label="To" value={target} onChange={setTarget} tiers={tiers.data} />
      </div>
      {lessPractised(tiers.data, target, sourceOrNull) ? (
        <p className="notice">
          Coglossa has less practice with {languageName(target)}, so check carefully.
        </p>
      ) : null}
      <details className="more-options">
        <summary>More options</summary>
        <div className="field">
          <label htmlFor={lengthId}>Length</label>
          <select
            id={lengthId}
            value={length}
            onChange={(e) => setLength(e.target.value as PipelineProfile | "")}
          >
            {LENGTHS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
      </details>
      {usable.length > 0 && usable.length < chosen.length ? (
        <p className="muted">Files marked in red will be left out.</p>
      ) : null}
      <button type="button" className="primary translate" disabled={!ready} onClick={translate}>
        {create.isPending ? "Starting…" : "Translate"}
      </button>
      {create.isError ? <ErrorMessage error={create.error} /> : null}
    </section>
  );
}
```

- [ ] **Step 4: Run the test**

Run: `cd web && pnpm vitest run src/translate/NewTranslationPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Run every web gate and commit**

Run: `cd web && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src/translate
git commit -m "feat(web): New translation screen — drop or paste, pick languages by name, Translate"
```

---

### Task 8: The passage panel

Spec §5.4. The panel the document view (Task 9) opens beside a passage.

**Files:**
- Create: `web/src/review/PassagePanel.tsx`, `web/src/review/PassagePanel.test.tsx`
- Create: `web/src/review/AlwaysTranslate.tsx`, `web/src/review/MoreDetails.tsx`
- Create: `web/src/review/candidates.ts` (move `byTranslator` out of `Candidates.tsx`)
- Create: `web/src/guide/entries.ts` (move `emptyEntity` and `ENTITY_KINDS`)
- Create: `web/src/styles/review.css` (import in `main.tsx` after `translate.css`)
- Modify: `web/src/review/languages.ts` (a context instead of the outlet), `web/src/pages/run/RunLayout.tsx` (provide it), `web/src/review/Candidates.tsx`, `web/src/guide/GuideEditor.tsx`, `web/src/guide/EntityTable.tsx`, `web/src/review/AddToGuide.tsx` (imports only)
- Modify: `web/src/api/review.ts` (`useSpanChanged` also refreshes the sidebar's counts)

**Interfaces:**
- Consumes: `useSpanDetail`, `useAcceptSpan`, `useChooseCandidate`, `useEditSpan`, `useFlagStatus`, `useAddToGuide`, `GuideAddition` (`api/review.ts`); `useRecheckSet` (Task 4); `flagTitle`, `FLAG_STATUS_WORDS`, `plural` (Task 3); `Findings`, `Signals`, `Revisions` (existing, unchanged).
- Produces:
  - `review/languages.ts`: `type RunLanguagesValue = { source: string | undefined; target: string }`, `RunLanguages` (React context), `runLanguages(detail: RunDetail): RunLanguagesValue`, `useRunLanguages(): RunLanguagesValue`.
  - `review/candidates.ts`: `type Choice = { translator: string; model: string; text: string }`, `byTranslator(detail: SpanDetail): Choice[]`.
  - `guide/entries.ts`: `ENTITY_KINDS: string[]`, `emptyEntity(): Entity`.
  - `PassagePanel({ runId, projectId, spanId, prefill, onNext, onClose }: { runId: string; projectId: string; spanId: string; prefill: string; onNext: () => void; onClose: () => void })`; status strings `CHANGED = "Changed and marked as checked."`, `CHANGED_STILL_OPEN = "Changed. The passage is still marked to check."`, `CHECKED = "Marked as checked."`.
  - `AlwaysTranslate({ runId, projectId, spanId, prefill })`, `MoreDetails({ runId, detail })`.

- [ ] **Step 1: Move the shared pieces (no behaviour change)**

`web/src/review/languages.ts` becomes:

```ts
import { createContext, useContext } from "react";
import type { RunDetail } from "../api/types";

/** The languages of a run's passages, for `lang` attributes. A browser chooses glyph forms and
 * fonts by `lang`: without it, Traditional Chinese and Japanese show Simplified forms. */
export type RunLanguagesValue = { source: string | undefined; target: string };

export const RunLanguages = createContext<RunLanguagesValue | null>(null);

export function runLanguages(detail: RunDetail): RunLanguagesValue {
  return {
    source: detail.profile?.source_lang ?? detail.run.source_lang ?? undefined,
    target: detail.profile?.target_lang ?? detail.run.target_lang,
  };
}

export function useRunLanguages(): RunLanguagesValue {
  const value = useContext(RunLanguages);
  if (value === null) throw new Error("useRunLanguages needs a RunLanguages provider");
  return value;
}
```

In `web/src/pages/run/RunLayout.tsx` wrap the outlet: `<RunLanguages.Provider value={runLanguages(detail)}><Outlet context={detail} /></RunLanguages.Provider>` (import both from `../../review/languages`). The existing `review/languages.test.tsx` keeps passing.

`web/src/review/candidates.ts` gets `Choice` and `byTranslator` exactly as they are in `Candidates.tsx` today (exported); `Candidates.tsx` imports them.

`web/src/guide/entries.ts`:

```ts
import type { Entity } from "../api/types";

export const ENTITY_KINDS = ["person", "place", "organisation", "work", "other"];

export function emptyEntity(): Entity {
  return {
    name: "",
    kind: "person",
    rendering: "",
    gender: null,
    forms_of_address: "",
    speech_style: "",
    locked: false,
  };
}
```

Delete the two definitions from `GuideEditor.tsx` and `EntityTable.tsx` and import them from `./entries` there and in `review/AddToGuide.tsx` and any test that imports them (`grep -rn "emptyEntity\|ENTITY_KINDS" web/src`).

In `web/src/api/review.ts`, `useSpanChanged` adds, after the existing invalidations:

```ts
    // the sidebar's "N to check" counts move with every flag resolved
    void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
```

(import `projectKeys` from `./projects`).

Run `cd web && pnpm test` — expected PASS before going on.

- [ ] **Step 2: Write the failing test** — `web/src/review/PassagePanel.test.tsx`

```tsx
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { SpanDetail } from "../api/types";
import { documentSummary, guideView, progress, runSummary, spanDetail } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { RunLanguages } from "./languages";
import { CHANGED, CHANGED_STILL_OPEN, CHECKED, PassagePanel } from "./PassagePanel";

const resolved: SpanDetail = {
  ...spanDetail,
  flags: spanDetail.flags.map((f) => ({ ...f, status: "accepted" as const })),
};

function renderPanel(prefill = "") {
  const onNext = vi.fn();
  const onClose = vi.fn();
  const view = renderAt(
    <RunLanguages.Provider value={{ source: "fr", target: "en" }}>
      <PassagePanel
        runId="r-1"
        projectId="p-1"
        spanId="s-2"
        prefill={prefill}
        onNext={onNext}
        onClose={onClose}
      />
    </RunLanguages.Provider>,
  );
  return { ...view, onNext, onClose };
}

const accepted: string[] = [];

beforeEach(() => {
  accepted.length = 0;
  server.use(
    http.get("*/api/runs/r-1/spans/s-2", () => HttpResponse.json(spanDetail)),
    http.post("*/api/runs/r-1/spans/s-2/accept", () => {
      accepted.push("s-2");
      return HttpResponse.json(resolved);
    }),
  );
});

it("explains each open flag in plain words", async () => {
  renderPanel();
  const panel = await screen.findByRole("complementary", { name: "Passage 2" });
  expect(within(panel).getByRole("heading", { name: "Passage 2 · worth a look" })).toBeInTheDocument();
  const why = within(panel).getByRole("region", { name: "Why it's flagged" });
  expect(why).toHaveTextContent("The meaning may have shifted");
  expect(why).toHaveTextContent("Meaning score 55.0 is below the 72 threshold.");
  expect(why).toHaveTextContent("The translators disagreed");
  expect(why).not.toHaveTextContent("low_meaning");
});

it("uses a better version and marks the passage checked", async () => {
  let chosen: unknown;
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/use-candidate", async ({ request }) => {
      chosen = await request.json();
      return HttpResponse.json({ ...spanDetail, text: "The cat is asleep. It rains." });
    }),
  );
  const { user } = renderPanel();
  const versions = await screen.findByRole("region", { name: "Better versions" });
  expect(within(versions).getByText("The cat is asleep. It rains.")).toHaveAttribute("lang", "en");
  await user.click(within(versions).getByRole("button", { name: "Use version 1" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED);
  expect(chosen).toEqual({ translator: "contextual" });
  expect(accepted).toEqual(["s-2"]);
});

it("saves an edit, and says so when marking it checked fails", async () => {
  let sent: unknown;
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/edit", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...spanDetail, text: "The cat is asleep." });
    }),
    http.post("*/api/runs/r-1/spans/s-2/accept", () =>
      HttpResponse.json({ detail: "busy" }, { status: 503 }),
    ),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  const box = screen.getByLabelText("Your translation");
  expect(box).toHaveAttribute("lang", "en");
  await user.clear(box);
  await user.type(box, "The cat is asleep.");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED_STILL_OPEN);
  expect(sent).toEqual({ text: "The cat is asleep.", reason: null });
});

it("It's fine marks the passage checked and moves on", async () => {
  const { user, onNext } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "It's fine" }));
  await vi.waitFor(() => expect(onNext).toHaveBeenCalled());
  expect(accepted).toEqual(["s-2"]);
  expect(screen.getByRole("status")).toHaveTextContent(CHECKED);
});

it("Always translate saves a fixed name to the guide and rechecks the set", async () => {
  let added: unknown;
  const swept: string[] = [];
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/guide", async ({ request }) => {
      added = await request.json();
      return HttpResponse.json(guideView);
    }),
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])),
    http.post("*/api/runs/:runId/sweep", ({ params }) => {
      swept.push(params["runId"] as string);
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const { user } = renderPanel("Marie");
  const box = await screen.findByRole("region", { name: "Always translate" });
  expect(within(box).getByLabelText("Always translate")).toHaveValue("Marie");
  expect(within(box).getByLabelText("Always translate")).toHaveAttribute("lang", "fr");
  await user.type(within(box).getByLabelText("as"), "Mary");
  await user.click(within(box).getByLabelText("This is a name (person, place, organisation…)"));
  await user.click(within(box).getByRole("button", { name: "Apply to all" }));
  expect(await within(box).findByRole("status")).toHaveTextContent(
    "Added to the translation guide. Rechecking 1 file…",
  );
  expect(added).toMatchObject({
    entity: { name: "Marie", rendering: "Mary", kind: "person", locked: true },
  });
  expect(swept).toEqual([runSummary.id]);
  expect(within(box).getByLabelText("Always translate")).toHaveValue("");
});

it("Always translate says when nothing could be rechecked", async () => {
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/guide", () => HttpResponse.json(guideView)),
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([{ ...documentSummary, runs: [{ ...runSummary, state: "translating" }] }]),
    ),
  );
  const { user } = renderPanel();
  const box = await screen.findByRole("region", { name: "Always translate" });
  await user.type(within(box).getByLabelText("Always translate"), "chat");
  await user.type(within(box).getByLabelText("as"), "cat");
  await user.click(within(box).getByRole("button", { name: "Apply to all" }));
  expect(await within(box).findByRole("status")).toHaveTextContent(
    "Added to the translation guide. New translations in this set will follow it.",
  );
});

it("keeps scores and flag history under More details", async () => {
  let change: unknown;
  server.use(
    http.post("*/api/runs/r-1/flags/:flagId", async ({ request }) => {
      change = await request.json();
      return HttpResponse.json(spanDetail);
    }),
  );
  const { user } = renderPanel();
  const more = (await screen.findByText("More details")).closest("details")!;
  expect(more).toHaveTextContent("Meaning match 55 / 100 · Style match 70 / 100");
  const flags = within(more).getByRole("list", { name: "Flags" });
  expect(flags).toHaveTextContent("The meaning may have shifted: to check");
  await user.click(within(more).getByText("More details"));
  await user.click(within(flags).getByRole("button", { name: "Not a problem: The meaning may have shifted" }));
  await user.type(within(more).getByLabelText("Why isn't it a problem?"), "the tense is right");
  await user.click(within(more).getByRole("button", { name: "Save" }));
  await vi.waitFor(() =>
    expect(change).toEqual({ status: "dismissed", reason: "the tense is right" }),
  );
});

it("closes", async () => {
  const { user, onClose } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Close the passage" }));
  expect(onClose).toHaveBeenCalled();
});
```

(`<aside>` has the implicit role `complementary`.)

- [ ] **Step 3: Run it to see it fail**

Run: `cd web && pnpm vitest run src/review/PassagePanel.test.tsx`
Expected: FAIL — `./PassagePanel` not found.

- [ ] **Step 4: Write `web/src/review/AlwaysTranslate.tsx`**

```tsx
import { useEffect, useId, useState } from "react";
import { useAddToGuide, type GuideAddition } from "../api/review";
import { useRecheckSet } from "../api/sets";
import { ErrorMessage } from "../components/ErrorMessage";
import { emptyEntity } from "../guide/entries";
import { plural } from "../lib/flags";
import { useRunLanguages } from "./languages";

type Props = { runId: string; projectId: string; spanId: string; prefill: string };

/** Spec §5.4 step 4: a fixed rule in the set's translation guide, then a recheck of every
 * finished file in the set. */
export function AlwaysTranslate({ runId, projectId, spanId, prefill }: Props) {
  const add = useAddToGuide(runId, projectId);
  const recheck = useRecheckSet(projectId);
  const languages = useRunLanguages();
  const [source, setSource] = useState(prefill);
  const [rendering, setRendering] = useState("");
  const [isName, setIsName] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const sourceId = useId();
  const renderingId = useId();
  useEffect(() => {
    if (prefill) setSource(prefill); // text selected in the Original column
  }, [prefill]);
  const ready = source.trim() !== "" && rendering.trim() !== "" && !add.isPending && !recheck.isPending;

  async function apply() {
    setDone(null);
    const addition: GuideAddition = isName
      ? { entity: { ...emptyEntity(), name: source.trim(), rendering: rendering.trim(), locked: true } }
      : { glossary: { term: source.trim(), rendering: rendering.trim(), notes: "", locked: true } };
    try {
      await add.mutateAsync({ spanId, addition });
    } catch {
      return; // shown below
    }
    setSource("");
    setRendering("");
    setIsName(false);
    try {
      const result = await recheck.mutateAsync();
      setDone(
        result.started.length > 0
          ? `Added to the translation guide. Rechecking ${plural(result.started.length, "file")}…`
          : "Added to the translation guide. New translations in this set will follow it.",
      );
    } catch {
      setDone("Added to the translation guide, but the recheck couldn't start.");
    }
  }

  return (
    <section aria-label="Always translate" className="always">
      <h3>Fix a name or term everywhere</h3>
      <div className="field">
        <label htmlFor={sourceId}>Always translate</label>
        <input
          id={sourceId}
          dir="auto"
          lang={languages.source}
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor={renderingId}>as</label>
        <input
          id={renderingId}
          dir="auto"
          lang={languages.target}
          value={rendering}
          onChange={(e) => setRendering(e.target.value)}
        />
      </div>
      <label>
        <input type="checkbox" checked={isName} onChange={(e) => setIsName(e.target.checked)} />{" "}
        This is a name (person, place, organisation…)
      </label>
      <div className="row">
        <button type="button" className="primary" disabled={!ready} onClick={() => void apply()}>
          Apply to all
        </button>
      </div>
      {done ? <p role="status">{done}</p> : null}
      {add.isError ? <ErrorMessage error={add.error} /> : null}
    </section>
  );
}
```

- [ ] **Step 5: Write `web/src/review/MoreDetails.tsx`**

```tsx
import { useState } from "react";
import { useFlagStatus } from "../api/review";
import type { SpanDetail } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { FLAG_STATUS_WORDS, flagTitle } from "../lib/flags";
import { formatScore } from "../lib/format";
import { Findings } from "./Findings";
import { Revisions } from "./Revisions";
import { Signals } from "./Signals";

function FlagStatusList({ runId, detail }: { runId: string; detail: SpanDetail }) {
  const change = useFlagStatus(runId);
  const [dismissing, setDismissing] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  if (detail.flags.length === 0) return null;
  return (
    <>
      <h3>Flags</h3>
      <ul aria-label="Flags" className="flag-list">
        {detail.flags.map((flag) => {
          const title = flagTitle(flag.code);
          return (
            <li key={flag.id}>
              {title}: {FLAG_STATUS_WORDS[flag.status]}
              {flag.dismiss_reason ? (
                <>
                  {" "}
                  (<span dir="auto">{flag.dismiss_reason}</span>)
                </>
              ) : null}{" "}
              {flag.status === "open" ? (
                <button
                  type="button"
                  aria-label={`Not a problem: ${title}`}
                  onClick={() => {
                    setDismissing(flag.id);
                    setReason("");
                  }}
                >
                  Not a problem…
                </button>
              ) : (
                <button
                  type="button"
                  aria-label={`Reopen: ${title}`}
                  disabled={change.isPending}
                  onClick={() => change.mutate({ flagId: flag.id, status: "open", reason: null })}
                >
                  Reopen
                </button>
              )}
              {dismissing === flag.id ? (
                <div className="row">
                  <label htmlFor={`dismiss-${flag.id}`}>Why isn&apos;t it a problem?</label>
                  <input
                    id={`dismiss-${flag.id}`}
                    dir="auto"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                  <button
                    type="button"
                    disabled={reason.trim() === "" || change.isPending}
                    onClick={() =>
                      change.mutate(
                        { flagId: flag.id, status: "dismissed", reason: reason.trim() },
                        { onSuccess: () => setDismissing(null) },
                      )
                    }
                  >
                    Save
                  </button>
                  <button type="button" onClick={() => setDismissing(null)}>
                    Cancel
                  </button>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      {change.isError ? <ErrorMessage error={change.error} /> : null}
    </>
  );
}

/** Spec §5.4 step 5: scores, signals, history and every flag, folded away. */
export function MoreDetails({ runId, detail }: { runId: string; detail: SpanDetail }) {
  return (
    <details className="more-details">
      <summary>More details</summary>
      <p>
        Meaning match {formatScore(detail.score.meaning)} / 100 · Style match{" "}
        {formatScore(detail.score.style)} / 100
      </p>
      <FlagStatusList runId={runId} detail={detail} />
      <Findings detail={detail} />
      <h3>Signals</h3>
      <Signals detail={detail} />
      <Revisions revisions={detail.revisions} />
    </details>
  );
}
```

- [ ] **Step 6: Write `web/src/review/PassagePanel.tsx`**

```tsx
import { useId, useState } from "react";
import { useAcceptSpan, useChooseCandidate, useEditSpan, useSpanDetail } from "../api/review";
import type { SpanDetail } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { flagTitle } from "../lib/flags";
import { AlwaysTranslate } from "./AlwaysTranslate";
import { byTranslator } from "./candidates";
import { useRunLanguages } from "./languages";
import { MoreDetails } from "./MoreDetails";

export const CHANGED = "Changed and marked as checked.";
export const CHANGED_STILL_OPEN = "Changed. The passage is still marked to check.";
export const CHECKED = "Marked as checked.";

type Change = (change: () => Promise<SpanDetail>) => Promise<void>;

function Versions({ runId, detail, onChange }: { runId: string; detail: SpanDetail; onChange: Change }) {
  const choose = useChooseCandidate(runId);
  const { target } = useRunLanguages();
  const others = byTranslator(detail).filter((c) => c.text !== detail.text);
  if (others.length === 0) return null;
  return (
    <section aria-label="Better versions">
      <h3>Pick a better version</h3>
      <ul className="versions">
        {others.map((c, i) => (
          <li key={c.translator}>
            <p dir="auto" lang={target}>
              {c.text}
            </p>
            <button
              type="button"
              aria-label={`Use version ${i + 1}`}
              disabled={choose.isPending}
              onClick={() =>
                void onChange(() =>
                  choose.mutateAsync({ spanId: detail.id, translator: c.translator }),
                )
              }
            >
              Use
            </button>
          </li>
        ))}
      </ul>
      {choose.isError ? <ErrorMessage error={choose.error} /> : null}
    </section>
  );
}

function EditText({ runId, detail, onChange }: { runId: string; detail: SpanDetail; onChange: Change }) {
  const edit = useEditSpan(runId);
  const { target } = useRunLanguages();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(detail.text);
  const id = useId();
  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => {
          setText(detail.text);
          setEditing(true);
        }}
      >
        Edit
      </button>
    );
  }
  const changed = text.trim() !== "" && text !== detail.text;
  return (
    <div className="edit">
      <label htmlFor={id}>Your translation</label>
      <textarea
        id={id}
        dir="auto"
        lang={target}
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="row">
        <button
          type="button"
          className="primary"
          disabled={!changed || edit.isPending}
          onClick={() =>
            void onChange(async () => {
              const saved = await edit.mutateAsync({ spanId: detail.id, text, reason: null });
              setEditing(false);
              return saved;
            })
          }
        >
          Save
        </button>
        <button type="button" onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
      {edit.isError ? <ErrorMessage error={edit.error} /> : null}
    </div>
  );
}

type Props = {
  runId: string;
  projectId: string;
  spanId: string;
  prefill: string;
  onNext: () => void;
  onClose: () => void;
};

/** Spec §5.4. */
export function PassagePanel({ runId, projectId, spanId, prefill, onNext, onClose }: Props) {
  const detail = useSpanDetail(runId, spanId);
  const accept = useAcceptSpan(runId);
  const [status, setStatus] = useState<string | null>(null);
  if (detail.isPending || detail.isError) {
    return (
      <aside className="passage-panel" aria-label="Passage">
        {detail.isError ? <ErrorMessage error={detail.error} /> : <Loading />}
      </aside>
    );
  }
  const d = detail.data;
  const open = d.flags.filter((f) => f.status === "open");

  // Spec §5.4 step 2: using a version or saving an edit also marks the passage checked.
  const changeThenCheck: Change = async (change) => {
    setStatus(null);
    let changed: SpanDetail;
    try {
      changed = await change();
    } catch {
      return; // the form shows its own error
    }
    if (!changed.flags.some((f) => f.status === "open")) return setStatus(CHANGED);
    try {
      await accept.mutateAsync(d.id);
      setStatus(CHANGED);
    } catch {
      setStatus(CHANGED_STILL_OPEN);
    }
  };

  return (
    <aside className="passage-panel" aria-label={`Passage ${d.number}`}>
      <div className="row between">
        <h2>
          Passage {d.number}
          {open.length > 0 ? " · worth a look" : ""}
        </h2>
        <button type="button" className="quiet" aria-label="Close the passage" onClick={onClose}>
          ×
        </button>
      </div>
      {open.length > 0 ? (
        <section aria-label="Why it's flagged">
          {open.map((flag) => (
            <div key={flag.id} className="why">
              <p>
                <span aria-hidden="true">⚠ </span>
                <strong>{flagTitle(flag.code)}</strong>
              </p>
              <p className="muted" dir="auto">
                {flag.explanation}
              </p>
            </div>
          ))}
        </section>
      ) : (
        <p className="muted">Nothing to check here.</p>
      )}
      <Versions runId={runId} detail={d} onChange={changeThenCheck} />
      <div className="row">
        <EditText key={d.text} runId={runId} detail={d} onChange={changeThenCheck} />
        {open.length > 0 ? (
          <button
            type="button"
            className="primary"
            disabled={accept.isPending}
            onClick={() =>
              accept.mutate(d.id, {
                onSuccess: () => {
                  setStatus(CHECKED);
                  onNext();
                },
              })
            }
          >
            It&apos;s fine
          </button>
        ) : null}
      </div>
      {accept.isError && status === null ? <ErrorMessage error={accept.error} /> : null}
      {status ? (
        <p role="status">
          {status}{" "}
          <button type="button" className="link" onClick={onNext}>
            Next to check →
          </button>
        </p>
      ) : null}
      <AlwaysTranslate runId={runId} projectId={projectId} spanId={d.id} prefill={prefill} />
      <MoreDetails runId={runId} detail={d} />
    </aside>
  );
}
```

Note: `EditText` is keyed by the text so a saved change resets it. The "It's fine" test above asserts `getByRole("status")` after `onNext` — in the real document view `onNext` remounts the panel for the next passage; here the mock leaves it mounted.

- [ ] **Step 7: Write `web/src/styles/review.css`**

```css
/* Spec §5.3, §5.4: the document and the passage panel. */
.passage-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-2);
}

.passage-panel h2 {
  margin: 0;
  font-size: var(--text-md);
}

.why {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-1);
  background: var(--warn-tint);
}

.why p {
  margin: 0;
}

.versions {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.versions li {
  display: flex;
  gap: var(--space-2);
  align-items: flex-start;
  justify-content: space-between;
  padding: var(--space-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-1);
}

.versions p {
  margin: 0;
}

.always {
  padding: var(--space-3);
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-1);
}

.always h3 {
  margin-top: 0;
}

.flag-list {
  padding-inline-start: var(--space-4);
}
```

- [ ] **Step 8: Run the tests, every web gate, and commit**

Run: `cd web && pnpm vitest run src/review && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src
git commit -m "feat(web): passage panel — plain reasons, better versions, edit, It's fine, Always translate"
```

---
### Task 9: The side-by-side document and the summary banner

Spec §5.2, §5.3. One file's finished translation: the banner, the Show menu, the two-column document loading as you scroll, flagged passages marked, n/p/Enter/Esc, and the passage panel (Task 8) on the right.

**Files:**
- Create: `web/src/set/SummaryBanner.tsx`, `web/src/set/SummaryBanner.test.tsx`
- Create: `web/src/review/DocumentView.tsx`, `web/src/review/DocumentView.test.tsx`
- Create: `web/src/review/useDocumentKeys.ts`
- Modify: `web/src/api/review.ts` (`useDocumentPages`, `fetchFlaggedPage`, `findFlagged`, `DOC_PAGE`, `FLAG_PAGE`)
- Modify: `web/src/review/filters.ts` (`ScoreFilter`, `scoreFilterFrom`)
- Modify: `web/src/styles/review.css` (document and banner rules)

**Interfaces:**
- Consumes: `bannerFor`, `Banner` (Task 3); `flagTitle` (Task 3); `languageName` (Task 3); `useTranslateAgain` (Task 4); `useRunAction` (`api/runs.ts`); `PassagePanel`, `RunLanguages`, `runLanguages` (Task 8); `keyBelongsToControl` (Task 5); `score` (`review/filters.ts`).
- Produces:
  - `SummaryBanner({ detail, onNext? }: { detail: RunDetail; onNext?: () => void })`.
  - `DocumentView({ detail }: { detail: RunDetail })`.
  - `useDocumentKeys(actions: { next: () => void; previous: () => void; toggle: () => void; close: () => void }): void`.
  - `api/review.ts`: `DOC_PAGE = 100`, `FLAG_PAGE = 500`, `useDocumentPages(runId: string, flaggedOnly: boolean, scores: ScoreFilter)`, `fetchFlaggedPage(runId: string, offset: number): Promise<SpanPage>`, `findFlagged(runId: string, from: number, forward: boolean): Promise<ReviewSpan | null>`.
  - `review/filters.ts`: `type ScoreFilter = { min_meaning?: number; max_meaning?: number; min_style?: number; max_style?: number }`, `SCORE_KEYS`, `scoreFilterFrom(params: URLSearchParams): ScoreFilter`.

- [ ] **Step 1: Write the failing tests**

`web/src/set/SummaryBanner.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { RunDetail } from "../api/types";
import { progress, runDetail, runSummary } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { SummaryBanner } from "./SummaryBanner";

function detail(
  state: RunDetail["run"]["state"],
  patch: Partial<RunDetail> = {},
  retryable = false,
): RunDetail {
  return {
    ...runDetail,
    ...patch,
    run: { ...runDetail.run, state, error: state === "failed" ? "translator: timeout" : null },
    progress: { ...runDetail.progress, state, retryable },
    stats: { spans: 6, open_flags: 2, flags_by_code: {}, edited_spans: 0, ...patch.stats },
  };
}

it("offers the next passage to check", async () => {
  const onNext = vi.fn();
  const { user } = renderAt(<SummaryBanner detail={detail("complete")} onNext={onNext} />);
  expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent(
    "Looks good. 2 are worth a quick look before you download.",
  );
  await user.click(screen.getByRole("button", { name: "Next to check →" }));
  expect(onNext).toHaveBeenCalled();
});

it("lists run notices in plain words, without repeating the practice note", () => {
  renderAt(
    <SummaryBanner
      detail={detail("complete", {
        language_tier: "C",
        notices: [
          { code: "low_reliability_language", explanation: "tier C", evidence: {}, status: "open" },
          { code: "unscored", explanation: "The scorer was offline.", evidence: {}, status: "open" },
        ],
      })}
    />,
  );
  const banner = screen.getByRole("region", { name: "Summary" });
  expect(banner).toHaveTextContent("Check carefully: Coglossa has less practice with English.");
  expect(banner).toHaveTextContent("Couldn't be checked. The scorer was offline.");
  expect(banner).not.toHaveTextContent("tier C");
});

it("tries unfinished checking again", async () => {
  let retried = false;
  server.use(
    http.post("*/api/runs/r-1/retry", () => {
      retried = true;
      return HttpResponse.json({ message: "retrying", progress }, { status: 202 });
    }),
  );
  const { user } = renderAt(
    <SummaryBanner
      detail={detail("complete_with_errors", { stats: { spans: 6, open_flags: 1, flags_by_code: { engine_error: 1 }, edited_spans: 0 } }, true)}
    />,
  );
  expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent(
    "Ready. Some checking didn't finish on 1 passage; they're marked.",
  );
  await user.click(screen.getByRole("button", { name: "Try those again" }));
  await vi.waitFor(() => expect(retried).toBe(true));
});

it("explains a failure and keeps the raw error under Details", () => {
  renderAt(<SummaryBanner detail={detail("failed", {}, true)} />);
  const banner = screen.getByRole("region", { name: "Summary" });
  expect(banner).toHaveTextContent("This translation couldn't finish.");
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  expect(screen.getByText("Details").closest("details")).toHaveTextContent("translator: timeout");
});

it("translates a stopped file again and opens the new version", async () => {
  server.use(
    http.post("*/api/documents/d-1/runs", () =>
      HttpResponse.json({ ...runSummary, id: "r-2", state: "created" }, { status: 202 }),
    ),
  );
  const { user, router } = renderAt(<SummaryBanner detail={detail("cancelled")} />);
  expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent(
    "Stopped before it finished.",
  );
  await user.click(screen.getByRole("button", { name: "Translate again" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-1/r-2"));
});
```

`web/src/review/DocumentView.test.tsx`:

```tsx
import { fireEvent, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReviewSpan, RunDetail } from "../api/types";
import { reviewSpans, runDetail, spanDetail } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { DocumentView } from "./DocumentView";

const complete: RunDetail = {
  ...runDetail,
  run: { ...runDetail.run, state: "complete", finished_at: "2026-10-01T09:05:00Z" },
  progress: { ...runDetail.progress, state: "complete" },
  report_available: true,
  stats: { spans: 3, open_flags: 2, flags_by_code: {}, edited_spans: 1 },
};

const plain = (n: number): ReviewSpan => ({
  ...reviewSpans[0]!,
  id: `s-${n}`,
  number: n,
  source: `Source ${n}`,
  text: `Text ${n}`,
  edited: false,
  flags: [],
});

const flagged = (span: ReviewSpan): ReviewSpan => ({ ...span, flags: reviewSpans[1]!.flags });

function serveSpans(all: ReviewSpan[]): URL[] {
  const requests: URL[] = [];
  server.use(
    http.get("*/api/runs/r-1/spans", ({ request }) => {
      const url = new URL(request.url);
      requests.push(url);
      const list =
        url.searchParams.get("flagged") === "true"
          ? all.filter((s) => s.flags.some((f) => f.status === "open"))
          : all;
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 100);
      return HttpResponse.json({ spans: list.slice(offset, offset + limit), total: list.length, offset, limit });
    }),
    http.get("*/api/runs/r-1/spans/:spanId", ({ params }) => {
      const span = all.find((s) => s.id === params["spanId"])!;
      return HttpResponse.json({ ...spanDetail, ...span });
    }),
  );
  return requests;
}

const rows = () => within(screen.getByRole("table", { name: "Document" })).getAllByRole("row").slice(1);

it("shows the original beside the translation, marked and labelled", async () => {
  serveSpans(reviewSpans);
  renderAt(<DocumentView detail={complete} />);
  const table = await screen.findByRole("table", { name: "Document" });
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
    "Original",
    "English",
  ]);
  const [first, second, third] = rows();
  expect(within(first!).getByText("Chapitre premier").closest("td")).toHaveAttribute("lang", "fr");
  expect(within(first!).getByText("Chapter One")).toHaveAttribute("lang", "en");
  expect(second).toHaveClass("flagged");
  expect(second).toHaveTextContent("Worth a look:");
  expect(third).toHaveTextContent("edited");
  expect(screen.getByText("3 of 3 passages shown")).toBeInTheDocument();
});

it("leaves the source unmarked when the run has no source language", async () => {
  serveSpans(reviewSpans);
  renderAt(
    <DocumentView detail={{ ...complete, profile: null, run: { ...complete.run, source_lang: null } }} />,
  );
  await screen.findByRole("table", { name: "Document" });
  expect(within(rows()[0]!).getByText("Chapitre premier").closest("td")).not.toHaveAttribute("lang");
});

it("walks the passages to check with Next, n and p", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByRole("button", { name: "Next to check →" }));
  expect(await screen.findByRole("complementary", { name: "Passage 2" })).toBeInTheDocument();
  await user.keyboard("n");
  expect(await screen.findByRole("complementary", { name: "Passage 3" })).toBeInTheDocument();
  await user.keyboard("n");
  expect(await screen.findByText("No later passage to check.")).toBeInTheDocument();
  await user.keyboard("p");
  expect(await screen.findByRole("complementary", { name: "Passage 2" })).toBeInTheDocument();
});

it("opens with a click, closes with Esc, and reopens with Enter", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("Chapter One"));
  expect(await screen.findByRole("complementary", { name: "Passage 1" })).toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  await user.keyboard("{Enter}");
  expect(await screen.findByRole("complementary", { name: "Passage 1" })).toBeInTheDocument();
});

it("shows only the passages to check when asked", async () => {
  const requests = serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.selectOptions(screen.getByLabelText("Show"), "To check");
  await vi.waitFor(() => expect(rows()).toHaveLength(2));
  expect(requests.at(-1)!.searchParams.get("flagged")).toBe("true");
});

it("says when nothing is left to check", async () => {
  serveSpans([plain(1)]);
  const { user } = renderAt(<DocumentView detail={{ ...complete, stats: { ...complete.stats, open_flags: 0 } }} />);
  await screen.findByRole("table", { name: "Document" });
  await user.selectOptions(screen.getByLabelText("Show"), "To check");
  expect(await screen.findByText("Nothing left to check.")).toBeInTheDocument();
});

it("loads more passages on request", async () => {
  serveSpans(Array.from({ length: 120 }, (_, i) => plain(i + 1)));
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  expect(rows()).toHaveLength(100);
  await user.click(screen.getByRole("button", { name: "Load more passages" }));
  await vi.waitFor(() => expect(rows()).toHaveLength(120));
  expect(screen.queryByRole("button", { name: "Load more passages" })).not.toBeInTheDocument();
});

it("loads more by itself when the end comes into view", async () => {
  let callback: IntersectionObserverCallback | undefined;
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(cb: IntersectionObserverCallback) {
        callback = cb;
      }
      observe() {}
      disconnect() {}
    },
  );
  const requests = serveSpans(Array.from({ length: 120 }, (_, i) => plain(i + 1)));
  renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  callback!([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
  await vi.waitFor(() => expect(requests.some((u) => u.searchParams.get("offset") === "100")).toBe(true));
});

it("reveals a passage to check beyond what is loaded", async () => {
  const all = Array.from({ length: 120 }, (_, i) => plain(i + 1));
  all[109] = flagged(all[109]!);
  serveSpans(all);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByRole("button", { name: "Next to check →" }));
  expect(await screen.findByRole("complementary", { name: "Passage 110" })).toBeInTheDocument();
  expect(rows()).toHaveLength(120);
});

it("fills Always translate from words selected in the original", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("The cat sleeps. It rains."));
  await screen.findByRole("complementary", { name: "Passage 2" });
  const cell = rows()[1]!.querySelector("td.source")!;
  const text = [...cell.childNodes].find((n) => n.nodeType === Node.TEXT_NODE)!;
  const range = document.createRange();
  range.setStart(text, 0);
  range.setEnd(text, 7);
  document.getSelection()!.removeAllRanges();
  document.getSelection()!.addRange(range);
  fireEvent.mouseUp(cell);
  expect(await screen.findByLabelText("Always translate")).toHaveValue("Le chat");
});

it("filters by score from the address, and clears it", async () => {
  const requests = serveSpans(reviewSpans);
  const { user, router } = renderAt(<DocumentView detail={complete} />, {
    path: "/t/p-1/r-1?min_meaning=0&max_meaning=60",
  });
  await screen.findByRole("table", { name: "Document" });
  expect(requests[0]!.searchParams.get("min_meaning")).toBe("0");
  expect(requests[0]!.searchParams.get("max_meaning")).toBe("60");
  expect(screen.getByText(/Showing passages by score/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Show everything" }));
  expect(router.state.location.search).toBe("");
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && pnpm vitest run src/set/SummaryBanner.test.tsx src/review/DocumentView.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Add the score filter** to `web/src/review/filters.ts` (keep the existing exports)

```ts
export type ScoreFilter = {
  min_meaning?: number;
  max_meaning?: number;
  min_style?: number;
  max_style?: number;
};

export const SCORE_KEYS = ["min_meaning", "max_meaning", "min_style", "max_style"] as const;

/** Settings ▸ Technical details opens a translation with score bounds in its address (§6.1). */
export function scoreFilterFrom(params: URLSearchParams): ScoreFilter {
  const filter: ScoreFilter = {};
  for (const key of SCORE_KEYS) {
    const value = score(params.get(key));
    if (value !== null) filter[key] = value;
  }
  return filter;
}
```

- [ ] **Step 4: Add the document queries** to `web/src/api/review.ts`

```ts
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ScoreFilter } from "../review/filters";
import type { Entity, GlossaryEntry, ReviewSpan, SpanDetail, SpanPage } from "./types";

export const DOC_PAGE = 100;
export const FLAG_PAGE = 500; // the API's largest page

/** Spec §5.3: the document in pages that load as the reader nears the end. */
export function useDocumentPages(runId: string, flaggedOnly: boolean, scores: ScoreFilter) {
  return useInfiniteQuery({
    queryKey: [...runKeys.pages(runId), "document", { flaggedOnly, ...scores }],
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/api/runs/{run_id}/spans", {
          params: {
            path: { run_id: runId },
            query: { ...scores, ...(flaggedOnly ? { flagged: true } : {}), offset: pageParam, limit: DOC_PAGE },
          },
        }),
      ),
    initialPageParam: 0,
    getNextPageParam: (last: SpanPage) => {
      const next = last.offset + last.spans.length;
      return last.spans.length > 0 && next < last.total ? next : undefined;
    },
  });
}

export function fetchFlaggedPage(runId: string, offset: number): Promise<SpanPage> {
  return unwrap(
    client.GET("/api/runs/{run_id}/spans", {
      params: { path: { run_id: runId }, query: { flagged: true, offset, limit: FLAG_PAGE } },
    }),
  );
}

/** The next (forward) or previous passage with an open flag after passage number `from`; null
 * when there is none. Reads the flagged list in pages of 500. */
export async function findFlagged(
  runId: string,
  from: number,
  forward: boolean,
): Promise<ReviewSpan | null> {
  let previous: ReviewSpan | null = null;
  for (let offset = 0; ; offset += FLAG_PAGE) {
    const page = await fetchFlaggedPage(runId, offset);
    for (const span of page.spans) {
      if (forward && span.number > from) return span;
      if (!forward) {
        if (span.number >= from) return previous;
        previous = span;
      }
    }
    if (page.spans.length === 0 || offset + page.spans.length >= page.total) {
      return forward ? null : previous;
    }
  }
}
```

- [ ] **Step 5: Write `web/src/review/useDocumentKeys.ts`**

```ts
import { useEffect, useRef } from "react";
import { keyBelongsToControl } from "../lib/keys";

type Actions = { next: () => void; previous: () => void; toggle: () => void; close: () => void };

/** Spec §5.3: n / p walk the passages to check, Enter opens or closes, Esc closes. Keys typed
 * into a field, or Enter on a button, belong to that control. */
export function useDocumentKeys(actions: Actions): void {
  const ref = useRef(actions);
  ref.current = actions;
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (keyBelongsToControl(event.target, event.key)) return;
      const a = ref.current;
      if (event.key === "n" || event.key === "p") {
        event.preventDefault();
        if (event.key === "n") a.next();
        else a.previous();
      } else if (event.key === "Enter") {
        a.toggle();
      } else if (event.key === "Escape") {
        a.close();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
```

- [ ] **Step 6: Write `web/src/set/SummaryBanner.tsx`**

```tsx
import { useNavigate } from "react-router";
import { useRunAction } from "../api/runs";
import { useTranslateAgain } from "../api/sets";
import type { RunDetail } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { flagTitle } from "../lib/flags";
import { languageName } from "../lib/languages";
import { bannerFor } from "../lib/status";

/** Spec §5.2: one plain sentence about the result, and the one action that fits it. */
export function SummaryBanner({ detail, onNext }: { detail: RunDetail; onNext?: () => void }) {
  const { run } = detail;
  const action = useRunAction(run.id, run.project_id);
  const again = useTranslateAgain(run.project_id);
  const navigate = useNavigate();
  const banner = bannerFor(detail, languageName(run.target_lang));
  const notices = detail.notices.filter(
    (n) => !(banner.careful && n.code === "low_reliability_language"),
  );
  return (
    <section aria-label="Summary" className={`banner banner-${banner.tone}`}>
      <p className="banner-text">{banner.text}</p>
      {banner.careful ? <p>{banner.careful}</p> : null}
      {notices.map((notice) => (
        <p key={notice.code} className="muted">
          <strong>{flagTitle(notice.code)}.</strong> <span dir="auto">{notice.explanation}</span>
        </p>
      ))}
      <div className="row">
        {banner.action === "next" && onNext ? (
          <button type="button" className="primary" onClick={onNext}>
            Next to check →
          </button>
        ) : null}
        {banner.action === "retry" ? (
          <button
            type="button"
            className="primary"
            disabled={action.isPending}
            onClick={() => action.mutate("retry")}
          >
            {run.state === "failed" ? "Try again" : "Try those again"}
          </button>
        ) : null}
        {banner.action === "again" ? (
          <button
            type="button"
            className="primary"
            disabled={again.isPending}
            onClick={() =>
              again.mutate(run.document_id, {
                onSuccess: (next) => navigate(`/t/${run.project_id}/${next.id}`),
              })
            }
          >
            Translate again
          </button>
        ) : null}
      </div>
      {run.state === "failed" && run.error ? (
        <details>
          <summary>Details</summary>
          <p dir="auto">{run.error}</p>
        </details>
      ) : null}
      {action.isError ? <ErrorMessage error={action.error} /> : null}
      {again.isError ? <ErrorMessage error={again.error} /> : null}
    </section>
  );
}
```

- [ ] **Step 7: Write `web/src/review/DocumentView.tsx`**

```tsx
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { findFlagged, useDocumentPages } from "../api/review";
import type { ReviewSpan, RunDetail } from "../api/types";
import { ErrorMessage, messageOf } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { languageName } from "../lib/languages";
import { SummaryBanner } from "../set/SummaryBanner";
import { scoreFilterFrom } from "./filters";
import { RunLanguages, runLanguages, type RunLanguagesValue } from "./languages";
import { PassagePanel } from "./PassagePanel";
import { useDocumentKeys } from "./useDocumentKeys";

type RowProps = {
  span: ReviewSpan;
  languages: RunLanguagesValue;
  selected: boolean;
  onOpen: () => void;
  onFocus: () => void;
};

function PassageRow({ span, languages, selected, onOpen, onFocus }: RowProps) {
  const flagged = span.flags.some((f) => f.status === "open");
  const classes = [flagged ? "flagged" : "", selected ? "selected" : ""].filter(Boolean).join(" ");
  return (
    <tr
      id={`passage-${span.id}`}
      className={classes || undefined}
      aria-selected={selected}
      tabIndex={0}
      onClick={onOpen}
      onFocus={onFocus}
    >
      <td className="source" dir="auto" lang={languages.source}>
        {flagged ? (
          <>
            <span aria-hidden="true">⚠ </span>
            <span className="visually-hidden">Worth a look: </span>
          </>
        ) : null}
        {span.source}
      </td>
      <td className="target">
        <span dir="auto" lang={languages.target}>
          {span.text}
        </span>
        {span.edited ? <span className="tag">edited</span> : null}
      </td>
    </tr>
  );
}

/** Spec §5.3: the original and the translation side by side, the passage panel beside them. */
export function DocumentView({ detail }: { detail: RunDetail }) {
  const runId = detail.run.id;
  const [params, setParams] = useSearchParams();
  const scores = useMemo(() => scoreFilterFrom(params), [params]);
  const byScore = Object.keys(scores).length > 0;
  const [show, setShow] = useState<"all" | "check">("all");
  const pages = useDocumentPages(runId, show === "check", scores);
  const spans = useMemo(() => pages.data?.pages.flatMap((p) => p.spans) ?? [], [pages.data]);
  const total = pages.data?.pages[0]?.total ?? 0;
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [prefill, setPrefill] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const showId = useId();
  const languages = runLanguages(detail);
  const selectedSpan = spans.find((s) => s.id === selected);
  const sentinel = useRef<HTMLDivElement>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = pages;

  useEffect(() => {
    if (selected !== null) {
      document.getElementById(`passage-${selected}`)?.scrollIntoView({ block: "center" });
    }
  }, [selected]);

  useEffect(() => {
    const element = sentinel.current;
    if (!element || !hasNextPage || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && !isFetchingNextPage) void fetchNextPage();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  async function reveal(target: ReviewSpan) {
    let data = pages.data;
    let more = pages.hasNextPage;
    const loaded = () => data?.pages.some((p) => p.spans.some((s) => s.id === target.id)) ?? false;
    while (!loaded() && more) {
      const result = await fetchNextPage();
      data = result.data;
      more = result.hasNextPage;
    }
    setSelected(target.id);
    setOpen(true);
  }

  async function goToFlag(forward: boolean) {
    setNote(null);
    const from = selectedSpan?.number ?? (forward ? 0 : Number.MAX_SAFE_INTEGER);
    try {
      const target = await findFlagged(runId, from, forward);
      if (target) await reveal(target);
      else setNote(forward ? "No later passage to check." : "No earlier passage to check.");
    } catch (error) {
      setNote(messageOf(error));
    }
  }

  useDocumentKeys({
    next: () => void goToFlag(true),
    previous: () => void goToFlag(false),
    toggle: () => {
      if (selected !== null) setOpen(!open);
    },
    close: () => setOpen(false),
  });

  function onMouseUp() {
    const selection = document.getSelection();
    const text = selection?.toString().trim() ?? "";
    const cell = selection?.anchorNode?.parentElement?.closest("td.source");
    if (text && text.length <= 200 && cell) setPrefill(text);
  }

  const panelOpen = open && selectedSpan !== undefined;
  return (
    <RunLanguages.Provider value={languages}>
      <SummaryBanner detail={detail} onNext={() => void goToFlag(true)} />
      <div className="row between doc-tools">
        <div className="row">
          <label htmlFor={showId}>Show</label>
          <select
            id={showId}
            value={show}
            onChange={(e) => {
              setShow(e.target.value as "all" | "check");
              setOpen(false);
            }}
          >
            <option value="all">Everything</option>
            <option value="check">To check</option>
          </select>
        </div>
        {byScore ? (
          <p className="notice">
            Showing passages by score.{" "}
            <button type="button" className="link" onClick={() => setParams({})}>
              Show everything
            </button>
          </p>
        ) : null}
        <p role="status" className="muted">
          {note}
        </p>
      </div>
      <div className={panelOpen ? "review with-panel" : "review"}>
        <div className="document-wrap">
          {pages.isPending ? <Loading /> : null}
          {pages.isError ? <ErrorMessage error={pages.error} /> : null}
          {pages.data && spans.length === 0 ? (
            <p className="muted">
              {show === "check" ? "Nothing left to check." : "No passages to show."}
            </p>
          ) : null}
          {spans.length > 0 ? (
            <table className="document" aria-label="Document" onMouseUp={onMouseUp}>
              <thead>
                <tr>
                  <th scope="col">Original</th>
                  <th scope="col">{languageName(languages.target)}</th>
                </tr>
              </thead>
              <tbody>
                {spans.map((span) => (
                  <PassageRow
                    key={span.id}
                    span={span}
                    languages={languages}
                    selected={span.id === selected}
                    onOpen={() => {
                      setSelected(span.id);
                      setOpen(true);
                    }}
                    onFocus={() => setSelected(span.id)}
                  />
                ))}
              </tbody>
            </table>
          ) : null}
          {hasNextPage ? (
            <div ref={sentinel} className="load-more">
              <button
                type="button"
                disabled={isFetchingNextPage}
                onClick={() => void fetchNextPage()}
              >
                Load more passages
              </button>
            </div>
          ) : null}
          {spans.length > 0 ? (
            <p className="muted">
              {spans.length} of {total} passages shown
            </p>
          ) : null}
        </div>
        {panelOpen ? (
          <PassagePanel
            key={selectedSpan.id}
            runId={runId}
            projectId={detail.run.project_id}
            spanId={selectedSpan.id}
            prefill={prefill}
            onNext={() => void goToFlag(true)}
            onClose={() => setOpen(false)}
          />
        ) : null}
      </div>
    </RunLanguages.Provider>
  );
}
```

- [ ] **Step 8: Add the document and banner rules** to `web/src/styles/review.css`

```css
.banner {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border);
  border-inline-start: 4px solid var(--border-strong);
  border-radius: var(--radius-2);
  background: var(--surface);
}

.banner p {
  margin: 0;
}

.banner-text {
  font-weight: 600;
}

.banner-ok {
  border-inline-start-color: var(--ok);
}

.banner-bad {
  border-inline-start-color: var(--danger);
}

.doc-tools {
  margin-bottom: var(--space-3);
}

.review {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.review.with-panel {
  grid-template-columns: minmax(0, 1fr) var(--panel-width);
}

.review .passage-panel {
  position: sticky;
  top: calc(var(--topbar-height) + var(--space-4));
  max-height: calc(100vh - var(--topbar-height) - 2 * var(--space-4));
  overflow-y: auto;
}

.document {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-2);
}

.document th {
  position: sticky;
  top: var(--topbar-height);
  padding: var(--space-2) var(--space-3);
  background: var(--surface-2);
  color: var(--text-muted);
  font-size: var(--text-xs);
  text-align: start;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.document td {
  padding: var(--space-2) var(--space-3);
  border-top: 1px solid var(--border);
  vertical-align: top;
}

.document tr {
  cursor: pointer;
}

.document tr.flagged td {
  background: var(--warn-tint);
}

.document tr.flagged td.source {
  box-shadow: inset 3px 0 0 var(--warn);
}

.document tr.selected td {
  background: var(--warn-tint-strong);
}

.document tr:not(.flagged).selected td {
  background: var(--selected);
}

.tag {
  margin-inline-start: var(--space-2);
  padding: 0 var(--space-1);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-1);
  color: var(--text-muted);
  font-size: var(--text-xs);
}

.load-more {
  display: flex;
  justify-content: center;
  padding: var(--space-4);
}

/* Spec §7.3: between 1024 and 1279 px the panel overlays the document instead of narrowing it. */
@media (max-width: 1279px) {
  .review.with-panel {
    grid-template-columns: minmax(0, 1fr);
  }

  .review .passage-panel {
    position: fixed;
    top: var(--topbar-height);
    right: 0;
    bottom: 0;
    z-index: 12;
    width: min(var(--panel-width), 92vw);
    max-height: none;
    border-radius: 0;
    box-shadow: var(--shadow);
  }
}
```

- [ ] **Step 9: Run the tests, every web gate, and commit**

Run: `cd web && pnpm vitest run src/set src/review && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src
git commit -m "feat(web): side-by-side document with the summary banner, walking the passages to check"
```

---

### Task 10: One file's run — progress, waiting, finished

Spec §4.2, §4.4, §5.2 and plan decision 5. Decides what a file's view shows for each run state, follows live updates, and sets the browser tab title.

**Files:**
- Create: `web/src/guide/mode.ts`
- Create: `web/src/set/ProgressSteps.tsx`, `web/src/set/RunView.tsx`, `web/src/set/RunView.test.tsx`
- Modify: `web/src/styles/review.css` (step list)

**Interfaces:**
- Consumes: `useRun`, `useRunAction` (`api/runs.ts`); `useRunEvents` (`api/events.ts`); `useApproveAsIs` (Task 4); `STEPS`, `stepIndex`, `percentDone` (Task 3); `Dialog` (Task 5); `SummaryBanner`, `DocumentView` (Task 9); `isTerminal` (`lib/format.ts`).
- Produces:
  - `guide/mode.ts`: `type GuideMode = { kind: "project" } | { kind: "draft"; runId: string }`.
  - `ProgressSteps({ detail }: { detail: RunDetail })`.
  - `RunView({ runId, onOpenGuide }: { runId: string; onOpenGuide: (mode: GuideMode) => void })`.
  - `useRunTitle(detail: RunDetail | undefined): void`.

- [ ] **Step 1: Write the failing test** — `web/src/set/RunView.test.tsx`

```tsx
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setEventSourceFactory } from "../api/events";
import type { RunDetail } from "../api/types";
import { progress, reviewSpans, runDetail, spanPage } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { RunView } from "./RunView";

beforeEach(() => {
  setEventSourceFactory(null); // poll instead of streaming
  document.title = "Coglossa";
});
afterEach(() => setEventSourceFactory(undefined));

function serve(detail: RunDetail) {
  server.use(
    http.get("*/api/runs/r-1", () => HttpResponse.json(detail)),
    http.get("*/api/runs/r-1/spans", () => HttpResponse.json(spanPage(reviewSpans))),
  );
}

const finished = (state: RunDetail["run"]["state"]): RunDetail => ({
  ...runDetail,
  run: { ...runDetail.run, state, finished_at: "2026-10-01T09:05:00Z", error: null },
  progress: { ...runDetail.progress, state },
  report_available: state === "complete" || state === "complete_with_errors",
  stats: { spans: 3, open_flags: 2, flags_by_code: {}, edited_spans: 0 },
});

it("shows plain steps and the percentage while translating", async () => {
  serve(runDetail); // translating, 50%
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  const steps = await screen.findByRole("list", { name: "Steps" });
  const items = within(steps).getAllByRole("listitem");
  expect(items.map((i) => i.textContent)).toEqual([
    "✓ Waiting to start",
    "✓ Reading your document",
    "✓ Building the translation guide",
    "● Translating (50%)",
    "○ Checking consistency",
    "○ Ready",
  ]);
  expect(items[3]).toHaveAttribute("aria-current", "step");
  expect(screen.getByText(/You can leave this page; it keeps going\./)).toBeInTheDocument();
  expect(document.title).toBe("50% – histoire.txt");
});

it("stops translating after confirming", async () => {
  let cancelled = false;
  serve(runDetail);
  server.use(
    http.post("*/api/runs/r-1/cancel", () => {
      cancelled = true;
      return HttpResponse.json({ message: "cancelled", progress });
    }),
  );
  const { user } = renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "Stop translating" }));
  const dialog = screen.getByRole("dialog", { name: "Stop translating" });
  await user.click(within(dialog).getByRole("button", { name: "Stop" }));
  await vi.waitFor(() => expect(cancelled).toBe(true));
});

it("offers Continue as is or a look at the guide when a run waits", async () => {
  let approved: unknown;
  serve({ ...runDetail, run: { ...runDetail.run, state: "awaiting_guide_approval" } });
  server.use(
    http.post("*/api/runs/r-1/guide/approve", async ({ request }) => {
      approved = await request.json();
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const onOpenGuide = vi.fn();
  const { user } = renderAt(<RunView runId="r-1" onOpenGuide={onOpenGuide} />);
  expect(await screen.findByText("Waiting for you to check the translation guide.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Look at the guide first" }));
  expect(onOpenGuide).toHaveBeenCalledWith({ kind: "draft", runId: "r-1" });
  await user.click(screen.getByRole("button", { name: "Continue as is" }));
  await vi.waitFor(() => expect(approved).toEqual({}));
});

it("shows the banner and the document when finished, and says Ready in the tab", async () => {
  serve(finished("complete"));
  const { unmount } = renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  expect(await screen.findByRole("table", { name: "Document" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent("Looks good.");
  expect(document.title).toBe("✓ Ready – histoire.txt");
  unmount();
  expect(document.title).toBe("Coglossa");
});

it("shows only the banner when nothing usable came out", async () => {
  serve({ ...finished("failed"), run: { ...finished("failed").run, error: "boom" } });
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  expect(await screen.findByRole("region", { name: "Summary" })).toHaveTextContent(
    "This translation couldn't finish.",
  );
  expect(screen.queryByRole("table", { name: "Document" })).not.toBeInTheDocument();
});

it("says it is rechecking during a re-sweep", async () => {
  serve({ ...finished("sweeping"), report_available: false });
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  expect(await screen.findByText("Rechecking against your translation guide…")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Stop translating" })).not.toBeInTheDocument();
});

it("shows why a run could not be loaded", async () => {
  server.use(
    http.get("*/api/runs/r-1", () => HttpResponse.json({ detail: "run r-1 not found" }, { status: 404 })),
  );
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("run r-1 not found");
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd web && pnpm vitest run src/set/RunView.test.tsx`
Expected: FAIL — `./RunView` not found.

- [ ] **Step 3: Write `web/src/guide/mode.ts`**

```ts
/** What the translation guide panel edits: the set's guide, or a waiting run's draft (§4.4). */
export type GuideMode = { kind: "project" } | { kind: "draft"; runId: string };
```

- [ ] **Step 4: Write `web/src/set/ProgressSteps.tsx`**

```tsx
import { useState } from "react";
import { useRunAction } from "../api/runs";
import type { RunDetail } from "../api/types";
import { Dialog } from "../components/Dialog";
import { ErrorMessage } from "../components/ErrorMessage";
import { percentDone, stepIndex, STEPS } from "../lib/status";

const TRANSLATING = 3;

/** Spec §4.2: plain steps, a tick on each finished one, and a quiet Stop. */
export function ProgressSteps({ detail }: { detail: RunDetail }) {
  const action = useRunAction(detail.run.id, detail.run.project_id);
  const [confirming, setConfirming] = useState(false);
  const current = stepIndex(detail.run.state);
  const percent = percentDone(detail.progress);
  return (
    <section aria-label="Progress" className="progress-steps">
      <ol aria-label="Steps">
        {STEPS.map((label, i) => {
          const state = i < current ? "done" : i === current ? "current" : "todo";
          const mark = state === "done" ? "✓" : state === "current" ? "●" : "○";
          return (
            <li key={label} className={state} aria-current={state === "current" ? "step" : undefined}>
              <span aria-hidden="true">{mark} </span>
              {label}
              {i === TRANSLATING && state === "current" ? ` (${percent}%)` : ""}
            </li>
          );
        })}
      </ol>
      <p className="muted">
        This usually takes a few minutes. You can leave this page; it keeps going.
      </p>
      <button type="button" className="quiet" onClick={() => setConfirming(true)}>
        Stop translating
      </button>
      {action.isError ? <ErrorMessage error={action.error} /> : null}
      <Dialog open={confirming} label="Stop translating" onClose={() => setConfirming(false)}>
        <h2>Stop translating?</h2>
        <p>A stopped translation can&apos;t be resumed. You can translate the file again later.</p>
        <div className="row end">
          <button type="button" onClick={() => setConfirming(false)}>
            Keep going
          </button>
          <button
            type="button"
            className="danger"
            disabled={action.isPending}
            onClick={() => action.mutate("cancel", { onSettled: () => setConfirming(false) })}
          >
            Stop
          </button>
        </div>
      </Dialog>
    </section>
  );
}
```

The test's `textContent` expectations assume the mark span and the label render as "✓ Waiting to start" (the `aria-hidden` span holds "✓ ").

- [ ] **Step 5: Write `web/src/set/RunView.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import { useRunEvents } from "../api/events";
import { useRun } from "../api/runs";
import { useApproveAsIs } from "../api/sets";
import type { RunDetail } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import type { GuideMode } from "../guide/mode";
import { isTerminal } from "../lib/format";
import { percentDone } from "../lib/status";
import { DocumentView } from "../review/DocumentView";
import { ProgressSteps } from "./ProgressSteps";
import { SummaryBanner } from "./SummaryBanner";

/** Spec §4.2: the tab title follows the open file. */
export function useRunTitle(detail: RunDetail | undefined): void {
  useEffect(() => {
    if (detail === undefined) return;
    const before = document.title;
    const { state, filename } = detail.run;
    if (state === "complete" || state === "complete_with_errors") {
      document.title = `✓ Ready – ${filename}`;
    } else if (!isTerminal(state)) {
      document.title = `${percentDone(detail.progress)}% – ${filename}`;
    }
    return () => {
      document.title = before;
    };
  }, [detail]);
}

function WaitingForGuide({
  detail,
  onOpenGuide,
}: {
  detail: RunDetail;
  onOpenGuide: (mode: GuideMode) => void;
}) {
  const approve = useApproveAsIs(detail.run.id, detail.run.project_id);
  return (
    <section aria-label="Waiting for you" className="notice">
      <p>Waiting for you to check the translation guide.</p>
      <div className="row">
        <button
          type="button"
          className="primary"
          disabled={approve.isPending}
          onClick={() => approve.mutate()}
        >
          Continue as is
        </button>
        <button type="button" onClick={() => onOpenGuide({ kind: "draft", runId: detail.run.id })}>
          Look at the guide first
        </button>
      </div>
      {approve.isError ? <ErrorMessage error={approve.error} /> : null}
    </section>
  );
}

/** One file's run, whatever its state. */
export function RunView({ runId, onOpenGuide }: { runId: string; onOpenGuide: (mode: GuideMode) => void }) {
  const [streamFailed, setStreamFailed] = useState(false);
  const onStreamFailed = useCallback(() => setStreamFailed(true), []);
  const run = useRun(runId, streamFailed);
  const detail = run.data;
  useRunEvents(runId, detail !== undefined && !isTerminal(detail.run.state), onStreamFailed);
  useRunTitle(detail);
  if (run.isError) return <ErrorMessage error={run.error} />;
  if (detail === undefined) return <Loading />;
  const { state, finished_at } = detail.run;
  if (state === "awaiting_guide_approval") {
    return <WaitingForGuide detail={detail} onOpenGuide={onOpenGuide} />;
  }
  if (state === "sweeping" && finished_at !== null) {
    // plan decision 5: a recheck of a finished file
    return (
      <p role="status" className="notice">
        Rechecking against your translation guide…
      </p>
    );
  }
  if (!isTerminal(state)) return <ProgressSteps detail={detail} />;
  if (detail.report_available) return <DocumentView detail={detail} />;
  return <SummaryBanner detail={detail} />;
}
```

- [ ] **Step 6: Add the step list rules** to `web/src/styles/review.css`

```css
.progress-steps ol {
  list-style: none;
  margin: 0 0 var(--space-4);
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-lg);
}

.progress-steps .done {
  color: var(--ok);
}

.progress-steps .current {
  color: var(--accent);
  font-weight: 600;
}

.progress-steps .todo {
  color: var(--text-muted);
}
```

- [ ] **Step 7: Run the test, every web gate, and commit**

Run: `cd web && pnpm vitest run src/set && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src
git commit -m "feat(web): a file's run view — plain steps, waiting for the guide, banner and document"
```

---
### Task 11: The translation guide panel

Spec §5.5 and §4.4 ("Look at the guide first"). A wide dialog with Names, Terms and Style tabs; readable rows that open into small forms; save with conflict handling; the recheck prompt after saving.

**Files:**
- Modify: `web/src/guide/entries.ts` (add `EMPTY_GUIDE`, `emptyTerm`, `sameGuide`, `guideProblems`, `KIND_WORDS`)
- Create: `web/src/guide/GuideRows.tsx` (`NameRows`, `TermRows`, `StyleFields`)
- Create: `web/src/guide/GuidePanel.tsx`, `web/src/guide/GuidePanel.test.tsx`
- Create: `web/src/styles/guide.css` (import in `main.tsx` after `review.css`)

**Interfaces:**
- Consumes: `useProjectGuide`, `useSaveProjectGuide`, `useRunGuide`, `useApproveGuide` (`api/guide.ts`); `isGuideChanged`, `ApiError` (`api/client.ts`); `useRecheckSet` (Task 4); `Dialog` (Task 5); `GuideMode` (Task 10); `emptyEntity`, `ENTITY_KINDS` (Task 8); `plural` (Task 3).
- Produces:
  - `guide/entries.ts`: `EMPTY_GUIDE: Guide`, `emptyTerm(): GlossaryEntry`, `sameGuide(a: Guide, b: Guide): boolean`, `guideProblems(guide: Guide): string[]`, `KIND_WORDS: Record<string, string>`.
  - `GuidePanel({ projectId, mode, onClose }: { projectId: string; mode: GuideMode | null; onClose: () => void })` — closed when `mode` is null.

The old `GuideEditor.tsx` keeps its own `problems()` until Task 14 deletes it; do not change it.

- [ ] **Step 1: Add the guide helpers** to `web/src/guide/entries.ts`

```ts
import type { Entity, GlossaryEntry, Guide } from "../api/types";

export const EMPTY_GUIDE: Guide = {
  summary: "",
  style_guide: "",
  glossary: [],
  entities: [],
  cultural_references: [],
};

export const KIND_WORDS: Record<string, string> = {
  person: "Person",
  place: "Place",
  organisation: "Organisation",
  work: "Title of a work",
  other: "Other",
};

export function emptyTerm(): GlossaryEntry {
  return { term: "", rendering: "", notes: "", locked: false };
}

export function sameGuide(a: Guide, b: Guide): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** What stops a guide from being saved, in plain words (the API refuses the same). */
export function guideProblems(guide: Guide): string[] {
  const found: string[] = [];
  if (!guide.summary.trim()) found.push("Add a short summary on the Style tab.");
  if (guide.glossary.some((g) => !g.term.trim())) found.push("Every term needs its words.");
  else if (guide.glossary.some((g) => !g.rendering.trim())) found.push("Every term needs a translation.");
  if (guide.entities.some((e) => !e.name.trim())) found.push("Every name needs its words.");
  else if (guide.entities.some((e) => !e.rendering.trim())) found.push("Every name needs a translation.");
  return found;
}
```

(`Entity` stays imported for `emptyEntity`, already in the file.)

- [ ] **Step 2: Write the failing test** — `web/src/guide/GuidePanel.test.tsx`

```tsx
import { fireEvent, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { Guide } from "../api/types";
import { documentSummary, guide, guideView, progress } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { GuidePanel } from "./GuidePanel";
import type { GuideMode } from "./mode";

function renderPanel(mode: GuideMode = { kind: "project" }) {
  const onClose = vi.fn();
  const view = renderAt(<GuidePanel projectId="p-1" mode={mode} onClose={onClose} />);
  return { ...view, onClose };
}

let saved: { guide: Guide; base_version: number | null } | undefined;

beforeEach(() => {
  saved = undefined;
  server.use(
    http.get("*/api/projects/p-1/guide", () => HttpResponse.json(guideView)),
    http.put("*/api/projects/p-1/guide", async ({ request }) => {
      saved = (await request.json()) as typeof saved;
      return HttpResponse.json({ guide: saved!.guide, info: { ...guideView.info, version: 2 } });
    }),
  );
});

it("lists names and terms as readable rows", async () => {
  const { user } = renderPanel();
  const panel = await screen.findByRole("dialog", { name: "Translation guide" });
  const names = await within(panel).findByRole("list", { name: "Names" });
  expect(names).toHaveTextContent("Marie → Marie · always");
  await user.click(within(panel).getByRole("tab", { name: "Terms" }));
  expect(within(panel).getByRole("list", { name: "Terms" })).toHaveTextContent("chat → cat");
});

it("edits a name, saves it, and offers to update the translations", async () => {
  const swept: string[] = [];
  server.use(
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])),
    http.post("*/api/runs/:runId/sweep", ({ params }) => {
      swept.push(params["runId"] as string);
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit Marie" }));
  const form = screen.getByRole("group", { name: "Name 1" });
  await user.clear(within(form).getByLabelText("Translation"));
  await user.type(within(form).getByLabelText("Translation"), "Mary");
  await user.click(within(form).getByText("More"));
  await user.clear(within(form).getByLabelText("Forms of address"));
  await user.type(within(form).getByLabelText("Forms of address"), "usted");
  await user.click(within(form).getByRole("button", { name: "Done" }));
  expect(screen.getByRole("list", { name: "Names" })).toHaveTextContent("Marie → Mary · always");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Saved. Update the translations to match?")).toBeInTheDocument();
  expect(saved?.base_version).toBe(1);
  expect(saved?.guide.entities[0]).toMatchObject({ rendering: "Mary", forms_of_address: "usted" });
  await user.click(screen.getByRole("button", { name: "Update the translations" }));
  expect(await screen.findByText("Rechecking 1 file…")).toBeInTheDocument();
  expect(swept).toEqual(["r-1"]);
});

it("adds a term and will not save it without a translation", async () => {
  const { user } = renderPanel();
  await screen.findByRole("list", { name: "Names" });
  await user.click(screen.getByRole("tab", { name: "Terms" }));
  await user.click(screen.getByRole("button", { name: "+ Add a term" }));
  const form = screen.getByRole("group", { name: "Term 2" });
  await user.type(within(form).getByLabelText("Term"), "fenêtre");
  expect(screen.getByText("Every term needs a translation.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  await user.type(within(form).getByLabelText("Translation"), "window");
  await user.click(within(form).getByLabelText("Always use this translation"));
  await user.click(screen.getByRole("button", { name: "Save" }));
  await vi.waitFor(() =>
    expect(saved?.guide.glossary[1]).toEqual({ term: "fenêtre", rendering: "window", notes: "", locked: true }),
  );
});

it("removes a name", async () => {
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit Marie" }));
  await user.click(within(screen.getByRole("group", { name: "Name 1" })).getByRole("button", { name: "Remove" }));
  expect(screen.getByText("No names yet.")).toBeInTheDocument();
});

it("edits the style fields, one cultural reference per line", async () => {
  const { user } = renderPanel();
  await screen.findByRole("list", { name: "Names" });
  await user.click(screen.getByRole("tab", { name: "Style" }));
  const style = screen.getByLabelText("Style guide");
  await user.clear(style);
  await user.type(style, "Warm and plain.");
  const culture = screen.getByLabelText("Cultural references (one per line)");
  await user.type(culture, "{Enter}le 14 juillet{Enter}");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await vi.waitFor(() => expect(saved?.guide.style_guide).toBe("Warm and plain."));
  expect(saved?.guide.cultural_references).toEqual([...guide.cultural_references, "le 14 juillet"]);
});

it("explains a conflict and reloads the current guide", async () => {
  let reads = 0;
  server.use(
    http.get("*/api/projects/p-1/guide", () => {
      reads += 1;
      const rendering = reads === 1 ? "Marie" : "Maria";
      return HttpResponse.json({
        ...guideView,
        guide: { ...guide, entities: [{ ...guide.entities[0]!, rendering }] },
        info: { ...guideView.info, version: reads },
      });
    }),
    http.put("*/api/projects/p-1/guide", () =>
      HttpResponse.json({ detail: "the guide changed", code: "guide_changed" }, { status: 409 }),
    ),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit Marie" }));
  await user.type(within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"), "x");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Someone else changed the guide. Reload to see their changes.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Reload" }));
  expect(await screen.findByText("Marie → Maria · always")).toBeInTheDocument();
});

it("starts a guide when the set has none", async () => {
  server.use(
    http.get("*/api/projects/p-1/guide", () =>
      HttpResponse.json({ detail: "no guide yet" }, { status: 404 }),
    ),
  );
  const { user } = renderPanel();
  expect(await screen.findByText("No names yet.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "+ Add a name" }));
  const form = screen.getByRole("group", { name: "Name 1" });
  await user.type(within(form).getByLabelText("Name"), "Riverside Community Centre");
  await user.type(within(form).getByLabelText("Translation"), "Centro Comunitario Riverside");
  expect(screen.getByText("Add a short summary on the Style tab.")).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Style" }));
  await user.type(screen.getByLabelText("Summary"), "A welcome letter.");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await vi.waitFor(() => expect(saved?.base_version).toBeNull());
});

it("asks before closing with unsaved changes", async () => {
  const { user, onClose } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit Marie" }));
  await user.type(within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"), "x");
  fireEvent(screen.getByRole("dialog", { name: "Translation guide" }), new Event("cancel", { cancelable: true }));
  const ask = screen.getByRole("alertdialog", { name: "Unsaved changes" });
  await user.click(within(ask).getByRole("button", { name: "Keep editing" }));
  expect(onClose).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Close the translation guide" }));
  await user.click(screen.getByRole("button", { name: "Close without saving" }));
  expect(onClose).toHaveBeenCalled();
});

it("closes at once when nothing changed", async () => {
  const { user, onClose } = renderPanel();
  await screen.findByRole("list", { name: "Names" });
  await user.click(screen.getByRole("button", { name: "Close the translation guide" }));
  expect(onClose).toHaveBeenCalled();
});

it("approves a waiting run's draft guide and closes", async () => {
  let approved: unknown;
  server.use(
    http.get("*/api/runs/r-1/guide", () => HttpResponse.json(guideView)),
    http.post("*/api/runs/r-1/guide/approve", async ({ request }) => {
      approved = await request.json();
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const { user, onClose } = renderPanel({ kind: "draft", runId: "r-1" });
  expect(await screen.findByText(/then start translating/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Save and translate" }));
  await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(approved).toEqual({ guide, base_version: 1 });
});
```

- [ ] **Step 3: Run it to see it fail**

Run: `cd web && pnpm vitest run src/guide/GuidePanel.test.tsx`
Expected: FAIL — `./GuidePanel` not found.

- [ ] **Step 4: Write `web/src/guide/GuideRows.tsx`**

```tsx
import { useId, useState } from "react";
import type { Entity, GlossaryEntry, Guide } from "../api/types";
import { emptyEntity, emptyTerm, ENTITY_KINDS, KIND_WORDS } from "./entries";

function Summary({ from, to, always }: { from: string; to: string; always: boolean }) {
  return (
    <>
      <span dir="auto">{from || "…"}</span> → <span dir="auto">{to || "…"}</span>
      {always ? <span className="muted"> · always</span> : null}
    </>
  );
}

function NameForm({
  entity,
  n,
  onChange,
  onRemove,
  onDone,
}: {
  entity: Entity;
  n: number;
  onChange: (patch: Partial<Entity>) => void;
  onRemove: () => void;
  onDone: () => void;
}) {
  const id = useId();
  return (
    <fieldset className="guide-row-form">
      <legend>Name {n}</legend>
      <div className="field">
        <label htmlFor={`${id}-name`}>Name</label>
        <input id={`${id}-name`} dir="auto" value={entity.name} onChange={(e) => onChange({ name: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-rendering`}>Translation</label>
        <input
          id={`${id}-rendering`}
          dir="auto"
          value={entity.rendering}
          onChange={(e) => onChange({ rendering: e.target.value })}
        />
      </div>
      <label>
        <input type="checkbox" checked={entity.locked} onChange={(e) => onChange({ locked: e.target.checked })} />{" "}
        Always use this translation
      </label>
      <div className="field">
        <label htmlFor={`${id}-kind`}>Kind</label>
        <select id={`${id}-kind`} value={entity.kind} onChange={(e) => onChange({ kind: e.target.value })}>
          {[...new Set([...ENTITY_KINDS, entity.kind])].map((kind) => (
            <option key={kind} value={kind}>
              {KIND_WORDS[kind] ?? kind}
            </option>
          ))}
        </select>
      </div>
      <details>
        <summary>More</summary>
        <div className="field">
          <label htmlFor={`${id}-gender`}>Gender</label>
          <input
            id={`${id}-gender`}
            dir="auto"
            value={entity.gender ?? ""}
            onChange={(e) => onChange({ gender: e.target.value || null })}
          />
        </div>
        <div className="field">
          <label htmlFor={`${id}-address`}>Forms of address</label>
          <input
            id={`${id}-address`}
            dir="auto"
            value={entity.forms_of_address}
            onChange={(e) => onChange({ forms_of_address: e.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor={`${id}-speech`}>Speech style</label>
          <input
            id={`${id}-speech`}
            dir="auto"
            value={entity.speech_style}
            onChange={(e) => onChange({ speech_style: e.target.value })}
          />
        </div>
      </details>
      <div className="row between">
        <button type="button" className="danger" onClick={onRemove}>
          Remove
        </button>
        <button type="button" onClick={onDone}>
          Done
        </button>
      </div>
    </fieldset>
  );
}

export function NameRows({ names, onChange }: { names: Entity[]; onChange: (names: Entity[]) => void }) {
  const [open, setOpen] = useState<number | null>(null);
  const update = (i: number, patch: Partial<Entity>) =>
    onChange(names.map((e, k) => (k === i ? { ...e, ...patch } : e)));
  return (
    <div className="guide-rows">
      {names.length === 0 ? <p className="muted">No names yet.</p> : null}
      <ul aria-label="Names">
        {names.map((entity, i) => (
          <li key={i} className="guide-row">
            {open === i ? (
              <NameForm
                entity={entity}
                n={i + 1}
                onChange={(patch) => update(i, patch)}
                onRemove={() => {
                  onChange(names.filter((_, k) => k !== i));
                  setOpen(null);
                }}
                onDone={() => setOpen(null)}
              />
            ) : (
              <button
                type="button"
                className="row-summary"
                aria-label={`Edit ${entity.name || `name ${i + 1}`}`}
                onClick={() => setOpen(i)}
              >
                <Summary from={entity.name} to={entity.rendering} always={entity.locked} />
              </button>
            )}
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={() => {
          onChange([...names, emptyEntity()]);
          setOpen(names.length);
        }}
      >
        + Add a name
      </button>
    </div>
  );
}

function TermForm({
  term,
  n,
  onChange,
  onRemove,
  onDone,
}: {
  term: GlossaryEntry;
  n: number;
  onChange: (patch: Partial<GlossaryEntry>) => void;
  onRemove: () => void;
  onDone: () => void;
}) {
  const id = useId();
  return (
    <fieldset className="guide-row-form">
      <legend>Term {n}</legend>
      <div className="field">
        <label htmlFor={`${id}-term`}>Term</label>
        <input id={`${id}-term`} dir="auto" value={term.term} onChange={(e) => onChange({ term: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-rendering`}>Translation</label>
        <input
          id={`${id}-rendering`}
          dir="auto"
          value={term.rendering}
          onChange={(e) => onChange({ rendering: e.target.value })}
        />
      </div>
      <label>
        <input type="checkbox" checked={term.locked} onChange={(e) => onChange({ locked: e.target.checked })} />{" "}
        Always use this translation
      </label>
      <details>
        <summary>More</summary>
        <div className="field">
          <label htmlFor={`${id}-notes`}>Notes</label>
          <input id={`${id}-notes`} dir="auto" value={term.notes} onChange={(e) => onChange({ notes: e.target.value })} />
        </div>
      </details>
      <div className="row between">
        <button type="button" className="danger" onClick={onRemove}>
          Remove
        </button>
        <button type="button" onClick={onDone}>
          Done
        </button>
      </div>
    </fieldset>
  );
}

export function TermRows({ terms, onChange }: { terms: GlossaryEntry[]; onChange: (terms: GlossaryEntry[]) => void }) {
  const [open, setOpen] = useState<number | null>(null);
  const update = (i: number, patch: Partial<GlossaryEntry>) =>
    onChange(terms.map((t, k) => (k === i ? { ...t, ...patch } : t)));
  return (
    <div className="guide-rows">
      {terms.length === 0 ? <p className="muted">No terms yet.</p> : null}
      <ul aria-label="Terms">
        {terms.map((term, i) => (
          <li key={i} className="guide-row">
            {open === i ? (
              <TermForm
                term={term}
                n={i + 1}
                onChange={(patch) => update(i, patch)}
                onRemove={() => {
                  onChange(terms.filter((_, k) => k !== i));
                  setOpen(null);
                }}
                onDone={() => setOpen(null)}
              />
            ) : (
              <button
                type="button"
                className="row-summary"
                aria-label={`Edit ${term.term || `term ${i + 1}`}`}
                onClick={() => setOpen(i)}
              >
                <Summary from={term.term} to={term.rendering} always={term.locked} />
              </button>
            )}
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={() => {
          onChange([...terms, emptyTerm()]);
          setOpen(terms.length);
        }}
      >
        + Add a term
      </button>
    </div>
  );
}

export function StyleFields({ guide, onChange }: { guide: Guide; onChange: (patch: Partial<Guide>) => void }) {
  const id = useId();
  const [culture, setCulture] = useState(guide.cultural_references.join("\n"));
  return (
    <div className="style-fields">
      <div className="field">
        <label htmlFor={`${id}-summary`}>Summary</label>
        <textarea id={`${id}-summary`} dir="auto" rows={3} value={guide.summary} onChange={(e) => onChange({ summary: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-style`}>Style guide</label>
        <textarea id={`${id}-style`} dir="auto" rows={4} value={guide.style_guide} onChange={(e) => onChange({ style_guide: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-culture`}>Cultural references (one per line)</label>
        <textarea
          id={`${id}-culture`}
          dir="auto"
          rows={4}
          value={culture}
          onChange={(e) => {
            setCulture(e.target.value);
            onChange({
              cultural_references: e.target.value
                .split("\n")
                .map((line) => line.trim())
                .filter(Boolean),
            });
          }}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Write `web/src/guide/GuidePanel.tsx`**

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, isGuideChanged } from "../api/client";
import { useApproveGuide, useProjectGuide, useRunGuide, useSaveProjectGuide } from "../api/guide";
import { useRecheckSet } from "../api/sets";
import type { Guide, GuideView } from "../api/types";
import { Dialog } from "../components/Dialog";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { plural } from "../lib/flags";
import { EMPTY_GUIDE, guideProblems, sameGuide } from "./entries";
import { NameRows, StyleFields, TermRows } from "./GuideRows";
import type { GuideMode } from "./mode";

type Tab = "names" | "terms" | "style";
const TABS: { id: Tab; label: string }[] = [
  { id: "names", label: "Names" },
  { id: "terms", label: "Terms" },
  { id: "style", label: "Style" },
];

type Shared = {
  projectId: string;
  onDirty: (dirty: boolean) => void;
  onDone: () => void;
  onRequestClose: () => void;
};

function RecheckPrompt({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const recheck = useRecheckSet(projectId);
  return (
    <div className="notice">
      <p>Saved. Update the translations to match?</p>
      {recheck.isSuccess ? (
        <p role="status">
          {recheck.data.started.length > 0
            ? `Rechecking ${plural(recheck.data.started.length, "file")}…`
            : "Nothing to recheck yet. New translations in this set will follow the guide."}
        </p>
      ) : null}
      {recheck.isError ? <ErrorMessage error={recheck.error} /> : null}
      <div className="row end">
        <button type="button" onClick={onDone}>
          {recheck.isSuccess ? "Close" : "Not now"}
        </button>
        {!recheck.isSuccess ? (
          <button type="button" className="primary" disabled={recheck.isPending} onClick={() => recheck.mutate()}>
            Update the translations
          </button>
        ) : null}
      </div>
    </div>
  );
}

function GuideForm({
  initial,
  base,
  mode,
  reload,
  projectId,
  onDirty,
  onDone,
  onRequestClose,
}: Shared & { initial: Guide; base: number | null; mode: GuideMode; reload: () => void }) {
  const [draft, setDraft] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const [tab, setTab] = useState<Tab>("names");
  const [saved, setSaved] = useState(false);
  const save = useSaveProjectGuide(projectId);
  const approve = useApproveGuide(mode.kind === "draft" ? mode.runId : "", projectId);
  const dirty = !sameGuide(draft, baseline);
  useEffect(() => onDirty(dirty), [dirty, onDirty]);
  const problems = guideProblems(draft);
  const pending = save.isPending || approve.isPending;
  const error = save.error ?? approve.error;
  const set = (patch: Partial<Guide>) => setDraft((d) => ({ ...d, ...patch }));

  async function submit() {
    setSaved(false);
    try {
      if (mode.kind === "project") await save.mutateAsync({ guide: draft, base_version: base });
      else await approve.mutateAsync({ guide: draft, base_version: base ?? 0 });
    } catch {
      return; // shown below
    }
    setBaseline(draft);
    if (mode.kind === "draft") onDone();
    else setSaved(true);
  }

  return (
    <div className="guide-form">
      <div className="row between">
        <h2>Translation guide</h2>
        <button type="button" className="quiet" aria-label="Close the translation guide" onClick={onRequestClose}>
          ×
        </button>
      </div>
      <p className="muted">
        {mode.kind === "draft"
          ? "Check the names, terms and style, then start translating."
          : "The names, terms and style this set's translations follow."}
      </p>
      <div role="tablist" aria-label="Guide sections" className="tabs">
        {TABS.map((t) => (
          <button key={t.id} role="tab" type="button" aria-selected={tab === t.id} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" aria-label={TABS.find((t) => t.id === tab)!.label}>
        {tab === "names" ? <NameRows names={draft.entities} onChange={(entities) => set({ entities })} /> : null}
        {tab === "terms" ? <TermRows terms={draft.glossary} onChange={(glossary) => set({ glossary })} /> : null}
        {tab === "style" ? <StyleFields guide={draft} onChange={set} /> : null}
      </div>
      {problems.map((problem) => (
        <p key={problem} className="error">
          {problem}
        </p>
      ))}
      {isGuideChanged(error) ? (
        <div role="alert" className="notice">
          <p>Someone else changed the guide. Reload to see their changes.</p>
          <button type="button" onClick={reload}>
            Reload
          </button>
        </div>
      ) : error ? (
        <ErrorMessage error={error} />
      ) : null}
      {saved && !dirty ? (
        <RecheckPrompt projectId={projectId} onDone={onDone} />
      ) : (
        <div className="row end">
          <button type="button" onClick={onRequestClose}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            disabled={(mode.kind === "project" && !dirty) || problems.length > 0 || pending}
            onClick={() => void submit()}
          >
            {mode.kind === "draft" ? "Save and translate" : "Save"}
          </button>
        </div>
      )}
    </div>
  );
}

/** Reload (after a conflict) remounts the form with the server's guide, dropping the edits. */
function Loaded({
  query,
  mode,
  allowMissing,
  ...shared
}: Shared & {
  query: { isPending: boolean; isError: boolean; error: unknown; data: GuideView | undefined; refetch: () => Promise<unknown> };
  mode: GuideMode;
  allowMissing: boolean;
}) {
  const [generation, setGeneration] = useState(0);
  if (query.isPending) return <Loading />;
  const missing = allowMissing && query.error instanceof ApiError && query.error.status === 404;
  if (query.isError && !missing) return <ErrorMessage error={query.error} />;
  return (
    <GuideForm
      key={generation}
      initial={query.data?.guide ?? EMPTY_GUIDE}
      base={query.data?.info.version ?? null}
      mode={mode}
      reload={() => void query.refetch().then(() => setGeneration((g) => g + 1))}
      {...shared}
    />
  );
}

function ProjectGuide(shared: Shared) {
  const query = useProjectGuide(shared.projectId);
  return <Loaded query={query} mode={{ kind: "project" }} allowMissing {...shared} />;
}

function DraftGuide({ runId, ...shared }: Shared & { runId: string }) {
  const query = useRunGuide(runId);
  return <Loaded query={query} mode={{ kind: "draft", runId }} allowMissing={false} {...shared} />;
}

/** Spec §5.5. */
export function GuidePanel({ projectId, mode, onClose }: { projectId: string; mode: GuideMode | null; onClose: () => void }) {
  const dirty = useRef(false);
  const [confirming, setConfirming] = useState(false);
  const onDirty = useCallback((value: boolean) => {
    dirty.current = value;
  }, []);
  const close = () => {
    dirty.current = false;
    setConfirming(false);
    onClose();
  };
  const requestClose = () => (dirty.current ? setConfirming(true) : close());
  const shared = { projectId, onDirty, onDone: close, onRequestClose: requestClose };
  return (
    <Dialog open={mode !== null} label="Translation guide" onClose={requestClose} className="dialog guide-panel">
      {mode?.kind === "project" ? <ProjectGuide {...shared} /> : null}
      {mode?.kind === "draft" ? <DraftGuide runId={mode.runId} {...shared} /> : null}
      {confirming ? (
        <div role="alertdialog" aria-label="Unsaved changes" className="notice">
          <p>You have unsaved changes. Close without saving them?</p>
          <div className="row end">
            <button type="button" onClick={() => setConfirming(false)}>
              Keep editing
            </button>
            <button type="button" className="danger" onClick={close}>
              Close without saving
            </button>
          </div>
        </div>
      ) : null}
    </Dialog>
  );
}
```

Notes for the implementer: `useApproveGuide`'s body type wants `base_version: number`; the draft always has a version, so `base ?? 0` never sends 0 in practice. The "conflict" test reloads by refetching the project guide; the query's second read returns version 2 with "Maria".

- [ ] **Step 6: Write `web/src/styles/guide.css`**

```css
/* Spec §5.5: the translation guide panel slides in from the right. */
dialog.guide-panel {
  margin: 0 0 0 auto;
  width: min(var(--guide-width), 100vw);
  max-width: none;
  height: 100vh;
  max-height: none;
  border-radius: 0;
  overflow-y: auto;
}

.guide-form .tabs {
  margin-bottom: var(--space-3);
}

.guide-rows ul {
  list-style: none;
  margin: 0 0 var(--space-3);
  padding: 0;
}

.guide-row {
  border-bottom: 1px solid var(--border);
}

.row-summary {
  width: 100%;
  justify-content: flex-start;
  border: 0;
  border-radius: 0;
  background: transparent;
  text-align: start;
}

.row-summary:hover {
  background: var(--surface-2);
}

.guide-row-form {
  margin: var(--space-2) 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-1);
}
```

- [ ] **Step 7: Run the test, every web gate, and commit**

Run: `cd web && pnpm vitest run src/guide && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src
git commit -m "feat(web): translation guide panel — readable rows, Names/Terms/Style, recheck after saving"
```

---

### Task 12: A set — header, file tabs, adding files

Spec §3.2, §4.3, §5.1. The page at `/t/:projectId/:runId?`: name and languages, Translation guide, Download, the ⋯ menu, file tabs, an earlier-version note, and the run view.

**Files:**
- Create: `web/src/set/SetPage.tsx`, `web/src/set/SetPage.test.tsx`
- Create: `web/src/set/SetHeader.tsx` (`SetHeader`, `DownloadMenu`, `MoreMenu`)
- Create: `web/src/set/FileTabs.tsx`, `web/src/set/FileTabs.test.tsx`
- Create: `web/src/set/AddFilesDialog.tsx`
- Modify: `web/src/styles/review.css` (header and tabs)

**Interfaces:**
- Consumes: `useProject` (`api/projects.ts`); `useSetDocuments`, `useTranslateAgain` (Task 4); `useUploads`, `enqueueUploads`, `retryUpload`, `dismissUpload`, `UploadItem` (Task 4); `useRun` (`api/runs.ts`); `useProjectGuide` (`api/guide.ts`); `exportUrl`, `reportUrl` (`api/client.ts`); `useConfig`, `useTiers` (`api/system.ts`); `Menu`, `Dialog`, `StatusText`, `RenameDialog` (Task 5); `runStateStatus` (Task 3); `languageName` (Task 3); `whenText` (Task 5); `DropArea`, `fileProblem`, `LanguagePicker`, `uploadLimit` (Tasks 6–7); `RunView` (Task 10); `GuidePanel` (Task 11); `GuideMode` (Task 10).
- Produces: `SetPage()`; `type Picked = { doc: DocumentSummary; run: RunSummary; latest: boolean }`, `pickRun(docs: DocumentSummary[], runId: string | undefined): Picked | null`; `FileTabs({ projectId, docs, uploads, picked })`; `AddFilesDialog({ project, picked, open, onClose })`; `SetHeader({ project, picked, onOpenGuide, onAddFiles })`; `DownloadMenu({ runId })`.

- [ ] **Step 1: Write the failing tests**

`web/src/set/FileTabs.test.tsx`:

```tsx
import { screen, within } from "@testing-library/react";
import { documentSummary, runSummary } from "../test/fixtures";
import { renderAt } from "../test/render";
import { dismissUpload, retryUpload, type UploadItem } from "../upload/queue";
import { FileTabs } from "./FileTabs";

vi.mock("../upload/queue", () => ({ retryUpload: vi.fn(), dismissUpload: vi.fn() }));

const docA = { ...documentSummary, id: "d-a", filename: "a.txt", runs: [{ ...runSummary, id: "r-a" }] };
const docB = { ...documentSummary, id: "d-b", filename: "b.txt", runs: [{ ...runSummary, id: "r-b", state: "translating" as const }] };
const upload = (patch: Partial<UploadItem>): UploadItem => ({
  key: "u-1",
  projectId: "p-1",
  name: "c.txt",
  size: 10,
  status: "uploading",
  error: null,
  runId: null,
  ...patch,
});

it("shows nothing for a single file", () => {
  renderAt(<FileTabs projectId="p-1" docs={[docA]} uploads={[]} picked={{ doc: docA, run: docA.runs[0]!, latest: true }} />);
  expect(screen.queryByRole("navigation", { name: "Files" })).not.toBeInTheDocument();
});

it("shows a tab per file with its status, and uploads still under way", async () => {
  renderAt(
    <FileTabs
      projectId="p-1"
      docs={[docA, docB]}
      uploads={[upload({}), upload({ key: "u-2", name: "d.pdf", status: "failed", error: "Too large for this server." })]}
      picked={{ doc: docA, run: docA.runs[0]!, latest: true }}
    />,
  );
  const tabs = screen.getByRole("navigation", { name: "Files" });
  const a = within(tabs).getByRole("link", { name: /a\.txt/ });
  expect(a).toHaveAttribute("aria-current", "page");
  expect(a).toHaveTextContent("ready");
  expect(within(tabs).getByRole("link", { name: /b\.txt/ })).toHaveAttribute("href", "/t/p-1/r-b");
  expect(tabs).toHaveTextContent("c.txt uploading…");
  const failed = within(tabs).getByRole("group", { name: "d.pdf" });
  expect(failed).toHaveTextContent("Too large for this server.");
});

it("retries or removes a failed upload", async () => {
  const { user } = renderAt(
    <FileTabs projectId="p-1" docs={[]} uploads={[upload({ status: "failed", error: "Too large for this server." })]} picked={null} />,
  );
  await user.click(screen.getByRole("button", { name: "Try again" }));
  expect(retryUpload).toHaveBeenCalledWith("u-1");
  await user.click(screen.getByRole("button", { name: "Remove c.txt" }));
  expect(dismissUpload).toHaveBeenCalledWith("u-1");
});
```

`web/src/set/SetPage.test.tsx`:

```tsx
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setEventSourceFactory } from "../api/events";
import type { DocumentSummary, RunDetail } from "../api/types";
import {
  configView,
  documentSummary,
  guideView,
  project,
  reviewSpans,
  runDetail,
  runSummary,
  spanPage,
  tiers,
} from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { enqueueUploads } from "../upload/queue";
import { pickRun, SetPage } from "./SetPage";

vi.mock("../upload/queue", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../upload/queue")>()),
  enqueueUploads: vi.fn(),
}));

const complete = (id: string): RunDetail => ({
  ...runDetail,
  run: { ...runDetail.run, id, state: "complete", finished_at: "2026-10-01T09:05:00Z" },
  progress: { ...runDetail.progress, run_id: id, state: "complete" },
  report_available: true,
  stats: { spans: 3, open_flags: 0, flags_by_code: {}, edited_spans: 0 },
});

function serve(docs: DocumentSummary[]) {
  server.use(
    http.get("*/api/projects/p-1", () => HttpResponse.json(project)),
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json(docs)),
    http.get("*/api/projects/p-1/guide", () => HttpResponse.json(guideView)),
    http.get("*/api/runs/:runId", ({ params }) => HttpResponse.json(complete(params["runId"] as string))),
    http.get("*/api/runs/:runId/spans", () => HttpResponse.json(spanPage(reviewSpans))),
    http.get("*/api/system/config", () => HttpResponse.json(configView)),
    http.get("*/api/system/tiers", () => HttpResponse.json(tiers)),
  );
}

const PATTERN = "/t/:projectId/:runId?";
const open = (path = "/t/p-1") => renderAt(<SetPage />, { path, pattern: PATTERN });

beforeEach(() => setEventSourceFactory(null));
afterEach(() => setEventSourceFactory(undefined));

it("opens a single file straight into its document", async () => {
  serve([documentSummary]);
  const { user } = open();
  expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
  expect(screen.getByText(/French → English · finished/)).toBeInTheDocument();
  expect(await screen.findByRole("table", { name: "Document" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Files" })).not.toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "Download" }));
  const menu = screen.getByRole("menu", { name: "Download" });
  expect(within(menu).getByRole("menuitem", { name: "Word document" })).toHaveAttribute(
    "href",
    "/api/runs/r-1/exports/docx",
  );
  expect(within(menu).getByRole("menuitem", { name: "Quality report" })).toHaveAttribute(
    "href",
    "/api/runs/r-1/report",
  );
});

it("shows a tab per file, in upload order", async () => {
  const later = { ...documentSummary, id: "d-2", filename: "b.txt", created_at: "2026-10-02T09:00:00Z", runs: [{ ...runSummary, id: "r-2", document_id: "d-2" }] };
  serve([later, documentSummary]);
  const { user, router } = open();
  const tabs = await screen.findByRole("navigation", { name: "Files" });
  expect(within(tabs).getAllByRole("link").map((l) => l.textContent)).toEqual([
    "histoire.txt✓ ready",
    "b.txt✓ ready",
  ]);
  await user.click(within(tabs).getByRole("link", { name: /b\.txt/ }));
  expect(router.state.location.pathname).toBe("/t/p-1/r-2");
});

it("marks an earlier version and lists versions in the ⋯ menu", async () => {
  const doc = { ...documentSummary, runs: [{ ...runSummary, id: "r-new" }, runSummary] };
  serve([doc]);
  const { user } = open("/t/p-1/r-1");
  const note = await screen.findByText(/An earlier version\./);
  expect(within(note).getByRole("link", { name: "See the latest" })).toHaveAttribute("href", "/t/p-1/r-new");
  await user.click(screen.getByRole("button", { name: "More actions" }));
  expect(screen.getByRole("menu", { name: "More actions" })).toHaveTextContent("Earlier versions");
});

it("translates the file again from the ⋯ menu", async () => {
  let sent: unknown;
  serve([documentSummary]);
  server.use(
    http.post("*/api/documents/d-1/runs", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...runSummary, id: "r-9", state: "created" }, { status: 202 });
    }),
  );
  const { user, router } = open();
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Translate again" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-1/r-9"));
  expect(sent).toEqual({ checkpoint: false });
});

it("a set with no files offers Add files", async () => {
  serve([]);
  const { user } = open();
  expect(await screen.findByText("No files yet.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "+ Add files" }));
  const dialog = screen.getByRole("dialog", { name: "Add files" });
  expect(dialog).toHaveTextContent("Translating into English.");
  await user.upload(within(dialog).getByLabelText("Choose files"), [new File(["x"], "c.txt")]);
  await user.click(within(dialog).getByRole("button", { name: "Add and translate" }));
  expect(enqueueUploads).toHaveBeenCalledWith(
    "p-1",
    [expect.objectContaining({ name: "c.txt" })],
    { target_lang: "en", source_lang: "fr", pipeline_profile: null },
    expect.anything(),
  );
  expect(screen.queryByRole("dialog", { name: "Add files" })).not.toBeInTheDocument();
});

it("asks for a language when the set has none", async () => {
  serve([]);
  server.use(http.get("*/api/projects/p-1", () => HttpResponse.json({ ...project, target_lang: null })));
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "+ Add files" }));
  const dialog = screen.getByRole("dialog", { name: "Add files" });
  expect(within(dialog).getByLabelText("To")).toBeInTheDocument();
});

it("renames from the title and opens the translation guide", async () => {
  serve([documentSummary]);
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "Rename Atlas" }));
  expect(screen.getByRole("dialog", { name: "Rename" })).toBeInTheDocument();
  await user.click(within(screen.getByRole("dialog", { name: "Rename" })).getByRole("button", { name: "Cancel" }));
  await user.click(await screen.findByRole("button", { name: "Translation guide (2)" }));
  expect(await screen.findByRole("dialog", { name: "Translation guide" })).toBeInTheDocument();
});

it("says when a version is not part of the set", async () => {
  serve([documentSummary]);
  open("/t/p-1/r-zzz");
  expect(await screen.findByText(/This version isn't part of this translation\./)).toBeInTheDocument();
});

it("shows why the set could not be loaded", async () => {
  server.use(http.get("*/api/projects/p-1", () => HttpResponse.json({ detail: "project p-1 not found" }, { status: 404 })));
  server.use(http.get("*/api/projects/p-1/documents", () => HttpResponse.json([])));
  open();
  expect(await screen.findByRole("alert")).toHaveTextContent("project p-1 not found");
});

it("picks the run to show", () => {
  const doc = { ...documentSummary, runs: [{ ...runSummary, id: "r-new" }, runSummary] };
  expect(pickRun([doc], undefined)).toMatchObject({ run: { id: "r-new" }, latest: true });
  expect(pickRun([doc], "r-1")).toMatchObject({ run: { id: "r-1" }, latest: false });
  expect(pickRun([{ ...documentSummary, runs: [] }], undefined)).toBeNull();
  expect(pickRun([], undefined)).toBeNull();
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && pnpm vitest run src/set/SetPage.test.tsx src/set/FileTabs.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write `web/src/set/FileTabs.tsx`**

```tsx
import { Link } from "react-router";
import type { DocumentSummary } from "../api/types";
import { StatusText } from "../components/StatusText";
import { runStateStatus } from "../lib/status";
import { dismissUpload, retryUpload, type UploadItem } from "../upload/queue";
import type { Picked } from "./SetPage";

function UploadTab({ item }: { item: UploadItem }) {
  if (item.status === "failed") {
    return (
      <div role="group" aria-label={item.name} className="tab failed">
        <span dir="auto">{item.name}</span> <span className="error">{item.error}</span>{" "}
        <button type="button" onClick={() => retryUpload(item.key)}>
          Try again
        </button>
        <button type="button" className="quiet" aria-label={`Remove ${item.name}`} onClick={() => dismissUpload(item.key)}>
          ×
        </button>
      </div>
    );
  }
  return (
    <div className="tab">
      <span dir="auto">{item.name}</span>{" "}
      <span className="status tone-busy">{item.status === "uploading" ? "uploading…" : "waiting to upload"}</span>
    </div>
  );
}

type Props = { projectId: string; docs: DocumentSummary[]; uploads: UploadItem[]; picked: Picked | null };

/** Spec §5.1: one tab per file, shown when there is more than one file or an upload is under way. */
export function FileTabs({ projectId, docs, uploads, picked }: Props) {
  if (docs.length <= 1 && uploads.length === 0) return null;
  return (
    <nav aria-label="Files" className="file-tabs">
      <ul>
        {docs.map((doc) => {
          const run = doc.runs[0];
          const current = picked?.doc.id === doc.id;
          return (
            <li key={doc.id}>
              <Link
                to={run ? `/t/${projectId}/${run.id}` : `/t/${projectId}`}
                className={current ? "tab current" : "tab"}
                aria-current={current ? "page" : undefined}
              >
                <span dir="auto">{doc.filename}</span>
                {run ? <StatusText status={runStateStatus(run.state)} /> : null}
              </Link>
            </li>
          );
        })}
        {uploads.map((item) => (
          <li key={item.key}>
            <UploadTab item={item} />
          </li>
        ))}
      </ul>
    </nav>
  );
}
```

- [ ] **Step 4: Write `web/src/set/AddFilesDialog.tsx`**

```tsx
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useConfig, useTiers } from "../api/system";
import type { ProjectInfo } from "../api/types";
import { Dialog } from "../components/Dialog";
import { languageOk } from "../components/LanguageInput";
import { languageName } from "../lib/languages";
import { DropArea, fileProblem } from "../translate/DropArea";
import { LanguagePicker } from "../translate/LanguagePicker";
import { uploadLimit } from "../translate/NewTranslationPage";
import { enqueueUploads } from "../upload/queue";
import type { Picked } from "./SetPage";

type Props = { project: ProjectInfo; picked: Picked | null; open: boolean; onClose: () => void };

function AddFilesForm({ project, picked, onClose }: Omit<Props, "open">) {
  const config = useConfig();
  const tiers = useTiers();
  const queryClient = useQueryClient();
  const known = project.target_lang ?? picked?.run.target_lang ?? "";
  const [files, setFiles] = useState<File[]>([]);
  const [target, setTarget] = useState(known);
  const maxMb = uploadLimit(config.data);
  const usable = files.filter((f) => fileProblem(f, maxMb) === null);
  const ready = usable.length > 0 && languageOk(target, true);
  return (
    <div>
      <h2>Add files</h2>
      <p className="muted">New files start from this set&apos;s translation guide, so names stay the same.</p>
      <DropArea files={files} onFiles={setFiles} maxMb={maxMb} />
      {known ? (
        <p>Translating into {languageName(known)}.</p>
      ) : (
        <LanguagePicker label="To" value={target} onChange={setTarget} tiers={tiers.data} />
      )}
      <div className="row end">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="primary"
          disabled={!ready}
          onClick={() => {
            enqueueUploads(
              project.id,
              usable,
              { target_lang: target, source_lang: project.source_lang, pipeline_profile: project.pipeline_profile },
              queryClient,
            );
            onClose();
          }}
        >
          Add and translate
        </button>
      </div>
    </div>
  );
}

/** Spec §4.3. */
export function AddFilesDialog({ open, onClose, ...rest }: Props) {
  return (
    <Dialog open={open} label="Add files" onClose={onClose}>
      <AddFilesForm {...rest} onClose={onClose} />
    </Dialog>
  );
}
```

- [ ] **Step 5: Write `web/src/set/SetHeader.tsx`**

```tsx
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { exportUrl, reportUrl } from "../api/client";
import { useProjectGuide } from "../api/guide";
import { useRun } from "../api/runs";
import { useTranslateAgain } from "../api/sets";
import type { ProjectInfo } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { Menu } from "../components/Menu";
import { RenameDialog } from "../components/SetDialogs";
import { whenText } from "../lib/dates";
import { languageName } from "../lib/languages";
import type { Picked } from "./SetPage";

/** Spec §5.1: shown once the file has a finished translation. */
export function DownloadMenu({ runId }: { runId: string }) {
  const run = useRun(runId);
  if (!run.data?.report_available) return null;
  return (
    <Menu label="Download" button={<>Download <span aria-hidden="true">▾</span></>} className="download">
      {(close) => (
        <>
          <a role="menuitem" href={exportUrl(runId, "docx")} download onClick={close}>
            Word document
          </a>
          <a role="menuitem" href={exportUrl(runId, "bilingual-docx")} download onClick={close}>
            Side-by-side Word file with reviewer notes
          </a>
          <a role="menuitem" href={exportUrl(runId, "html")} download onClick={close}>
            Web page
          </a>
          <p className="menu-heading">Other formats</p>
          <a role="menuitem" href={exportUrl(runId, "json")} download onClick={close}>
            Data file (JSON)
          </a>
          <a role="menuitem" href={reportUrl(runId)} download onClick={close}>
            Quality report
          </a>
        </>
      )}
    </Menu>
  );
}

function MoreMenu({ project, picked, onAddFiles }: { project: ProjectInfo; picked: Picked | null; onAddFiles: () => void }) {
  const again = useTranslateAgain(project.id);
  const navigate = useNavigate();
  const earlier = picked ? picked.doc.runs.slice(1) : [];
  return (
    <>
      <Menu label="More actions" button="⋯">
        {(close) => (
          <>
            {picked ? (
              <button
                role="menuitem"
                type="button"
                disabled={again.isPending}
                onClick={() => {
                  close();
                  again.mutate(picked.doc.id, { onSuccess: (run) => navigate(`/t/${project.id}/${run.id}`) });
                }}
              >
                Translate again
              </button>
            ) : null}
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                close();
                onAddFiles();
              }}
            >
              + Add files
            </button>
            {earlier.length > 0 ? <p className="menu-heading">Earlier versions</p> : null}
            {earlier.map((run) => (
              <Link key={run.id} role="menuitem" to={`/t/${project.id}/${run.id}`} onClick={close}>
                Started {whenText(run.created_at)}
              </Link>
            ))}
          </>
        )}
      </Menu>
      {again.isError ? <ErrorMessage error={again.error} /> : null}
    </>
  );
}

type Props = { project: ProjectInfo; picked: Picked | null; onOpenGuide: () => void; onAddFiles: () => void };

export function SetHeader({ project, picked, onOpenGuide, onAddFiles }: Props) {
  const [renaming, setRenaming] = useState(false);
  const guide = useProjectGuide(project.id);
  const count = guide.data ? guide.data.guide.entities.length + guide.data.guide.glossary.length : 0;
  const run = picked?.run ?? null;
  const source = run?.source_lang ?? project.source_lang;
  const target = run?.target_lang ?? project.target_lang;
  return (
    <header className="set-header">
      <div>
        <h1 aria-label={project.name}>
          <button type="button" className="title-button" aria-label={`Rename ${project.name}`} onClick={() => setRenaming(true)}>
            <span dir="auto">{project.name}</span>
          </button>
        </h1>
        <p className="muted">
          {source ? languageName(source) : "Detected language"} → {target ? languageName(target) : "—"}
          {run?.finished_at ? ` · finished ${whenText(run.finished_at)}` : ""}
        </p>
      </div>
      <div className="row">
        <button type="button" onClick={onOpenGuide}>
          Translation guide ({count})
        </button>
        {run ? <DownloadMenu runId={run.id} /> : null}
        <MoreMenu project={project} picked={picked} onAddFiles={onAddFiles} />
      </div>
      <RenameDialog project={project} open={renaming} onClose={() => setRenaming(false)} />
    </header>
  );
}
```

The `h1` carries the set's name as its label because the button inside it is labelled "Rename <name>".

- [ ] **Step 6: Write `web/src/set/SetPage.tsx`**

```tsx
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { useProject } from "../api/projects";
import { useSetDocuments } from "../api/sets";
import type { DocumentSummary, RunSummary } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { GuidePanel } from "../guide/GuidePanel";
import type { GuideMode } from "../guide/mode";
import { useUploads } from "../upload/queue";
import { AddFilesDialog } from "./AddFilesDialog";
import { FileTabs } from "./FileTabs";
import { RunView } from "./RunView";
import { SetHeader } from "./SetHeader";

export type Picked = { doc: DocumentSummary; run: RunSummary; latest: boolean };

/** The run the address names, else the newest run of the first file (spec §3.2). */
export function pickRun(docs: DocumentSummary[], runId: string | undefined): Picked | null {
  for (const doc of docs) {
    const index = doc.runs.findIndex((r) => r.id === runId);
    if (index >= 0) return { doc, run: doc.runs[index]!, latest: index === 0 };
  }
  const doc = docs[0];
  const run = doc?.runs[0];
  return doc && run ? { doc, run, latest: true } : null;
}

export function SetPage() {
  const { projectId = "", runId } = useParams();
  const project = useProject(projectId);
  const documents = useSetDocuments(projectId);
  const uploads = useUploads(projectId);
  const [guide, setGuide] = useState<GuideMode | null>(null);
  const [adding, setAdding] = useState(false);
  const docs = useMemo(
    () => [...(documents.data ?? [])].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)),
    [documents.data],
  );
  if (project.isPending) return <Loading />;
  if (project.isError) return <ErrorMessage error={project.error} />;
  const p = project.data;
  const picked = pickRun(docs, runId);
  const pending = uploads.filter((u) => u.status !== "done");
  const unknown = runId !== undefined && documents.data !== undefined && picked?.run.id !== runId;
  return (
    <section className="set">
      <SetHeader project={p} picked={picked} onOpenGuide={() => setGuide({ kind: "project" })} onAddFiles={() => setAdding(true)} />
      <FileTabs projectId={p.id} docs={docs} uploads={pending} picked={picked} />
      {documents.isError ? <ErrorMessage error={documents.error} /> : null}
      {unknown ? (
        <p className="notice">
          This version isn&apos;t part of this translation. <Link to={`/t/${p.id}`}>Open the latest</Link>
        </p>
      ) : null}
      {picked && !unknown ? (
        <>
          {!picked.latest ? (
            <p className="notice">
              An earlier version. <Link to={`/t/${p.id}/${picked.doc.runs[0]!.id}`}>See the latest</Link>
            </p>
          ) : null}
          <RunView key={picked.run.id} runId={picked.run.id} onOpenGuide={setGuide} />
        </>
      ) : null}
      {documents.data && docs.length === 0 && pending.length === 0 ? (
        <div className="notice">
          <p>No files yet.</p>
          <button type="button" className="primary" onClick={() => setAdding(true)}>
            + Add files
          </button>
        </div>
      ) : null}
      <AddFilesDialog project={p} picked={picked} open={adding} onClose={() => setAdding(false)} />
      <GuidePanel projectId={p.id} mode={guide} onClose={() => setGuide(null)} />
    </section>
  );
}
```

- [ ] **Step 7: Add the header and tab rules** to `web/src/styles/review.css`

```css
.set-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.set-header h1 {
  margin: 0;
}

.title-button {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  font-weight: 700;
  text-align: start;
}

.title-button:hover {
  text-decoration: underline;
}

.download > button {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-text);
  font-weight: 600;
}

.file-tabs ul {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  list-style: none;
  margin: 0 0 var(--space-4);
  padding: 0;
  border-bottom: 1px solid var(--border);
}

.tab {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-bottom: 3px solid transparent;
  color: var(--text);
  text-decoration: none;
}

.tab.current {
  border-bottom-color: var(--accent);
  font-weight: 600;
}

.tab.failed {
  border-bottom-color: var(--danger);
}
```

- [ ] **Step 8: Run the tests, every web gate, and commit**

Run: `cd web && pnpm vitest run src/set && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src
git commit -m "feat(web): set page — header, Download and ⋯ menus, file tabs, adding files"
```

---

### Task 13: Settings

Spec §6.1, §6.2. Appearance, a status sentence, and today's System page folded under Technical details, plus "Find passages by score".

**Files:**
- Create: `web/src/settings/statusSentence.ts`, `web/src/settings/statusSentence.test.ts`
- Create: `web/src/settings/TechnicalDetails.tsx`
- Create: `web/src/settings/SettingsPage.tsx`, `web/src/settings/SettingsPage.test.tsx`
- Modify: `web/src/api/system.ts` (`useDoctorStatus`)

**Interfaces:**
- Consumes: `useDoctor`, `useConfig`, `useTiers` (`api/system.ts`); `useSetList` (Task 4); `useTheme`, `ThemeChoice` (Task 2); `languageName` (Task 3); `SCORE_KEYS`, `score` (Task 9, `review/filters.ts`).
- Produces: `statusSentence(report: DoctorReport): string`, `reportFailed(report: DoctorReport): boolean`; `useDoctorStatus()` (query, `probe=false`); `SettingsPage()`; `TechnicalDetails()`.

- [ ] **Step 1: Write the failing tests**

`web/src/settings/statusSentence.test.ts`:

```ts
import type { DoctorCheck } from "../api/types";
import { reportFailed, statusSentence } from "./statusSentence";

const check = (name: string, status: DoctorCheck["status"]): DoctorCheck => ({ name, status, message: "", fix: "" });
const report = (...checks: DoctorCheck[]) => ({ ok: checks.every((c) => c.status !== "fail"), checks });

it("says everything is working when nothing failed", () => {
  expect(statusSentence(report(check("config", "pass"), check("scorer", "warn")))).toBe("Everything is working.");
  expect(reportFailed(report(check("config", "pass")))).toBe(false);
});

it("explains what a failure means for the reader", () => {
  expect(statusSentence(report(check("scorer", "fail")))).toBe(
    "The quality checker is offline. Translations still finish but aren't double-checked.",
  );
  expect(statusSentence(report(check("scorer", "fail"), check("backend local", "fail")))).toBe(
    "Coglossa can't reach the language models. Translations can't start until an administrator fixes it.",
  );
  expect(statusSentence(report(check("database", "fail")))).toBe(
    "Coglossa can't reach its database. Translations can't start until an administrator fixes it.",
  );
  expect(statusSentence(report(check("storage", "fail")))).toMatch(/can't reach its file storage/);
  expect(statusSentence(report(check("prompts", "fail")))).toBe(
    "Something needs an administrator's attention: prompts.",
  );
  expect(reportFailed(report(check("prompts", "fail")))).toBe(true);
});
```

`web/src/settings/SettingsPage.test.tsx`:

```tsx
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { configView, doctorReport, project, tiers } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { resetThemeForTests } from "../theme/theme";
import { SettingsPage } from "./SettingsPage";

let probes: string[] = [];

beforeEach(() => {
  probes = [];
  localStorage.clear();
  resetThemeForTests();
  server.use(
    http.get("*/api/system/doctor", ({ request }) => {
      probes.push(new URL(request.url).searchParams.get("probe") ?? "");
      return HttpResponse.json(doctorReport);
    }),
    http.get("*/api/system/config", () => HttpResponse.json(configView)),
    http.get("*/api/system/tiers", () => HttpResponse.json(tiers)),
    http.get("*/api/projects", () => HttpResponse.json([project])),
  );
});

it("chooses the theme", async () => {
  const { user } = renderAt(<SettingsPage />);
  expect(screen.getByRole("radio", { name: "Match my computer" })).toBeChecked();
  await user.click(screen.getByRole("radio", { name: "Dark" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem("coglossa.theme")).toBe("dark");
});

it("says how the installation is doing, and runs a full check", async () => {
  const { user } = renderAt(<SettingsPage />);
  const status = screen.getByRole("region", { name: "Status" });
  expect(await within(status).findByText(/The quality checker is offline/)).toBeInTheDocument();
  await user.click(within(status).getByRole("button", { name: "Run a full check" }));
  await vi.waitFor(() => expect(probes).toEqual(["false", "true"]));
});

it("keeps the technical details folded away", async () => {
  const { user } = renderAt(<SettingsPage />);
  const details = screen.getByText("Technical details").closest("details")!;
  expect(details).not.toHaveAttribute("open");
  await user.click(screen.getByText("Technical details"));
  expect(await within(details).findByText("/etc/coglossa/coglossa.yaml")).toBeInTheDocument();
  expect(within(details).getByRole("table", { name: "Checks" })).toHaveTextContent("http://scorer:8080 is not reachable");
  expect(within(details).getByRole("table", { name: "Languages" })).toHaveTextContent("Amharic");
});

it("finds passages by score", async () => {
  const { user, router } = renderAt(<SettingsPage />);
  await user.click(screen.getByText("Technical details"));
  const form = screen.getByRole("form", { name: "Find passages by score" });
  await within(form).findByRole("option", { name: "Atlas" });
  await user.selectOptions(within(form).getByLabelText("Translation"), "p-1");
  await user.type(within(form).getByLabelText("Meaning from"), "0");
  await user.type(within(form).getByLabelText("Meaning to"), "60");
  await user.click(within(form).getByRole("button", { name: "Show passages" }));
  expect(router.state.location.pathname).toBe("/t/p-1");
  expect(router.state.location.search).toBe("?min_meaning=0&max_meaning=60");
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && pnpm vitest run src/settings`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write `web/src/settings/statusSentence.ts`**

```ts
import type { DoctorReport } from "../api/types";

/** The parts without which a translation cannot start, and what to call them. */
function blockingPart(name: string): string | null {
  if (name === "backends" || name.startsWith("backend ")) return "the language models";
  if (name === "database") return "its database";
  if (name === "storage") return "its file storage";
  return null;
}

export function reportFailed(report: DoctorReport): boolean {
  return report.checks.some((c) => c.status === "fail");
}

/** Spec §6.2: what the installation checks mean for the reader, in one sentence. */
export function statusSentence(report: DoctorReport): string {
  const failed = report.checks.filter((c) => c.status === "fail");
  if (failed.length === 0) return "Everything is working.";
  for (const check of failed) {
    const part = blockingPart(check.name);
    if (part) return `Coglossa can't reach ${part}. Translations can't start until an administrator fixes it.`;
  }
  const other = failed.find((c) => c.name !== "scorer");
  if (other) return `Something needs an administrator's attention: ${other.name}.`;
  return "The quality checker is offline. Translations still finish but aren't double-checked.";
}
```

- [ ] **Step 4: Add `useDoctorStatus`** to `web/src/api/system.ts`

```ts
/** The quick installation check, loaded when Settings opens (spec §6.2). */
export function useDoctorStatus() {
  return useQuery({
    queryKey: ["system", "doctor"],
    queryFn: () => unwrap(client.GET("/api/system/doctor", { params: { query: { probe: false } } })),
  });
}
```

- [ ] **Step 5: Write `web/src/settings/TechnicalDetails.tsx`**

Copy `Config`, `TierRows` and `Tiers` from `web/src/pages/SystemPage.tsx` here (SystemPage stays until Task 14 deletes it), unchanged except `TierRows` shows `languageName(key)` beside each language code (`<td>{languageName(key)} <span className="muted">({key})</span></td>`) — keep the pair rows as codes. Then add:

```tsx
import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { useSetList } from "../api/sets";
import { useConfig, useDoctorStatus, useTiers } from "../api/system";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { languageName } from "../lib/languages";
import { score, SCORE_KEYS } from "../review/filters";

const BOUND_LABELS: Record<(typeof SCORE_KEYS)[number], string> = {
  min_meaning: "Meaning from",
  max_meaning: "Meaning to",
  min_style: "Style from",
  max_style: "Style to",
};

/** Spec §6.1: the numeric filters, as a form that opens a translation filtered by score. */
function ScoreSearch() {
  const sets = useSetList();
  const navigate = useNavigate();
  const id = useId();
  const [setId, setSetId] = useState("");
  const [bounds, setBounds] = useState<Record<string, string>>({});
  function submit(event: FormEvent) {
    event.preventDefault();
    const params = new URLSearchParams();
    for (const key of SCORE_KEYS) {
      const value = score(bounds[key] ?? null);
      if (value !== null) params.set(key, String(value));
    }
    navigate(`/t/${setId}?${params}`);
  }
  return (
    <form aria-label="Find passages by score" onSubmit={submit}>
      <h3>Find passages by score</h3>
      <div className="field">
        <label htmlFor={`${id}-set`}>Translation</label>
        <select id={`${id}-set`} value={setId} onChange={(e) => setSetId(e.target.value)}>
          <option value="" disabled>
            Choose a translation
          </option>
          {(sets.data ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div className="row">
        {SCORE_KEYS.map((key) => (
          <div key={key} className="field">
            <label htmlFor={`${id}-${key}`}>{BOUND_LABELS[key]}</label>
            <input
              id={`${id}-${key}`}
              type="number"
              min={0}
              max={100}
              value={bounds[key] ?? ""}
              onChange={(e) => setBounds({ ...bounds, [key]: e.target.value })}
            />
          </div>
        ))}
      </div>
      <button type="submit" disabled={setId === ""}>
        Show passages
      </button>
    </form>
  );
}

function Checks() {
  const doctor = useDoctorStatus();
  if (doctor.data === undefined) return null;
  return (
    <table aria-label="Checks">
      <thead>
        <tr>
          <th>Check</th>
          <th>Result</th>
          <th>Details</th>
          <th>What to do</th>
        </tr>
      </thead>
      <tbody>
        {doctor.data.checks.map((check, i) => (
          <tr key={`${check.name}-${i}`}>
            <td>{check.name}</td>
            <td>{check.status}</td>
            <td>{check.message}</td>
            <td>{check.fix}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Spec §6.2: today's System page, for administrators, folded away. */
export function TechnicalDetails() {
  return (
    <details className="technical">
      <summary>Technical details</summary>
      <ScoreSearch />
      <h3>Installation checks</h3>
      <Checks />
      <h3>Active configuration</h3>
      <p className="muted">Read-only: change coglossa.yaml on the server and restart the services.</p>
      <Config />
      <h3>Language reliability tiers</h3>
      <Tiers />
    </details>
  );
}
```

(`Config` and `Tiers` use `useConfig`, `useTiers`, `Loading` and `ErrorMessage` as they do today.)

- [ ] **Step 6: Write `web/src/settings/SettingsPage.tsx`**

```tsx
import { useDoctor, useDoctorStatus } from "../api/system";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { useTheme, type ThemeChoice } from "../theme/theme";
import { reportFailed, statusSentence } from "./statusSentence";
import { TechnicalDetails } from "./TechnicalDetails";

const THEMES: { value: ThemeChoice; label: string }[] = [
  { value: "system", label: "Match my computer" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

function Appearance() {
  const { choice, setChoice } = useTheme();
  return (
    <fieldset className="appearance">
      <legend>Appearance</legend>
      <p className="muted">Theme</p>
      {THEMES.map((t) => (
        <label key={t.value}>
          <input
            type="radio"
            name="theme"
            value={t.value}
            checked={choice === t.value}
            onChange={() => setChoice(t.value)}
          />{" "}
          {t.label}
        </label>
      ))}
    </fieldset>
  );
}

function Status() {
  const quick = useDoctorStatus();
  const full = useDoctor();
  const report = full.data ?? quick.data;
  const failed = report !== undefined && reportFailed(report);
  return (
    <section aria-label="Status">
      <h2>Status</h2>
      {report === undefined && quick.isPending ? <Loading /> : null}
      {report === undefined && quick.isError ? <ErrorMessage error={quick.error} /> : null}
      {report ? (
        <p className={failed ? "tone-bad" : "tone-ok"}>
          <span aria-hidden="true">{failed ? "! " : "✓ "}</span>
          {statusSentence(report)}
        </p>
      ) : null}
      <button type="button" disabled={full.isPending} onClick={() => full.mutate(true)}>
        {full.isPending ? "Checking…" : "Run a full check"}
      </button>
      {full.isError ? <ErrorMessage error={full.error} /> : null}
    </section>
  );
}

/** Spec §6.2. */
export function SettingsPage() {
  return (
    <section className="settings">
      <h1>Settings</h1>
      <Appearance />
      <Status />
      <TechnicalDetails />
    </section>
  );
}
```

- [ ] **Step 7: Run the tests, every web gate, and commit**

Run: `cd web && pnpm vitest run src/settings && pnpm fmt && pnpm lint && pnpm test`
Expected: PASS.

```bash
git add web/src
git commit -m "feat(web): Settings — theme, a status sentence, technical details folded away"
```

---
### Task 14: Switch the routes, redirect old addresses, delete the old screens

Spec §3.2. The new screens become the app; every old page and its tests go; the old stylesheet's font rules move into `base.css`.

**Files:**
- Modify: `web/src/routes.tsx`
- Create: `web/src/set/Redirects.tsx`, `web/src/routes.test.tsx`
- Modify: `web/src/pages/NotFound.tsx`, `web/src/pages/RouteError.tsx`, `web/src/components/Layout.test.tsx`
- Modify: `web/src/styles/base.css` (font stack and `:lang` rules from `app.css`), `web/src/main.tsx`, `web/src/styles/no-raw-colours.test.ts`
- Delete: the old screens and everything only they use (list in Step 5)

**Interfaces:**
- Consumes: `NewTranslationPage` (Task 7), `SetPage` (Task 12), `SettingsPage` (Task 13), `useRun` (`api/runs.ts`).
- Produces: `ProjectRedirect()`, `RunRedirect()`; the route table below.

- [ ] **Step 1: Write the failing test** — `web/src/routes.test.tsx`

```tsx
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setEventSourceFactory } from "./api/events";
import { configView, documentSummary, guideView, project, runDetail, tiers } from "./test/fixtures";
import { renderApp } from "./test/render";
import { server } from "./test/server";

beforeEach(() => {
  setEventSourceFactory(null);
  server.use(
    http.get("*/api/system/tiers", () => HttpResponse.json(tiers)),
    http.get("*/api/system/config", () => HttpResponse.json(configView)),
    http.get("*/api/projects/p-1", () => HttpResponse.json(project)),
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])),
    http.get("*/api/projects/p-1/guide", () => HttpResponse.json(guideView)),
    http.get("*/api/runs/r-1", () => HttpResponse.json(runDetail)),
  );
});
afterEach(() => setEventSourceFactory(undefined));

it("opens on New translation", async () => {
  renderApp("/");
  expect(await screen.findByRole("heading", { name: "New translation" })).toBeInTheDocument();
});

it("opens a set and Settings at their addresses", async () => {
  renderApp("/t/p-1/r-1");
  expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
});

it.each([
  ["/projects", "/"],
  ["/projects/p-1", "/t/p-1"],
  ["/projects/p-1/guide", "/t/p-1"],
  ["/runs/r-1", "/t/p-1/r-1"],
  ["/runs/r-1/review", "/t/p-1/r-1"],
  ["/system", "/settings"],
])("sends the old address %s to %s", async (from, to) => {
  server.use(http.get("*/api/system/doctor", () => HttpResponse.json({ ok: true, checks: [] })));
  const { router } = renderApp(from);
  await vi.waitFor(() => expect(router.state.location.pathname).toBe(to));
});

it("explains an old run address that no longer exists", async () => {
  server.use(http.get("*/api/runs/r-9", () => HttpResponse.json({ detail: "run r-9 not found" }, { status: 404 })));
  renderApp("/runs/r-9");
  expect(await screen.findByRole("alert")).toHaveTextContent("run r-9 not found");
});

it("offers a way back from an unknown address", async () => {
  renderApp("/no/such/page");
  expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Start a new translation" })).toHaveAttribute("href", "/");
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd web && pnpm vitest run src/routes.test.tsx`
Expected: FAIL — `/` still redirects to `/projects`, `/t/...` is not found.

- [ ] **Step 3: Write `web/src/set/Redirects.tsx`**

```tsx
import { Navigate, useParams } from "react-router";
import { useRun } from "../api/runs";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";

/** Old project addresses (spec §3.2). */
export function ProjectRedirect() {
  const { projectId = "" } = useParams();
  return <Navigate to={`/t/${projectId}`} replace />;
}

/** Old run addresses, and their tabs: the run names its project. */
export function RunRedirect() {
  const { runId = "" } = useParams();
  const run = useRun(runId);
  if (run.isError) return <ErrorMessage error={run.error} />;
  if (run.data === undefined) return <Loading />;
  return <Navigate to={`/t/${run.data.run.project_id}/${runId}`} replace />;
}
```

- [ ] **Step 4: Replace the page routes** in `web/src/routes.tsx` (keep the frame and the two error elements as they are)

```tsx
import { Navigate, type RouteObject } from "react-router";
import { Layout } from "./components/Layout";
import { NotFound } from "./pages/NotFound";
import { RouteError } from "./pages/RouteError";
import { ProjectRedirect, RunRedirect } from "./set/Redirects";
import { SetPage } from "./set/SetPage";
import { SettingsPage } from "./settings/SettingsPage";
import { NewTranslationPage } from "./translate/NewTranslationPage";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <Layout />,
    errorElement: <RouteError />,
    children: [
      {
        errorElement: <RouteError />,
        children: [
          { index: true, element: <NewTranslationPage /> },
          { path: "t/:projectId/:runId?", element: <SetPage /> },
          { path: "settings", element: <SettingsPage /> },
          // spec §3.2: old addresses keep working
          { path: "projects", element: <Navigate to="/" replace /> },
          { path: "projects/:projectId", element: <ProjectRedirect /> },
          { path: "projects/:projectId/guide", element: <ProjectRedirect /> },
          { path: "runs/:runId/*", element: <RunRedirect /> },
          { path: "system", element: <Navigate to="/settings" replace /> },
          { path: "*", element: <NotFound /> },
        ],
      },
    ],
  },
];
```

In `NotFound.tsx` and `RouteError.tsx`, replace the link to `/projects` with `<Link to="/">Start a new translation</Link>` (NotFound's sentence becomes "There is nothing at this address. <Link>Start a new translation</Link>.").

- [ ] **Step 5: Delete the old screens and what only they use**

Delete these files (and their `.test.tsx` / `.test.ts` siblings):

- `web/src/pages/`: `ProjectsPage`, `ProjectPage`, `ProjectGuidePage`, `UploadForm`, `DeleteProject`, `DocumentList`, `SystemPage`, and the whole `pages/run/` folder.
- `web/src/review/`: `ReviewPage`, `SpanPanel`, `SpanRow`, `FilterForm`, `AddToGuide`, `FlagList`, `EditTranslation`, `ReviewHeader`, `Candidates.tsx`, `useFlagKeys.ts`, `languages.test.tsx`.
- `web/src/guide/`: `GuideEditor`, `EntityTable`, `GlossaryTable`, `ProjectGuideEditor`, `GuideDisplay`, `diff.ts`.
- `web/src/components/`: `StateBadge.tsx`, `UnsavedChanges.tsx`.

Then remove exports nothing imports any more (check each with `grep -rn "<name>" web/src`), with their tests:

- `api/projects.ts`: `useProjects`, `useCreateProject`, `useDocuments`, `useUpload`, `useNewRun` (keep `projectKeys`, `useProject`, `useDeleteProject`, `uploadDocument`, `Upload`).
- `api/review.ts`: `useSpanPage`.
- `api/guide.ts`: `useGuideVersions`, `useGuideVersion`, `guideKeys.versions`/`guideKeys.version` if unused.
- `review/filters.ts`: `PAGE_SIZE`, `ReviewFilter`, `filterFromParams`, `paramsFromFilter`, `SpanQuery`, `spanQuery` (keep `FLAG_CODES`, `score`, `ScoreFilter`, `SCORE_KEYS`, `scoreFilterFrom`); update `filters.test.ts` to test only what stays, adding `scoreFilterFrom(new URLSearchParams("min_meaning=-5&max_style=x"))` → `{ min_meaning: 0 }`.
- `lib/format.ts`: `stateLabel`, `profileLabel`, `languagePair`, `formatDate` if unused (`formatScore`, `isTerminal`, `TERMINAL_STATES`, `isLanguageCode`, `LANGUAGE_CODE` stay); update `format.test.ts`.

`Revisions.tsx` uses `formatDate`: if it does, keep `formatDate`.

- [ ] **Step 6: Move the fonts and drop the old stylesheet**

Move the `--font-stack` custom property and every `:lang(...)` rule from `web/src/styles/app.css` into `web/src/styles/base.css` (the font stack goes on `:root` beside nothing else; `body` gets `font-family: var(--font-stack);` in place of the Task 2 comment). Copy any other `app.css` rule a remaining component still relies on (`grep -rn 'className="' web/src` against the selectors in `app.css`; for example `.config`, `.signals`, `.history`, `.back-translation`) into `review.css` or `base.css`, rewritten on tokens. Then:

- delete `web/src/styles/app.css` and its import in `main.tsx`;
- in `no-raw-colours.test.ts`, `ALLOWED` becomes `new Set(["./tokens.css"])`.

- [ ] **Step 7: Update `Layout.test.tsx`**

Its `renderApp("/nowhere")` tests keep working. Add:

```tsx
it("starts on New translation", async () => {
  renderApp("/");
  expect(await screen.findByRole("heading", { name: "New translation" })).toBeInTheDocument();
});
```

with handlers for `*/api/system/tiers` and `*/api/system/config` in that test (`server.use(...)`).

- [ ] **Step 8: Run every web gate**

Run: `cd web && pnpm fmt && pnpm lint && pnpm test && pnpm build`
Expected: PASS. Coverage stays ≥ 95% on all four measures; if a kept module dropped below, it has dead code left — delete it rather than adding tests for it.

- [ ] **Step 9: Commit**

```bash
git add -A web
git commit -m "feat(web): switch to the new screens, redirect old addresses, remove the old pages"
```

---

### Task 15: End to end, the hosted sign-in page, and the docs

Spec §9.3, §10. The browser journeys for the new flow and dark mode, the hosted sign-in page in the same look, and the operator guide's wording.

**Files:**
- Modify: `web/e2e/journey.spec.ts` (rewrite), `web/e2e/system.spec.ts`
- Modify: `deploy/hosted/vercel/static/login.html`
- Modify: `docs/operator-guide.md`, and any other doc that names the old screens (`grep -rn "System page\|Projects page\|project's document list\|Review tab\|Export tab\|Upload and translate" README.md docs deploy --include=*.md`)
- Modify: `docs/superpowers/specs/2026-10-16-coglossa-ui-ux-design.md` §7.2 table: light `--ok` is `#1a6f40` (the contrast test moved it)

**Interfaces:**
- Consumes: every screen's exact labels from Tasks 5–13.

- [ ] **Step 1: Rewrite `web/e2e/journey.spec.ts`**

```ts
import { expect, test, type Page, type Request } from "@playwright/test";
import { readFile } from "node:fs/promises";

const STORY = [
  "Le chat dort près de la fenêtre. Il pleut sur la ville.",
  "Marie ouvre la porte. Le chat MISTRANSLATED se réveille.",
].join("\n\n");

function watchRequests(page: Page, origin: string): Request[] {
  const outside: Request[] = [];
  page.on("request", (request) => {
    if (!request.url().startsWith(origin) && !request.url().startsWith("data:")) outside.push(request);
  });
  return outside;
}

async function pickCode(page: Page, label: "From" | "To", code: string) {
  await page.getByLabel(label, { exact: true }).selectOption({ label: "Other language…" });
  await page.getByLabel(`${label}: language code`).fill(code);
}

test("drop a file, check a passage, fix a term everywhere and download", async ({ page, baseURL }) => {
  const outside = watchRequests(page, baseURL!);
  const filename = `histoire-${Date.now()}.txt`;

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "New translation" })).toBeVisible();
  await page.getByLabel("Choose files").setInputFiles({
    name: filename,
    mimeType: "text/plain",
    buffer: Buffer.from(STORY, "utf-8"),
  });
  await pickCode(page, "From", "fr");
  await pickCode(page, "To", "en");
  await page.getByRole("button", { name: "Translate" }).click();
  await expect(page).toHaveURL(/\/t\/[0-9a-f-]+$/);

  // The steps, then the summary banner (the fake models take seconds).
  const summary = page.getByRole("region", { name: "Summary" });
  await expect(summary).toContainText("Looks good.", { timeout: 60_000 });

  // Open the flagged passage and fix it.
  await page.getByRole("row").filter({ hasText: "MISTRANSLATED" }).first().click();
  const panel = page.getByRole("complementary", { name: /Passage \d+/ });
  await expect(panel.getByRole("region", { name: "Why it's flagged" })).toBeVisible();
  await panel.getByRole("button", { name: "Edit" }).click();
  await panel.getByLabel("Your translation").fill("Marie opens the door. The cat wakes up.");
  await panel.getByRole("button", { name: "Save" }).click();
  await expect(panel.getByRole("status")).toContainText("Changed and marked as checked.");

  // Always translate a term, everywhere in the set.
  const always = panel.getByRole("region", { name: "Always translate" });
  await always.getByLabel("Always translate").fill("fenêtre");
  await always.getByLabel("as", { exact: true }).fill("window");
  await always.getByRole("button", { name: "Apply to all" }).click();
  // Either the panel confirms, or a recheck has already replaced the document (plan decision 5).
  await expect(
    page.getByText(/Added to the translation guide|Rechecking against your translation guide/).first(),
  ).toBeVisible();

  // A recheck hides the document until it finishes.
  await expect(page.getByRole("table", { name: "Document" })).toBeVisible({ timeout: 60_000 });

  // Download: the edit is in the files.
  await page.getByRole("button", { name: "Download" }).click();
  const [html] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: "Web page" }).click(),
  ]);
  expect(html.suggestedFilename()).toMatch(/\.html$/);
  expect(await readFile((await html.path())!, "utf-8")).toContain("Marie opens the door. The cat wakes up.");
  await page.getByRole("button", { name: "Download" }).click();
  const [docx] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: "Word document" }).click(),
  ]);
  expect(docx.suggestedFilename()).toMatch(/\.docx$/);

  // The translation guide now holds the term.
  await page.getByRole("button", { name: /^Translation guide/ }).click();
  const guide = page.getByRole("dialog", { name: "Translation guide" });
  await guide.getByRole("tab", { name: "Terms" }).click();
  await expect(guide.getByRole("list", { name: "Terms" })).toContainText("fenêtre → window · always");

  // Spec §8: the app works offline: nothing was requested from anywhere else.
  expect(outside.map((r) => r.url())).toEqual([]);
});

test("a reload keeps the open translation", async ({ page }) => {
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "Your translations" })
    .getByRole("link", { name: /^histoire-/ })
    .first()
    .click();
  await expect(page.getByRole("table", { name: "Document" })).toBeVisible({ timeout: 60_000 });
  const url = page.url();
  await page.reload();
  expect(page.url()).toBe(url);
  await expect(page.getByRole("table", { name: "Document" })).toBeVisible();
});

test("dark mode applies before the first paint", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => {
    new MutationObserver((records, observer) => {
      if (records.some((r) => r.attributeName === "data-theme")) {
        (window as unknown as { firstTheme: unknown }).firstTheme = {
          theme: document.documentElement.dataset.theme,
          rendered: (document.getElementById("root")?.childElementCount ?? 0) > 0,
        };
        observer.disconnect();
      }
    }).observe(document.documentElement, { attributes: true });
  });
  await page.goto("/settings");
  const first = await page.evaluate(() => (window as unknown as { firstTheme: unknown }).firstTheme);
  expect(first).toEqual({ theme: "dark", rendered: false });
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(background).toBe("rgb(21, 23, 28)"); // --bg in the dark theme
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "New translation" })).toBeVisible();
});
```

- [ ] **Step 2: Update `web/e2e/system.spec.ts`** — replace the first test

```ts
test("Settings runs the checks against the live stack", async ({ page }) => {
  await page.goto("/system");
  await expect(page).toHaveURL(/\/settings$/);
  const status = page.getByRole("region", { name: "Status" });
  await expect(status).toContainText(/Everything is working|offline|attention|can't reach/, { timeout: 60_000 });
  await page.getByText("Technical details").click();
  await expect(page.locator("code", { hasText: /coglossa\.yaml$/ })).toBeVisible();
  const checks = page.getByRole("table", { name: "Checks" });
  await expect(checks.getByRole("row", { name: /database/ })).toContainText("pass");
  await expect(page.getByRole("table", { name: "Language pairs" })).toBeVisible();
});
```

Keep the other two tests unchanged.

- [ ] **Step 3: Run the end-to-end tests**

Run: `make e2e`
Expected: PASS (all tests in both files). If a label differs from the plan's, the plan's spelling is the binding one: fix the component, not the test.

- [ ] **Step 4: Restyle the hosted sign-in page** — `deploy/hosted/vercel/static/login.html`

Replace its `<style>` block and add the theme script at the top of `<head>` so the sign-in page matches the app and honours the saved theme (it shares the app's origin and `localStorage`):

```html
<script>
  (function () {
    var c = "system";
    try { c = localStorage.getItem("coglossa.theme") || "system"; } catch { c = "system"; }
    if (c !== "light" && c !== "dark") {
      c = typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    document.documentElement.dataset.theme = c;
  })();
</script>
<style>
  :root, :root[data-theme="light"] {
    color-scheme: light;
    --bg: #faf8f5; --surface: #ffffff; --border-strong: #8a8378; --text: #1f2328;
    --text-muted: #5c6370; --accent: #3554d1; --accent-text: #ffffff; --danger: #b42318;
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --bg: #15171c; --surface: #1b1e25; --border-strong: #6b7385; --text: #e6e8ee;
    --text-muted: #9aa3b5; --accent: #7d97ff; --accent-text: #0d1020; --danger: #ff8a80;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 1rem;
    font-family: "Noto Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text);
  }
  main {
    width: 100%; max-width: 22rem; padding: 1.5rem; background: var(--surface);
    border: 1px solid var(--border-strong); border-radius: 0.625rem;
  }
  h1 { margin: 0 0 1rem; font-size: 1.5rem; }
  label { display: block; margin-bottom: 0.75rem; font-size: 0.9rem; color: var(--text-muted); }
  input {
    display: block; width: 100%; margin-top: 0.25rem; padding: 0.55rem 0.65rem; font: inherit;
    color: var(--text); background: var(--surface);
    border: 1px solid var(--border-strong); border-radius: 0.375rem;
  }
  input:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  button {
    width: 100%; margin-top: 0.5rem; padding: 0.6rem; font: inherit; font-weight: 600;
    border: 0; border-radius: 0.375rem; cursor: pointer; background: var(--accent); color: var(--accent-text);
  }
  .error { margin: 0 0 0.75rem; color: var(--danger); font-size: 0.9rem; }
</style>
```

Keep the body markup and the existing `failed=1` script. Run the front door's own tests: `cd deploy/hosted/vercel && pnpm test` — expected PASS (if a test asserts the old `<style>`, update that assertion only).

- [ ] **Step 5: Update the docs**

- `docs/operator-guide.md` line ~272: "The web app's System page shows the same checks." → "The web app's Settings page shows the same checks under Technical details."
- line ~378: "click **Translate again** on the document in the project's document list" → "open the translation, then choose **Translate again** from its ⋯ menu".
- Any other hit from the grep in **Files** above, worded to the spec's §3.3 table.
- The spec's §7.2 table: light `--ok` `#1d7a46` → `#1a6f40`.

- [ ] **Step 6: Run every gate and commit**

Run: `make test && make e2e && cd web && pnpm lint`
Expected: PASS.

```bash
git add web/e2e deploy/hosted/vercel/static/login.html docs
git commit -m "test(web): end-to-end journeys for the new flow and dark mode; sign-in page and docs to match"
```

---

## After the last task (controller, not an implementer)

These need the session's own access and are outside any task's diff:

1. Push the branch, open the PR (body ending with the Claude Code attribution lines), wait for every CI check including `airgap`, merge when green.
2. Redeploy the hosted preview by hand (Modal: `modal deploy deploy/hosted/modal/app.py`; Vercel: `deploy/hosted/vercel/build.sh` then `vercel deploy --prod` from `deploy/hosted/vercel`) and run `deploy/hosted/smoke.py` as the runbook says.
3. File the spec §1.3 follow-ups in Jira (project CGL): cross-file name consistency during parallel translation (engine), translating a set into a second language, phone-sized layouts, user roles for Technical details. CGL-20 already covers the upload cap.
4. Append a Session Log entry in Confluence and update the Decision Log with the plan decisions above.
