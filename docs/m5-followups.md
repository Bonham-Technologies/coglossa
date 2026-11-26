# M5 follow-ups

## Should fix soon (M6)
Triaged on 2026-10-30 (CGL-13). Each item names its ticket; items fixed together share one.
- ~~**`restore.sh` checks only the dump's table of contents** (CGL-46). `pg_restore -l` passes a
  truncated dump; the single transaction rolls the database back, but the operator learns it late.~~
  Done in CGL-46: `restore.sh` also reads the whole dump (`pg_restore -f /dev/null`) before it
  stops anything, and refuses a truncated one in one line naming the file.
- ~~**`restore.sh`'s files-step message can be wrong** (CGL-46). When `docker compose run` fails
  before it starts, the catch-all message still says the old files were removed.~~ Done in
  CGL-46: the files step prints a marker before it touches anything; without it the message
  says the old files are untouched.
- ~~**The scorer's health check allows about 75 s after its 10-minute start period** (CGL-48)
  (5 retries × 15 s); a slower cold CPU load of MetricX fails `up --wait`.~~ Done in CGL-48: the
  start period is 15 minutes, and `COGLOSSA_SCORER_START_PERIOD` in `.env` raises it.
- **The bundle build and its checks:** fixed in CGL-49. `check-bundle.sh` resolves `compose.yaml`
  alone and with each overlay, checks that they name only shipped images, and reads each image's
  platform from the image; a failed `bundle.sh` leaves no stage or `SHA256SUMS.$$` (the weights'
  three copies at the peak are documented); the CUDA scorer is `coglossa/scorer:<version>-cuda`;
  `check_compose.py` asserts the demo worker's `depends_on`; the app Dockerfile's comment passes
  `--platform`. The shellcheck container's `-v "$(CURDIR):/mnt"` was already quoted, and works
  from a path with a space; `deploy/tests/test-bundle.sh` covers the build and the checks.
- ~~**The torch install in the scorer image** (CGL-50) uses `--no-hashes` constraints (versions
  pinned from `scorer/uv.lock`, hashes not checked) and two indexes.~~
  Done in CGL-50: `scorer/uv.lock` holds both torch builds, the `cpu` dependency group (a default
  group, so `uv sync` and `uv run` are unchanged) and the `cu126` extra, which conflict, each
  pinned to its explicit PyTorch index. The image installs with `uv sync --frozen
  --no-default-groups` and the build's group or extra, and the hosted scorer from a pylock.toml
  exported from the lock, so uv checks every wheel against the lock's sha256 and takes each
  package from the one index the lock names. `deploy/tests/check_scorer_lock.py` (in
  `make deploy-check`) holds the Dockerfile, the Modal app, `pyproject.toml` and the lock to that,
  and `make torch-wheel-check` now also checks the index still serves each locked wheel's hash.
  ~~The project itself is still built from source with hatchling, which uv fetches from PyPI
  without a hash into a throwaway build environment; it is not installed in the image.~~
  Done in CGL-130: that build environment comes from the lock's `build` group (hatchling and its
  dependencies), exported with its hashes and passed as `--build-constraints` with
  `--require-build-hashes`; `uv sync` and the pylock.toml install use `--no-build`, so nothing
  else is built from source. The Modal images install uv from a hashed requirements file, and
  the hosted API image's export keeps its hashes. `check_scorer_lock.py` reads every step of the
  Modal `scorer_image` and rejects other uv commands (`uv add`, `uv run`, `uv tool`, `uvx`) and a
  requirements file beside the project. Still open: CI builds only the CPU scorer image (the
  CUDA image is built by `make image-scorer-cuda` and the bundle); Dependabot's uv updater has
  not yet run against the `cpu` group and `cu126` extra; the uv binary
  (`ghcr.io/astral-sh/uv:0.13.0`) and the Python base images are pinned by tag, not digest; and
  the hosted API image still builds the engine and API projects with an unhashed hatchling.
- ~~**`coglossa-scorer-weights`** (CGL-51):
  - `main` catches only `OSError`, so a conversion error gives a traceback and a hub error can be
    several lines.
  - a re-fetch into an existing directory keeps stale shards and records them in `weights.json`.
  - `--revision` for a model not chosen with `--model` is ignored without a word.
  - `verify` does not check that the shards named in `model.safetensors.index.json` exist, nor
    LaBSE's `2_Dense/model.safetensors`.~~
  Done in CGL-51: every failure is one line and a non-zero exit; a fetch downloads into
  `.<name>-<revision>.partial` and then replaces `<name>`, so a re-fetch drops what the new
  revision lacks (a failed fetch leaves the partial directory, and a retry of that revision
  skips the files it holds); `--revision` for an unselected model is refused; `verify` checks
  the indexed shards and LaBSE's dense layer.
