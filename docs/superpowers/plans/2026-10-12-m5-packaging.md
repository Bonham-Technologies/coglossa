# Coglossa M5 — Packaging: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The offline bundle of spec §10 and the air-gap install test of spec §9.1. An operator copies one tarball into an air-gapped Linux host, runs `./install.sh`, edits `config/coglossa.yaml`, runs `docker compose up -d --wait`, and `coglossa doctor` passes. The bundle carries:
- the images, as `docker save` archives
- the scorer weights (MetricX-24 XL and large, the mT5 tokenizer, LaBSE), as safetensors
- the compose files
- the config template and `tiers.yaml`
- SHA-256 checksums
- a CycloneDX SBOM
- the operator guide.

CI builds a bundle (without weights), installs it on a Docker network with no route out, and runs `doctor`, an end-to-end translation, a backup, an upgrade into a new directory and a restore.

Task 12 widens the bundled fonts to every script Noto covers for a living language: the customer translates the main languages and, from time to time, low-resource ones (the user's direction of 2026-10-12).

Tasks 1–2 first fix three M4b follow-ups that the customer would meet in their first week (`docs/m4b-followups.md`):
- approving a checkpoint guide is refused after another run's draft became the project guide
- the guide conflict is recognised by its message text
- the sweep offer ignores the profile.

**Architecture:**
- **Two images built from the repository.**
  - **`coglossa/app`** holds the engine and the API, with the built web app at `/opt/coglossa/web`. Compose runs it as `migrate` (`coglossa db upgrade`, once per start), `api`, `worker` and, in the demo, `fake-openai` and the fake scorer.
  - **`coglossa/scorer`** holds `coglossa-scorer` with CPU or CUDA torch. The weights are not in the image: compose mounts the bundle's `models/` directory at `/models`.
  - Postgres is the stock `postgres:16-alpine` image, saved into the bundle as is.
- **One compose project, `coglossa`.**
  - `deploy/compose.yaml` is production.
  - `compose.gpu.yaml` gives the scorer an NVIDIA GPU and MetricX XL.
  - `compose.demo.yaml` swaps the models for the synthetic servers.
  - Named volumes `pgdata` and `files` hold the data, so a new bundle extracted elsewhere finds them.
  - The config directory is mounted read-only at `/etc/coglossa`.
  - `.env`, created once by `install.sh`, holds the database password and any backend API keys.
- **Operator scripts.** `install.sh`, `backup.sh` and `restore.sh` are plain bash that needs only coreutils, tar and Docker with Compose ≥ 2.24 on the host.
- **Build tooling** runs on a machine with internet access:
  - `deploy/bundle.sh` (`make bundle`) builds, saves, inventories (syft), checksums and tars.
  - `coglossa-scorer-weights` (`make weights`) downloads the weights from Hugging Face once and converts MetricX's pickles to safetensors.

**Tech Stack:**
- **Images:** Docker (BuildKit), with these bases: `python:3.12-slim-bookworm`, `node:22-bookworm-slim` and `ghcr.io/astral-sh/uv:0.13.0`.
- **Deployment and build:**
  - Docker Compose ≥ 2.24 (`!reset`, `env_file.required`)
  - bash
  - syft (`anchore/syft:v1.18.1`, run as a container) for the CycloneDX SBOMs
  - shellcheck (`koalaman/shellcheck:v0.10.0`, run as a container)
- **Weights:** `huggingface_hub` and `safetensors`, in the scorer package.
- **Unchanged from M4b:** Python (uv, FastAPI), React (pnpm).

**Spec:** `docs/superpowers/specs/2026-10-09-coglossa-v1-design.md`. The relevant sections:
- §10 (packaging and deployment)
- §9.1 (air-gap install test)
- §1.1–1.2 (criterion 1: install from the bundle with no internet; the only setup step is editing one config file)
- §2.1–2.2 (images, `deploy/`)
- §3 (configuration)
- §7.3 (invalid config refuses to start)
- §7.4 (doctor)
- §7.6 (structured JSON logs)
- §8.3 (the sweep offer)
- §11 (milestone M5).

## Global Constraints

- **Every M1–M4b constraint still holds:**
  - 95% branch coverage per Python package
  - 95% lines, branches, functions and statements for `web/`
  - `filterwarnings = ["error"]` in the engine and the api
  - `make lint` clean
  - `make schema` shows no drift
  - unit tests touch no network except `127.0.0.1`
  - the library boundary
  - every user-facing message is one line
  - "translation guide", never "bible".
- **Target platform:** `linux/amd64` only. Every `docker build` and `docker pull` in the bundle passes `--platform linux/amd64`.
- **Pinned build inputs:**
  - Base images by exact tag: `python:3.12-slim-bookworm`, `node:22-bookworm-slim`, `ghcr.io/astral-sh/uv:0.13.0`, `postgres:16-alpine`, `anchore/syft:v1.18.1` and `koalaman/shellcheck:v0.10.0`.
  - Python dependencies from the committed `uv.lock` files (`--frozen`), and web dependencies from `pnpm-lock.yaml` (`--frozen-lockfile`).
  - Torch is the version `scorer/uv.lock` pins for Linux; only its build (CPU or CUDA) varies.
  - `manifest.json` in the bundle records every image's id and repo digests.
- **Containers run as uid 10001 / gid 10001** (user `coglossa`), never root. `/data/files` is owned by that user in the image.
- **Nothing reaches the internet at runtime.**
  - No image downloads anything when it starts.
  - The scorer sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.
  - The web app loads nothing from another origin (already enforced by M4b's journeys).
- **Operator scripts** (`install.sh`, `backup.sh`, `restore.sh`):
  - They start with `#!/usr/bin/env bash` and `set -euo pipefail`.
  - They need only bash, coreutils, tar and Docker with the Compose plugin ≥ 2.24 on the host.
  - They are shellcheck-clean.
  - Every error is one line starting `error: ` and says what to do.
  - `install.sh` never overwrites `config/coglossa.yaml` or `.env`.
- **Compose:**
  - Project name `coglossa`; images `coglossa/app:<version>` and `coglossa/scorer:<version>`.
  - In the repository the tag is `${COGLOSSA_VERSION:-dev}`; the bundle replaces that text with the literal version.
  - Every service has `json-file` logging with `max-size: 50m` and `max-file: "5"`.
- **Version string.**
  - `deploy/bundle.sh` takes `--version`.
  - The default is the tag at `HEAD` without its leading `v` (e.g. `0.1.0`) or, when `HEAD` is not tagged, `<coglossa.__version__>-g<short sha>` (e.g. `0.1.0-gd5a7a67`).
  - The bundle is `coglossa-<version>-linux-amd64.tar`, and it extracts to `coglossa-<version>/`.
- **Container paths:**
  - the config at `/etc/coglossa/coglossa.yaml` (`COGLOSSA_CONFIG`)
  - files at `/data/files`
  - the web app at `/opt/coglossa/web`
  - the weights at `/models/<name>`, with these names: `metricx-24-hybrid-xl-v2p6`, `metricx-24-hybrid-large-v2p6`, `mt5-xl` and `labse`.

## Review Focus

These are the failure modes most likely to bite an operator that no unit test exercises. Each has its test in the task named.

1. **Running `install.sh` a second time** (after an interrupted install, or to check a copy) must keep the edited `config/coglossa.yaml` and the existing `.env`. A new database password would lock Coglossa out of its own data. Task 8, `test-install.sh` case "a second run keeps the config and .env".
2. **A damaged or incomplete bundle copy** (a truncated transfer, a changed file, a missing image) is refused before anything is loaded, naming the file. Task 8 (stub docker) and Task 11 (a real bundle).
3. **An upgrade into a new directory keeps every project.** The volumes belong to the fixed project name, not the directory. Task 11, the "upgrade into a new directory" step.
4. **A model server on the Docker host itself** (Ollama on the same machine) must be reachable as `host.docker.internal` from `api` and `worker`. Task 7, `check_compose.py`.
5. **The worker starting while the scorer is still loading weights** (minutes on CPU) would score the first runs without MetricX and LaBSE. The worker waits for the scorer to be healthy, and the scorer's health check allows a long start. Task 7, `check_compose.py`.

## Decisions this plan makes (for review)

- **One app image** runs the API, the worker, the migrations and the demo's fake servers (spec §10 lists `api`, `worker` and `fake-openai` images). It is the same code with different commands. The images would share all but a few layers anyway, and one image makes the bundle smaller and the upgrades simpler.
- **The scorer weights live in the bundle's `models/` directory**, mounted read-only, not baked into the image. The scorer image stays a few GB, and the weights load from disk without unpacking an image layer.
- **The scorer image's torch build:**
  - `make bundle` builds the **CUDA** variant by default (`SCORER=cuda`). It also runs on a CPU-only host, at the cost of a few GB of bundle size. The customer's host may or may not have a GPU (the user, 2026-10-12), so one bundle covers both.
  - `SCORER=cpu` builds the small one.
  - CI builds the CPU variant (runner disk).
- **Translation models are the customer's own.** The customer has in-house models behind OpenAI-compatible APIs, which `config/coglossa.yaml` points at. This is the path M5 packages. Bundling translation models (and a server for them) for customers without their own is a separate milestone with its own design. The config already mixes backends, so bundled and in-house models can serve different roles in one install. `coglossa bench` (M6) vets the in-house models against the benchmarks.
- **FLORES-200 moves to M6** with `coglossa bench`, which is what uses it. M5's bundle has no `bench/` directory.
- **The API port is published on all interfaces by default** (`COGLOSSA_HTTP_BIND=0.0.0.0`, port 8080). v1 has no user management (spec §1.3). The operator guide says to bind `127.0.0.1` behind a reverse proxy, and that this is required with `trusted_header`.
- **Migrations run automatically at every `docker compose up`** through a one-shot `migrate` service that `api` and `worker` wait for (spec §10). The guide's upgrade steps run `./backup.sh` first.

---

### Task 1: Guide approval — only an approved guide outranks a draft; a code for guide conflicts

Resolves two items of `docs/m4b-followups.md`:
- "Approval refused after another run's draft became the project guide"
- "The guide-conflict 409 is matched by its text".

**Files:**
- Modify: `engine/coglossa/service.py` (`ConflictError`, new `GuideChangedError`, `approve_guide`)
- Modify: `engine/coglossa/catalog.py` (`save_project_guide` raises `GuideChangedError`)
- Modify: `api/coglossa_api/errors.py` (send `code` beside `detail`)
- Modify: `web/src/api/client.ts` (`ApiError.code`, `isGuideChanged`)
- Modify: `web/src/pages/run/RunGuide.tsx`, `web/src/guide/ProjectGuideEditor.tsx`
- Test: `engine/tests/test_service.py`, `engine/tests/test_guides.py` (or wherever the project-guide save conflict is tested: `grep -rn "the guide changed since you opened it" engine/tests`), `api/tests/test_guides.py`, `web/src/api/client.test.ts`, `web/src/pages/run/RunGuide.test.tsx`, `web/src/pages/ProjectGuidePage.test.tsx`

**Interfaces:**
- Produces:
  - `service.GuideChangedError(ConflictError)` with `code = "guide_changed"`
  - `ConflictError.code: str | None = None`
  - the API's error body `{"detail": str, "code"?: str}`: `code` is present only when the exception has one
  - in the web app, `ApiError.code: string | null`, `errorCode(body: unknown): string | null` and `isGuideChanged(error: unknown): boolean`.

- [ ] **Step 1: Write the failing engine tests**

Append to `engine/tests/test_service.py`:

```python
async def test_a_finished_runs_draft_does_not_block_approval(env, tmp_path):
    """Chapter 1 waits at the checkpoint while chapter 2, run without one, finishes: chapter 2's
    draft becomes the project guide (nothing is approved yet), and chapter 1 is still approved as
    it is. Only a person's approval or save outranks a draft."""
    db, _, queue, _, _ = env
    waiting = await _awaiting(env, tmp_path)  # draft v1, built without a project guide
    finished = await _awaiting(env, tmp_path)  # draft v2 ...
    await _set_state(db, finished, RunState.COMPLETE)  # ... is now the project guide
    assert await service.approve_guide(db, queue, waiting) == 3


async def test_an_edit_started_from_a_finished_runs_draft_is_approved(env, tmp_path):
    db, _, queue, _, _ = env
    waiting = await _awaiting(env, tmp_path)
    finished = await _awaiting(env, tmp_path)
    await _set_state(db, finished, RunState.COMPLETE)
    edited = Guide(summary="s", style_guide="from the project guide")
    assert await service.approve_guide(db, queue, waiting, edited, base_version=2) == 3


async def test_guide_conflicts_carry_a_code(env, tmp_path):
    db, _, queue, _, _ = env
    first = await _awaiting(env, tmp_path)
    second = await _awaiting(env, tmp_path)
    await service.approve_guide(db, queue, first)  # a person approved v3 after second's draft
    with pytest.raises(service.GuideChangedError) as e:
        await service.approve_guide(db, queue, second)
    assert e.value.code == "guide_changed"
    assert service.InvalidRunStateError("x").code is None
```

In `test_approve_refuses_when_the_project_guide_changed`, change `pytest.raises(service.ConflictError)` to `pytest.raises(service.GuideChangedError)`. In the project-guide save conflict test (the message "the guide changed since you opened it"), assert `isinstance(e.value, service.GuideChangedError)` and `e.value.code == "guide_changed"`. Change `pytest.raises(...)` to capture `as e` if it does not already.

- [ ] **Step 2: Run them to verify they fail**

Run: `make db-up && cd engine && uv run pytest tests/test_service.py -k "finished_runs_draft or started_from or carry_a_code or project_guide_changed" -v --no-cov`
Expected: FAIL. The first two fail with `ConflictError: the project guide changed since this draft (now version 2)`; the others fail with `AttributeError: module 'coglossa.service' has no attribute 'GuideChangedError'`.

- [ ] **Step 3: Implement**

In `engine/coglossa/service.py`, replace the `ConflictError` class and add the new one after `InvalidRunStateError`:

```python
class ConflictError(Exception):
    """The request conflicts with the current data; the message says why (HTTP 409). `code`
    names the conflicts a client handles specially; the API sends it beside the message."""

    code: str | None = None


class InvalidRunStateError(ConflictError):
    """The run is not in a state that allows the action; the message says why."""


class GuideChangedError(ConflictError):
    """The project guide changed after the edit or the draft was based on it: reload, redo."""

    code = "guide_changed"
```

In `approve_guide`, rewrite the docstring's middle sentence and replace the block from `if current is not None and current != base:` through its `raise`:

```python
    """Approve the guide of a run waiting at the checkpoint: `guide` (an edit) or the draft.
    `base_version` is the version the edit started from: the run's draft (the default), or the
    project guide the editor loaded. A version approved or saved by a person after the draft's
    base refuses the approval, as a stale save is refused. Another run's unapproved draft does
    not: while nothing is approved it is the project guide, and approving this run's guide
    supersedes it. Any other `base_version` is refused. The draft as generated is always based
    on itself, so `base_version` needs a `guide`."""
```

```python
        approved = await runs.latest_guide(s, doc.project_id, approved_only=True)
        if approved is not None and approved.version > (base or 0):
            raise GuideChangedError(
                f"the project guide changed since this draft (now version {approved.version}); "
                "reload it and approve the current guide"
            )
```

Remove the comment saying the web app matches the message's start. Keep the `current` computation: the `base_version` check above it still uses it.

In `engine/coglossa/catalog.py` `save_project_guide`, change `raise service.ConflictError(` (the "the guide changed since you opened it" one) to `raise service.GuideChangedError(`.

- [ ] **Step 4: Run the engine suite**

Run: `cd engine && uv run pytest`
Expected: PASS, coverage ≥ 95%.

- [ ] **Step 5: The API sends the code (test first)**

Find the API tests that answer the guide conflicts: `grep -rn "changed since" api/tests`. In each, assert that the body is exactly `{"detail": <the message>, "code": "guide_changed"}`. Add one assertion that a run-state 409 (e.g. approving a run that is not waiting) has no `code` key:

```python
    assert "code" not in response.json()
```

Run `cd api && uv run pytest -k guide --no-cov -v`. It fails on the missing `code`. Then, in `api/coglossa_api/errors.py`:

```python
def _response(status: int, message: str, code: str | None = None) -> JSONResponse:
    content = {"detail": " ".join(message.split())}
    if code is not None:
        content["code"] = code
    return JSONResponse(status_code=status, content=content)


def _handler(status: int) -> Handler:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        return _response(status, str(exc), getattr(exc, "code", None))

    return handle
```

Update the module docstring: `The body is always {"detail": "<one line>"}, plus "code" for the conflicts a client handles specially (ConflictError.code): never a traceback, ...`.

Run: `cd api && uv run pytest`. Expected: PASS, 95%+. `make schema` shows no drift: error bodies are not in the OpenAPI document.

- [ ] **Step 6: The web app reads the code (test first)**

In `web/src/api/client.test.ts` add:

```ts
it("keeps the server's conflict code", async () => {
  server.use(
    http.get("*/api/projects", () =>
      HttpResponse.json({ detail: "the guide changed", code: "guide_changed" }, { status: 409 }),
    ),
  );
  const error = await unwrap(client.GET("/api/projects")).catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect((error as ApiError).code).toBe("guide_changed");
  expect(isGuideChanged(error)).toBe(true);
});

it("has no code when the server sends none, or not a string", () => {
  expect(errorCode({ detail: "x" })).toBeNull();
  expect(errorCode({ code: 7 })).toBeNull();
  expect(errorCode(null)).toBeNull();
  expect(isGuideChanged(new ApiError(409, "busy"))).toBe(false);
  expect(isGuideChanged(new Error("x"))).toBe(false);
});
```

Match the file's existing imports and msw setup. In `RunGuide.test.tsx` and `ProjectGuidePage.test.tsx`, add `code: "guide_changed"` to every 409 body that stands for a guide conflict. Add one test to each that a 409 **without** the code (`{ detail: "the database was busy with a conflicting change; try again" }`) shows the error but no reload button ("Start from the current project guide (discards your edits)" in RunGuide; the project guide editor's reload button in ProjectGuidePage). Run `cd web && pnpm test`; the new tests fail.

Then, in `web/src/api/client.ts`:

```ts
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The API's machine-readable conflict code, sent beside `detail` for the conflicts the app
 * handles specially. */
export function errorCode(body: unknown): string | null {
  const code = (body as { code?: unknown } | null | undefined)?.code;
  return typeof code === "string" ? code : null;
}

/** The project guide changed under an edit or a draft: the answer is to reload it. */
export function isGuideChanged(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.code === "guide_changed";
}
```

In `unwrap`: `throw new ApiError(result.response.status, errorText(result.response.status, result.error), errorCode(result.error));`

Make these changes:
- In `RunGuide.tsx`, replace the `conflict` expression and its comment with `const conflict = isGuideChanged(approve.error);`.
- In `ProjectGuideEditor.tsx`, replace `save.error instanceof ApiError && save.error.status === 409` with `isGuideChanged(save.error)`.
- Drop any `ApiError` import that is now unused.

Run: `cd web && pnpm lint && pnpm test`. Expected: PASS, coverage ≥ 95% on all four measures.

- [ ] **Step 7: Commit**

```bash
git add engine api web
git commit -m "fix: only an approved guide blocks a checkpoint approval; guide conflicts carry a code"
```

---

### Task 2: The sweep offer follows the run's config

Resolves the item "The re-run-the-sweep offer ignores the profile" of `docs/m4b-followups.md`.

**Files:**
- Modify: `engine/coglossa/service.py` (new `sweep_enabled`; `rerun_sweep` uses it)
- Modify: `engine/coglossa/schema/views.py` (`RunDetail.sweep_enabled`)
- Modify: `engine/coglossa/catalog.py` (`run_detail` fills it)
- Regenerate: `schema/openapi.json`, `web/src/api/schema.d.ts` (`make schema`)
- Modify: `web/src/test/fixtures.ts`, `web/src/pages/run/RunGuide.tsx`, `web/src/review/SpanPanel.tsx`, `web/src/review/AddToGuide.tsx`, `web/src/review/ReviewPage.tsx`
- Test: `engine/tests/test_catalog.py`, `web/src/pages/run/RunGuideFinished.test.tsx`, the AddToGuide tests (`grep -rln "A locked entry reaches this translation" web/src`)

**Interfaces:**
- Produces:
  - `service.sweep_enabled(run: Run) -> bool`
  - `RunDetail.sweep_enabled: bool`
  - `AddToGuide` and `SpanPanel` take a new prop `sweepEnabled: boolean`.

- [ ] **Step 1: Write the failing engine test**

Append to `engine/tests/test_catalog.py`. Add the imports `Run` (from `coglossa.store.models`) and `update` (from `sqlalchemy`) if they are not already there:

```python
async def _snapshot_profiles(db, run_id, profiles):
    async with db.session() as s, s.begin():
        run = await s.get(Run, run_id)
        run.config_snapshot = {**run.config_snapshot, "profiles": profiles}


async def test_run_detail_says_whether_the_sweep_runs(db):
    seeded = await seed_run(db)  # a standard document; the sweep is on for standard by default
    assert (await catalog.run_detail(db, seeded.run_id)).sweep_enabled is True
    await _snapshot_profiles(db, seeded.run_id, {"sweep": {"standard": False}})
    assert (await catalog.run_detail(db, seeded.run_id)).sweep_enabled is False
    # a snapshot this version cannot read (an older version's profiles section): no offer
    await _snapshot_profiles(db, seeded.run_id, {"no_such_setting": 1})
    assert (await catalog.run_detail(db, seeded.run_id)).sweep_enabled is False
```

In `test_run_detail_of_a_new_run`, add `assert detail.sweep_enabled is False` (not profiled yet).

- [ ] **Step 2: Run it to verify it fails**

Run: `cd engine && uv run pytest tests/test_catalog.py -k "sweep_runs or new_run" --no-cov -v`
Expected: FAIL with `AttributeError: 'RunDetail' object has no attribute 'sweep_enabled'`.

- [ ] **Step 3: Implement**

In `engine/coglossa/service.py`:
- Import `ProfilesConfig` from `coglossa.config`, `ValidationError` from `pydantic`, and `Run` from `coglossa.store.models`.
- Add after the error classes:

```python
def sweep_enabled(run: Run) -> bool:
    """Spec §8.3: whether the config the run was started with (its snapshot's profiles.sweep)
    runs the sweep for the run's pipeline profile. False before profiling, and for a snapshot
    this version cannot read (a run from an older version whose profiles section changed)."""
    if not run.profile:
        return False
    try:
        profiles = ProfilesConfig.model_validate((run.config_snapshot or {}).get("profiles") or {})
    except ValidationError:
        return False
    profile = DocumentProfile.model_validate(run.profile).pipeline_profile
    return profiles.sweep.get(profile, True)
```

In `rerun_sweep`, replace the `profile = ...`, `snapshot = ...` and `if not snapshot.profiles.sweep.get(profile, True):` lines with:

```python
        if not sweep_enabled(run):
            profile = DocumentProfile.model_validate(run.profile).pipeline_profile
            raise InvalidRunStateError(
                f"the sweep is off for {profile} documents (profiles.sweep in the config)"
            )
```

If `CoglossaConfig` is now unused in `service.py`, drop its import.

In `engine/coglossa/schema/views.py`, add after `report_available` in `RunDetail`:

```python
    sweep_enabled: bool  # the run's config runs the sweep for its profile (profiles.sweep)
```

In `catalog.run_detail`, pass `sweep_enabled=service.sweep_enabled(run)` to `RunDetail(...)`.

Run: `cd engine && uv run pytest`. Expected: PASS, ≥ 95%.

- [ ] **Step 4: Regenerate the contract**

Run: `make schema`. Expected: `schema/openapi.json` and `web/src/api/schema.d.ts` gain `sweep_enabled`. `cd web && pnpm lint` now fails in `src/test/fixtures.ts`: `runDetail` lacks `sweep_enabled`. Add `sweep_enabled: true,` after `report_available` there. Run `cd api && uv run pytest` (its OpenAPI drift test) → PASS.

- [ ] **Step 5: The web app hides the offer (test first)**

In `RunGuideFinished.test.tsx`, add:

```ts
it("offers no sweep when the run's config has the sweep off", async () => {
  serve(guideView, approved(lockedTerm, 2));
  server.use(
    http.get("*/api/runs/r-1", () => HttpResponse.json({ ...complete, sweep_enabled: false })),
  );
  renderApp("/runs/r-1/guide");
  const project = await screen.findByRole("region", { name: "Project guide" });
  await within(project).findByText(/Guide version 2/);
  expect(screen.queryByText(OFFER)).not.toBeInTheDocument();
});
```

In the AddToGuide tests, add a test that a locked entry added with `sweepEnabled={false}` shows "Added to the project guide (version …)" but not "A locked entry reaches this translation when the consistency sweep is re-run". If those tests render the whole app rather than the component, serve a run detail with `sweep_enabled: false`. Run `pnpm test`; the new tests fail.

Then:
- In `RunGuide.tsx` `SweepOffer`: `const offer = detail.sweep_enabled && used.data !== undefined && project.data !== undefined && lockedChanged(used.data.guide, project.data.guide);`
- In `AddToGuide.tsx`:
  - `type Props = { runId: string; projectId: string; spanId: string; sweepEnabled: boolean };`
  - destructure `sweepEnabled`
  - the hint's condition becomes `add.isSuccess && sweepEnabled && isLocked(add.variables.addition)`.
- In `SpanPanel.tsx`: add `sweepEnabled: boolean` to `Props` and pass `sweepEnabled={sweepEnabled}` to `AddToGuide`.
- In `ReviewPage.tsx`: `<SpanPanel ... sweepEnabled={detail.sweep_enabled} />`.

Run: `cd web && pnpm lint && pnpm test`. Expected: PASS, ≥ 95% on all four measures.

- [ ] **Step 6: Commit**

```bash
git add engine schema api web
git commit -m "fix: offer the sweep only when the run's config runs it"
```

---

### Task 3: Container entry points

**Files:**
- Modify: `engine/coglossa/config.py` (`CONFIG_ENV`, `default_config_path`)
- Modify: `engine/coglossa/cli.py` (`run --config` default; fake servers without request logs)
- Modify: `engine/coglossa/cli_ops.py` (`_config_arg`)
- Modify: `engine/coglossa/testing/fake_openai.py`, `engine/coglossa/testing/fake_scorer.py` (`keep_requests`)
- Modify: `api/coglossa_api/main.py` (`--config` default, `--web-dir`)
- Test: `engine/tests/test_config_m4.py` (or a new `engine/tests/test_config_m5.py`), `engine/tests/test_cli.py`, `engine/tests/testing/` (the fake servers' tests: `ls engine/tests/testing`), `api/tests/test_main.py`

**Interfaces:**
- Produces:
  - `coglossa.config.CONFIG_ENV = "COGLOSSA_CONFIG"`
  - `default_config_path() -> Path`
  - `FakeOpenAIServer(..., keep_requests: bool = True)` and `FakeScorerServer(..., keep_requests: bool = True)`
  - `coglossa-api --web-dir DIR`.
- Task 4's image sets `COGLOSSA_CONFIG=/etc/coglossa/coglossa.yaml` and runs `coglossa-api --host 0.0.0.0 --port 8080 --web-dir /opt/coglossa/web`. The demo runs `coglossa fake-openai` and `coglossa fake-scorer` for days, so they must not keep every request in memory.

- [ ] **Step 1: Write the failing tests**

`engine/tests/test_config_m5.py`:

```python
from pathlib import Path

from coglossa.cli import build_parser
from coglossa.config import CONFIG_ENV, default_config_path


def test_the_config_path_defaults_to_the_environment(monkeypatch):
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    assert default_config_path() == Path("coglossa.yaml")
    monkeypatch.setenv(CONFIG_ENV, "/etc/coglossa/coglossa.yaml")
    assert default_config_path() == Path("/etc/coglossa/coglossa.yaml")
    monkeypatch.setenv(CONFIG_ENV, "")
    assert default_config_path() == Path("coglossa.yaml")


def test_every_command_takes_the_environments_config(monkeypatch):
    monkeypatch.setenv(CONFIG_ENV, "/etc/coglossa/coglossa.yaml")
    parser = build_parser()
    for argv in (
        ["doctor"],
        ["worker"],
        ["db", "upgrade"],
        ["status", "r"],
        ["run", "x.txt", "--target-lang", "en"],
    ):
        assert parser.parse_args(argv).config == Path("/etc/coglossa/coglossa.yaml"), argv
```

In the fake servers' tests, add for each server:

```python
def test_the_fake_openai_server_can_forget_requests():
    server = FakeOpenAIServer(keep_requests=False).start()
    try:
        body = {"model": "fake-model", "messages": [{"role": "user", "content": "hi"}]}
        with httpx.Client() as client:
            assert client.post(f"{server.base_url}/chat/completions", json=body).status_code == 200
        assert server.requests == []
    finally:
        server.stop()
```

Do the same for `FakeScorerServer(keep_requests=False)` with `GET /health`. Use the request style these test files already use. In `engine/tests/test_cli.py`, extend `test_fake_openai_command` (and the fake-scorer command test) to capture the constructor's keyword arguments with `monkeypatch` and assert `keep_requests is False`.

In `api/tests/test_main.py`, add tests in the file's existing style (it already runs `main` with `uvicorn.run` patched):
- `--web-dir DIR` with an `index.html` passes a config whose `api.web_dir` is `DIR` resolved.
- `--web-dir` naming a directory without `index.html` exits 1 with the `missing_index` message.
- With `COGLOSSA_CONFIG` set and no `--config`, `main` loads that file.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd engine && uv run pytest tests/test_config_m5.py tests/test_cli.py tests/testing --no-cov -q`, then `cd api && uv run pytest tests/test_main.py --no-cov -q`
Expected: FAIL. Among the errors: `ImportError: cannot import name 'CONFIG_ENV'`, `TypeError: ... unexpected keyword argument 'keep_requests'`, and `error: unrecognized arguments: --web-dir`.

- [ ] **Step 3: Implement**

`engine/coglossa/config.py`, after the imports:

```python
CONFIG_ENV = "COGLOSSA_CONFIG"


def default_config_path() -> Path:
    """`--config`'s default: $COGLOSSA_CONFIG (the container images set it), else
    ./coglossa.yaml."""
    return Path(os.environ.get(CONFIG_ENV) or "coglossa.yaml")
```

In `cli.py` (`run`) and `cli_ops.py` (`_config_arg`), use `default=default_config_path()`. Parsers are built per `main()` call, so the environment is read at run time.

In `fake_openai.py`:
- add `keep_requests: bool` to `_Server`'s annotations
- add `keep_requests: bool = True` to `FakeOpenAIServer.__init__` and store it as `self._server.keep_requests = keep_requests`
- in `do_POST`, append only `if self.server.keep_requests:`, inside the existing lock.

Do the same in `fake_scorer.py`: guard both `requests.append` calls, and add `keep_requests` as keyword-only next to `fail_status`. Update the module docstring of `fake_openai.py`: "Used by integration tests, by `coglossa fake-openai`, and by the demo stack (compose.demo.yaml), which keeps no request log."

In `cli.py`, pass `keep_requests=False`: `FakeOpenAIServer(args.host, args.port, tuple(args.models or ["fake-model"]), keep_requests=False)` and `FakeScorerServer(args.host, args.port, keep_requests=False)`.

`api/coglossa_api/main.py`:

```python
    parser.add_argument("--config", type=Path, default=default_config_path())
    ...
    parser.add_argument(
        "--web-dir", type=Path, help="the built web app (web/dist); overrides api.web_dir"
    )
    ...
        cfg, warnings = load_config(args.config)
        if args.web_dir is not None:
            api = cfg.api.model_copy(update={"web_dir": args.web_dir.resolve()})
            cfg = cfg.model_copy(update={"api": api})
```

`default_config_path` is imported from `coglossa.config`. The existing `missing_index` check then covers `--web-dir`.

- [ ] **Step 4: Run the suites**

Run: `make test-engine test-api`
Expected: PASS, both ≥ 95%.

- [ ] **Step 5: Commit**

```bash
git add engine api
git commit -m "feat: COGLOSSA_CONFIG, coglossa-api --web-dir, fake servers without request logs"
```

---

### Task 4: The app image

**Files:**
- Create: `deploy/docker/app.Dockerfile`
- Create: `.dockerignore` (repository root)
- Create: `deploy/tests/check-app-image.sh`
- Modify: `Makefile` (`COGLOSSA_VERSION`, `PLATFORM`, `images`, `image-app`)
- Modify: `.gitignore` (deployment files that must not be committed)

**Interfaces:**
- Consumes: Task 3's `COGLOSSA_CONFIG` and `--web-dir`.
- Produces:
  - the image `coglossa/app:<version>`
  - `deploy/tests/check-app-image.sh IMAGE`, which exits 0 when the image is right
  - the Make variables `COGLOSSA_VERSION ?= dev` and `PLATFORM ?= linux/amd64`, and the target `image-app`.

- [ ] **Step 1: Write the image check (the failing test)**

`deploy/tests/check-app-image.sh` (mode 755):

```bash
#!/usr/bin/env bash
# Checks a built app image: its commands, the bundled web app, migrations and prompts, the
# non-root user, the config path, and that it refuses to start without a config.
# Usage: deploy/tests/check-app-image.sh IMAGE
set -euo pipefail
image=${1:?usage: check-app-image.sh IMAGE}
fail() { echo "FAIL: $image: $*" >&2; exit 1; }
run() { docker run --rm --network none --entrypoint "" "$image" "$@"; }

docker image inspect "$image" >/dev/null 2>&1 || fail "no such image (build it: make image-app)"
[ "$(run id -u):$(run id -g)" = "10001:10001" ] || fail "must run as uid 10001, gid 10001"
run coglossa --version | grep -q '^coglossa ' || fail "coglossa --version"
run coglossa-api --help >/dev/null || fail "coglossa-api --help"
[ "$(run sh -c 'echo "$COGLOSSA_CONFIG"')" = /etc/coglossa/coglossa.yaml ] \
  || fail "COGLOSSA_CONFIG must be /etc/coglossa/coglossa.yaml"
run test -f /opt/coglossa/web/index.html || fail "the web app is missing from /opt/coglossa/web"
run sh -c 'ls /opt/coglossa/web/assets/*.woff2 >/dev/null 2>&1' || fail "the bundled fonts are missing"
run sh -c 'touch /data/files/.write-test' || fail "/data/files is not writable by the app user"
run python -c '
from importlib.resources import files
for path in ("store/migrations/versions/0003_guide_built_from.py",
             "store/migrations/versions/0001_procrastinate_3_10_0.sql",
             "prompts/templates/literal/v1.j2", "scoring/tiers.yaml"):
    assert files("coglossa").joinpath(path).is_file(), path
' || fail "package data (migrations, prompts, tiers) is missing"
label=$(docker image inspect -f '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$image")
[ -n "$label" ] || fail "the org.opencontainers.image.version label is missing"
if out=$(docker run --rm --network none "$image" 2>&1); then
  fail "the API started without a config"
fi
grep -q "config file not found: /etc/coglossa/coglossa.yaml" <<<"$out" \
  || fail "without a config the API must say so in one line, got: $out"
echo "ok: $image"
```

Run: `deploy/tests/check-app-image.sh coglossa/app:dev`
Expected: `FAIL: coglossa/app:dev: no such image (build it: make image-app)`, exit 1.

- [ ] **Step 2: Write the build context allowlist**

`.dockerignore`:

```
# The build context is an allowlist: only what the images need. This keeps .venv,
# node_modules, caches and local configs out of the images and the build.
*
!engine/pyproject.toml
!engine/uv.lock
!engine/coglossa/
!api/pyproject.toml
!api/uv.lock
!api/coglossa_api/
!scorer/pyproject.toml
!scorer/uv.lock
!scorer/coglossa_scorer/
!web/
web/node_modules/
web/dist/
web/coverage/
web/playwright-report/
web/test-results/
**/__pycache__/
**/.pytest_cache/
```

- [ ] **Step 3: Write the Dockerfile**

`deploy/docker/app.Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# The Coglossa app image (spec §10): the API serving the built web app, the worker, the CLI
# (migrations, doctor) and the fake model and scorer servers of the demo. Build it from the
# repository root:  docker build -f deploy/docker/app.Dockerfile -t coglossa/app:dev .

FROM node:22-bookworm-slim AS web
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
WORKDIR /src/web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim-bookworm AS python
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY engine/pyproject.toml engine/uv.lock engine/
COPY api/pyproject.toml api/uv.lock api/
# Dependencies first, so a code change does not reinstall them.
RUN cd api && uv sync --frozen --no-dev --no-install-project --no-install-package coglossa
COPY engine/coglossa engine/coglossa
COPY api/coglossa_api api/coglossa_api
RUN cd api && uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
ARG VERSION=dev
LABEL org.opencontainers.image.title="coglossa-app" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/Bonham-Technologies/coglossa"
RUN groupadd --gid 10001 coglossa \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin coglossa \
 && mkdir -p /data/files /etc/coglossa \
 && chown coglossa:coglossa /data/files
COPY --from=python /opt/venv /opt/venv
COPY --from=web /src/web/dist /opt/coglossa/web
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    COGLOSSA_CONFIG=/etc/coglossa/coglossa.yaml
USER 10001:10001
WORKDIR /data
EXPOSE 8080
CMD ["coglossa-api", "--host", "0.0.0.0", "--port", "8080", "--web-dir", "/opt/coglossa/web"]
```

If `uv sync --no-install-package coglossa` rejects the path dependency, use `--no-install-workspace` or move the engine install into the second `uv sync`. In either case the second step must leave `/opt/venv` without editable installs (`--no-editable`). Record the variant you used in the report.

- [ ] **Step 4: Makefile and .gitignore**

Add to the `Makefile` (extend `.PHONY` too):

```make
COGLOSSA_VERSION ?= dev
PLATFORM ?= linux/amd64

images: image-app image-scorer

image-app:
	docker build --platform $(PLATFORM) -f deploy/docker/app.Dockerfile \
	  --build-arg VERSION=$(COGLOSSA_VERSION) -t coglossa/app:$(COGLOSSA_VERSION) .
	deploy/tests/check-app-image.sh coglossa/app:$(COGLOSSA_VERSION)
```

`image-scorer` is Task 5's; until then, `make images` fails on the missing target, which is fine for one task. Append to `.gitignore`:

```
# a local deployment from deploy/ (make demo, or testing the compose files)
deploy/.env
deploy/config/coglossa.yaml
deploy/models/
deploy/backups/
```

- [ ] **Step 5: Build and check**

Run: `make image-app`
Expected: the build succeeds and the check prints `ok: coglossa/app:dev`. Report the image size (`docker image ls coglossa/app:dev`); expect a few hundred MB.

- [ ] **Step 6: Commit**

```bash
git add .dockerignore .gitignore Makefile deploy/docker/app.Dockerfile deploy/tests/check-app-image.sh
git commit -m "feat: the app image (API with the web app, worker, CLI)"
```

---

### Task 5: The scorer image

**Files:**
- Create: `deploy/docker/scorer.Dockerfile`
- Create: `deploy/tests/check-scorer-image.sh`
- Modify: `Makefile` (`CUDA_INDEX`, `image-scorer`)

**Interfaces:**
- Produces:
  - the image `coglossa/scorer:<version>`, built with `--build-arg TORCH_INDEX=cpu|<cuda index>`
  - `deploy/tests/check-scorer-image.sh IMAGE TORCH_INDEX`
  - the Make variable `CUDA_INDEX` (the CUDA index this task settles on).
- Task 10 uses `CUDA_INDEX`'s value as `bundle.sh`'s default.

- [ ] **Step 1: Write the image check (the failing test)**

`deploy/tests/check-scorer-image.sh` (mode 755):

```bash
#!/usr/bin/env bash
# Checks a built scorer image: the command, the torch build it was asked for, offline settings,
# the non-root user, and a one-line refusal when the weights are missing.
# Usage: deploy/tests/check-scorer-image.sh IMAGE TORCH_INDEX   (TORCH_INDEX: cpu, cu128, ...)
set -euo pipefail
image=${1:?usage: check-scorer-image.sh IMAGE TORCH_INDEX}
index=${2:?usage: check-scorer-image.sh IMAGE TORCH_INDEX}
fail() { echo "FAIL: $image: $*" >&2; exit 1; }
run() { docker run --rm --network none --entrypoint "" "$image" "$@"; }

docker image inspect "$image" >/dev/null 2>&1 || fail "no such image (build it: make image-scorer)"
[ "$(run id -u):$(run id -g)" = "10001:10001" ] || fail "must run as uid 10001, gid 10001"
run coglossa-scorer --help >/dev/null || fail "coglossa-scorer --help"
torch=$(run python -c 'import torch; print(torch.__version__)')
[[ $torch == *"+$index" ]] || fail "torch is $torch, expected a +$index build"
run python -c 'import transformers, sentence_transformers, sentencepiece, safetensors' \
  || fail "a scorer dependency is missing"
[ "$(run sh -c 'echo "$HF_HUB_OFFLINE:$TRANSFORMERS_OFFLINE"')" = "1:1" ] \
  || fail "HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE must be 1"
if out=$(run coglossa-scorer --embed-model /models/labse 2>&1); then
  fail "the scorer started without weights"
fi
grep -q "^error: cannot load model" <<<"$out" || fail "missing weights must fail in one line, got: $out"
echo "ok: $image (torch $torch)"
```

Run: `deploy/tests/check-scorer-image.sh coglossa/scorer:dev cpu`
Expected: `FAIL: ... no such image`, exit 1.

- [ ] **Step 2: Write the Dockerfile**

`deploy/docker/scorer.Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# The scorer image (spec §2.1): coglossa-scorer serving MetricX-24 and LaBSE. The weights are
# not in the image: compose mounts the bundle's models/ directory at /models (read-only).
#   docker build -f deploy/docker/scorer.Dockerfile --build-arg TORCH_INDEX=cpu -t coglossa/scorer:dev .
# TORCH_INDEX picks the torch build from download.pytorch.org/whl/<index>: cpu, or a CUDA build
# (which also runs on a CPU-only host). The torch version is always the one scorer/uv.lock pins
# for Linux.

FROM python:3.12-slim-bookworm AS python
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ARG TORCH_INDEX=cpu
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src/scorer
COPY scorer/pyproject.toml scorer/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-install-package torch
RUN version=$(python3 -c 'import tomllib; lock = tomllib.load(open("uv.lock", "rb")); print(next(p["version"] for p in lock["package"] if p["name"] == "torch" and "download.pytorch.org" in p["source"].get("registry", "")).split("+")[0])') \
 && uv pip install --python /opt/venv/bin/python "torch==${version}" \
      --index-url "https://download.pytorch.org/whl/${TORCH_INDEX}" \
      --extra-index-url https://pypi.org/simple
COPY scorer/coglossa_scorer coglossa_scorer
RUN uv pip install --python /opt/venv/bin/python --no-deps .

FROM python:3.12-slim-bookworm
ARG VERSION=dev
ARG TORCH_INDEX=cpu
LABEL org.opencontainers.image.title="coglossa-scorer" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/Bonham-Technologies/coglossa" \
      coglossa.torch-index="${TORCH_INDEX}"
RUN groupadd --gid 10001 coglossa \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin coglossa
COPY --from=python /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/tmp/huggingface
USER 10001:10001
EXPOSE 8080
CMD ["coglossa-scorer", "--qe-model", "/models/metricx-24-hybrid-large-v2p6", \
     "--qe-tokenizer", "/models/mt5-xl", "--embed-model", "/models/labse", \
     "--host", "0.0.0.0", "--port", "8080"]
```

`check-scorer-image.sh` imports `safetensors`. It is a dependency of transformers; Task 6 makes it an explicit one.

- [ ] **Step 3: Settle the CUDA index**

Find the CUDA build of the locked torch version (the Linux `torch` entry of `scorer/uv.lock`, e.g. `2.14.1`). List `https://download.pytorch.org/whl/<index>/torch/` for `cu130`, `cu129`, `cu128` and `cu126`. Pick the **newest CUDA 12.x index** with a `torch-<version>+<index>-cp312-cp312-manylinux*_x86_64.whl` wheel. CUDA 12 builds run on NVIDIA drivers ≥ 525, which keeps older customer drivers working. Use a 13.x index only when no 12.x one has the version. Write the result into the Makefile as `CUDA_INDEX ?= <index>`, with a comment naming the driver floor. Record the index and the driver floor in the report; Task 9 puts them in the operator guide.

- [ ] **Step 4: Makefile**

```make
# CUDA build of torch for the scorer (download.pytorch.org/whl/<index>); CUDA 12 needs NVIDIA
# driver 525 or newer.
CUDA_INDEX ?= cu128

image-scorer:
	docker build --platform $(PLATFORM) -f deploy/docker/scorer.Dockerfile \
	  --build-arg VERSION=$(COGLOSSA_VERSION) --build-arg TORCH_INDEX=cpu \
	  -t coglossa/scorer:$(COGLOSSA_VERSION) .
	deploy/tests/check-scorer-image.sh coglossa/scorer:$(COGLOSSA_VERSION) cpu

image-scorer-cuda:
	docker build --platform $(PLATFORM) -f deploy/docker/scorer.Dockerfile \
	  --build-arg VERSION=$(COGLOSSA_VERSION) --build-arg TORCH_INDEX=$(CUDA_INDEX) \
	  -t coglossa/scorer:$(COGLOSSA_VERSION) .
	deploy/tests/check-scorer-image.sh coglossa/scorer:$(COGLOSSA_VERSION) $(CUDA_INDEX)
```

Use the index Step 3 settled on in place of `cu128` if it differs. `make images` builds the CPU scorer, the one development and CI use.

- [ ] **Step 5: Build and check both variants**

Run: `make image-scorer`. Expected: `ok: coglossa/scorer:dev (torch <v>+cpu)`.

Then run: `make image-scorer-cuda COGLOSSA_VERSION=dev-cuda`. Expected: `ok: coglossa/scorer:dev-cuda (torch <v>+<index>)`. This download is several GB. If the machine lacks the disk, report DONE_WITH_CONCERNS with the error, rather than skipping silently. Then remove it: `docker image rm coglossa/scorer:dev-cuda`.

Report both image sizes.

The check might print a traceback for the missing weights instead of `error: cannot load model`. sentence-transformers can raise an exception that `coglossa_scorer.cli` does not catch (e.g. a `huggingface_hub` validation error). In that case, widen the `except` in `cli.main` to that exception's class. Add a test to `scorer/tests/test_cli.py` that a missing `--embed-model` directory exits 1 with the one-line error. `make test-scorer` must stay ≥ 95%.

- [ ] **Step 6: Commit**

```bash
git add Makefile deploy/docker/scorer.Dockerfile deploy/tests/check-scorer-image.sh
git commit -m "feat: the scorer image, with a CPU or CUDA torch build"
```

---

### Task 6: Scorer weights — fetch once, convert to safetensors

Resolves the item "Ship MetricX as safetensors in the bundle (M5)" of `docs/m2-followups.md`.

**Files:**
- Create: `scorer/coglossa_scorer/weights.py`
- Modify: `scorer/pyproject.toml` (dependencies `huggingface_hub`, `safetensors`; script `coglossa-scorer-weights`), `scorer/uv.lock` (`uv lock`)
- Modify: `Makefile` (`WEIGHTS_DIR`, `weights`)
- Test: `scorer/tests/test_weights.py`

**Interfaces:**
- Produces:
  - `coglossa_scorer.weights.MODELS: dict[str, WeightSpec]`, whose keys are exactly `metricx-24-hybrid-xl-v2p6`, `metricx-24-hybrid-large-v2p6`, `mt5-xl` and `labse`
  - `fetch(out: Path, names, *, revisions=None, hub=None, log=print) -> dict`
  - `convert_to_safetensors(model_dir: Path) -> None`
  - `verify(out: Path, names) -> list[str]`, which lists problems (empty when the weights are fine)
  - the CLI `coglossa-scorer-weights fetch --out DIR [--model NAME]... [--revision NAME=SHA]...` and `coglossa-scorer-weights verify DIR [--model NAME]...`, with exit 0 on success, 1 on a problem and 2 on a usage error
  - `DIR/weights.json`, which maps each name to `{"repo", "revision", "files"}`.
- Tasks 7 and 10 rely on the directory layout `DIR/<name>/…` and on `verify`.

- [ ] **Step 1: Write the failing tests**

`scorer/tests/test_weights.py`:

```python
import json
from pathlib import Path

import pytest
import torch
from safetensors.torch import load_file
from transformers import MT5Config, MT5ForConditionalGeneration

from coglossa_scorer import weights
from coglossa_scorer.weights import MODELS, convert_to_safetensors, fetch, main, verify


def tiny_mt5() -> MT5ForConditionalGeneration:
    torch.manual_seed(0)
    config = MT5Config(
        vocab_size=64, d_model=16, d_kv=4, d_ff=32, num_layers=1, num_decoder_layers=1,
        num_heads=2, decoder_start_token_id=0,
    )
    return MT5ForConditionalGeneration(config)


def save_bin(model, directory: Path, shards: int = 1) -> dict[str, torch.Tensor]:
    """A checkpoint as MetricX publishes it: torch.save pickles, tied embeddings sharing storage."""
    directory.mkdir(parents=True, exist_ok=True)
    model.config.save_pretrained(directory)
    state = model.state_dict()
    if shards == 1:
        torch.save(state, directory / "pytorch_model.bin")
        return state
    names = sorted(state)
    weight_map = {}
    for i in range(shards):
        shard = f"pytorch_model-{i + 1:05d}-of-{shards:05d}.bin"
        part = {n: state[n] for n in names[i::shards]}
        torch.save(part, directory / shard)
        weight_map |= dict.fromkeys(part, shard)
    (directory / "pytorch_model.bin.index.json").write_text(
        json.dumps({"metadata": {}, "weight_map": weight_map})
    )
    return state


def assert_same_model(directory: Path, state: dict[str, torch.Tensor]) -> None:
    loaded = MT5ForConditionalGeneration.from_pretrained(directory, local_files_only=True)
    for name, tensor in loaded.state_dict().items():
        assert torch.equal(tensor, state[name]), name


def test_a_single_file_checkpoint_becomes_safetensors(tmp_path):
    state = save_bin(tiny_mt5(), tmp_path)
    convert_to_safetensors(tmp_path)
    assert not list(tmp_path.glob("*.bin"))
    stored = load_file(tmp_path / "model.safetensors")
    # the tied embeddings are stored once, under the shortest name; transformers re-ties them
    assert "shared.weight" in stored and "encoder.embed_tokens.weight" not in stored
    assert_same_model(tmp_path, state)


def test_a_sharded_checkpoint_becomes_sharded_safetensors(tmp_path):
    state = save_bin(tiny_mt5(), tmp_path, shards=2)
    convert_to_safetensors(tmp_path)
    assert not list(tmp_path.glob("*.bin")) and not (tmp_path / "pytorch_model.bin.index.json").exists()
    index = json.loads((tmp_path / "model.safetensors.index.json").read_text())
    assert set(index["weight_map"].values()) == {
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
    }
    assert index["metadata"]["total_size"] > 0
    assert_same_model(tmp_path, state)


def test_overlapping_views_are_stored_as_copies(tmp_path):
    base = torch.arange(6.0)
    torch.save({"a": base[:4], "b": base[2:]}, tmp_path / "pytorch_model.bin")
    convert_to_safetensors(tmp_path)
    stored = load_file(tmp_path / "model.safetensors")
    assert stored["a"].tolist() == [0, 1, 2, 3] and stored["b"].tolist() == [2, 3, 4, 5]


def test_nothing_to_convert_is_left_alone(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"x")
    convert_to_safetensors(tmp_path)
    assert (tmp_path / "model.safetensors").read_bytes() == b"x"


class FakeHub:
    """Stands in for huggingface_hub: records calls, writes the files a download would."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def resolve(self, repo: str, revision: str | None) -> str:
        if self.fail:
            raise OSError("connection refused")
        return revision or f"sha-of-{repo}"

    def download(self, **kwargs) -> str:
        self.calls.append(kwargs)
        target = Path(kwargs["local_dir"])
        (target / ".cache").mkdir(parents=True, exist_ok=True)
        if "metricx" in kwargs["repo_id"]:
            save_bin(tiny_mt5(), target)
        elif kwargs["repo_id"] == "google/mt5-xl":
            (target / "spiece.model").write_bytes(b"sp")
        else:
            (target / "modules.json").write_text("[]")
            (target / "config.json").write_text("{}")
        return str(target)


def test_fetch_downloads_converts_and_records(tmp_path):
    hub = FakeHub()
    manifest = fetch(
        tmp_path, ["metricx-24-hybrid-large-v2p6", "mt5-xl"],
        revisions={"mt5-xl": "abc123"}, hub=(hub.resolve, hub.download), log=lambda _: None,
    )
    assert [c["repo_id"] for c in hub.calls] == ["google/metricx-24-hybrid-large-v2p6", "google/mt5-xl"]
    assert hub.calls[1]["revision"] == "abc123"
    assert hub.calls[1]["allow_patterns"] == list(MODELS["mt5-xl"].allow)
    assert not (tmp_path / "mt5-xl" / ".cache").exists()
    assert list((tmp_path / "metricx-24-hybrid-large-v2p6").glob("*.safetensors"))
    assert manifest["mt5-xl"] == {"repo": "google/mt5-xl", "revision": "abc123", "files": ["spiece.model"]}
    assert json.loads((tmp_path / "weights.json").read_text()) == manifest
    # a second fetch adds to the manifest instead of replacing it
    fetch(tmp_path, ["labse"], hub=(hub.resolve, hub.download), log=lambda _: None)
    assert set(json.loads((tmp_path / "weights.json").read_text())) == {
        "metricx-24-hybrid-large-v2p6", "mt5-xl", "labse",
    }


def test_verify_lists_every_problem(tmp_path):
    names = ["metricx-24-hybrid-large-v2p6", "mt5-xl", "labse"]
    assert verify(tmp_path, names) == [
        "metricx-24-hybrid-large-v2p6: missing; fetch it: coglossa-scorer-weights fetch --out "
        f"{tmp_path} --model metricx-24-hybrid-large-v2p6",
        f"mt5-xl: missing; fetch it: coglossa-scorer-weights fetch --out {tmp_path} --model mt5-xl",
        f"labse: missing; fetch it: coglossa-scorer-weights fetch --out {tmp_path} --model labse",
        "weights.json is missing; fetch the weights with coglossa-scorer-weights fetch",
    ]
    hub = FakeHub()
    fetch(tmp_path, names, hub=(hub.resolve, hub.download), log=lambda _: None)
    assert verify(tmp_path, names) == []
    (tmp_path / "mt5-xl" / "spiece.model").unlink()
    torch.save({}, tmp_path / "metricx-24-hybrid-large-v2p6" / "pytorch_model.bin")
    for f in (tmp_path / "metricx-24-hybrid-large-v2p6").glob("*.safetensors"):
        f.unlink()
    assert verify(tmp_path, names) == [
        "metricx-24-hybrid-large-v2p6: has unconverted .bin files; fetch it again",
        "metricx-24-hybrid-large-v2p6: has no *.safetensors weights; fetch it again",
        "mt5-xl: spiece.model is missing; fetch it again",
    ]


def test_cli_fetch_and_verify(tmp_path, monkeypatch, capsys):
    hub = FakeHub()
    monkeypatch.setattr(weights, "_hub", lambda: (hub.resolve, hub.download))
    assert main(["fetch", "--out", str(tmp_path), "--model", "mt5-xl", "--revision", "mt5-xl=r1"]) == 0
    assert hub.calls[0]["revision"] == "r1"
    assert main(["verify", str(tmp_path), "--model", "mt5-xl"]) == 0
    assert main(["verify", str(tmp_path)]) == 1  # the other three are missing
    assert "error: labse: missing" in capsys.readouterr().err


def test_cli_errors_are_one_line(tmp_path, monkeypatch, capsys):
    hub = FakeHub(fail=True)
    monkeypatch.setattr(weights, "_hub", lambda: (hub.resolve, hub.download))
    assert main(["fetch", "--out", str(tmp_path), "--model", "labse"]) == 1
    assert capsys.readouterr().err == (
        "error: cannot fetch sentence-transformers/LaBSE: connection refused\n"
    )
    with pytest.raises(SystemExit) as e:
        main(["fetch", "--out", str(tmp_path), "--model", "nope"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        main(["fetch", "--out", str(tmp_path), "--revision", "no-equals-sign"])
    assert e.value.code == 2


def test_the_real_hub_is_wired_without_calling_it():
    resolve, download = weights._hub()
    assert callable(resolve) and download.__name__ == "snapshot_download"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd scorer && uv run pytest tests/test_weights.py --no-cov -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'coglossa_scorer.weights'`.

- [ ] **Step 3: Implement**

`scorer/coglossa_scorer/weights.py`:

```python
"""coglossa-scorer-weights: fetch the scorer's model weights once, on a machine with internet
access, into the directory the bundle ships as models/ (spec §10), and check that directory.

MetricX publishes PyTorch pickles (pytorch_model*.bin); they are rewritten as safetensors,
which load without unpickling and faster. Only the mT5 tokenizer is fetched (MetricX carries
its own weights), and LaBSE is fetched without its TensorFlow, Flax and ONNX copies."""

import argparse
import json
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Resolve = Callable[[str, str | None], str]  # (repo, revision or None) -> commit sha
Download = Callable[..., str]  # huggingface_hub.snapshot_download


@dataclass(frozen=True)
class WeightSpec:
    repo: str
    allow: tuple[str, ...] | None = None  # only these files (None: all but IGNORE)
    convert: bool = False  # rewrite pytorch_model*.bin as safetensors
    required: tuple[str, ...] = ()  # files verify() expects


_METRICX_FILES = (
    "config.json",
    "pytorch_model*.bin",
    "pytorch_model.bin.index.json",
    "*.safetensors",
    "model.safetensors.index.json",
)
IGNORE = ("*.h5", "*.msgpack", "*.onnx", "onnx/*", "openvino/*", "*.ot", "tf_model*", "flax_model*")
MODELS: dict[str, WeightSpec] = {
    "metricx-24-hybrid-xl-v2p6": WeightSpec(
        "google/metricx-24-hybrid-xl-v2p6", _METRICX_FILES, convert=True, required=("config.json",)
    ),
    "metricx-24-hybrid-large-v2p6": WeightSpec(
        "google/metricx-24-hybrid-large-v2p6", _METRICX_FILES, convert=True, required=("config.json",)
    ),
    "mt5-xl": WeightSpec(
        "google/mt5-xl",
        ("config.json", "spiece.model", "tokenizer_config.json", "special_tokens_map.json"),
        required=("spiece.model",),
    ),
    "labse": WeightSpec("sentence-transformers/LaBSE", required=("modules.json", "config.json")),
}
MANIFEST = "weights.json"


def _hub() -> tuple[Resolve, Download]:
    from huggingface_hub import HfApi, snapshot_download

    api = HfApi()
    return (lambda repo, revision: api.model_info(repo, revision=revision).sha), snapshot_download


def _read_manifest(out: Path) -> dict[str, Any]:
    path = out / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def fetch(
    out: Path,
    names: Iterable[str],
    *,
    revisions: dict[str, str] | None = None,
    hub: tuple[Resolve, Download] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    resolve, download = hub or _hub()
    manifest = _read_manifest(out)
    for name in names:
        spec = MODELS[name]
        sha = resolve(spec.repo, (revisions or {}).get(name))
        target = out / name
        log(f"fetching {spec.repo} at {sha[:12]} into {target}")
        download(
            repo_id=spec.repo,
            revision=sha,
            local_dir=target,
            allow_patterns=list(spec.allow) if spec.allow else None,
            ignore_patterns=list(IGNORE),
        )
        shutil.rmtree(target / ".cache", ignore_errors=True)  # huggingface_hub's bookkeeping
        if spec.convert:
            convert_to_safetensors(target)
        files = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())
        manifest[name] = {"repo": spec.repo, "revision": sha, "files": files}
        (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _safetensors_name(shard: str) -> str:
    """pytorch_model-00001-of-00002.bin -> model-00001-of-00002.safetensors."""
    return shard.removeprefix("pytorch_").removesuffix(".bin") + ".safetensors"


def _deduplicate(state: dict[str, Any]) -> dict[str, Any]:
    """safetensors stores each tensor once. Names that alias one tensor (mT5's shared, encoder
    and decoder embeddings) keep the shortest name, which transformers re-ties on load;
    tensors that only overlap in memory are stored as copies."""
    aliases: dict[tuple[Any, ...], list[str]] = {}
    for name, t in state.items():
        key = (t.untyped_storage().data_ptr(), t.storage_offset(), tuple(t.shape), t.stride())
        aliases.setdefault(key, []).append(name)
    kept = {min(names, key=lambda n: (n.count("."), n)): state[names[0]] for names in aliases.values()}
    shared = Counter(t.untyped_storage().data_ptr() for t in kept.values())
    return {
        name: (t.clone() if shared[t.untyped_storage().data_ptr()] > 1 else t).contiguous()
        for name, t in kept.items()
    }


def convert_to_safetensors(model_dir: Path) -> None:
    """Rewrite pytorch_model.bin, or its shards and index, as safetensors; delete the pickles."""
    import torch
    from safetensors.torch import save_file

    index = model_dir / "pytorch_model.bin.index.json"
    if index.is_file():
        old_map: dict[str, str] = json.loads(index.read_text(encoding="utf-8"))["weight_map"]
        shards = sorted(set(old_map.values()))
    elif (model_dir / "pytorch_model.bin").is_file():
        shards = ["pytorch_model.bin"]
    else:
        return
    weight_map: dict[str, str] = {}
    total = 0
    for shard in shards:
        state = _deduplicate(torch.load(model_dir / shard, map_location="cpu", weights_only=True))
        save_file(state, model_dir / _safetensors_name(shard), metadata={"format": "pt"})
        weight_map |= dict.fromkeys(state, _safetensors_name(shard))
        total += sum(t.numel() * t.element_size() for t in state.values())
        (model_dir / shard).unlink()  # one shard at a time: the disk never holds two copies
    if index.is_file():
        new_index = {"metadata": {"total_size": total}, "weight_map": dict(sorted(weight_map.items()))}
        (model_dir / "model.safetensors.index.json").write_text(
            json.dumps(new_index, indent=2) + "\n", encoding="utf-8"
        )
        index.unlink()


def verify(out: Path, names: Iterable[str]) -> list[str]:
    problems: list[str] = []
    for name in names:
        spec, directory = MODELS[name], out / name
        if not directory.is_dir():
            problems.append(
                f"{name}: missing; fetch it: coglossa-scorer-weights fetch --out {out} --model {name}"
            )
            continue
        problems += [
            f"{name}: {f} is missing; fetch it again"
            for f in spec.required
            if not (directory / f).is_file()
        ]
        if spec.convert:
            if any(directory.glob("*.bin")):
                problems.append(f"{name}: has unconverted .bin files; fetch it again")
            if not any(directory.glob("*.safetensors")):
                problems.append(f"{name}: has no *.safetensors weights; fetch it again")
    if not (out / MANIFEST).is_file():
        problems.append(f"{MANIFEST} is missing; fetch the weights with coglossa-scorer-weights fetch")
    return problems


def _revision(text: str) -> tuple[str, str]:
    name, sep, sha = text.partition("=")
    if not sep or name not in MODELS or not sha:
        raise argparse.ArgumentTypeError(f"expected NAME=REVISION with NAME one of {', '.join(MODELS)}")
    return name, sha


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coglossa-scorer-weights", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_p = sub.add_parser("fetch", help="download (needs internet access) and convert")
    fetch_p.add_argument("--out", type=Path, required=True)
    verify_p = sub.add_parser("verify", help="check a weights directory")
    verify_p.add_argument("out", type=Path)
    for p in (fetch_p, verify_p):
        p.add_argument("--model", action="append", choices=list(MODELS), help="default: all")
    fetch_p.add_argument("--revision", action="append", type=_revision, default=[], help="NAME=SHA")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    names = args.model or list(MODELS)
    if args.command == "verify":
        problems = verify(args.out, names)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1 if problems else 0
    args.out.mkdir(parents=True, exist_ok=True)
    for name in names:
        try:
            fetch(args.out, [name], revisions=dict(args.revision))
        except OSError as e:  # network and Hugging Face errors (HfHubHTTPError is an OSError)
            print(f"error: cannot fetch {MODELS[name].repo}: {e}", file=sys.stderr)
            return 1
    return 0
```

Run `ruff format` and fix any line over 100 characters that it cannot wrap (the `_METRICX_FILES` spec lines, `_revision`'s message).

`scorer/pyproject.toml`:
- add `"huggingface_hub>=0.24"` and `"safetensors>=0.4"` to `dependencies`
- add `coglossa-scorer-weights = "coglossa_scorer.weights:main"` under `[project.scripts]`.

Then run `cd scorer && uv lock` and `uv sync --all-groups`. Both packages are already locked as transitive dependencies; check that `git diff scorer/uv.lock` changes only the scorer package's dependency list.

- [ ] **Step 4: Run the scorer suite**

Run: `make test-scorer`
Expected: PASS, coverage ≥ 95%.

- [ ] **Step 5: Makefile**

```make
WEIGHTS_DIR ?= $(HOME)/coglossa-weights

weights:
	cd scorer && uv run coglossa-scorer-weights fetch --out $(WEIGHTS_DIR)
	cd scorer && uv run coglossa-scorer-weights verify $(WEIGHTS_DIR)
```

- [ ] **Step 6: Fetch real weights once (needs internet access, about 7 GB)**

Run:

```bash
cd scorer && uv run coglossa-scorer-weights fetch --out /tmp/coglossa-weights \
  --model metricx-24-hybrid-large-v2p6 --model mt5-xl --model labse
uv run coglossa-scorer-weights verify /tmp/coglossa-weights \
  --model metricx-24-hybrid-large-v2p6 --model mt5-xl --model labse
COGLOSSA_REAL_MODELS=1 COGLOSSA_METRICX_DIR=/tmp/coglossa-weights/metricx-24-hybrid-large-v2p6 \
  COGLOSSA_MT5_TOKENIZER_DIR=/tmp/coglossa-weights/mt5-xl COGLOSSA_LABSE_DIR=/tmp/coglossa-weights/labse \
  uv run pytest tests/test_real_models.py --no-cov -v
```

Expected:
- `verify` exits 0.
- The real-model tests pass on the converted large checkpoint. If one asserts an XL-specific number, note that in the report.
- `ls /tmp/coglossa-weights/mt5-xl` shows tokenizer files only, with no model weights.

Report each directory's size (`du -sh`). If the allow-list misses a file that a model needs at load time, fix `MODELS` and add the file to the test's `FakeHub`. Without network access, report DONE_WITH_CONCERNS: this step is the only check of the real repositories' file names before `make bundle`. Delete `/tmp/coglossa-weights` afterwards.

- [ ] **Step 7: Commit**

```bash
git add scorer Makefile
git commit -m "feat: coglossa-scorer-weights fetches the scorer weights and converts MetricX to safetensors"
```

---

### Task 7: Compose files and deployment configs

**Files:**
- Create: `deploy/compose.yaml`, `deploy/compose.gpu.yaml`, `deploy/compose.demo.yaml`
- Create: `deploy/config/coglossa.example.yaml`, `deploy/config/coglossa.demo.yaml`
- Create: `deploy/tests/check_compose.py`
- Modify: `deploy/compose.dev.yaml` (its first comment: the deployable compose file is `deploy/compose.yaml`)
- Modify: `Makefile` (`demo`, `demo-down`, `deploy-check`), `.github/workflows/ci.yml` (job `deploy`)
- Test: `engine/tests/test_deploy_config.py`

**Interfaces:**
- Consumes:
  - the images of Tasks 4–5
  - `COGLOSSA_CONFIG` (Task 3)
  - the weights layout of Task 6.
- Produces:
  - **Services:** `postgres`, `migrate`, `scorer`, `worker` and `api`, plus `fake-openai` in the demo.
  - **Volumes:** `pgdata` and `files`.
  - **Host environment variables:**
    - `COGLOSSA_DB_PASSWORD` (required)
    - `COGLOSSA_HTTP_BIND` (default `0.0.0.0`)
    - `COGLOSSA_HTTP_PORT` (default `8080`)
    - `COGLOSSA_QE_MODEL` (default `metricx-24-hybrid-large-v2p6`; with the GPU file, `metricx-24-hybrid-xl-v2p6`)
    - `COGLOSSA_SCORER_DEVICE` (default `cpu`; with the GPU file, `cuda`).
  - **Make targets:** `make deploy-check` runs the shellcheck, stub and compose checks; Tasks 8 and 11 extend it.

- [ ] **Step 1: Write the failing checks**

`engine/tests/test_deploy_config.py`:

```python
"""The configs the bundle ships (deploy/config) load and validate (spec §3)."""

from pathlib import Path

import pytest

from coglossa.config import load_config

DEPLOY_CONFIG = Path(__file__).resolve().parents[2] / "deploy" / "config"


@pytest.mark.parametrize("name", ["coglossa.example.yaml", "coglossa.demo.yaml"])
def test_the_bundled_configs_load(name):
    cfg, issues = load_config(DEPLOY_CONFIG / name)
    assert [i for i in issues if i.level == "error"] == []
    assert cfg.scorer is not None and cfg.scorer.url == "http://scorer:8080"
    assert cfg.storage.path == Path("/data/files")
    assert cfg.api.web_dir is None  # the image passes --web-dir
    assert cfg.database.url_env == "COGLOSSA_DATABASE_URL"  # compose sets it


def test_the_demo_uses_the_fake_model_server():
    cfg, _ = load_config(DEPLOY_CONFIG / "coglossa.demo.yaml")
    assert {b.base_url for b in cfg.backends.values()} == {"http://fake-openai:8000/v1"}
    assert cfg.roles.judge is not None and cfg.roles.roundtrip is not None
```

`deploy/tests/check_compose.py`:

```python
"""Checks the compose files as Docker Compose resolves them (docker compose config). Needs
Docker with the Compose plugin; no images and no daemon state are needed.
Usage: python3 deploy/tests/check_compose.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
PASSWORD = "check-password"


def config(*files: str, env: dict[str, str] | None = None) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".env") as empty:  # ignore a local deploy/.env
        cmd = ["docker", "compose", "--env-file", empty.name]
        for f in files:
            cmd += ["-f", str(DEPLOY / f)]
        full_env = {**os.environ, "COGLOSSA_DB_PASSWORD": PASSWORD, **(env or {})}
        for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME"):
            full_env.pop(name, None)
        out = subprocess.run(
            [*cmd, "config", "--format", "json"], env=full_env, capture_output=True, text=True
        )
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def check(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    base = config("compose.yaml")
    s = base["services"]
    check(base["name"] == "coglossa", "the project is named coglossa")
    check(set(s) == {"postgres", "migrate", "scorer", "worker", "api"}, f"services: {sorted(s)}")
    check(set(base["volumes"]) == {"pgdata", "files"}, "named volumes pgdata and files")
    for name, svc in s.items():
        check(":" in svc["image"] and not svc["image"].endswith(":latest"), f"{name}: pinned image")
        check(svc["logging"]["driver"] == "json-file", f"{name}: json-file logs")
        check(svc["logging"]["options"] == {"max-size": "50m", "max-file": "5"}, f"{name}: rotation")
        want = "no" if name == "migrate" else "unless-stopped"
        check(svc.get("restart") == want, f"{name}: restart {want}")
    check(s["api"]["image"] == "coglossa/app:dev" and s["scorer"]["image"] == "coglossa/scorer:dev",
          "images default to the dev tag")
    for name in ("migrate", "worker", "api"):
        svc = s[name]
        url = svc["environment"]["COGLOSSA_DATABASE_URL"]
        check(url == f"postgresql://coglossa:{PASSWORD}@postgres:5432/coglossa", f"{name}: database URL")
        mounts = {(v["target"], v.get("read_only", False)) for v in svc["volumes"]}
        check(("/etc/coglossa", True) in mounts and ("/data/files", False) in mounts, f"{name}: mounts")
    # Review Focus 4: a model server on the Docker host is host.docker.internal
    for name in ("worker", "api"):
        hosts = json.dumps(s[name].get("extra_hosts"))
        check("host.docker.internal" in hosts and "host-gateway" in hosts, f"{name}: host.docker.internal")
    # Review Focus 5: no run starts before the scorer has loaded its weights
    check(s["worker"]["depends_on"]["scorer"]["condition"] == "service_healthy", "worker waits for the scorer")
    check(s["worker"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully",
          "worker waits for the migrations")
    check(s["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully",
          "api waits for the migrations")
    check(s["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy", "migrate waits for postgres")
    check(s["migrate"]["command"] == ["coglossa", "db", "upgrade"], "migrate runs coglossa db upgrade")
    check(s["worker"]["command"] == ["coglossa", "worker"], "worker runs coglossa worker")
    for name in ("postgres", "scorer", "api"):
        check("test" in s[name].get("healthcheck", {}), f"{name}: health check")
    check(s["scorer"]["healthcheck"].get("start_period") in ("10m0s", "600s", "10m"),
          "the scorer's health check allows ten minutes to load the weights")
    check(s["scorer"]["volumes"][0]["target"] == "/models" and s["scorer"]["volumes"][0]["read_only"],
          "the scorer mounts models/ read-only at /models")
    check("/models/metricx-24-hybrid-large-v2p6" in s["scorer"]["command"]
          and s["scorer"]["command"][s["scorer"]["command"].index("--device") + 1] == "cpu",
          "the scorer defaults to MetricX large on CPU")
    port = s["api"]["ports"][0]
    check((port["host_ip"], port["published"], port["target"]) == ("0.0.0.0", "8080", 8080),
          f"api port: {port}")
    bound = config("compose.yaml", env={"COGLOSSA_HTTP_BIND": "127.0.0.1", "COGLOSSA_HTTP_PORT": "9090"})
    port = bound["services"]["api"]["ports"][0]
    check((port["host_ip"], port["published"]) == ("127.0.0.1", "9090"), "COGLOSSA_HTTP_BIND/PORT")
    check(s["worker"].get("stop_grace_period") in ("2m0s", "120s", "2m"), "the worker gets 2 minutes to stop")

    gpu = config("compose.yaml", "compose.gpu.yaml")["services"]["scorer"]
    devices = gpu["deploy"]["resources"]["reservations"]["devices"]
    check(devices[0]["driver"] == "nvidia" and "gpu" in devices[0]["capabilities"], "gpu: an NVIDIA device")
    check("/models/metricx-24-hybrid-xl-v2p6" in gpu["command"]
          and gpu["command"][gpu["command"].index("--device") + 1] == "cuda", "gpu: MetricX XL on cuda")

    demo = config("compose.yaml", "compose.demo.yaml")["services"]
    check("fake-openai" in demo, "demo: a fake model server")
    check(demo["scorer"]["image"] == "coglossa/app:dev"
          and demo["scorer"]["command"][:2] == ["coglossa", "fake-scorer"], "demo: the fake scorer")
    check(not demo["scorer"].get("volumes"), "demo: the fake scorer needs no weights")
    for name in ("migrate", "worker", "api"):
        check(demo[name]["environment"]["COGLOSSA_CONFIG"] == "/etc/coglossa/coglossa.demo.yaml",
              f"demo: {name} reads the demo config")

    missing = subprocess.run(
        ["docker", "compose", "--env-file", os.devnull, "-f", str(DEPLOY / "compose.yaml"), "config"],
        env={k: v for k, v in os.environ.items() if k != "COGLOSSA_DB_PASSWORD"},
        capture_output=True, text=True,
    )
    check(missing.returncode != 0 and "run ./install.sh first" in missing.stderr,
          "without COGLOSSA_DB_PASSWORD, compose says to run install.sh")
    print("ok: compose files")


if __name__ == "__main__":
    main()
```

Compose's JSON output normalises some fields differently across versions: durations (`10m0s`), `extra_hosts` (a list or a map) and the `published` port (a string). Where your Compose version's output differs from the checks above, adjust the comparison, never the requirement it checks. Run `ruff format deploy/tests/check_compose.py` (via `cd engine && uv run ruff format ../deploy/tests/check_compose.py`) so it reads like the rest of the Python.

Run: `cd engine && uv run pytest tests/test_deploy_config.py --no-cov -q` and `python3 deploy/tests/check_compose.py`
Expected: FAIL. The configs and compose files do not exist yet.

- [ ] **Step 2: Write the compose files**

`deploy/compose.yaml`:

```yaml
# Coglossa on one host (spec §10). Run it from the directory this file is in:
#   ./install.sh                       once: loads the images, creates config/coglossa.yaml and .env
#   docker compose up -d --wait        start (or apply a config change); migrations run first
#   docker compose exec api coglossa doctor
# The operator guide (operator-guide.md) explains every setting.
name: coglossa

x-logging: &logging
  driver: json-file
  options: { max-size: 50m, max-file: "5" }

x-app: &app
  image: coglossa/app:${COGLOSSA_VERSION:-dev}
  restart: unless-stopped
  env_file:
    - path: .env # backend API keys (api_key_env in the config) go here too
      required: false
  environment:
    COGLOSSA_DATABASE_URL: postgresql://coglossa:${COGLOSSA_DB_PASSWORD:?run ./install.sh first; it creates .env with the database password}@postgres:5432/coglossa
  volumes:
    - ./config:/etc/coglossa:ro
    - files:/data/files
  extra_hosts:
    - host.docker.internal:host-gateway # a model server on this host (e.g. Ollama)
  logging: *logging

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: coglossa
      POSTGRES_PASSWORD: ${COGLOSSA_DB_PASSWORD:?run ./install.sh first; it creates .env with the database password}
      POSTGRES_DB: coglossa
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U coglossa -d coglossa"]
      interval: 5s
      timeout: 5s
      retries: 30
    logging: *logging

  migrate: # creates or upgrades the database schema, then exits; api and worker wait for it
    <<: *app
    restart: "no"
    command: [coglossa, db, upgrade]
    depends_on:
      postgres: { condition: service_healthy }

  scorer:
    image: coglossa/scorer:${COGLOSSA_VERSION:-dev}
    restart: unless-stopped
    command:
      - coglossa-scorer
      - --qe-model
      - /models/${COGLOSSA_QE_MODEL:-metricx-24-hybrid-large-v2p6}
      - --qe-tokenizer
      - /models/mt5-xl
      - --embed-model
      - /models/labse
      - --device
      - ${COGLOSSA_SCORER_DEVICE:-cpu}
      - --host
      - 0.0.0.0
      - --port
      - "8080"
    volumes:
      - ./models:/models:ro
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5)"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 10m # loading MetricX takes minutes on CPU
    logging: *logging

  worker:
    <<: *app
    command: [coglossa, worker]
    stop_grace_period: 2m # lets the running chunk finish; an unfinished one is re-queued
    depends_on:
      migrate: { condition: service_completed_successfully }
      scorer: { condition: service_healthy } # no run is scored without the scorer

  api:
    <<: *app # the image's command: coglossa-api serving the web app on 8080
    ports:
      - ${COGLOSSA_HTTP_BIND:-0.0.0.0}:${COGLOSSA_HTTP_PORT:-8080}:8080
    depends_on:
      migrate: { condition: service_completed_successfully }
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=5)"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 30s

volumes:
  pgdata: {}
  files: {}
```

`deploy/compose.gpu.yaml`:

```yaml
# The scorer on an NVIDIA GPU, with MetricX-24 XL. Needs the NVIDIA driver and the NVIDIA
# Container Toolkit on the host (operator guide, "GPU"). `./install.sh --gpu` adds it to .env:
#   COMPOSE_FILE=compose.yaml:compose.gpu.yaml
services:
  scorer:
    command:
      - coglossa-scorer
      - --qe-model
      - /models/${COGLOSSA_QE_MODEL:-metricx-24-hybrid-xl-v2p6}
      - --qe-tokenizer
      - /models/mt5-xl
      - --embed-model
      - /models/labse
      - --device
      - ${COGLOSSA_SCORER_DEVICE:-cuda}
      - --host
      - 0.0.0.0
      - --port
      - "8080"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

`deploy/compose.demo.yaml`:

```yaml
# The demo: Coglossa with synthetic models and scores, to try the stack before pointing it at
# model servers (operator guide, "Try it first"). Translations are placeholders.
#   docker compose -f compose.yaml -f compose.demo.yaml up -d --wait
x-demo-config: &demo-config
  COGLOSSA_CONFIG: /etc/coglossa/coglossa.demo.yaml

services:
  fake-openai:
    image: coglossa/app:${COGLOSSA_VERSION:-dev}
    restart: unless-stopped
    command: [coglossa, fake-openai, --host, 0.0.0.0, --port, "8000", --model, fake-model]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/models', timeout=5)"]
      interval: 5s
      timeout: 5s
      retries: 12
    logging:
      driver: json-file
      options: { max-size: 50m, max-file: "5" }

  scorer:
    image: coglossa/app:${COGLOSSA_VERSION:-dev}
    command: [coglossa, fake-scorer, --host, 0.0.0.0, --port, "8080"]
    volumes: !reset []
    healthcheck:
      start_period: 0s

  migrate:
    environment: *demo-config
  worker:
    environment: *demo-config
    depends_on:
      fake-openai: { condition: service_healthy }
  api:
    environment: *demo-config
```

`deploy/config/coglossa.example.yaml`:

```yaml
# Coglossa configuration (spec §3). install.sh copies this file to coglossa.yaml in this
# directory; edit that copy. The containers read it at /etc/coglossa/coglossa.yaml. After a
# change:  docker compose up -d --wait   then check:  docker compose exec api coglossa doctor
# Any OpenAI-compatible server works: vLLM, Ollama, TGI, llama.cpp server, LM Studio.

backends:
  gpu-a:
    base_url: http://10.0.4.12:8000/v1   # a model server on your network
    # base_url: http://host.docker.internal:11434/v1   # a server on this host (e.g. Ollama)
    # api_key_env: GPU_A_KEY   # the name of a variable you set in .env, if the server needs a key
    max_concurrency: 4                   # never more simultaneous requests than this
    timeout_s: 120

models:
  main-model:                            # how roles refer to the model
    backend: gpu-a
    name: Qwen/Qwen3-72B-Instruct        # the id the server lists at /v1/models
    context_window: 32768
    structured_output: json_schema       # json_schema | json_object | none
  # second-model: { backend: gpu-a, name: google/gemma-3-27b-it, context_window: 131072 }

roles:
  literal_translator:    { model: main-model }
  contextual_translator: { model: main-model }   # a second model family catches more
  reviewer:              { model: main-model }
  profiler_guide:        { model: main-model }
  # judge:     { model: second-model }   # scores errors; must differ from the reviewer's model
  # roundtrip: { model: second-model }   # back-translates; must differ from every translator's
  # Without a judge there is no style score, and spans keep a lower reliability tier.

scorer:
  url: http://scorer:8080                # the scorer container: MetricX-24 and LaBSE from models/

storage:
  path: /data/files                      # the files volume; leave as is

# scoring:
#   thresholds: { meaning: { A: 70, B: 72, C: 75 }, style: 60 }
#   language_tiers_file: tiers.yaml      # this directory's copy; edit it to change the tiers
# worker: { concurrency: 8 }             # chunks translated at the same time
# api: { max_upload_mb: 200 }            # keep the reverse proxy's body limit at least this
# auth: { mode: trusted_header, header: X-Remote-User }   # behind an authenticating proxy
# profiles:
#   checkpoint: { short: false, standard: false, long-form: true }   # stop for guide review
#   sweep: { short: false, standard: true, long-form: true }         # enforce locked entries
# The database is set by the containers (COGLOSSA_DATABASE_URL); do not add a database section.
```

`deploy/config/coglossa.demo.yaml`:

```yaml
# The demo configuration (compose.demo.yaml): synthetic models and scores, so the stack can be
# tried before it is pointed at model servers. Translations are placeholders.
backends:
  demo: { base_url: http://fake-openai:8000/v1, max_concurrency: 4 }
models:
  m1: { backend: demo, name: fake-model, context_window: 32768 }
  m2: { backend: demo, name: fake-model, context_window: 32768 }
roles:
  literal_translator:    { model: m1 }
  contextual_translator: { model: m1 }
  reviewer:              { model: m1 }
  profiler_guide:        { model: m1 }
  judge:                 { model: m2 }
  roundtrip:             { model: m2 }
scorer: { url: http://scorer:8080 }
storage: { path: /data/files }
profiles: { sweep: { short: true } }     # demo documents are short; show the sweep anyway
```

Change the first line of `deploy/compose.dev.yaml` to `# Development and test database only; the deployable compose file is deploy/compose.yaml.`

- [ ] **Step 3: Run the checks**

Run: `cd engine && uv run pytest` (whole suite, so coverage is measured), then `python3 deploy/tests/check_compose.py`
Expected: PASS, and `ok: compose files`.

- [ ] **Step 4: Try the demo once**

Add to the `Makefile`:

```make
COMPOSE_DEMO = cd deploy && COGLOSSA_DB_PASSWORD=$${COGLOSSA_DB_PASSWORD:-demo} \
  COGLOSSA_HTTP_BIND=127.0.0.1 docker compose -p coglossa-demo -f compose.yaml -f compose.demo.yaml

demo: images
	$(COMPOSE_DEMO) up -d --wait
	@echo "Coglossa with demo models: http://127.0.0.1:8080"

demo-down:
	$(COMPOSE_DEMO) down -v

SHELLCHECK = docker run --rm -v "$(CURDIR):/mnt" -w /mnt koalaman/shellcheck:v0.10.0

deploy-check:
	$(SHELLCHECK) deploy/tests/*.sh
	python3 deploy/tests/check_compose.py
```

Run: `make demo`
Expected: every service is healthy and `curl -s http://127.0.0.1:8080/api/health` answers `{"status":"ok",...}`. Then:
- `cd deploy && COGLOSSA_DB_PASSWORD=demo docker compose -p coglossa-demo -f compose.yaml -f compose.demo.yaml exec api coglossa doctor` exits 0.
- Opening `http://127.0.0.1:8080` in a browser shows the projects page.

Then run `make demo-down` and `make deploy-check`. Expected: both pass.

- [ ] **Step 5: CI**

Add a job to `.github/workflows/ci.yml`:

```yaml
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make deploy-check
```

- [ ] **Step 6: Commit**

```bash
git add deploy engine/tests/test_deploy_config.py Makefile .github/workflows/ci.yml
git commit -m "feat: compose files for production, GPU and the demo; the bundled configs"
```

---

### Task 8: Operator scripts — install, backup, restore

**Files:**
- Create: `deploy/install.sh`, `deploy/backup.sh`, `deploy/restore.sh` (mode 755)
- Create: `deploy/tests/test-install.sh` (mode 755)
- Modify: `Makefile` (`deploy-check` lints `deploy/*.sh` and runs `test-install.sh`)

**Interfaces:**
- Consumes: the bundle layout of Task 10:
  - `SHA256SUMS`
  - `images/*.tar`
  - `config/coglossa.example.yaml`
  - `VERSION`
  - the compose files, all in the extracted directory.
- Produces:
  - `./install.sh [--gpu]`: exits 0 when installed, 1 on a problem, 2 on bad usage
  - `./backup.sh [DIR]`, which writes `DIR/coglossa-db-<UTC stamp>.dump` and `DIR/coglossa-files-<UTC stamp>.tar.gz`
  - `./restore.sh [--yes] DB_DUMP FILES_ARCHIVE`.
- Task 11's air-gap test runs all three for real.

- [ ] **Step 1: Write the failing install test (a stub docker; no daemon needed)**

`deploy/tests/test-install.sh`:

```bash
#!/usr/bin/env bash
# Tests install.sh against a minimal fake bundle and a stub `docker` that records its calls.
# No images and no Docker daemon are needed. Usage: deploy/tests/test-install.sh
set -euo pipefail
deploy=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

mkdir -p "$work/bin"
cat >"$work/bin/sha256" <<'EOF'
#!/usr/bin/env bash
if command -v sha256sum >/dev/null; then exec sha256sum "$@"; else exec shasum -a 256 "$@"; fi
EOF
cat >"$work/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$DOCKER_LOG"
if [ "$1 $2" = "compose version" ]; then echo "${STUB_COMPOSE_VERSION:-2.29.1}"; fi
EOF
chmod +x "$work/bin/docker" "$work/bin/sha256"
export PATH="$work/bin:$PATH"

new_bundle() {  # a fresh bundle directory, checksummed as bundle.sh does; prints its path
  local b
  b=$(mktemp -d "$work/bundle-XXXXXX")
  mkdir -p "$b/images" "$b/config"
  cp "$deploy/install.sh" "$b/"
  echo app >"$b/images/app.tar"
  echo scorer >"$b/images/scorer.tar"
  cp "$deploy/config/coglossa.example.yaml" "$b/config/"
  echo 0.0.0-test >"$b/VERSION"
  (cd "$b" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256 >SHA256SUMS)
  echo "$b"
}
```

`sha256` is a script in `$work/bin`, not a function, because `xargs` cannot call a shell function. Then the cases:

```bash
install() {  # runs install.sh in bundle $1 with args; output in $work/out, docker calls in $work/log
  local b=$1; shift
  : >"$work/log"
  DOCKER_LOG="$work/log" "$b/install.sh" "$@" >"$work/out" 2>&1
}

# a fresh install loads every image and creates the config and .env
b=$(new_bundle)
install "$b" || fail "fresh install: $(cat "$work/out")"
grep -qx "load -i images/app.tar" "$work/log" && grep -qx "load -i images/scorer.tar" "$work/log" \
  || fail "fresh install: images not loaded: $(cat "$work/log")"
cmp -s "$b/config/coglossa.yaml" "$b/config/coglossa.example.yaml" || fail "config not created"
grep -Eq '^COGLOSSA_DB_PASSWORD=[0-9a-f]{48}$' "$b/.env" || fail ".env has no 48-hex password"
[ "$(stat -c %a "$b/.env" 2>/dev/null || stat -f %Lp "$b/.env")" = 600 ] || fail ".env must be mode 600"
grep -q "config/coglossa.yaml" "$work/out" && grep -q "docker compose up -d --wait" "$work/out" \
  || fail "the next steps are not printed"
echo "ok - a fresh install"

# Review Focus 1: a second run keeps the config and .env
echo "# edited" >>"$b/config/coglossa.yaml"
cp "$b/config/coglossa.yaml" "$work/config.before"
cp "$b/.env" "$work/env.before"
install "$b" || fail "second run: $(cat "$work/out")"
cmp -s "$b/config/coglossa.yaml" "$work/config.before" || fail "second run changed the config"
cmp -s "$b/.env" "$work/env.before" || fail "second run changed .env"
echo "ok - a second run keeps the config and .env"

# Review Focus 2: a damaged or incomplete bundle is refused before anything is loaded
b=$(new_bundle)
echo x >>"$b/images/scorer.tar"
if install "$b"; then fail "a damaged bundle was installed"; fi
grep -q "^error: the bundle is damaged or incomplete (.*images/scorer.tar" "$work/out" \
  || fail "damaged: $(cat "$work/out")"
grep -q "^load" "$work/log" && fail "damaged: an image was loaded"
[ -e "$b/.env" ] && fail "damaged: .env was created"
echo "ok - a damaged bundle is refused"

b=$(new_bundle)
rm "$b/images/app.tar"
if install "$b"; then fail "an incomplete bundle was installed"; fi
grep -q "^error: the bundle is damaged or incomplete (.*images/app.tar" "$work/out" \
  || fail "incomplete: $(cat "$work/out")"
echo "ok - an incomplete bundle is refused"

b=$(new_bundle)
rm "$b/SHA256SUMS"
if install "$b"; then fail "a bundle without SHA256SUMS was installed"; fi
grep -q "^error: SHA256SUMS is missing" "$work/out" || fail "no SHA256SUMS: $(cat "$work/out")"
echo "ok - a bundle without checksums is refused"

b=$(new_bundle)
if STUB_COMPOSE_VERSION=2.20.3 install "$b"; then fail "an old Compose was accepted"; fi
grep -q "^error: Docker Compose 2.20.3 is too old; Coglossa needs 2.24.0 or newer" "$work/out" \
  || fail "old compose: $(cat "$work/out")"
STUB_COMPOSE_VERSION=v5.1.3 install "$b" || fail "Compose v5.1.3 was refused: $(cat "$work/out")"
echo "ok - the Compose version is checked"

b=$(new_bundle)
install "$b" --gpu || fail "--gpu: $(cat "$work/out")"
install "$b" --gpu || fail "--gpu twice: $(cat "$work/out")"
[ "$(grep -c '^COMPOSE_FILE=compose.yaml:compose.gpu.yaml$' "$b/.env")" = 1 ] || fail "--gpu: COMPOSE_FILE once"
echo "ok - --gpu adds the GPU compose file once"

b=$(new_bundle)
if install "$b" --bogus; then fail "an unknown option was accepted"; fi
grep -q "^error: unknown option --bogus" "$work/out" || fail "usage: $(cat "$work/out")"
echo "ok - unknown options are refused"

echo "install.sh: all tests passed"
```

Run: `deploy/tests/test-install.sh`
Expected: FAIL (`deploy/install.sh` does not exist; `cp` fails).

- [ ] **Step 2: Write install.sh**

`deploy/install.sh`:

```bash
#!/usr/bin/env bash
# Install or upgrade Coglossa from this bundle (operator guide, "Install"). It checks the
# bundle's checksums, loads its images into Docker, and creates config/coglossa.yaml and .env
# when they do not exist yet; it never overwrites them.
# Usage: ./install.sh [--gpu]    --gpu: run the scorer on an NVIDIA GPU (compose.gpu.yaml)
set -euo pipefail
cd "$(dirname "$0")"

MIN_COMPOSE=2.24.0
die() { echo "error: $*" >&2; exit 1; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

gpu=false
for arg in "$@"; do
  case $arg in
    --gpu) gpu=true ;;
    -h | --help) sed -n '2,6s/^# \{0,1\}//p' "$0"; exit 0 ;;
    *) echo "error: unknown option $arg (usage: ./install.sh [--gpu])" >&2; exit 2 ;;
  esac
done

# 1. The bundle is complete and undamaged.
[ -f SHA256SUMS ] || die "SHA256SUMS is missing: this is not a complete Coglossa bundle; copy it again"
echo "Checking the bundle's checksums ..."
if ! report=$(sha256 -c SHA256SUMS 2>&1); then
  bad=$(printf '%s\n' "$report" | grep 'FAILED' | sed 's/: FAILED.*//' | head -3 | paste -sd ' ' -)
  die "the bundle is damaged or incomplete (${bad:-checksums do not match}); copy it again and re-run ./install.sh"
fi

# 2. Docker and a recent enough Compose.
command -v docker >/dev/null || die "docker is not installed (operator guide, Requirements)"
compose=$(docker compose version --short 2>/dev/null) \
  || die "the Docker Compose plugin is missing; install Docker Compose $MIN_COMPOSE or newer"
compose=${compose#v}
if [ "$(printf '%s\n%s\n' "$MIN_COMPOSE" "$compose" | sort -V | head -n1)" != "$MIN_COMPOSE" ]; then
  die "Docker Compose $compose is too old; Coglossa needs $MIN_COMPOSE or newer"
fi

# 3. The images.
for archive in images/*.tar; do
  echo "Loading $archive ..."
  docker load -i "$archive" >/dev/null || die "docker could not load $archive; is the Docker daemon running?"
done

# 4. The config and the database password: created once, never overwritten.
if [ ! -f config/coglossa.yaml ]; then
  cp config/coglossa.example.yaml config/coglossa.yaml
fi
if [ ! -f .env ]; then
  password=$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')
  (
    umask 077
    printf '%s\n' \
      "# Created by install.sh. Keep this file: the database was created with this password." \
      "COGLOSSA_DB_PASSWORD=$password" >.env
  )
fi
if $gpu && ! grep -q '^COMPOSE_FILE=' .env; then
  echo 'COMPOSE_FILE=compose.yaml:compose.gpu.yaml' >>.env
fi

cat <<EOF

Coglossa $(cat VERSION) is installed. Next:
  1. Edit config/coglossa.yaml: your model servers (backends), models and roles.
  2. docker compose up -d --wait
  3. docker compose exec api coglossa doctor
Then open http://<this host>:8080. The operator guide (operator-guide.md) has the details.
EOF
```

When the test passes the `install "$b" --gpu` case, check what `docker load -i` is logged as: the stub records `load -i images/app.tar` (the glob is relative, since the script `cd`s into the bundle).

- [ ] **Step 3: Write backup.sh and restore.sh**

`deploy/backup.sh`:

```bash
#!/usr/bin/env bash
# Back up Coglossa's database and stored files (operator guide, "Backups"). Run it from the
# install directory while Coglossa is running.
# Usage: ./backup.sh [DIR]    default DIR: ./backups
set -euo pipefail
dir=${1:-backups}
mkdir -p "$dir"
dir=$(cd "$dir" && pwd)
cd "$(dirname "$0")"
die() { echo "error: $*" >&2; exit 1; }

stamp=$(date -u +%Y%m%dT%H%M%SZ)
db="$dir/coglossa-db-$stamp.dump"
files="coglossa-files-$stamp.tar.gz"
if ! docker compose exec -T postgres pg_dump -U coglossa -d coglossa --format=custom >"$db"; then
  rm -f "$db"
  die "the database backup failed; is Coglossa running (docker compose ps)?"
fi
docker compose run --rm --no-deps -T --user "$(id -u):$(id -g)" -v "$dir:/backup" \
  --entrypoint tar worker -C /data/files -czf "/backup/$files" . \
  || die "the files backup failed; see the message above"
echo "Backed up to $db and $dir/$files"
```

`deploy/restore.sh`:

```bash
#!/usr/bin/env bash
# Restore a backup made by backup.sh (operator guide, "Restore"). It REPLACES all of Coglossa's
# data with the backup's. Run it from the install directory.
# Usage: ./restore.sh [--yes] DB_DUMP FILES_ARCHIVE
set -euo pipefail
die() { echo "error: $*" >&2; exit 1; }
usage() { echo "usage: ./restore.sh [--yes] DB_DUMP FILES_ARCHIVE" >&2; exit 2; }

yes=false
paths=()
for arg in "$@"; do
  case $arg in
    --yes) yes=true ;;
    -*) usage ;;
    *) paths+=("$arg") ;;
  esac
done
[ ${#paths[@]} -eq 2 ] || usage
for p in "${paths[@]}"; do [ -f "$p" ] || die "$p is not a file"; done
db="$(cd "$(dirname "${paths[0]}")" && pwd)/$(basename "${paths[0]}")"
files_dir=$(cd "$(dirname "${paths[1]}")" && pwd)
files=$(basename "${paths[1]}")
cd "$(dirname "$0")"

if ! $yes; then
  read -r -p "This replaces all Coglossa data with the backup. Type 'restore' to go on: " answer
  [ "$answer" = restore ] || die "nothing was restored"
fi
docker compose stop api worker
docker compose up -d --wait postgres
docker compose exec -T postgres pg_restore -U coglossa -d coglossa --clean --if-exists \
  --no-owner --single-transaction <"$db" || die "the database restore failed; see the message above"
docker compose run --rm --no-deps -T -v "$files_dir:/backup:ro" --entrypoint sh worker \
  -c 'find /data/files -mindepth 1 -delete && tar -C /data/files -xzf "/backup/$1"' sh "$files" \
  || die "the files restore failed; see the message above"
docker compose up -d --wait
echo "Restored $db and $files_dir/$files"
```

- [ ] **Step 4: Lint and test**

Add to `deploy-check` in the `Makefile` (replace its shellcheck line, and add the test):

```make
deploy-check:
	$(SHELLCHECK) deploy/*.sh deploy/tests/*.sh
	deploy/tests/test-install.sh
	python3 deploy/tests/check_compose.py
```

Run: `make deploy-check`
Expected: shellcheck reports nothing, every `ok - …` line prints, then `install.sh: all tests passed` and `ok: compose files`. `backup.sh` and `restore.sh` run against real containers in Task 11.

- [ ] **Step 5: Commit**

```bash
git add deploy Makefile
git commit -m "feat: install, backup and restore scripts for operators"
```

---

### Task 9: The operator guide

**Files:**
- Create: `docs/operator-guide.md`

**Interfaces:**
- Consumes everything Tasks 3–8 produce, with exactly the names, paths, variables and commands given there.
- Task 10 copies the guide into the bundle as `operator-guide.md`.

- [ ] **Step 1: Write the guide**

`docs/operator-guide.md` is for the customer's operator: someone who runs Linux servers and Docker, and knows nothing about Coglossa's code. Use plain language and short sections; give every command in full. It must cover, in this order:

1. **What Coglossa is** (two sentences), and **the bundle's contents**. Show this tree, with one line on each entry:
   ```
   coglossa-<version>/
     install.sh  backup.sh  restore.sh
     compose.yaml  compose.gpu.yaml  compose.demo.yaml
     config/coglossa.example.yaml  config/coglossa.demo.yaml  config/tiers.yaml
     images/app.tar  images/scorer.tar  images/postgres.tar
     models/metricx-24-hybrid-xl-v2p6/  models/metricx-24-hybrid-large-v2p6/  models/mt5-xl/  models/labse/  models/weights.json
     sbom/app.cdx.json  sbom/scorer.cdx.json  sbom/postgres.cdx.json  sbom/web.cdx.json
     manifest.json  VERSION  SHA256SUMS  operator-guide.md
   ```
2. **Requirements:**
   - **The host:** Linux x86_64; Docker Engine 24 or newer with the Compose plugin 2.24 or newer (`docker compose version`).
   - **Software on the host:** bash, coreutils, tar.
   - **Disk:** twice the bundle's size during the install, plus room for the documents.
   - **Memory:** at least 16 GB with the scorer on CPU (MetricX large), and 32 GB to run MetricX XL on CPU.
   - **GPU (optional):** an NVIDIA GPU with at least 24 GB for MetricX XL; the NVIDIA driver at or above the floor Task 5 recorded (CUDA 12: driver 525 or newer); and the NVIDIA Container Toolkit.
   - **Network:** the host must reach the model servers. Coglossa never needs the internet.
3. **Install:**
   - Check the copy with `sha256sum -c coglossa-<version>-linux-amd64.tar.sha256`, then `tar -xf`.
   - Run `./install.sh` (`--gpu` for the GPU scorer). Say what it does and what it never overwrites.
   - Edit `config/coglossa.yaml`: walk through backends, models (the id from `curl http://<server>/v1/models`) and roles, and why a judge and roundtrip on a second model family matter.
   - Run `docker compose up -d --wait`, then `docker compose exec api coglossa doctor` (every check must pass; warnings explain themselves), then open `http://<host>:8080`.
   - Note that the first start of the scorer takes minutes (it loads MetricX), and that `docker compose ps` shows it as `starting` until then.
4. **Try it first (demo):**
   - `docker compose -f compose.yaml -f compose.demo.yaml up -d --wait`. It needs no model servers, and the translations are placeholders.
   - Stop it with the same `-f` flags and `down`, which keeps the data; `down -v` deletes it.
5. **Configuration:**
   - **A model server on the same host:** `http://host.docker.internal:<port>/v1`. Ollama must listen on more than 127.0.0.1 (`OLLAMA_HOST=0.0.0.0`).
   - **API keys:** add `GPU_A_KEY=...` to `.env` and `api_key_env: GPU_A_KEY` to the backend.
   - **The scorer:**
     - MetricX large on CPU by default.
     - `./install.sh --gpu`, or `COMPOSE_FILE=compose.yaml:compose.gpu.yaml` in `.env`, gives MetricX XL on the GPU.
     - `COGLOSSA_QE_MODEL` and `COGLOSSA_SCORER_DEVICE` in `.env` override either.
     - Without a scorer, signals stay empty and reliability drops, never invented.
   - **Language tiers:** `config/tiers.yaml` plus `scoring.language_tiers_file: tiers.yaml`.
   - **The port:** `COGLOSSA_HTTP_PORT` and `COGLOSSA_HTTP_BIND` in `.env`.
   - **Applying changes:** after any change, run `docker compose up -d --wait` and `doctor`. An invalid config stops `api`, `worker` and `migrate` with a one-line error in `docker compose logs api`.
6. **Users, access and the reverse proxy:**
   - `auth: none` (the default) lets anyone who reaches the port use Coglossa.
   - For named users, put an authenticating reverse proxy in front: TLS, the user name in `X-Remote-User`, and `auth: { mode: trusted_header }`. Then **bind the API to 127.0.0.1** (`COGLOSSA_HTTP_BIND=127.0.0.1`), or anyone could send the header themselves.
   - **Upload size:** set the proxy's body limit at or above `api.max_upload_mb` (200 MB by default). The API checks the size only after the body has arrived (`docs/m4a-followups.md`).
   - **Live progress:** progress is a server-sent event stream, so turn off response buffering for `/api/runs/*/events`.
   - **CSRF:** a cookie-based SSO proxy must set its session cookie `SameSite=Lax` or `Strict` (`docs/m4a-followups.md`).
   - Include a complete nginx `server` block with TLS, `client_max_body_size 200m`, `proxy_buffering off` on the events location, `proxy_read_timeout 1h` there, and `proxy_set_header X-Remote-User $remote_user` with a comment saying where the user comes from in their SSO module.
7. **Running it:**
   - `docker compose ps` and `docker compose logs -f api worker`. Logs are JSON lines, and Docker keeps 5 × 50 MB per service.
   - `doctor`, also shown on the web app's System page.
   - Stop with `docker compose down`, which keeps the data; start with `up -d --wait`.
   - The services restart by themselves after a reboot once Docker starts.
8. **Backups:**
   - `./backup.sh [DIR]`: what it saves (the database and the uploaded files) and the file names.
   - A cron example: daily at 02:00, keeping 14 days with `find DIR -mtime +14 -delete`.
   - Keep copies off the host, and test a restore now and then.
9. **Restore:** `./restore.sh DB_DUMP FILES_ARCHIVE` replaces everything. It asks for `restore`; `--yes` skips the question.
10. **Upgrades:**
    - Back up first: `./backup.sh`.
    - Extract the new bundle beside the old directory. Copy `config/coglossa.yaml`, `.env` and any edited `config/tiers.yaml` into it.
    - In the old directory, `docker compose down`. In the new one, `./install.sh`, then `docker compose up -d --wait` (the `migrate` service upgrades the database first), then `doctor`.
    - The data lives in Docker volumes that belong to the project name `coglossa`, not to the directory.
    - To go back: migrations only go forward, so restore the backup with the old bundle.
11. **Troubleshooting:** a table of symptom → what to run → the fix. It covers:
    - `api` keeps restarting: an invalid config (`docker compose logs api`)
    - `doctor` fails on a backend: unreachable, wrong model id, no structured output
    - the scorer stays `starting` or becomes unhealthy: missing `models/` or memory (`docker compose logs scorer`)
    - runs marked unscored
    - the GPU is not visible: the NVIDIA Container Toolkit, `docker run --rm --gpus all ubuntu nvidia-smi`
    - port 8080 is taken: `COGLOSSA_HTTP_PORT`
    - permission errors on `config/`: the files must be readable by uid 10001
    - `install.sh` reports a damaged bundle
    - compose says "run ./install.sh first": a missing `.env`.
12. **Uninstall:** `docker compose down -v`, which deletes all data (say so in bold), then `docker image rm` the three images.
13. **What is inside:**
    - The SBOMs in `sbom/` are CycloneDX, one per image plus the web app's packages.
    - `manifest.json` has the image ids and the weights' Hugging Face revisions.
    - The weights' licences: MetricX-24, mT5 and LaBSE are Apache-2.0.
    - Coglossa sends nothing outside the host except to the configured model servers.

- [ ] **Step 2: Check every command and name against the code**

For each command, path, variable and file name in the guide, find its source (`deploy/*.sh`, `deploy/compose*.yaml`, `deploy/config/*`, `Makefile`, Task 5's report for the CUDA index), and correct the guide wherever they differ. Run each command that works locally against `make demo` (Task 7), with `export COMPOSE_PROJECT_NAME=coglossa-demo COMPOSE_FILE=compose.yaml:compose.demo.yaml COGLOSSA_DB_PASSWORD=demo` and the current directory `deploy/`. Those commands are:
- `docker compose ps`
- `docker compose logs api`
- `docker compose exec api coglossa doctor`
- `./backup.sh /tmp/coglossa-backup`: two non-empty files; delete them afterwards.

Then `make demo-down`. `restore.sh` runs for real in Task 11.

- [ ] **Step 3: Commit**

```bash
git add docs/operator-guide.md
git commit -m "docs: the operator guide"
```

---

### Task 10: The bundle build

**Files:**
- Create: `deploy/bundle.sh` (mode 755)
- Create: `deploy/tests/check-bundle.sh` (mode 755)
- Modify: `Makefile` (`SCORER`, `bundle`)

**Interfaces:**
- Consumes:
  - the Dockerfiles and image checks (Tasks 4–5)
  - `coglossa-scorer-weights verify` (Task 6)
  - the compose files and configs (Task 7)
  - the scripts (Task 8)
  - the guide (Task 9).
- Produces:
  - `deploy/bundle.sh [--version V] [--out DIR] (--weights DIR | --no-weights) [--scorer cpu|cuda] [--allow-dirty] [--keep-stage]`, which writes `OUT/coglossa-V-linux-amd64.tar` and `OUT/coglossa-V-linux-amd64.tar.sha256`
  - `deploy/tests/check-bundle.sh TAR`, which exits 0 when the bundle is complete and consistent.
- Task 11 runs both.

- [ ] **Step 1: Write the bundle check (the failing test)**

`deploy/tests/check-bundle.sh`:

```bash
#!/usr/bin/env bash
# Checks a built bundle: its transfer checksum, its own SHA256SUMS, every file the operator
# guide promises, the versions in the compose files, the images' tags, the SBOMs, and that the
# compose files resolve. Usage: deploy/tests/check-bundle.sh coglossa-V-linux-amd64.tar
set -euo pipefail
tar_file=$(cd "$(dirname "${1:?usage: check-bundle.sh TAR}")" && pwd)/$(basename "$1")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "FAIL: $(basename "$tar_file"): $*" >&2; exit 1; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

(cd "$(dirname "$tar_file")" && sha256 -c "$(basename "$tar_file").sha256" >/dev/null) \
  || fail "the .sha256 file does not match"
tar -xf "$tar_file" -C "$work"
dirs=("$work"/coglossa-*)
[ ${#dirs[@]} -eq 1 ] && [ -d "${dirs[0]}" ] || fail "must extract to one coglossa-<version>/ directory"
b=${dirs[0]}
version=$(cat "$b/VERSION")
[ "$(basename "$b")" = "coglossa-$version" ] || fail "the directory and VERSION disagree"
[ "$(basename "$tar_file")" = "coglossa-$version-linux-amd64.tar" ] || fail "the file name and VERSION disagree"
(cd "$b" && sha256 -c SHA256SUMS >/dev/null) || fail "SHA256SUMS does not verify"
for f in install.sh backup.sh restore.sh; do [ -x "$b/$f" ] || fail "$f is missing or not executable"; done
for f in compose.yaml compose.gpu.yaml compose.demo.yaml config/coglossa.example.yaml \
  config/coglossa.demo.yaml config/tiers.yaml operator-guide.md manifest.json \
  images/app.tar images/scorer.tar images/postgres.tar \
  sbom/app.cdx.json sbom/scorer.cdx.json sbom/postgres.cdx.json sbom/web.cdx.json; do
  [ -s "$b/$f" ] || fail "$f is missing or empty"
done
if grep -q 'COGLOSSA_VERSION' "$b"/compose*.yaml; then fail "a compose file still names COGLOSSA_VERSION"; fi
grep -q "coglossa/app:$version" "$b/compose.yaml" || fail "compose.yaml does not name coglossa/app:$version"
[ -z "$(find "$b" -name '._*' -o -name '.DS_Store')" ] || fail "macOS metadata files are in the bundle"
cmp -s "$b/config/coglossa.example.yaml" "$(dirname "$0")/../config/coglossa.example.yaml" \
  || fail "config/coglossa.example.yaml differs from deploy/config/"
python3 - "$b" "$version" <<'EOF' || fail "manifest, image tags or SBOMs (see above)"
import json, sys, tarfile
from pathlib import Path
b, version = Path(sys.argv[1]), sys.argv[2]
manifest = json.loads((b / "manifest.json").read_text())
assert manifest["version"] == version, manifest["version"]
assert manifest["platform"] == "linux/amd64"
expected = {"app.tar": f"coglossa/app:{version}", "scorer.tar": f"coglossa/scorer:{version}",
            "postgres.tar": "postgres:16-alpine"}
for archive, tag in expected.items():
    with tarfile.open(b / "images" / archive) as t:
        tags = [tag for entry in json.load(t.extractfile("manifest.json")) for tag in entry.get("RepoTags") or []]
    assert any(t in (tag, f"docker.io/library/{tag}") for t in tags), (archive, tags)
    assert any(i["file"] == f"images/{archive}" for i in manifest["images"]), archive
for sbom in (b / "sbom").glob("*.cdx.json"):
    assert json.loads(sbom.read_text())["bomFormat"] == "CycloneDX", sbom
if manifest["weights"] is not None:
    for name in ("metricx-24-hybrid-xl-v2p6", "metricx-24-hybrid-large-v2p6", "mt5-xl", "labse"):
        assert (b / "models" / name).is_dir(), name
        assert name in manifest["weights"], name
else:
    assert not (b / "models").exists()
EOF
(cd "$b" && COGLOSSA_DB_PASSWORD=check docker compose --env-file /dev/null config -q) \
  || fail "compose.yaml does not resolve"
echo "ok: $(basename "$tar_file")"
```

Run: `deploy/tests/check-bundle.sh /tmp/nope.tar`. Expected: FAIL (no such file).

- [ ] **Step 2: Write bundle.sh**

`deploy/bundle.sh`:

```bash
#!/usr/bin/env bash
# Build the offline bundle (spec §10) on a machine with Docker and internet access.
#   deploy/bundle.sh --weights DIR        the full bundle (make weights fetches DIR once)
#   deploy/bundle.sh --no-weights         without scorer weights (tests; the demo still works)
# Options: --version V (default: the tag at HEAD, else <coglossa version>-g<sha>), --out DIR
# (default: dist), --scorer cuda|cpu (default: cuda, which also runs on CPU), --allow-dirty,
# --keep-stage (keep the unpacked directory next to the tarball).
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
die() { echo "error: $*" >&2; exit 1; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

PLATFORM=linux/amd64
POSTGRES_IMAGE=postgres:16-alpine
SYFT_IMAGE=anchore/syft:v1.18.1
CUDA_INDEX=${CUDA_INDEX:-cu128} # Task 5's index; keep it in step with the Makefile

version="" out="$repo/dist" weights="" no_weights=false scorer=cuda allow_dirty=false keep=false
while [ $# -gt 0 ]; do
  case $1 in
    --version) version=${2:?--version needs a value}; shift ;;
    --out) out=${2:?--out needs a directory}; shift ;;
    --weights) weights=${2:?--weights needs a directory}; shift ;;
    --no-weights) no_weights=true ;;
    --scorer) scorer=${2:?--scorer needs cpu or cuda}; shift ;;
    --allow-dirty) allow_dirty=true ;;
    --keep-stage) keep=true ;;
    *) die "unknown option $1 (see the comment at the top of deploy/bundle.sh)" ;;
  esac
  shift
done
case $scorer in cpu) torch_index=cpu ;; cuda) torch_index=$CUDA_INDEX ;; *) die "--scorer is cpu or cuda" ;; esac
if $no_weights; then
  [ -z "$weights" ] || die "pass --weights DIR or --no-weights, not both"
else
  [ -n "$weights" ] || die "pass --weights DIR (make weights fetches them) or --no-weights"
  weights=$(cd "$weights" && pwd)
  (cd "$repo/scorer" && uv run coglossa-scorer-weights verify "$weights") \
    || die "the weights in $weights are incomplete (see above)"
fi
if ! $allow_dirty && [ -n "$(git -C "$repo" status --porcelain)" ]; then
  die "the working tree has uncommitted changes; commit them or pass --allow-dirty"
fi
if [ -z "$version" ]; then
  if tag=$(git -C "$repo" describe --tags --exact-match 2>/dev/null); then
    version=${tag#v}
  else
    base=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$repo/engine/coglossa/__init__.py")
    version="$base-g$(git -C "$repo" rev-parse --short HEAD)"
  fi
fi

name="coglossa-$version"
mkdir -p "$out"
out=$(cd "$out" && pwd)
stage="$out/$name"
rm -rf "$stage"
mkdir -p "$stage/images" "$stage/config" "$stage/sbom"
app="coglossa/app:$version"
scorer_image="coglossa/scorer:$version"

echo "== images ($PLATFORM, scorer torch: $torch_index)"
docker build --platform "$PLATFORM" -f "$repo/deploy/docker/app.Dockerfile" \
  --build-arg VERSION="$version" -t "$app" "$repo"
docker build --platform "$PLATFORM" -f "$repo/deploy/docker/scorer.Dockerfile" \
  --build-arg VERSION="$version" --build-arg TORCH_INDEX="$torch_index" -t "$scorer_image" "$repo"
"$repo/deploy/tests/check-app-image.sh" "$app"
"$repo/deploy/tests/check-scorer-image.sh" "$scorer_image" "$torch_index"
docker pull --platform "$PLATFORM" "$POSTGRES_IMAGE"
docker save -o "$stage/images/app.tar" "$app"
docker save -o "$stage/images/scorer.tar" "$scorer_image"
docker save -o "$stage/images/postgres.tar" "$POSTGRES_IMAGE"

echo "== files"
for f in compose.yaml compose.gpu.yaml compose.demo.yaml; do
  sed "s/\${COGLOSSA_VERSION:-dev}/$version/g" "$repo/deploy/$f" >"$stage/$f"
done
cp "$repo/deploy/config/coglossa.example.yaml" "$repo/deploy/config/coglossa.demo.yaml" "$stage/config/"
cp "$repo/engine/coglossa/scoring/tiers.yaml" "$stage/config/tiers.yaml"
cp "$repo/deploy/install.sh" "$repo/deploy/backup.sh" "$repo/deploy/restore.sh" "$stage/"
chmod 755 "$stage"/*.sh
cp "$repo/docs/operator-guide.md" "$stage/operator-guide.md"
echo "$version" >"$stage/VERSION"
if ! $no_weights; then
  mkdir "$stage/models"
  for model in metricx-24-hybrid-xl-v2p6 metricx-24-hybrid-large-v2p6 mt5-xl labse; do
    cp -R "$weights/$model" "$stage/models/"
  done
  cp "$weights/weights.json" "$stage/models/"
fi

echo "== SBOMs (CycloneDX)"
for image in app scorer postgres; do
  docker run --rm --user "$(id -u):$(id -g)" -v "$stage/images:/images:ro" -v "$stage/sbom:/sbom" \
    "$SYFT_IMAGE" "docker-archive:/images/$image.tar" -q -o "cyclonedx-json=/sbom/$image.cdx.json"
done
docker run --rm --user "$(id -u):$(id -g)" -v "$repo/web:/src:ro" -v "$stage/sbom:/sbom" \
  "$SYFT_IMAGE" dir:/src --exclude './node_modules/**' -q -o "cyclonedx-json=/sbom/web.cdx.json"

echo "== manifest and checksums"
python3 - "$stage" "$version" "$(git -C "$repo" rev-parse HEAD)" "$PLATFORM" "$scorer" "$torch_index" \
  "$app" "$scorer_image" "$POSTGRES_IMAGE" <<'EOF'
import datetime, json, subprocess, sys
from pathlib import Path
stage, version, commit, platform, scorer, torch_index, *images = sys.argv[1:]
def inspect(image):
    out = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, check=True)
    info = json.loads(out.stdout)[0]
    return {"id": info["Id"], "repo_digests": info.get("RepoDigests") or []}
files = ["images/app.tar", "images/scorer.tar", "images/postgres.tar"]
weights = Path(stage, "models", "weights.json")
manifest = {
    "version": version, "commit": commit, "platform": platform,
    "built_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    "scorer": {"build": scorer, "torch_index": torch_index},
    "images": [{"file": f, "tag": i, **inspect(i)} for f, i in zip(files, images)],
    "weights": json.loads(weights.read_text()) if weights.is_file() else None,
}
Path(stage, "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
EOF
(cd "$stage" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 bash -c \
  'if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi' _ >SHA256SUMS)

echo "== tarball"
tarball="$name-linux-amd64.tar"
(cd "$out" && COPYFILE_DISABLE=1 tar -cf "$tarball" "$name" && sha256 "$tarball" >"$tarball.sha256")
$keep || rm -rf "$stage"
echo "Built $out/$tarball ($(du -h "$out/$tarball" | cut -f1))"
```

Use the CUDA index Task 5 settled on as the `CUDA_INDEX` default. Add to the `Makefile`:

```make
SCORER ?= cuda

bundle:
	CUDA_INDEX=$(CUDA_INDEX) deploy/bundle.sh --weights $(WEIGHTS_DIR) --scorer $(SCORER)
	deploy/tests/check-bundle.sh dist/$$(ls -t dist | grep -m1 'linux-amd64.tar$$')
```

- [ ] **Step 3: Build a bundle without weights and check it**

Run: `deploy/bundle.sh --no-weights --scorer cpu --allow-dirty --version m5-test --out /tmp/coglossa-dist && deploy/tests/check-bundle.sh /tmp/coglossa-dist/coglossa-m5-test-linux-amd64.tar`
Expected: `Built /tmp/coglossa-dist/coglossa-m5-test-linux-amd64.tar (…)`, then `ok: coglossa-m5-test-linux-amd64.tar`.

Then check that the checker catches damage:
- Extract the bundle, change one byte in `config/coglossa.demo.yaml`, re-tar it with a fresh `.sha256`, and expect `FAIL: … SHA256SUMS does not verify`.
- Run `bundle.sh` without `--weights` or `--no-weights`, and expect `error: pass --weights DIR …`.

Report the bundle's size. Delete `/tmp/coglossa-dist` and the `m5-test` images.

Add `deploy/bundle.sh` to the shellcheck run (it is already in `deploy/*.sh`) and run `make deploy-check`. Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add deploy Makefile
git commit -m "feat: make bundle builds the offline bundle with checksums, SBOMs and a manifest"
```

---

### Task 11: The air-gap install test, CI and the docs

**Files:**
- Create: `deploy/tests/airgap-test.sh` (mode 755), `deploy/tests/smoke.py`, `deploy/tests/compose.airgap.yaml`
- Modify: `Makefile` (`airgap-test`), `.github/workflows/ci.yml` (job `airgap`)
- Modify: `README.md`, `docs/m4b-followups.md`, `docs/m2-followups.md`
- Create: `docs/m5-followups.md`

**Interfaces:**
- Consumes everything above.
- Produces:
  - `make airgap-test`
  - the CI job `airgap`.

- [ ] **Step 1: Write the smoke test and the air-gap override**

`deploy/tests/compose.airgap.yaml`:

```yaml
# The air-gap install test (spec §9.1): the stack on a network with no route out, reading the
# operator's config/coglossa.yaml (the demo file only adds the synthetic servers).
networks:
  default:
    internal: true

x-operator-config: &operator-config
  COGLOSSA_CONFIG: /etc/coglossa/coglossa.yaml

services:
  migrate:
    environment: *operator-config
  worker:
    environment: *operator-config
  api:
    environment: *operator-config
    ports: !reset []
```

`deploy/tests/smoke.py`:

```python
"""The air-gap smoke test (spec §9.1). It runs inside the stack's network with the app image's
Python and uses only the standard library:
  python - BASE_URL run NAME     the web app answers; doctor passes; project NAME translates a
                                 document end to end; every export downloads
  python - BASE_URL check NAME   project NAME is there and its document translates again: the
                                 database and the stored files survived (upgrade, restore)"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

TEXT = "Il pleut sur la ville.\n\nMarie dort. Le chat dort aussi.\n"
EXPORTS = {"docx": b"PK", "bilingual-docx": b"PK", "html": b"<", "json": b"{"}


def fail(message: str) -> None:
    sys.exit(f"FAIL: {message}")


def call(method: str, url: str, body: bytes | None = None, headers: dict | None = None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def api(base: str, method: str, path: str, payload=None, expect: int = 200):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    status, data = call(method, base + path, body, headers)
    if status != expect:
        fail(f"{method} {path} answered {status}: {data[:300]!r}")
    return json.loads(data) if data else None


def upload(base: str, project_id: str, filename: str, text: str) -> str:
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode() + text.encode() + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    status, data = call("POST", f"{base}/api/projects/{project_id}/runs", body, headers)
    if status != 202:
        fail(f"upload answered {status}: {data[:300]!r}")
    return json.loads(data)["id"]


def wait_until_complete(base: str, run_id: str, timeout_s: float = 300) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = api(base, "GET", f"/api/runs/{run_id}")["run"]["state"]
        if state == "complete":
            return
        if state in ("failed", "cancelled", "complete_with_errors", "awaiting_guide_approval"):
            fail(f"run {run_id} ended {state}")
        time.sleep(2)
    fail(f"run {run_id} did not finish within {timeout_s:.0f} s")


def check_exports(base: str, run_id: str) -> None:
    for kind, start in EXPORTS.items():
        status, data = call("GET", f"{base}/api/runs/{run_id}/exports/{kind}")
        if status != 200 or not data.lstrip().startswith(start):
            fail(f"export {kind} answered {status}, starting {data[:20]!r}")


def run(base: str, name: str) -> None:
    status, page = call("GET", base + "/")
    if status != 200 or b'<div id="root">' not in page:
        fail(f"the web app is not served at / ({status})")
    doctor = api(base, "GET", "/api/system/doctor")
    if not doctor["ok"]:
        fail(f"doctor: {[c for c in doctor['checks'] if c['status'] == 'fail']}")
    project = api(base, "POST", "/api/projects", {"name": name, "target_lang": "en"}, expect=201)
    run_id = upload(base, project["id"], "pluie.txt", TEXT)
    wait_until_complete(base, run_id)
    check_exports(base, run_id)
    print(f"ok: project {name} translated a document; every export downloads")


def check(base: str, name: str) -> None:
    projects = api(base, "GET", "/api/projects")
    project = next((p for p in projects if p["name"] == name), None)
    if project is None:
        fail(f"project {name} is gone")
    documents = api(base, "GET", f"/api/projects/{project['id']}/documents")
    if not documents:
        fail(f"project {name} has no documents")
    again = api(base, "POST", f"/api/documents/{documents[0]['id']}/runs", {}, expect=202)
    wait_until_complete(base, again["id"])
    check_exports(base, again["id"])
    print(f"ok: project {name} is there and its document translates again")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[2] not in ("run", "check"):
        sys.exit("usage: smoke.py BASE_URL run|check NAME")
    {"run": run, "check": check}[sys.argv[2]](sys.argv[1].rstrip("/"), sys.argv[3])
```

Check these response shapes against `schema/openapi.json` and fix any that differ:
- `GET /api/projects` returns a list of objects with `name` and `id`.
- `GET /api/runs/{id}` has `run.state`.
- `DoctorReport` has `ok` and `checks[].status`.
- The upload answers `202` with `RunSummary.id`.
- `GET /api/projects/{id}/documents` returns a list with `id`.
- The JSON export answers a JSON object.

- [ ] **Step 2: Write the air-gap test**

`deploy/tests/airgap-test.sh`:

```bash
#!/usr/bin/env bash
# The air-gap install test (spec §9.1). Builds a bundle without scorer weights and installs it
# the way an operator does, on a Docker network with no route out. Then: doctor, a document
# translated end to end, a backup, an upgrade into a new directory (the data stays), and a
# restore after the data is deleted. Usage: make airgap-test (Docker; about 15 GB of disk).
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
tests="$repo/deploy/tests"
version="airgap-$(git -C "$repo" rev-parse --short HEAD)"
work=$(mktemp -d)
export COMPOSE_PROJECT_NAME=coglossa-airgaptest # never a real install's volumes
export COMPOSE_FILE="compose.yaml:compose.demo.yaml:$tests/compose.airgap.yaml"
site1="$work/site1/coglossa-$version"
site2="$work/site2/coglossa-$version"
tarball="$work/dist/coglossa-$version-linux-amd64.tar"
step() { printf '\n== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
smoke() { docker compose exec -T worker python - http://api:8080 "$@" <"$tests/smoke.py"; }
cleanup() {
  for site in "$site2" "$site1"; do
    if [ -f "$site/compose.yaml" ]; then (cd "$site" && docker compose down -v --remove-orphans) >/dev/null 2>&1 || true; fi
  done
  docker image rm "coglossa/app:$version" "coglossa/scorer:$version" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

step "build and check the bundle (no weights, CPU scorer)"
"$repo/deploy/bundle.sh" --version "$version" --out "$work/dist" --no-weights --scorer cpu --allow-dirty
"$tests/check-bundle.sh" "$tarball"

step "a damaged copy is refused"
mkdir -p "$work/damaged" && tar -xf "$tarball" -C "$work/damaged"
echo "# changed in transit" >>"$work/damaged/coglossa-$version/config/coglossa.example.yaml"
if "$work/damaged/coglossa-$version/install.sh" >"$work/damaged.log" 2>&1; then
  fail "install.sh accepted a damaged bundle"
fi
grep -q "damaged or incomplete" "$work/damaged.log" || fail "damaged bundle: $(cat "$work/damaged.log")"

step "forget the images: from here on they can only come from the bundle"
docker image rm "coglossa/app:$version" "coglossa/scorer:$version" >/dev/null

step "install (site 1) and edit the one config file"
mkdir -p "$work/site1" && tar -xf "$tarball" -C "$work/site1"
cd "$site1"
./install.sh
cp config/coglossa.demo.yaml config/coglossa.yaml # the operator's edit: here, the demo models
docker compose up -d --wait --wait-timeout 300

step "no route out"
if docker compose exec -T api python -c \
  "import socket; socket.create_connection(('1.1.1.1', 443), timeout=5)" >/dev/null 2>&1; then
  fail "a container reached the internet"
fi

step "doctor"
docker compose exec -T api coglossa doctor || fail "doctor reported a failure"

step "end to end"
smoke run airgap

step "backup"
./backup.sh "$work/backups"
db=$(ls "$work"/backups/coglossa-db-*.dump)
files=$(ls "$work"/backups/coglossa-files-*.tar.gz)
[ -s "$db" ] && [ -s "$files" ] || fail "the backup is empty"

step "upgrade into a new directory (site 2): the data stays"
docker compose down # the volumes stay
mkdir -p "$work/site2" && tar -xf "$tarball" -C "$work/site2"
cp config/coglossa.yaml "$site2/config/"
cp .env "$site2/"
cd "$site2"
./install.sh
docker compose up -d --wait --wait-timeout 300
smoke check airgap

step "restore after the data is gone"
docker compose down -v
docker compose up -d --wait --wait-timeout 300
if smoke check airgap >/dev/null 2>&1; then fail "the data survived docker compose down -v"; fi
./restore.sh --yes "$db" "$files"
smoke check airgap

printf '\nair-gap install test passed\n'
```

Add to the `Makefile`:

```make
airgap-test:
	deploy/tests/airgap-test.sh
```

- [ ] **Step 3: Run it**

Run: `make deploy-check && make airgap-test`
Expected: every step passes, ending with `air-gap install test passed`. Report the wall-clock time. If a step fails, fix the cause in the script, compose file or code that owns it, not in the test's expectations. One example: if `pg_restore --clean` fails on procrastinate's objects, fix `restore.sh` (e.g. drop and recreate the `public` schema before restoring) and say why in a comment.

- [ ] **Step 4: CI**

Add to `.github/workflows/ci.yml`:

```yaml
  airgap:
    runs-on: ubuntu-latest
    timeout-minutes: 60 # building two images with torch, then three full stack starts
    steps:
      - uses: actions/checkout@v4
      - name: free disk space for the images and the bundle
        run: |
          sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc /opt/hostedtoolcache/CodeQL
          df -h /
      - run: make airgap-test
```

`bundle.sh --no-weights` needs no `uv`; the job runs on the runner's Docker and Python 3.

- [ ] **Step 5: The docs**

- **`README.md`:**
  - Set the status line to `**M5** (engine + CLI + scoring + queued runs + REST API + web app + offline bundle)`.
  - Add a section `## Installing (offline bundle)` before `## Development`. In it:
    - one paragraph on the bundle
    - the four operator commands (`./install.sh`, edit `config/coglossa.yaml`, `docker compose up -d --wait`, `docker compose exec api coglossa doctor`)
    - a pointer to `docs/operator-guide.md`
    - the build commands for someone with internet access: `make weights` (once, about 25 GB), `make bundle` (`SCORER=cpu` for the CPU-only scorer), `make demo` / `make demo-down`, `make airgap-test` and `make deploy-check`.
  - In `## Development`'s command block, add `make images`, `make demo` and `make deploy-check` with one comment each.
- **`docs/m5-followups.md`:** write it in the shape of `docs/m4b-followups.md`, with these sections:
  - **Should fix soon (M6):** whatever the task reviews park.
  - **Later:**
    - only `linux/amd64`
    - a CI build of the CUDA scorer image (CI builds the CPU one)
    - an upgrade test from a previous bundle (the first bundle has no predecessor)
    - FLORES-200 in the bundle with `bench` (M6)
    - stored run config snapshots must stay readable by later versions (`extra="forbid"`), since a removed or renamed config field would make old runs' snapshots fail to load in the worker; config changes must stay backward compatible, or migrate the snapshots.
  - **Spec deviations in M5:**
    - one app image for api, worker and fake-openai
    - the weights in `models/`, not in the image
    - FLORES-200 deferred to M6
    - the API published on all interfaces by default.
  - **M5 acceptance:** spec §10 and §9.1 item by item, each with the file or test that meets it.
- **`docs/m4b-followups.md`:** move these items into a new section `## Resolved in M5`, one line each naming the fix:
  - "The API image must build the web app"
  - "Approval refused after another run's draft became the project guide"
  - "The guide-conflict 409 is matched by its text"
  - "The re-run-the-sweep offer ignores the profile".

  Leave the other "Should fix soon" items where they are.
- **`docs/m2-followups.md`:** mark "Ship MetricX as safetensors in the bundle (M5)" as resolved by `coglossa-scorer-weights` (Task 6).

- [ ] **Step 6: Full verification**

Run: `make lint && make test && make deploy-check && make e2e`
Expected: all pass; every coverage gate ≥ 95%; `git status` clean apart from the intended changes. (`make airgap-test` already passed in Step 3.)

- [ ] **Step 7: Commit**

```bash
git add deploy Makefile .github/workflows/ci.yml README.md docs
git commit -m "test: the air-gap install test in CI; README and follow-ups for M5"
```


---

### Task 12: Fonts for every script

The customer translates the main languages and, from time to time, low-resource ones. The app must show every script without the internet. Resolves the "Font coverage" item of `docs/m4b-followups.md`.

**Files:**
- Create: `web/src/fonts.ts` (every font import, moved out of `main.tsx`)
- Create: `web/src/fonts.test.ts`
- Modify: `web/src/main.tsx` (imports `./fonts`), `web/src/styles/app.css` (the `font-family` stack), `web/package.json`, `web/pnpm-lock.yaml`
- Modify: `web/e2e/system.spec.ts` (fonts load from the app's own origin)
- Modify: `docs/m4b-followups.md` (the font item moves to "Resolved in M5"), `README.md` (one line on the scripts covered)

**Interfaces:**
- Produces: `web/src/fonts.ts` exports `FONT_FAMILIES: readonly string[]`, the CSS family names in the order of the stack.

- [ ] **Step 1: Choose the packages**

The app keeps everything it has:
- `noto-sans` (Latin, Cyrillic, Greek, Devanagari, Vietnamese), plus Arabic, Hebrew, Bengali, Thai, Ethiopic, SC, JP and KR
- weights 400 and 700 where they are imported today.

Add the `@fontsource` Noto package for each of these scripts, at weight 400 only (browsers synthesise bold):
- **Chinese:** `noto-sans-tc` and `noto-sans-hk` (Traditional)
- **Urdu:** `noto-nastaliq-urdu`
- **South Asian scripts:** `noto-sans-gujarati`, `noto-sans-gurmukhi`, `noto-sans-kannada`, `noto-sans-malayalam`, `noto-sans-tamil`, `noto-sans-telugu`, `noto-sans-oriya` and `noto-sans-sinhala`
- **Tibetan:** `noto-serif-tibetan` (Noto has no sans Tibetan)
- **South-East Asian scripts:** `noto-sans-khmer`, `noto-sans-lao` and `noto-sans-myanmar`
- **Caucasus:** `noto-sans-armenian` and `noto-sans-georgian`
- **Other scripts of the Middle East and Central Asia:** `noto-sans-thaana`, `noto-sans-syriac` and `noto-sans-mongolian`
- **African scripts:** `noto-sans-tifinagh`, `noto-sans-nko`, `noto-sans-adlam`, `noto-sans-vai` and `noto-sans-bamum`
- **North American scripts:** `noto-sans-cherokee`, `noto-sans-canadian-aboriginal` and `noto-sans-osage`
- **Other South and South-East Asian scripts:** `noto-sans-ol-chiki`, `noto-sans-javanese`, `noto-sans-balinese`, `noto-sans-sundanese`, `noto-sans-buginese`, `noto-sans-chakma`, `noto-sans-meetei-mayek` and `noto-sans-hanifi-rohingya`
- **Scripts of China and the Mekong:** `noto-sans-yi`, `noto-sans-lisu`, `noto-sans-tai-viet`, `noto-sans-new-tai-lue`, `noto-sans-tai-tham` and `noto-sans-kayah-li`
- **Symbols:** `noto-sans-symbols` and `noto-sans-symbols-2`.

For each package:
1. Check with `pnpm view @fontsource/<id> versions --json` and `pnpm view @fontsource/<id> time --json`.
2. Pin the newest 5.x version published at least two weeks before today, exactly, as the existing font packages are pinned.
3. If a package does not exist, leave it out and list it in the report.

Add any further `@fontsource` Noto family (sans, or serif where no sans exists) that covers the script of a living language and is missing from this list; list those in the report too.

- [ ] **Step 2: Write the failing test**

`web/src/fonts.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { FONT_FAMILIES } from "./fonts";

const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")) as {
  dependencies: Record<string, string>;
};
const fontsSource = readFileSync(new URL("./fonts.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("./styles/app.css", import.meta.url), "utf8");
const fontPackages = Object.keys(pkg.dependencies).filter((d) => d.startsWith("@fontsource/"));

describe("bundled fonts (spec §8: the app works offline)", () => {
  it("covers the scripts the customer translates", () => {
    for (const family of ["Noto Sans", "Noto Sans SC", "Noto Sans TC", "Noto Sans Arabic",
      "Noto Nastaliq Urdu", "Noto Sans Armenian", "Noto Sans Georgian", "Noto Sans Tamil",
      "Noto Sans Khmer", "Noto Sans Myanmar", "Noto Serif Tibetan", "Noto Sans Ethiopic",
      "Noto Sans Tifinagh", "Noto Sans NKo", "Noto Sans Canadian Aboriginal"]) {
      expect(FONT_FAMILIES).toContain(family);
    }
  });

  it("imports every font package it depends on, and nothing else", () => {
    const imported = new Set(
      [...fontsSource.matchAll(/import "(@fontsource\/[^/]+)\//g)].map((m) => m[1]),
    );
    expect([...imported].sort()).toEqual([...fontPackages].sort());
  });

  it("names every family in the page's font stack, Noto Sans first", () => {
    const stack = /font-family:([^;]+);/.exec(css)?.[1] ?? "";
    const names = [...stack.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    expect(names).toEqual([...FONT_FAMILIES]);
    expect(names[0]).toBe("Noto Sans");
    expect(stack.trim().endsWith("sans-serif")).toBe(true);
    expect(FONT_FAMILIES.length).toBe(fontPackages.length);
  });
});
```

Adjust the expected family spellings to each package's `font-family` (read it from the package's `400.css`; e.g. N'Ko is `"Noto Sans NKo"`). Run `cd web && pnpm test src/fonts.test.ts`. It fails: `./fonts` does not exist.

- [ ] **Step 3: Implement**

`web/src/fonts.ts`:
- Start with the comment moved from `main.tsx`, rewritten: "Spec §8: the app works offline, so its fonts ship with it, one Noto family per script. A browser downloads a font file only when the page shows characters in its range (unicode-range), so the many families cost nothing until they are used."
- Then every `import "@fontsource/<id>/400.css";` (and the existing `700.css` ones).
- Then `export const FONT_FAMILIES = [ ... ] as const;`, in the stack's order: Noto Sans first, then the families of the existing scripts, then the new ones grouped by region as in Step 1. Each name exactly as the package's CSS declares it.

In `main.tsx`, replace the font imports and their comment with `import "./fonts";`.

In `app.css`, write the `font-family` of `:root` as the same names, each quoted, ending in `sans-serif`. Prettier formats the long list.

`pnpm install` (lockfile updated), `pnpm lint`, `pnpm test` → PASS, coverage ≥ 95% on all four measures. `fonts.ts` is now covered by its test; it is not excluded.

- [ ] **Step 4: The fonts load offline (journey)**

Add to `web/e2e/system.spec.ts`:

```ts
test("fonts for other scripts come from the app itself", async ({ page }) => {
  const fonts: string[] = [];
  page.on("request", (r) => {
    if (r.resourceType() === "font") fonts.push(r.url());
  });
  await page.goto("/");
  for (const [family, sample] of [
    ["Noto Sans Armenian", "Հայերեն"],
    ["Noto Sans Georgian", "ქართული"],
    ["Noto Sans Tamil", "தமிழ்"],
    ["Noto Sans Khmer", "ភាសាខ្មែរ"],
    ["Noto Sans TC", "繁體中文"],
  ]) {
    const loaded = await page.evaluate(
      async ([f, s]) => (await document.fonts.load(`16px "${f}"`, s)).length,
      [family, sample],
    );
    expect(loaded, family).toBeGreaterThan(0);
  }
  const base = new URL(page.url()).origin;
  expect(fonts.length).toBeGreaterThan(0);
  expect(fonts.filter((u) => !u.startsWith(base))).toEqual([]);
});
```

Every font request must start with the page's own origin. Run `make e2e`. Expected: all journeys pass, the new one included.

- [ ] **Step 5: Report the cost**

Report the size of `web/dist` before and after (`du -sh web/dist` after `pnpm build`), and the app image's size after `make image-app`. A browser downloads only the fonts a page needs, so the page weight is unchanged for Latin-only text.

- [ ] **Step 6: Docs and commit**

Move the "Font coverage" item of `docs/m4b-followups.md` to its "Resolved in M5" section: "every script Noto covers for a living language is bundled (Task 12); a script outside Noto falls back to the system's fonts". In `README.md`'s web app paragraph, replace "the fonts (Noto) are bundled" with "Noto fonts for every script Noto covers are bundled".

```bash
git add web docs README.md
git commit -m "feat: bundle Noto fonts for every script, so any language displays offline"
```
