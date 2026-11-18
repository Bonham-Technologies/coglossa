# Contributing to Coglossa

Thanks for helping! This guide covers setup and what a pull request needs.

## Setup

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm (the version
in `web/package.json`), and Docker (the test database, shellcheck and the images run in it).

```bash
cd engine && uv sync --all-groups && cd ..   # likewise in api/ (and scorer/, except on an Intel Mac)
cd web && pnpm install && cd ..
make db-up                                   # Postgres for the engine and API tests
make dev-stack                               # API with a synthetic model server, port 8080
```

### Dev box parity

Your machine should run what a client runs, so a regression shows up before a deployment:

- **Python 3.12.** `engine/`, `api/` and `scorer/` pin it in `.python-version`; uv uses it
  for their environments, as the images use `python:3.12-slim-bookworm`.
- **Node 22.** `web/.npmrc` pins `use-node-version` to the Node of the app image
  (`node:22-bookworm-slim`); pnpm fetches it and runs every script on it, whatever Node is
  installed. Bump it when that image moves.
- **The scorer on Linux.** torch has no Intel-macOS packages after 2.2.2, so `scorer/uv.lock`
  covers Linux (and Apple Silicon) only. On an Intel Mac, `make test-scorer`, `make weights`
  and the scorer's part of `make lint` / `make fmt` run in a Linux container with the scorer
  image's base, uv and lock (`deploy/docker/scorer-dev.Dockerfile`); its environment lives in
  Docker volumes. `SCORER_IN_DOCKER=1` forces the container anywhere, `=0` runs natively.
  To check the real models there:
  `COGLOSSA_REAL_MODELS=1 COGLOSSA_METRICX_DIR=… COGLOSSA_MT5_TOKENIZER_DIR=… COGLOSSA_LABSE_DIR=… make test-scorer`.
- **Postgres 16,** the same image in `deploy/compose.dev.yaml` as in `deploy/compose.yaml`.

The README's quick start runs the engine against your own OpenAI-compatible model server or
the built-in fake one. Never commit secrets: configs name the environment variable that holds
a key (`api_key_env`), not the key.

## Before you open a PR

```bash
make lint
make test
make e2e
make deploy-check
```

- Keep PRs focused; follow the existing code style.
- Add or update tests for behaviour changes.
- Conventional-commit style titles (`feat:`, `fix:`, `chore:`) are appreciated.
- `main` is protected: open a pull request, wait for the `ci-status` check to
  pass, and squash-merge.

## Sign your commits (DCO)

Coglossa is licensed under the [Apache License 2.0](LICENSE). By contributing, you agree that
your contribution is licensed under the same terms.

Every commit in a pull request from outside the project must carry a sign-off certifying the
[Developer Certificate of Origin](https://developercertificate.org/): that you wrote the
change, or otherwise have the right to submit it under the project's licence. Add it with
`git commit -s`, which appends a line like this to the commit message:

```
Signed-off-by: Your Name <you@example.com>
```

Use your real name and an email address you can be reached at. To sign off commits you've
already made, run `git rebase --signoff HEAD~N`, where N is the number of commits on your
branch, and force-push it. The DCO check on the pull request tells you if a commit is missing
its sign-off.
