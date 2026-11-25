# Coglossa hosted preview

A hosted preview of Coglossa: Vercel is the front door, Modal runs the backend and scales to
zero, Supabase holds the data, and OpenRouter serves the language models. The design is in
`docs/superpowers/specs/2026-10-14-coglossa-hosted-preview-design.md`. The air-gapped install
is in `docs/operator-guide.md` and is unaffected by anything here.

## What runs where

| Part | Runs | Cost when idle |
|---|---|---|
| Vercel project (`deploy/hosted/vercel`) | always | already paid (Pro) |
| Supabase project (Postgres and Storage) | always | already paid, or about $10/month |
| Modal `api`, `worker`, `scorer`, `kick` | on demand | $0 (the Starter plan includes $30/month of credit) |
| Modal `cron` | 2-5 s of CPU every 5 min | cents per month |
| OpenRouter | per token | $0 |

The Modal app is `coglossa`. Its web endpoints are `https://<workspace>--coglossa-api.modal.run`,
`https://<workspace>--coglossa-kick.modal.run` and `https://<workspace>--coglossa-scorer.modal.run`.
All three require Modal proxy auth: a request without the workspace's proxy-auth token
(headers `Modal-Key` and `Modal-Secret`) is refused by Modal and starts no container. The front
door, the API (for the kick) and the worker (for the scorer) send the token; the bearer tokens
and the proxy secret are checked as well.

## Secrets

Make a 32-byte secret with `openssl rand -base64 32`.

| Variable | Lives in | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | Modal Secret | A new key with a credit limit |
| `COGLOSSA_DATABASE_URL` | Modal Secret | The Supabase session-pooler URL, with `?sslmode=require` |
| `COGLOSSA_S3_ACCESS_KEY_ID`, `COGLOSSA_S3_SECRET_ACCESS_KEY` | Modal Secret | Supabase Storage S3 keys (full access: server-side only) |
| `COGLOSSA_S3_ENDPOINT` | Modal Secret | `https://<project-ref>.storage.supabase.co/storage/v1/s3` |
| `COGLOSSA_S3_REGION` | Modal Secret | The Supabase project's region, e.g. `ca-central-1` |
| `COGLOSSA_PROXY_SECRET` | Modal Secret and Vercel env | 32 random bytes; the same value in both |
| `COGLOSSA_MODAL_KEY`, `COGLOSSA_MODAL_SECRET` | Modal Secret, Vercel env and the `hosted-preview` Environment | The workspace proxy-auth token (`wk-...` and `ws-...`, step 2); the same values in all three |
| `COGLOSSA_KICK_TOKEN` | Modal Secret | 32 random bytes |
| `COGLOSSA_SCORER_TOKEN` | Modal Secret and the scorer's Modal Secret | 32 random bytes; the same value in both |
| `COGLOSSA_KICK_URL`, `COGLOSSA_SCORER_URL` | Modal Secret | The deployed endpoint URLs (step 4) |
| `COGLOSSA_SESSION_SECRET`, `COGLOSSA_USERS`, `COGLOSSA_API_ORIGIN` | Vercel env | |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `COGLOSSA_SMOKE_PASSWORD` | `hosted-preview` Environment secrets | The deploy job only (step 7) |

"Modal Secret" is `coglossa-hosted`, which every function but the scorer reads. The scorer reads
only `coglossa-scorer`, which holds `COGLOSSA_SCORER_TOKEN` and nothing else. No secret is committed. `deploy/hosted/coglossa.hosted.yaml` names the variables; the account-specific URLs and the region are filled in from the Modal Secret when a container starts.

## One-time setup

Run the Modal commands from the repository root. Install the Modal client with
`uv tool install modal --with-requirements deploy/hosted/modal/requirements.txt`: that pins it to
the version the image uses and adds the packages `app.py` imports when it deploys (FastAPI,
PyYAML). After a Dependabot bump of that file, run it again with `--force`.

1. **Supabase.**
   1. Create the project.
   2. Storage: create a **private** bucket named `coglossa-files`.
   3. Storage > S3 Connection: generate an access key pair. Note the endpoint URL.
   4. Connect: copy the **session pooler** URL (port 5432, IPv4). Append `?sslmode=require`.
   5. Database > Connection pooling: raise the session pooler **Pool Size** to 40. The Micro
      compute allows 60 connections in all. One API container (`max_containers=1`) and up to
      two workers (`max_containers=2`) each keep a SQLAlchemy pool (default 5, up to 10 more
      under load) and a procrastinate pool, so a busy system can hold well over the pooler's
      default size. The config has no
      pool-size setting, so the limit is set here.
