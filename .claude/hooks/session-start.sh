#!/bin/bash
# SessionStart: get a Claude Code cloud session ready for the verify command in CLAUDE.md
# (`make lint && make -o db-up test`). Cloud sessions have Postgres 16 installed natively but
# not running, and no Docker for `make db-up`, so start the native cluster, give it the
# compose file's superuser, and point the tests at it. Local sessions are left alone.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Postgres: the same user and password as deploy/compose.dev.yaml, on the native port.
if command -v pg_ctlcluster >/dev/null; then
  # Start every stopped cluster. The hook fires on a resume too (no matcher), when a restored
  # container's Postgres is down with a stale pid file, which pg_ctlcluster removes. The
  # Makefile's test targets do the same for a container resumed under a running session.
  pg_lsclusters -h | awk '$4 == "down" {print $1, $2}' | while read -r ver name; do
    pg_ctlcluster "$ver" "$name" start || echo "session-start: could not start Postgres $ver/$name" >&2
  done
  for _ in $(seq 1 30); do pg_isready -q -h 127.0.0.1 && break; sleep 1; done
  runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'coglossa') THEN
    CREATE ROLE coglossa LOGIN SUPERUSER PASSWORD 'coglossa';
  END IF;
END
$$;
SQL
  port=$(runuser -u postgres -- psql -tAc 'SHOW port')
  if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    echo "export COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:$port/postgres" \
      >>"$CLAUDE_ENV_FILE"
  fi
fi

# Dependencies, so the first lint or test run doesn't pay for them.
(cd engine && uv sync --quiet)
(cd api && uv sync --quiet)
(cd scorer && uv sync --quiet)
(cd web && pnpm install --frozen-lockfile --silent)
