#!/usr/bin/env bash
# Back up Coglossa's database and stored files (operator guide, "Backups"). Run it from the
# install directory while Coglossa is running. The backups hold every document: keep them private.
# Usage: ./backup.sh [DIR]    default DIR: ./backups
set -euo pipefail
umask 077
dir=${1:-backups}
mkdir -p "$dir"
dir=$(cd "$dir" && pwd)
cd "$(dirname "$0")"
die() { echo "error: $*" >&2; exit 1; }

stamp=$(date -u +%Y%m%dT%H%M%SZ)
db="$dir/coglossa-db-$stamp.dump"
files="$dir/coglossa-files-$stamp.tar.gz"
# Each file is written as <name>.partial and renamed only when both backups are complete,
# so a failed run never leaves something that looks like a backup.
trap 'rm -f "$db.partial" "$files.partial"' EXIT

docker compose exec -T postgres pg_dump -U coglossa -d coglossa --format=custom >"$db.partial" \
  || die "the database backup failed; is Coglossa running (docker compose ps)?"
docker compose run --rm --no-deps -T --entrypoint tar worker -C /data/files -czf - . >"$files.partial" \
  || die "the files backup failed; see the message above"
gzip -t "$files.partial" || die "the files backup is damaged (is the disk full?); nothing was kept"
[ -s "$db.partial" ] || die "the database backup is empty; nothing was kept"
mv "$db.partial" "$db"
mv "$files.partial" "$files"
echo "Backed up to $db and $files"