2. **Modal.**
   1. `modal token new`
   2. Upload the weights (about 3 GiB) from a directory made by `coglossa-scorer-weights fetch --out <dir>` (check it with `coglossa-scorer-weights verify <dir>`):
      `modal run deploy/hosted/modal/upload_weights.py --source ~/coglossa-weights`
      The script skips what is already in the Volume `coglossa-weights`; add `--force` to replace it.
   3. Create a proxy-auth token: in the Modal dashboard, Settings > Proxy Auth Tokens > New
      Token. It shows a token ID (`wk-...`) and a secret (`ws-...`) once. They are
      `COGLOSSA_MODAL_KEY` and `COGLOSSA_MODAL_SECRET`: the Modal Secret, the Vercel project
      (step 5) and GitHub (step 7) all need them.
   4. Create the two Secrets. Leave the two URLs as placeholders for now:
      ```
      scorer_token=$(openssl rand -base64 32)
      modal secret create coglossa-hosted \
        OPENROUTER_API_KEY=... COGLOSSA_DATABASE_URL=... \
        COGLOSSA_S3_ACCESS_KEY_ID=... COGLOSSA_S3_SECRET_ACCESS_KEY=... \
        COGLOSSA_S3_ENDPOINT=https://<ref>.storage.supabase.co/storage/v1/s3 \
        COGLOSSA_S3_REGION=<the project's region, e.g. ca-central-1> \
        COGLOSSA_PROXY_SECRET=$(openssl rand -base64 32) \
        COGLOSSA_KICK_TOKEN=$(openssl rand -base64 32) \
        COGLOSSA_SCORER_TOKEN="$scorer_token" \
        COGLOSSA_MODAL_KEY=wk-... COGLOSSA_MODAL_SECRET=ws-... \
        COGLOSSA_KICK_URL=https://placeholder.invalid \
        COGLOSSA_SCORER_URL=https://placeholder.invalid
      modal secret create coglossa-scorer COGLOSSA_SCORER_TOKEN="$scorer_token"
      ```
      Keep the `COGLOSSA_PROXY_SECRET` value: Vercel needs the same one.
3. **Migrate.** `modal run deploy/hosted/modal/app.py::migrate`
4. **First deploy.** `modal deploy deploy/hosted/modal/app.py` prints the URLs of `coglossa-api`,
   `coglossa-kick` and `coglossa-scorer`. Put the kick and scorer URLs into the Secret
   `coglossa-hosted` (`modal secret create --force coglossa-hosted ...` with every value, or edit
   it in the dashboard), then run `modal deploy deploy/hosted/modal/app.py` again.
5. **Vercel.** Create a project from `deploy/hosted/vercel` and set its env vars:
   `COGLOSSA_API_ORIGIN` (the `coglossa-api` URL), `COGLOSSA_PROXY_SECRET`,
   `COGLOSSA_MODAL_KEY`, `COGLOSSA_MODAL_SECRET`,
   `COGLOSSA_SESSION_SECRET` (`openssl rand -base64 32`) and `COGLOSSA_USERS` (step 6). Leave **Root Directory** empty: the workflow's CLI deploys from
   inside `deploy/hosted/vercel`, and a Root Directory set to that path would double it.
   `.vercelignore` there keeps the built `public/` in the upload.
6. **Users.** For each person, run
   `node --experimental-strip-types deploy/hosted/add-user.mjs <name>` (Node 22.6 or later). It asks
   for a password and prints a line such as `"alice": "pbkdf2_sha256$..."`. Put the lines in
   `COGLOSSA_USERS` as one JSON object. Names must be printable ASCII. The hashes contain `$`:
   when you set the value from a shell, put it in single quotes, e.g.
   `COGLOSSA_USERS='{"alice": "pbkdf2_sha256$..."}'`, or the shell expands the `$` parts.
   Add `--groups a,b` to give the person teams, or `--groups coglossa-admins` to make them an
   admin; the line is then `"alice": {"hash": "...", "groups": [...]}`. On the preview, `ci` is
   an admin and the demo login is in the team `demo`. People see only their teams' sets, and
   sets from before teams are admin-only until moved (CGL-26).
