#!/usr/bin/env bash
# The air-gap install test (spec §9.1). Builds a bundle without scorer weights, with a stand-in
# FLORES-200 tree (flores-mini/), and installs it the way an operator does, on a Docker network
# with no route out. Then: doctor, a document translated end to end, `coglossa bench run` and
# `bench flags` on the bundle's FLORES data as the operator guide runs them, a config change
# applied as the guide says, a backup, an upgrade into a new directory (the data and the edited
# tiers stay), and a restore over a newer schema after the data is deleted.
# Usage: make airgap-test (Docker; about 15 GB of disk).
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
tests="$repo/deploy/tests"
version="airgap-$(git -C "$repo" rev-parse --short HEAD)"
work=$(mktemp -d)
# The running stack's project name is overridden, so the test never touches a real install's
# volumes on this machine. That hides the shipped `name: coglossa`, which is what lets an upgrade
# in a new directory find the data; the upgrade step asserts it separately, without the override.
export COMPOSE_PROJECT_NAME=coglossa-airgaptest
export COMPOSE_FILE="compose.yaml:compose.demo.yaml:$tests/compose.airgap.yaml"
site1="$work/site1/coglossa-$version"
site2="$work/site2/coglossa-$version-next" # a real upgrade's directory has another name
tarball="$work/dist/coglossa-$version-linux-amd64.tar"
step() { printf '\n== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
smoke() { docker compose exec -T worker python - http://api:8080 "$@" <"$tests/smoke.py"; }
bench() { # the operator guide's "Benchmark your models": results in $work/bench, owned by us
  docker compose run --rm --no-deps -T -v "$work/bench:/out" --user "$(id -u):$(id -g)" \
    worker coglossa bench "$@"
}
started() { docker inspect -f '{{.State.StartedAt}}' "$(docker compose ps -q "$1")"; }
sql() { docker compose exec -T postgres psql -X -q -At -v ON_ERROR_STOP=1 -U coglossa -d coglossa -c "$1"; }
pair_tier() { # the tier the running API gives the pair $1 (e.g. en>yo), or None
  docker compose exec -T worker python -c 'import json, sys, urllib.request
pairs = json.load(urllib.request.urlopen("http://api:8080/api/system/tiers"))["pairs"]
print(pairs.get(sys.argv[1]))' "$1"
}
edit() { # replace the text $2 with $3 in file $1, once; the text must be there
  python3 - "$@" <<'PY'
import sys
from pathlib import Path
path, old, new = sys.argv[1:]
text = Path(path).read_text()
assert old in text, f"{old!r} is not in {path}"
Path(path).write_text(text.replace(old, new, 1))
PY
}
apply_config() { # the operator guide's "Applying changes"
  docker compose up -d --wait --wait-timeout 300 --force-recreate migrate api worker
}
shipped_name() { # the project name compose.yaml in directory $1 gives, without the override
  (cd "$1" && env -u COMPOSE_PROJECT_NAME docker compose -f compose.yaml config --format json) \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["name"])'
}
cleanup() {
  for site in "$site2" "$site1"; do
    if [ -f "$site/compose.yaml" ]; then (cd "$site" && docker compose down -v --remove-orphans) >/dev/null 2>&1 || true; fi
  done
  docker image rm "coglossa/app:$version" "coglossa/scorer:$version" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

step "build and check the bundle (no weights, CPU scorer, the stand-in FLORES tree)"
"$repo/deploy/bundle.sh" --version "$version" --out "$work/dist" --no-weights --scorer cpu \
  --flores "$tests/flores-mini" --allow-dirty
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

step "bench run: the bundle's FLORES data, the demo models and the fake scorer"
cp "$tests/bench.airgap.yaml" config/bench.yaml
mkdir -p "$work/bench"
bench run /etc/coglossa/bench.yaml --out /out || fail "bench run failed"
cat "$work/bench/summary.md"
for row in '| coglossa | en>es |' '| baseline | en>es |' '| coglossa | en>yo |' '| baseline | en>yo |'; do
  grep -qF "$row" "$work/bench/summary.md" || fail "summary.md has no row $row"
done
grep -qF '| scored/units | too long src/out |' "$work/bench/summary.md" || fail "summary.md has no coverage columns"
grep -qF 'Units every system scored: en>es ' "$work/bench/summary.md" || fail "summary.md does not say which units MetricX covers"

step "bench flags: the demo judge and the fake scorer"
bench flags /etc/coglossa/bench.yaml --scoring-config /etc/coglossa/coglossa.yaml --out /out \
  || fail "bench flags failed"
cat "$work/bench/flag-summary.md"
grep -q '^## Tier ' "$work/bench/flag-summary.md" || fail "flag-summary.md has no tier"
grep -q '^Clean false-positive rate (any flag): ' "$work/bench/flag-summary.md" || fail "flag-summary.md has no clean false-positive rate"
grep -q '^## Sample injections' "$work/bench/flag-summary.md" || fail "flag-summary.md has no sample injections"
for pair in en-es en-yo; do
  [ -s "$work/bench/flags/$pair.jsonl" ] || fail "bench flags wrote no flags/$pair.jsonl"
done
grep -q 'injected errors' "$work/bench/ATTRIBUTION.txt" || fail "no FLORES attribution with the flags notice"

step "a config change: the operator's own tiers, applied as the guide says"
[ -f config/tiers.yaml ] || fail "install.sh did not create config/tiers.yaml"
[ "$(pair_tier 'en>yo')" = None ] || fail "en>yo already has a tier of its own"
scorer_started=$(started scorer)
worker_started=$(started worker)
edit config/tiers.yaml 'pairs: {}' 'pairs: { "en>yo": B }'
printf 'scoring: { language_tiers_file: tiers.yaml }\n' >>config/coglossa.yaml
apply_config
[ "$(started worker)" != "$worker_started" ] || fail "the worker kept running with the old config"
[ "$(started scorer)" = "$scorer_started" ] || fail "applying a config change restarted the scorer"
[ "$(pair_tier 'en>yo')" = B ] || fail "the API does not serve the edited config/tiers.yaml"
docker compose exec -T api coglossa doctor >/dev/null || fail "doctor failed after the config change"
# the change alters the config hash: a worker still on the old config would end this run
# complete_with_errors, which smoke treats as a failure
smoke check airgap

step "backup"
./backup.sh "$work/backups"
db=$(ls "$work"/backups/coglossa-db-*.dump)
files=$(ls "$work"/backups/coglossa-files-*.tar.gz)
if [ ! -s "$db" ] || [ ! -s "$files" ]; then fail "the backup is empty"; fi

step "upgrade into a new directory (site 2): the data stays"
docker compose down # the volumes stay
mkdir -p "$work/site2" && tar -xf "$tarball" -C "$work/site2"
mv "$work/site2/coglossa-$version" "$site2"
cp config/coglossa.yaml config/tiers.yaml "$site2/config/"
cp .env "$site2/"
cd "$site2"
./install.sh # the edited tiers.yaml must not trip the checksums
for site in "$site1" "$site2"; do
  name=$(shipped_name "$site") || fail "docker compose config failed in $site"
  [ "$name" = coglossa ] || fail "$site/compose.yaml names project '$name', not coglossa"
done
docker compose up -d --wait --wait-timeout 300
smoke check airgap
[ "$(pair_tier 'en>yo')" = B ] || fail "the upgrade lost the edited config/tiers.yaml"

step "restore after the data is gone, over a newer schema"
docker compose down -v
docker compose up -d --wait --wait-timeout 300
if smoke check airgap >/dev/null 2>&1; then fail "the data survived docker compose down -v"; fi
# what a later version's migrations leave behind when the operator goes back to an older
# backup: a new table that references an old one, and a newer schema revision
sql "CREATE TABLE newer_feature (id serial PRIMARY KEY, project_id uuid REFERENCES project (id));
      UPDATE alembic_version SET version_num = '9999_newer';"
./restore.sh --yes "$db" "$files"
[ -z "$(sql "SELECT to_regclass('public.newer_feature')")" ] || fail "the restore kept the newer table"
[ "$(sql "SELECT version_num FROM alembic_version")" != 9999_newer ] || fail "the restore kept the newer revision"
smoke check airgap

printf '\nair-gap install test passed\n'
