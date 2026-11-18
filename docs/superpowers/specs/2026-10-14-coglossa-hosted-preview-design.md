# Coglossa hosted preview — Design Spec

- **Date:** 2026-10-14
- **Status:** Draft for review
- **Builds on:** `2026-10-09-coglossa-v1-design.md` (v1, M1–M6 merged)

## 1. Purpose

A public, password-protected instance of Coglossa that people can try in a browser. The front door is always on, and the backend services run only while they are needed. It also serves as the template for customer-facing demo instances.

### 1.1 Decisions already made (2026-10-14)

- **Front door:** Vercel (the existing Pro account). **Database and files:** Supabase (the existing Pro account). **Compute that scales to zero:** Modal, the one new account. Vercel and Supabase can run neither a GPU nor a job that lasts hours, and a book-length run does.
- **Models:** OpenRouter. Gemma 4 26B-A4B is the default translator, reviewer and profiler/guide model (CGL-12). Qwen3.6-35B-A3B judges and back-translates, because those roles need a second model family (v1 spec §6.4).
- **Access:** a public HTTPS URL. Each person signs in with their own name and password.
- **The air-gapped, container-only v1 deployment is unchanged.** Every change to the shared code is additive and off by default. The compose stack, the offline bundle and the air-gap CI test behave exactly as before.

### 1.2 Success criteria

1. A person given a login opens the URL and signs in. They create a project, upload a document, approve the guide and review the result with real scores and flags. Exports work.
2. When nobody is using the preview, only Vercel and Supabase are running. Both are already paid for; Modal bills nothing.
3. A run starts without anyone running a worker by hand. The scorer starts on its own and stops when idle.
4. The Modal URLs refuse requests that did not come through the signed-in front door.
5. A push to `main` deploys the preview. The v1 CI jobs (including air-gap), the coverage gates (≥ 95%) and `make test` are unchanged and green.
6. Spending is capped: an OpenRouter key with a credit limit, a budget on the Modal workspace, and a lower upload limit.

### 1.3 Out of scope

- Sign-up, password reset, roles and per-user data separation: every signed-in person sees every project. A customer-facing demo for a second customer is a second deployment (another Vercel project, Supabase project and Modal environment), not a tenant.
- Billing, usage metering and the SaaS layer (still v1 spec §1.3).
- Moving off Modal or Supabase. The design keeps both behind interfaces (HTTP scorer, S3 API, Postgres URL, a kick URL), but only these providers are built and tested.

## 2. Architecture

```
browser ──HTTPS──▶ Vercel (always on)
                    ├─ static web app (web/dist) and /login
                    └─ middleware: checks the session cookie, then rewrites /api/* to Modal,
                       adding X-Remote-User and X-Coglossa-Proxy-Secret
                                   │
                                   ▼
                   Modal (each part scales to zero)
                    ├─ api      ASGI (coglossa_api), CPU; sleeps ~10 min after the last request
                    ├─ kick     web endpoint: starts a burst worker
                    ├─ worker   `coglossa worker --burst` as a function; drains the queue, exits
                    ├─ scorer   coglossa-scorer on an L4 GPU; weights on a Modal Volume;
                    │           sleeps 5 min after the last request
                    └─ cron     every 5 min: start a burst worker (it exits at once if idle)
                                   │
              ┌────────────────────┼─────────────────────┐
              ▼                    ▼                     ▼
   Supabase Postgres      Supabase Storage (S3 API)   OpenRouter
   (session pooler,       files and exports           Gemma 4 / Qwen3.6
    IPv4, port 5432)
```

| Part | Runs | Cost when idle |
|---|---|---|
| Vercel project | always (Pro) | already paid |
| Supabase project | always (Pro; one Micro compute) | already paid, or about $10/month if the plan's compute credit is in use by another project |
| Modal api, worker, scorer, kick | on demand | $0. The Starter plan includes $30/month of credit. |
| Modal cron | about 2–5 s of CPU every 5 min | cents per month |
| OpenRouter | per token | $0 |

## 3. Request flows

**Page load.** Vercel serves the web app straight away, while the backend may still be asleep. Middleware sends anyone without a valid session cookie to `/login`.

**Sign-in.** `POST /login` is handled in Vercel middleware. It checks the name and password against `COGLOSSA_USERS` (§5), sets the session cookie and redirects to `/`.

**API calls.** For `/api/*`, the middleware checks the cookie. It then removes any client-supplied `X-Remote-User` and `X-Coglossa-Proxy-Secret` headers, sets both itself, and rewrites the request to `${COGLOSSA_API_ORIGIN}/api/*`. The first call after an idle period waits for the API container to start, a few seconds. The web app already shows loading states.