7. **GitHub.** The deploy workflow (`.github/workflows/hosted.yml`) runs after `ci` passes on
   `main`, or by hand from the Actions tab. The automatic run needs a push to `main` in this repository (not a fork) whose `ci` run
   passed. Running it by hand (`workflow_dispatch`) deploys `main` with no CI gate; a run
   dispatched from any other branch is skipped. The job uses the `hosted-preview` Environment,
   so each run waits for the owner's approval before it can read the deploy secrets.
   It skips with a notice when `MODAL_TOKEN_ID` or
   `VERCEL_TOKEN` is not set. It runs the migrations, deploys Modal, deploys Vercel
   (`build.sh`, then `vercel deploy --prod`) and runs `deploy/hosted/smoke.py`.
   - Create the `hosted-preview` Environment (Settings → Environments) with the owner as a
     required reviewer and deployments limited to `main`. Its secrets: `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `VERCEL_TOKEN`,
     `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `COGLOSSA_SMOKE_PASSWORD`, and `COGLOSSA_MODAL_KEY`
     and `COGLOSSA_MODAL_SECRET` (the proxy-auth token, step 2; the smoke test calls the API
     with it).
   - Repository variables: `COGLOSSA_SITE_URL` (the Vercel site) and `COGLOSSA_API_ORIGIN`
     (the `coglossa-api` URL).
   - The smoke test signs in as the user `ci`. Add it with `add-user.mjs ci` (step 6) using the
     password stored in `COGLOSSA_SMOKE_PASSWORD`. Without that secret the smoke test is skipped;
     with it but without the proxy-auth token secrets, the smoke test fails and names them.
   - By hand: `COGLOSSA_SMOKE_PASSWORD=... COGLOSSA_MODAL_KEY=wk-... COGLOSSA_MODAL_SECRET=ws-... uv run --with httpx python deploy/hosted/smoke.py --site URL --api-origin URL --user ci`

## Revoking access

A session is tied to the user's name.
- To sign one person out, remove them from `COGLOSSA_USERS`. Their cookie is refused on the next request.
- To sign everyone out, rotate `COGLOSSA_SESSION_SECRET`.

Redeploy the Vercel project after changing either variable.

## Checks and operations

- `modal run deploy/hosted/modal/app.py::doctor` checks the config, database, storage and scorer.
- `modal run deploy/hosted/modal/app.py::migrate` applies database migrations (forward only).
- The deploy migrates first, while the previous deployment's API and any running worker still
  run the old code. Revision 0012 (the procrastinate 3.12 upgrade, CGL-106) removes the
  functions older code calls to queue a job, so approve that `hosted` run only while no
  translation is running (no running `worker` call in the Modal dashboard). Until the new
  deployment answers, starting a translation can fail; start it again after the deploy.
- Rollback: deploy an earlier commit. Code refuses a database that is not at its own head
  revision, so once a migration has run, fix forward, or first downgrade the database to the
  head revision of the commit you are deploying (for code from before the procrastinate 3.12
  upgrade that is 0008: `coglossa.store.migrate.downgrade(url, "0008")`; there is no CLI
  command for it). Run the downgrade with the newer code, which still has the revisions it
  removes, then deploy the older commit.
- Logs: Modal keeps function logs for one day on Starter; Vercel keeps its own.
- CI imports the Modal app without an account: `deploy/hosted/modal/check_app.py`.
- The preview keeps out of search engines (CGL-85): the front door answers `/robots.txt`
  itself, without a sign-in (`User-agent: *`, `Disallow: /`), and sends
  `X-Robots-Tag: noindex, nofollow` on every response, forwarded `/api` calls included
  (`vercel/lib/route.ts` and `vercel/middleware.ts`, with `vercel/vercel.json` as a backstop).
  Smoke check 6 confirms it on the live deployment. The public site (`site/`) sends neither.

## Costs and spending limits

Set all three before sharing the URL.
- OpenRouter: a credit limit of $25 on the key.
- Modal: a workspace budget of $30 in the dashboard (the included credit).
- `api.max_upload_mb: 4` in the config limits uploads. Vercel refuses request bodies over about 4.5 MB, so the preview caps uploads at 4 MB; a larger file gets the API's one-line refusal, and anything over Vercel's limit gets Vercel's own 413.

An open browser tab on a running job keeps the API container awake (about $0.05/hour).