- **Smaller ones:** fixed in CGL-54. `rerun_sweep` on an unreadable config snapshot now says the
  settings can't be read (not "the sweep is off") and `sweep_enabled` guards the stored profile
  too; the api's `test_main` uses its own directory; the edit-from-a-finished-draft test checks
  the saved content.

- **Two doc slips from the final review:** fixed in CGL-13. The operator guide's "Permission
  errors on `config/`" row now says `docker compose logs migrate`, and the README's install
  comment names `config/tiers.yaml` among the files `install.sh` creates.

## Later
- **The worker's wait for the scorer is a fixed bound** (`worker.scorer_wait_s`, 900 s). On a host
  that loads MetricX XL on CPU more slowly than that after a reboot, the worker starts without
  the scorer and passages translated until it is up stay unscored; raise the setting there.
- **Only `linux/amd64`.** The bundle, its images and the CI job build for that platform alone.
- **A CI build of the CUDA scorer image.** CI builds the CPU one (the `airgap` job); the CUDA
  image (about 11 GB) is built only by `make bundle`.
- **An upgrade test from a previous bundle.** The air-gap test upgrades into a new directory from
  the same bundle; the first bundle has no predecessor to upgrade from.
- **Stored run config snapshots must stay readable by later versions.** The config models use
  `extra="forbid"`, so a removed or renamed config field would make old runs' snapshots fail to
  load in the worker. Config changes must stay backward compatible, or migrate the snapshots.
- **The real-model smoke test** (spec §9.1, nightly or on demand, Ollama with a small instruct
  model) is not written. The scorer's opt-in real-weights tests (`COGLOSSA_REAL_MODELS=1`) cover
  the scorer, not the prompts.

## Resolved in M6
- **`coglossa worker --burst` no longer waits for a stopped scorer** (CGL-52). A drain checks the
  scorer once and starts at once, logging that the scorer is not reachable; only the
  long-running worker waits up to `worker.scorer_wait_s`. A drain waits up to
  `worker.burst_scorer_wait_s` (default 0); the hosted preview sets 300 for its cold start.
- **CJK passages show their own glyph forms.** Source and translated text carry `lang` from the
  run's languages (`useRunLanguages`), and `app.css` puts Noto Sans JP, KR, TC or HK first for
  `:lang(ja)`, `:lang(ko)`, `:lang(zh-Hant|zh-TW|zh-MO)` and `:lang(zh-HK)`. A target written
  plain `zh` keeps the Simplified default.
- **Config errors are one line.** An invalid `coglossa.yaml` gives a one-line message from
  `coglossa db upgrade`, `doctor` and the API (Task 1).
- **FLORES-200 is in the bundle** (the M5 spec deviation "FLORES-200 is deferred to M6").
  `make flores` fetches it, and `make bundle` ships it in `bench/flores200/` with its
  `LICENSE.txt` and `ATTRIBUTION.txt` (CC BY-SA 4.0) when `FLORES_DIR` exists; the manifest says
  `"bench": {"flores200": true}`. The worker mounts it read-only at `/opt/coglossa/bench`, and
  `coglossa bench` runs offline in the worker (operator guide, "Benchmark your models"). The
  air-gap test ships a stand-in tree (`deploy/tests/flores-mini/`) and runs `bench run` and
  `bench flags` on it.
- **`install.sh` loads only the image archives `SHA256SUMS` lists** (CGL-47): an unlisted
  `images/*.tar` is named in a warning and skipped. `--gpu` over another `COMPOSE_FILE` in `.env`
  stops before anything changes and names the line to write, unless that value already has
  `compose.gpu.yaml`; a plain `COMPOSE_FILE=compose.yaml` is replaced.

## Resolved in the M5 final review
- **The config check sees the tiers file's content** (CGL-53). `config_hash` now covers the bytes
  of `scoring.language_tiers_file` as well as its path, so editing an enabled `tiers.yaml` between
  submit and execution is reported as a config change. A config with no tiers file keeps its old
  hash; a run submitted with one before this change gets a new hash, so it is reported once
  (operator guide, "Language tiers").
- **Applying a config change:** the guide, `compose.yaml`, the example config and `install.sh` say
  `docker compose up -d --wait --force-recreate migrate api worker` (a plain `up` keeps the
  running services on the old config); an invalid config is read with `docker compose logs
  migrate`. The air-gap test applies a change and checks that the API and the worker use it.
- **An edited `config/tiers.yaml` no longer fails the checksums:** the bundle ships
  `config/tiers.example.yaml`, and `install.sh` creates `config/tiers.yaml` once.
- **Rolling back over a newer schema:** `restore.sh` replaces the whole `public` schema in the
  restore's transaction; the air-gap test restores over a newer table and revision.
- **The bundle tarball is root-owned** (uid and gid 0, no names), checked by `check-bundle.sh`.
- **After a reboot the worker waits for the scorer** (`worker.scorer_wait_s`, default 900 s).
- **Disaster recovery** is in the operator guide: what to keep beside the backups, and how to
  restore on a new host. The guide already said that a running backup is not one moment.