**Live progress (SSE).** The rewrite passes the stream through. The existing 15 s heartbeat (`HEARTBEAT_S`) keeps it well inside Vercel's 120 s proxy limit. The browser's `EventSource` reconnects after `retry: 3000`, so a stream cut by any proxy recovers.

**Uploads.** The upload goes through the same rewrite. Spike S1 (§9) found that Vercel documents a 4.5 MB request-body limit and enforces it (`413 FUNCTION_PAYLOAD_TOO_LARGE`) on any request that runs middleware; measured, 4 MiB passed and 4.4 MiB failed, so `api.max_upload_mb` is 4 for the preview. Direct-to-storage upload (the fallback in §9) is a follow-up.

**Starting work.** Every job the API queues is written to Postgres by procrastinate, as in v1. After the commit, the API sends `POST worker.kick_url` with a bearer token (§4.3). That endpoint spawns the burst worker function. Uploading, approving the guide, retrying chunks and the sweep all queue through one `ProcrastinateQueue.defer`, so they all kick.

**Worker.** It runs `run_worker(cfg, burst=True)`, which already exists. It drains the queue, including jobs queued by running jobs, requeues stalled jobs, and then exits. At most two worker containers run at once; procrastinate's locks keep jobs from running twice. The function timeout is 24 h.

**Scoring.** The worker calls the scorer over HTTP, as in v1, with a bearer token (§4.4). The first call after an idle period waits about a minute while the scorer loads MetricX and LaBSE onto the GPU. `worker.burst_scorer_wait_s` (the worker drains in burst mode) and `scorer.timeout_s` cover that wait.

**Cron.** Every 5 minutes the cron starts a burst worker. This catches a job queued in the gap after a worker decided to exit, and a kick that failed. An empty queue costs one database query.

## 4. Changes to the shared code (all additive, off by default)

### 4.1 S3 storage — `storage.kind: s3`

`StorageConfig` becomes a discriminated union on `kind`:

```yaml
storage:
  kind: s3
  endpoint_url: https://<ref>.storage.supabase.co/storage/v1/s3
  region: us-east-1           # the project's region, as Supabase's S3 settings show it
  bucket: coglossa-files
  prefix: ""                  # optional key prefix
  access_key_id_env: COGLOSSA_S3_ACCESS_KEY_ID
  secret_access_key_env: COGLOSSA_S3_SECRET_ACCESS_KEY
```

- `S3Storage` implements the existing `Storage` protocol:
  - `put` and `get` map to `put_object` and `get_object`
  - `local_file` downloads to a temporary file and deletes it on exit
  - `delete_prefix` lists the keys and deletes them in batches
  - `check_writable` writes and deletes a probe object, and reports failures as `StorageError` with an operator-safe message
- It uses boto3 with path-style addressing. Keys stay relative and `/`-separated, and the existing key validation applies unchanged.
- `doctor` reports the bucket and endpoint and runs `check_writable`.
- Tests run against `moto` (an in-process S3 fake), with no network access.
- `kind: local` and its default path behave exactly as today.

### 4.2 Proxy secret — `auth.proxy_secret_env`

```yaml
auth: { mode: trusted_header, header: X-Remote-User, proxy_secret_env: COGLOSSA_PROXY_SECRET }
```

- When this is set, every request except `/api/health` must carry `X-Coglossa-Proxy-Secret` equal to the variable's value; any other request gets 401.
- The comparison runs in constant time (`hmac.compare_digest`).
- The check is middleware on the whole app, so it also covers the web-app routes. On Modal it guards the API's public URL against anyone who skips the Vercel front door.
- `doctor` reports a missing or empty variable as an error.
- When the setting is unset, behaviour is unchanged.

### 4.3 Kicking a worker — `worker.kick_url`

```yaml
worker: { kick_url: https://<workspace>--coglossa-kick.modal.run, kick_token_env: COGLOSSA_KICK_TOKEN }
```

- After each successful `defer`, `ProcrastinateQueue` calls a `Kicker`.
- The default `Kicker` does nothing.
- `HttpKicker` sends `POST kick_url` with `Authorization: Bearer <token>` and a 5 s timeout.
  - It is fire-and-forget: a failure is logged as a warning and never fails the request, because the cron (§3) is the backstop.
  - Kicks closer together than 2 s are merged into one.
- `doctor` reports the configured URL. It does not call the URL, so `doctor` has no side effects.

### 4.4 Scorer token — `scorer.api_key_env` (client) and `COGLOSSA_SCORER_TOKEN` (service)

