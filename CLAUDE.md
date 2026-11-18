# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Coglossa is a multi-agent translation system that translates for meaning and shows its work.
Several translator agents render each passage, a reviewer reconciles them, a judge and a scorer
service rate the result, and every span carries its alternatives, the reviewer's rationale and
plain-language flags. v1 is built for an air-gapped, on-premises customer that runs its own
OpenAI-compatible model servers; nothing calls out of the network. A hosted preview (Vercel +
Modal + Supabase) runs the same code for demos.

Flow: ingest → profile → translation guide (terms, names, style; called the "guide", never the
"bible") → translate chunks with every translator → reconcile → judge, back-translate, score →
sweep (enforce locked guide entries) → report. Queued runs live in Postgres and are processed by
a worker; the REST API and web app read and drive them. The design is in
`docs/superpowers/specs/2026-10-09-coglossa-v1-design.md`.

## Layout

- `engine/`: the `coglossa` Python package: pipeline stages (`coglossa/stages/`), the LLM agent
  runner (`coglossa/llm/`), prompts (`coglossa/prompts/templates/`), the worker, the CLI and
  `coglossa bench`.
- `api/`: the FastAPI REST API (`coglossa_api`); `devstack.py` runs it with synthetic models.
- `web/`: the React 19 web app (Vite, TanStack Query, React Router, Vitest + MSW, Playwright e2e).
- `scorer/`: the scoring service (MetricX-24 quality estimate, LaBSE similarity).
- `schema/`: generated JSON schemas and `openapi.json` (`make schema`; the web app's
  `src/api/schema.d.ts` is generated from it and CI checks it matches).
- `deploy/`: Dockerfiles, compose files, install/backup/restore, the offline bundle and their
  tests; `deploy/hosted/` is the hosted preview (Modal app, Vercel site, smoke test).
- `bench/`: calibration inputs and results. `docs/`: operator guide, specs, plans, follow-ups.

## Commands

Verify: `make lint && make -o db-up test` against a Postgres superuser at `COGLOSSA_TEST_DATABASE_URL` (`make db-up` first locally; `-o db-up` skips Docker where Postgres runs natively, as in cloud sessions).

```bash
make lint            # ruff check + format check (engine, scorer, api) and pnpm lint (web)
make fmt             # apply the formatters
make test            # engine, scorer, api (start Postgres via db-up) and web unit tests
make test-engine     # one suite; also test-scorer, test-api, test-web
make e2e             # builds the web app, runs Playwright against a real API
make deploy-check    # shellcheck (pinned container), install/restore tests, compose and workflow checks
make airgap-test     # builds the images and installs the bundle with no network (slow)
make schema          # regenerate schema/ and the web API types after a model or route change
make dev-stack       # local API with synthetic models on :8080
make db-up / db-down # the test Postgres (deploy/compose.dev.yaml)
```

Engine and API tests need the database: run them through `make`, not a bare `pytest`. The dev
box runs what a client runs (CONTRIBUTING.md, "Dev box parity"): Python 3.12 and Node 22 are
pinned, and on an Intel Mac the scorer's make targets run in a Linux container
(`deploy/docker/scorer-dev.Dockerfile`), because torch has no Intel-macOS packages; never add
Intel-macOS exceptions to `scorer/pyproject.toml`. Inside a
package: `cd engine && uv run pytest tests/stages -q`; `cd web && pnpm test`.

## Conventions

- Python: ruff for lint and format (no mypy). TypeScript: the web app's ESLint and Prettier.
- Web copy is for non-technical users: plain words, no engine jargon in the UI.
- Colours come from CSS tokens (tests reject raw colours and check contrast in light and dark).
- Secrets never enter the repo, logs or chat: configs use `api_key_env`.
- Branches off `main`, one pull request each; `main` is protected: the `ci-status` check must
  pass, and merges are squash only. Conventional-commit style subjects (`feat:`, `fix(web):`).
- Every new CI job goes into `ci-status`'s `needs` in the same commit.
- After a merge the hosted preview is redeployed by hand (`deploy/hosted/README.md`), then
  checked with `deploy/hosted/smoke.py`.

## Record

- Jira key: CGL. Ticket keys go in as plain text, never as links.
- Confluence tree: OPS > Projects > Coglossa.
- Worktrees: none; work happens on branches in this checkout. Plan scratch (SDD ledgers and
  briefs) lives in the git-ignored `.superpowers/`.
- Follow-ups: Jira CGL. `docs/m*-followups.md` record each milestone's deferred items.