- **`install.sh`** adds the missing last newline to `.env` before `COMPOSE_FILE`, and refuses
  `--gpu` on a bundle with the CPU scorer.
- **The operator guide:** the disk requirement counts the loaded images, the upgrade reminds to
  move the backup cron job, and the nginx example sets `Host` and `X-Forwarded-*` for the
  progress stream and shows `auth_request_set`.

## Spec deviations in M5
- **One app image for api, worker and fake-openai.** §10 lists them as separate images. One image
  (`coglossa/app`) runs the API with the web app, the worker, the CLI, `fake-openai` and
  `fake-scorer`; compose picks the command. One build, one archive and one SBOM instead of three.
- **The weights are in `models/`, not in the image.** The scorer image holds no weights; the bundle
  carries them in `models/`, mounted read-only at `/models`. The image stays about 2 GB (CPU), one
  image serves either MetricX size (`COGLOSSA_QE_MODEL`), and the weights are checked and listed
  on their own (`weights.json`).
- **FLORES-200 is deferred to M6**, with `bench`, which is its only user.
- **The API is published on all interfaces by default** (`COGLOSSA_HTTP_BIND=0.0.0.0`), so the
  reviewers on the network can reach it. The operator guide shows how to bind it to `127.0.0.1`
  behind a reverse proxy.

## M5 acceptance
Spec §10, item by item:
- **Images: `api` serving `web`, `worker`, `scorer`, `postgres`, `fake-openai`:**
  `deploy/docker/app.Dockerfile` (api with the web app at `/opt/coglossa/web`, worker, CLI,
  fake-openai; checked by `deploy/tests/check-app-image.sh`), `deploy/docker/scorer.Dockerfile`
  (CPU or CUDA torch; `deploy/tests/check-scorer-image.sh`), `postgres:16-alpine` pinned in
  `deploy/compose.yaml`.
- **`make bundle` produces a versioned tarball:** `deploy/bundle.sh` writes
  `coglossa-<version>-linux-amd64.tar` and its `.sha256`; `deploy/tests/check-bundle.sh` checks
  every item below.
  - **the `docker save` image archives:** `images/app.tar`, `images/scorer.tar`,
    `images/postgres.tar`, with every image's id and digests in `manifest.json`.
  - **the scorer weights (MetricX-24 XL and large, LaBSE):** `models/`, fetched once by
    `make weights` (`coglossa-scorer-weights`, MetricX as safetensors) with the mT5-XL tokenizer.
  - **`compose.yaml`:** with `compose.gpu.yaml` and `compose.demo.yaml`, the version written in.
  - **`coglossa.example.yaml` and `tiers.yaml`:** `config/` (the tiers as `tiers.example.yaml`,
    which `install.sh` copies to `tiers.yaml`), with `coglossa.demo.yaml`.
  - **the FLORES-200 data:** deferred to M6 (above); shipped in M6 (Resolved in M6).
  - **SHA-256 checksums:** `SHA256SUMS` inside, the `.sha256` beside it.
  - **an SBOM (CycloneDX):** `sbom/{app,scorer,postgres,web}.cdx.json` (syft).
  - **an operator guide:** `docs/operator-guide.md`, shipped as `operator-guide.md`.
- **Install steps (verify → `docker load` → config → `up -d` → `doctor`):** `deploy/install.sh`
  (checksums, Compose version, images, config and `.env` created once), tested by
  `deploy/tests/test-install.sh` and end to end by `deploy/tests/airgap-test.sh`.
- **Upgrades: load the new bundle; migrations at startup after a backup:** the `migrate` service
  runs `coglossa db upgrade` before api and worker start; `deploy/backup.sh` and
  `deploy/restore.sh` (tested by `deploy/tests/test-restore.sh`); the operator guide's
  "Upgrades"; the air-gap test backs up, upgrades into a new directory and restores.

Spec §9.1, item by item:
- **Unit, property, contract, integration, web and E2E layers; the 95% coverage gates:** unchanged
  from M1–M4b and still green (`make test`, `make e2e`, CI's engine, scorer, api, web and e2e
  jobs).
- **Real-model smoke test (outside the gate):** not written (Later, above).
- **Air-gap install test (CI):** `deploy/tests/airgap-test.sh` (`make airgap-test`) and CI's
  `airgap` job. It builds a bundle without weights, refuses a damaged copy, removes the images so
  they can only come from the bundle, installs it on a Compose network with `internal: true`
  (`deploy/tests/compose.airgap.yaml`), checks that nothing reaches the internet, runs `doctor`
  and `deploy/tests/smoke.py` (the web app, a document translated end to end, every export),
  applies a config change, then backs up, upgrades into a new directory (the data and the edited
  tiers stay) and restores over a newer schema after `down -v`.