- When `scorer.api_key_env` names a variable, the HTTP scorer client sends `Authorization: Bearer <value>`.
- When the scorer service has `COGLOSSA_SCORER_TOKEN` in its environment, it refuses requests without that token with 401, except `/health`.
- Both are optional, and both are off in compose.

### 4.5 Nothing else in v1 changes

- The SSE heartbeat, upload limits and `trusted_header` already exist.
- The prompts, pipeline, scoring and thresholds are unchanged. The preview uses the calibrated defaults.

## 5. Front door — `deploy/hosted/vercel/`

**A Vercel project** whose build runs `npm ci && npm run build` in `web/` and publishes `web/dist`. It adds:
- `middleware.ts`, a Vercel Routing Middleware on the Node.js runtime, using `@vercel/functions`
- `login.html`: a static page in the web app's style, with name, password, an error message and no JavaScript dependencies
- `vercel.json`: an SPA fallback to `index.html`.

**Users.** `COGLOSSA_USERS` is a JSON object mapping names to `pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>`, using 210,000 iterations. The middleware verifies passwords with WebCrypto. `deploy/hosted/add-user.mjs` asks for a password, prints the entry, and runs no server.

**Session.** The cookie is `cg_session`, set `HttpOnly; Secure; SameSite=Lax; Path=/` and lasting 7 days. Its value is `base64url(name|expiry)` plus an HMAC-SHA256 under `COGLOSSA_SESSION_SECRET`.
- A cookie is valid only while its user is still in `COGLOSSA_USERS`, so removing a user signs them out.
- `POST /logout` clears the cookie. The web app gets a small "Sign out" link, shown only when `/api/system/info` reports a user.

**Brute force.** A failed sign-in waits 1 s before answering. There is no lockout at demo scale.

**Rewrite.** `/api/*` goes to `COGLOSSA_API_ORIGIN` with the two headers from §3, and query strings pass through unchanged.

**Tests.** Vitest covers the middleware's pure functions: password verification, cookie signing and checking, header stripping, and routing decisions. They use the web workspace's coverage gate of ≥ 95% lines and branches.

## 6. Backend — `deploy/hosted/modal/`

`app.py` defines Modal app `coglossa`:

| Function | Image | Resources | Scaling |
|---|---|---|---|
| `api` (`@modal.asgi_app`) | app image: engine plus api, built from the repo's lockfiles | 1 CPU, 2 GiB | `min_containers=0`, `scaledown_window=600` |
| `kick` (`@modal.fastapi_endpoint`, POST) | app image | 0.25 CPU | checks the bearer token, then `worker.spawn()` |
| `worker` | app image | 2 CPU, 4 GiB | `max_containers=2`, `timeout=86400` |
| `cron` (`modal.Period(minutes=5)`) | app image | 0.25 CPU | `worker.spawn()` |
| `scorer` (`@modal.asgi_app`) | scorer image, CUDA torch (`TORCH_INDEX=cu126`) | L4 GPU, 16 GiB | `min_containers=0`, `scaledown_window=300`, weights on the Modal Volume `coglossa-weights` |
| `migrate` | app image | 1 CPU | run by the deploy job: `coglossa db upgrade` |

- **Weights:** MetricX-24 hybrid **large** v2p6, the mt5-xl tokenizer and LaBSE, the same as the compose default the thresholds were calibrated with. `deploy/hosted/modal/upload_weights.py` uploads them once from a local `weights.json` directory. That directory comes from `coglossa-scorer fetch-weights` or the bundle.
- **Config:** a Modal Secret `coglossa-hosted` holds every environment variable in §7. The config file `deploy/hosted/coglossa.hosted.yaml` is baked into the app image, with no secrets in it.
- **The hosted config:**
  - one `openrouter` backend
  - Gemma 4 for literal and contextual translation, the reviewer and profiler_guide
  - Qwen3.6 for judge and roundtrip, with reasoning off
  - the M6 provider pins and `max_output_tokens`
  - `storage.kind: s3`, the proxy secret, the kick URL and the scorer token
  - `api.max_upload_mb: 4`
  - `worker.concurrency: 8`, `scorer.max_concurrency: 4`
  - `scorer.timeout_s: 600`, `worker.scorer_wait_s: 300`, `worker.burst_scorer_wait_s: 300`
- **Database connections.** The Supabase session pooler on port 5432 limits connections. The API and up to two workers use small pools, and the README asks to raise the Supabase pooler Pool Size to 40.

## 7. Secrets

