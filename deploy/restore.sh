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
archive="$(cd "$(dirname "${paths[1]}")" && pwd)/$(basename "${paths[1]}")"
cd "$(dirname "$0")"

# 1. Check both inputs before anything is stopped or changed.
{ gzip -t "$archive" && tar -tzf "$archive" >/dev/null; } 2>/dev/null \
  || die "$archive is not a valid files archive (coglossa-files-*.tar.gz); the order is DB_DUMP then FILES_ARCHIVE"
docker compose up -d --wait postgres >/dev/null || die "could not start the database; see the message above"
docker compose exec -T postgres pg_restore -l <"$db" >/dev/null 2>&1 \
  || die "$db is not a valid database dump (coglossa-db-*.dump); the order is DB_DUMP then FILES_ARCHIVE"
# -l reads only the table of contents; reading every data block catches a truncated dump.
docker compose exec -T postgres pg_restore -f /dev/null <"$db" >/dev/null 2>&1 \
  || die "$db is incomplete or damaged (it could not be read to the end); nothing was changed; use another backup"

if ! $yes; then
  [ -t 0 ] || die "this needs a terminal to confirm; add --yes to restore without asking"
  read -r -p "This replaces all Coglossa data with the backup. Type 'restore' to go on: " answer
  [ "$answer" = restore ] || die "nothing was restored"
fi

# 2. From here on, bring Coglossa back up if anything fails.
stopped=false
restart() {
  local rc=$?
  if $stopped && [ $rc -ne 0 ]; then
    docker compose up -d >/dev/null 2>&1 || echo "error: Coglossa could not be restarted; run docker compose up -d" >&2
  fi
}
trap restart EXIT
stopped=true
docker compose stop api worker >/dev/null || die "could not stop Coglossa; nothing was changed"
# One transaction: empty the schema (a newer version's tables too; everything Coglossa and its
# job queue create lives in public), then the dump's SQL. COMMIT is sent only when pg_restore
# read the whole dump, so a failure anywhere leaves the database as it was.
docker compose exec -T postgres sh -c 'set -o pipefail
  { echo "SET client_min_messages = warning; BEGIN; DROP SCHEMA public CASCADE;"
    echo "CREATE SCHEMA public AUTHORIZATION pg_database_owner; GRANT USAGE ON SCHEMA public TO PUBLIC;"
    pg_restore --no-owner -f - && echo "COMMIT;"
  } | psql -X -q -v ON_ERROR_STOP=1 -U coglossa -d coglossa' <"$db" >/dev/null \
  || die "the database restore failed and was rolled back; Coglossa is being restarted with the data as it was before the restore"
rc=0
# The step prints a marker before it touches anything, so a failure before it started (the
# container could not be created, say) is told apart from one after the old files went. Exit
# codes 3 and 4 come only from the step itself, so they never depend on the marker arriving.
step=$(docker compose run --rm --no-deps -T --entrypoint sh worker \
  -c 'echo files-step-started; find /data/files -mindepth 1 -delete || exit 3; tar -C /data/files -xzf - || exit 4' <"$archive") || rc=$?
removed="the files restore failed after the old files were removed; the database is restored but the stored files are incomplete; run ./restore.sh again with a good archive"
case $rc in
  0) ;;
  3) die "the old files could not be cleared, so the stored files may be partly removed; the database is restored; run ./restore.sh again" ;;
  4) die "$removed" ;;
  *) [[ $step == *files-step-started* ]] \
       || die "the files restore could not start, so the old files are untouched; the database is restored; fix the problem above and run ./restore.sh again"
     die "$removed" ;;
esac
stopped=false
docker compose up -d --wait >/dev/null || die "Coglossa did not become healthy after the restore; see docker compose ps and docker compose logs"
echo "Restored $db and $archive"
