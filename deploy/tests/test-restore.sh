#!/usr/bin/env bash
# Tests restore.sh's validation against a stub `docker` that records its calls: a bad input must be
# refused before any service is stopped. Usage: deploy/tests/test-restore.sh
set -euo pipefail
deploy=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

mkdir -p "$work/bin" "$work/src"
cat >"$work/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$DOCKER_LOG"
case "$*" in
  *"pg_restore -l"*) [ -z "${STUB_BAD_DUMP:-}" ] || exit 1 ;;
  *"pg_restore -f /dev/null"*) [ -z "${STUB_TRUNCATED_DUMP:-}" ] || exit 1 ;;
  *"--entrypoint sh"*)  # the files step; STUB_FILES_NOSTART fails before it starts
    [ -z "${STUB_FILES_NOSTART:-}" ] || exit 1
    [ -n "${STUB_FILES_NO_MARKER:-}" ] || echo files-step-started
    exit "${STUB_FILES_RC:-0}" ;;
esac
EOF
chmod +x "$work/bin/docker"
export PATH="$work/bin:$PATH"

echo hello >"$work/src/a.txt"
tar -czf "$work/files.tar.gz" -C "$work/src" .
echo dump >"$work/db.dump"
echo "not gzip" >"$work/bad.tar.gz"

restore() {  # runs restore.sh with --yes; output in $work/out, docker calls in $work/log
  : >"$work/log"
  DOCKER_LOG="$work/log" "$deploy/restore.sh" --yes "$@" >"$work/out" 2>&1
}

if restore "$work/db.dump" "$work/bad.tar.gz"; then fail "a bad archive was accepted"; fi
grep -q "^error: .*bad.tar.gz is not a valid files archive" "$work/out" || fail "bad archive: $(cat "$work/out")"
! grep -q "stop" "$work/log" || fail "bad archive: services were stopped"
echo "ok - a bad files archive is refused before anything is stopped"

if restore "$work/files.tar.gz" "$work/db.dump"; then fail "swapped arguments were accepted"; fi
grep -q "^error: .*db.dump is not a valid files archive" "$work/out" || fail "swapped: $(cat "$work/out")"
! grep -q "stop" "$work/log" || fail "swapped: services were stopped"
echo "ok - swapped arguments are refused"

if STUB_BAD_DUMP=1 restore "$work/db.dump" "$work/files.tar.gz"; then fail "a bad dump was accepted"; fi
grep -q "^error: .*db.dump is not a valid database dump" "$work/out" || fail "bad dump: $(cat "$work/out")"
! grep -q "stop" "$work/log" || fail "bad dump: services were stopped"
echo "ok - a bad database dump is refused before anything is stopped"

# pg_restore -l reads only the table of contents, so a truncated dump passes it: the whole dump is
# read before anything is stopped or changed, and the refusal is one line naming the file
if STUB_TRUNCATED_DUMP=1 restore "$work/db.dump" "$work/files.tar.gz"; then fail "a truncated dump was accepted"; fi
grep -q "^error: .*db.dump is incomplete or damaged" "$work/out" || fail "truncated dump: $(cat "$work/out")"
[ "$(wc -l <"$work/out")" -eq 1 ] || fail "truncated dump: not one line: $(cat "$work/out")"
if grep -q "stop\|DROP SCHEMA\|--entrypoint" "$work/log"; then fail "truncated dump: something was changed: $(cat "$work/log")"; fi
echo "ok - a truncated database dump is refused before anything is changed"

if "$deploy/restore.sh" "$work/db.dump" "$work/files.tar.gz" </dev/null >"$work/out" 2>&1; then fail "no terminal, no --yes"; fi
grep -q "^error: this needs a terminal" "$work/out" || fail "no tty: $(cat "$work/out")"
echo "ok - no terminal and no --yes is refused"

restore "$work/db.dump" "$work/files.tar.gz" || fail "good restore: $(cat "$work/out")"
grep -q "^compose stop api worker" "$work/log" || fail "good restore: no stop"
read_at=$(grep -n "pg_restore -f /dev/null" "$work/log" | cut -d: -f1 || true)
stop_at=$(grep -n "^compose stop" "$work/log" | cut -d: -f1 || true)
if [ -z "$read_at" ] || [ "$read_at" -gt "$stop_at" ]; then fail "good restore: the whole dump is not read before the stop"; fi
# a backup from before a schema-changing upgrade: the newer tables must go too, in the same
# transaction as the restore (the air-gap test restores over a newer schema for real)
grep -q "BEGIN; DROP SCHEMA public CASCADE;" "$work/log" || fail "good restore: the schema is not emptied first"
grep -q 'pg_restore --no-owner -f - && echo "COMMIT;"' "$work/log" || fail "good restore: COMMIT does not wait for pg_restore"
[ "$(tail -n1 "$work/log")" = "compose up -d --wait" ] || fail "good restore: not restarted: $(cat "$work/log")"
echo "ok - a good restore stops, restores and restarts"

if STUB_FILES_RC=4 restore "$work/db.dump" "$work/files.tar.gz"; then fail "a files failure was ignored"; fi
grep -q "^error: the files restore failed after the old files were removed" "$work/out" || fail "files fail: $(cat "$work/out")"
[ "$(tail -n1 "$work/log")" = "compose up -d" ] || fail "files fail: not restarted: $(cat "$work/log")"
echo "ok - a files failure says so and restarts Coglossa"

if STUB_FILES_NOSTART=1 restore "$work/db.dump" "$work/files.tar.gz"; then fail "a files step that did not start was ignored"; fi
grep -q "^error: the files restore could not start, so the old files are untouched" "$work/out" || fail "files no start: $(cat "$work/out")"
if grep -q "were removed" "$work/out"; then fail "files no start: says the old files were removed"; fi
[ "$(tail -n1 "$work/log")" = "compose up -d" ] || fail "files no start: not restarted: $(cat "$work/log")"
echo "ok - a files step that could not start says the old files are untouched"

# exit 4 comes only from the step itself: the old files went even if the marker never arrived
if STUB_FILES_NO_MARKER=1 STUB_FILES_RC=4 restore "$work/db.dump" "$work/files.tar.gz"; then fail "a files failure without the marker was ignored"; fi
grep -q "^error: the files restore failed after the old files were removed" "$work/out" || fail "files fail, no marker: $(cat "$work/out")"
echo "ok - a files failure is reported as one even if the marker was lost"

# a failure after the marker with any other code (the container was killed, say) means the old files may be gone
if STUB_FILES_RC=137 restore "$work/db.dump" "$work/files.tar.gz"; then fail "a killed files step was ignored"; fi
grep -q "^error: the files restore failed after the old files were removed" "$work/out" || fail "files killed: $(cat "$work/out")"
echo "ok - a files step that started and then died says the old files were removed"

echo "restore.sh: all tests passed"
