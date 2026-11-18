#!/usr/bin/env bash
# Install or upgrade Coglossa from this bundle (operator guide, "Install"). It checks the
# bundle's checksums, loads its images into Docker, and creates config/coglossa.yaml,
# config/tiers.yaml and .env when they do not exist yet; it never overwrites them.
# Usage: ./install.sh [--gpu]    --gpu: run the scorer on an NVIDIA GPU (compose.gpu.yaml)
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
self=$here/$(basename "$0")
cd "$here"

MIN_COMPOSE=2.24.0
die() { echo "error: $*" >&2; exit 1; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

gpu=false
for arg in "$@"; do
  case $arg in
    --gpu) gpu=true ;;
    -h | --help) sed -n '2,6s/^# \{0,1\}//p' "$self"; exit 0 ;;
    *) echo "error: unknown option $arg (usage: ./install.sh [--gpu])" >&2; exit 2 ;;
  esac
done

if $gpu && [ -f manifest.json ] && grep -q '"build": "cpu"' manifest.json; then
  die "this bundle's scorer is the CPU build; build the bundle with SCORER=cuda for --gpu"
fi
gpu_files=compose.yaml:compose.gpu.yaml
# A COMPOSE_FILE line as compose reads .env: leading blanks, an optional "export", blanks around =.
cf_line='^[[:space:]]*(export[[:space:]]+)?COMPOSE_FILE[[:space:]]*='
compose_file() {  # the COMPOSE_FILE compose uses: the last line's value, unquoted, without a comment
  local value
  value=$(sed -nE "s/${cf_line}[[:space:]]*//p" .env | tail -n1 | tr -d '\r')
  case $value in
    \"*) value=${value#\"}; value=${value%%\"*} ;;
    \'*) value=${value#\'}; value=${value%%\'*} ;;
    *) value=${value%%[[:space:]]#*}; value=${value%"${value##*[![:space:]]}"} ;;
  esac
  printf '%s\n' "$value"
}
# --gpu over a COMPOSE_FILE in .env: one that has the GPU file is kept and the plain default is
# replaced below; any other was chosen on purpose, so stop before anything changes.
if $gpu && [ -f .env ] && grep -Eq "$cf_line" .env; then
  value=$(compose_file)
  case $value in
    *compose.gpu.yaml* | '' | compose.yaml | ./compose.yaml) ;;
    *) die ".env has COMPOSE_FILE=$value, without compose.gpu.yaml; to use the GPU, change that line to COMPOSE_FILE=$value:compose.gpu.yaml and re-run ./install.sh --gpu" ;;
  esac
fi

# 1. The bundle is complete and undamaged.
[ -f SHA256SUMS ] || die "SHA256SUMS is missing: this is not a complete Coglossa bundle; copy it again"
echo "Checking the bundle's checksums ..."
if ! report=$(sha256 -c SHA256SUMS 2>&1); then
  # sha256sum and shasum word a missing or altered file differently; collect the names from both.
  bad=$(printf '%s\n' "$report" | { grep -E 'FAILED|No such file' || true; } \
    | sed -E 's/: FAILED.*//; s/^[a-z0-9]*sum: +//; s/: No such file.*//' | sort -u | head -3 | paste -sd ' ' -)
  die "the bundle is damaged or incomplete (${bad:-checksums do not match}); copy it again and re-run ./install.sh"
fi

# 2. Docker and a recent enough Compose.
command -v docker >/dev/null || die "docker is not installed (operator guide, Requirements)"
compose=$(docker compose version --short 2>/dev/null) \
  || die "the Docker Compose plugin is missing; install Docker Compose $MIN_COMPOSE or newer"
compose=${compose#v}
if [ "$(printf '%s\n%s\n' "$MIN_COMPOSE" "$compose" | sort -V | head -n1)" != "$MIN_COMPOSE" ]; then
  die "Docker Compose $compose is too old; Coglossa needs $MIN_COMPOSE or newer"
fi

# 3. The images: only the archives SHA256SUMS lists, which step 1 checked. A line is
# "<hash>  ./images/app.tar", or "<hash> *images/app.tar" when written in binary mode.
listed=$(sed -nE 's#^[0-9a-f]{64} [ *](\./)?(images/[^/]+\.tar)$#\2#p' SHA256SUMS)
[ -n "$listed" ] || die "SHA256SUMS lists no images: this is not a complete Coglossa bundle; copy it again"
for archive in images/*.tar; do
  if [ -e "$archive" ] && ! printf '%s\n' "$listed" | grep -qxF -- "$archive"; then
    echo "warning: $archive is not in SHA256SUMS, so it was not checked; not loading it" >&2
  fi
done
while IFS= read -r archive; do
  echo "Loading $archive ..."
  docker load -i "$archive" </dev/null >/dev/null || die "docker could not load $archive; is the Docker daemon running?"
done <<<"$listed"

# 4. The config, the tiers and the database password: created once, never overwritten.
for f in coglossa tiers; do
  if [ ! -f "config/$f.yaml" ]; then cp "config/$f.example.yaml" "config/$f.yaml"; fi
done
# The containers run as uid 10001 and read config/, models/ and bench/ through bind mounts. A
# hardened host's umask (027, 077) applies to the extracted bundle and to the copy above, so open
# them up. They hold no secrets: backend keys live in .env, which stays mode 600.
chmod -R a+rX config
# Only what lacks the permission: without bench data, compose creates an empty bench/ owned by
# root, which a second run could not chmod.
for d in models bench; do
  if [ -d "$d" ]; then find "$d" \( ! -perm -o=r -o -type d ! -perm -o=x \) -exec chmod a+rX {} +; fi
done
if [ ! -f .env ]; then
  password=$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')
  (
    umask 077
    printf '%s\n' \
      "# Created by install.sh. Keep this file: the database was created with this password." \
      "COGLOSSA_DB_PASSWORD=$password" >.env
  )
fi
if $gpu && grep -Eq "$cf_line" .env; then
  case $(compose_file) in
    *compose.gpu.yaml*) ;;
    *) # only the plain default is left (checked above): replace it, keeping .env mode 600
      rm -f .env.new
      (umask 077 && sed -E "s/$cf_line.*/COMPOSE_FILE=$gpu_files/" .env >.env.new)
      mv .env.new .env ;;
  esac
elif $gpu; then
  # an edited .env may lack its last newline; the line must not join an API key's
  if [ -n "$(tail -c1 .env)" ]; then echo >>.env; fi
  echo "COMPOSE_FILE=$gpu_files" >>.env
fi

cat <<EOF

Coglossa $(cat VERSION) is installed. Next:
  1. Edit config/coglossa.yaml: your model servers (backends), models and roles.
  2. docker compose up -d --wait
  3. docker compose exec api coglossa doctor
Then open http://<this host>:8080. The operator guide (operator-guide.md) has the details.
To apply a later change to config/ or .env:
  docker compose up -d --wait --force-recreate migrate api worker
EOF