| Variable | Lives in | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | Modal secret | A **new** key with a credit limit. The old key is being rotated (CGL-11). |
| `COGLOSSA_DATABASE_URL` | Modal secret | Supabase session-pooler URL, with `sslmode=require` |
| `COGLOSSA_S3_ACCESS_KEY_ID`, `COGLOSSA_S3_SECRET_ACCESS_KEY` | Modal secret | Supabase Storage S3 keys. They have full access, so they are server-side only. |
| `COGLOSSA_PROXY_SECRET` | Modal secret and Vercel env | 32 random bytes |
| `COGLOSSA_KICK_TOKEN`, `COGLOSSA_SCORER_TOKEN` | Modal secret | 32 random bytes each |
| `COGLOSSA_SESSION_SECRET`, `COGLOSSA_USERS`, `COGLOSSA_API_ORIGIN` | Vercel env | |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `VERCEL_TOKEN` | GitHub Actions secrets | used by the deploy job only |

No secret is committed. `deploy/hosted/README.md` lists each one and how to create it.

## 8. Operations

**Deploy.** A new GitHub Actions workflow, `hosted.yml`, runs on push to `main` after the v1 checks pass, and can also be started by hand.
- It runs `modal deploy deploy/hosted/modal/app.py`, then `modal run …::migrate`, then `vercel deploy --prod`.
- It skips cleanly, with a notice, when the secrets are absent, so forks and PRs stay green.

**Rollback.** Redeploy an earlier commit. Migrations only ever move forward, which is already true in v1.

**Backups.** Supabase Pro takes daily backups. The files bucket has no separate backup, which is acceptable for a preview.

**Logs.** Modal keeps function logs (one day on Starter) and Vercel keeps logs. `doctor` runs as a one-off Modal command (`modal run …::doctor`).

**Spending limits:**
- an OpenRouter key credit limit, $25 to start
- a Modal workspace budget in the Modal dashboard, $30 to start, which is the included credit
- `api.max_upload_mb: 4` (Vercel refuses request bodies over about 4.5 MB; direct-to-storage upload is a follow-up).

**A browser tab left open** on a running job keeps the API container awake while the stream is open; the stream closes when the run ends. That costs about $0.05/hour of CPU, which is acceptable.

## 9. Spikes, done first and before anything is built on them

- **S1, Vercel proxying.** From a throwaway Vercel project:
  - Does `rewrite()` in Routing Middleware add request headers on an external rewrite?
  - Does it stream SSE?
  - Does it pass a large multipart body to an outside URL? Found: 4 MiB passes, 4.4 MiB fails with 413, so the preview caps uploads at 4 MB.

  **Fallback** if body size fails: the web app uploads straight to `COGLOSSA_API_ORIGIN/api/...upload` with a short-lived HMAC upload token from a new middleware endpoint `/upload-token`. The API checks it with the proxy secret. **Fallback** if headers fail: a Vercel Function proxy in `api/[...path].ts` that streams the request and response.
- **S2, Supabase session pooler.** Do procrastinate migrations, defer and burst-worker fetching work through the pooler on port 5432? Burst mode needs no LISTEN. The always-on mode isn't used in the preview.
- **S3, Modal GPU scorer.** How long does a cold start take with the CUDA image and weights on a Volume, from the first request to the first `/score` response? Done when it is under 120 s. If it is slower, the scorer keeps its Volume mounted with Modal's memory snapshots enabled.

## 10. Testing

| Layer | Scope |
|---|---|
| Unit (engine, api, scorer) | `S3Storage` on moto; proxy-secret middleware; `HttpKicker` (success, failure, debounce) with a fake transport; scorer client bearer header; scorer token check; config validation for the new fields; `doctor` lines |
| Unit (web) | middleware helpers (§5); the Sign-out link |
| Smoke (deploy job) | after deploy: sign in with a CI user, `GET /api/system/info` through Vercel, then a direct `GET` on the Modal API URL without the secret, which must return 401 |
| End to end (manual, recorded in the PR) | upload a short document, approve the guide, a run completes with qe and xsim present, export DOCX |
| v1 | All existing jobs unchanged, including air-gap, and coverage stays ≥ 95% everywhere |

## 11. Risks

| Risk | Mitigation |
|---|---|
| First request after idle is slow: about 5 s for the API, about 60 s for the scorer | The web app shows loading states. Scoring happens inside a run that takes minutes anyway. Keeping the API warm (`min_containers=1`, about $15/month) is a later switch. |
| A queued job sits unnoticed | Kick on every defer, plus the 5-minute cron |
| Vercel proxy limits (S1) | Spike first; both fallbacks are designed (§9) |
| Supabase pooler limits | Small pools, at most 2 workers, S2 |
| Signed-in users all see each other's projects | Stated in §1.3. One deployment per customer for customer demos. |
| The Qwen judge hasn't been flag-calibrated | The thresholds are the M6 defaults, and the open item is on CGL-12 |
