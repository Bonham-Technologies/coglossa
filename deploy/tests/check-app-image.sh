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
for path in ("store/migrations/versions/0005_project_team.py",
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
