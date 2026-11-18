#!/usr/bin/env bash
# Tests install.sh against a minimal fake bundle and a stub `docker` that records its calls.
# No images and no Docker daemon are needed. Usage: deploy/tests/test-install.sh
set -euo pipefail
deploy=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

mkdir -p "$work/bin"
cat >"$work/bin/sha256" <<'EOF'
#!/usr/bin/env bash
if command -v sha256sum >/dev/null; then exec sha256sum "$@"; else exec shasum -a 256 "$@"; fi
EOF
cat >"$work/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$DOCKER_LOG"
if [ "$1 $2" = "compose version" ]; then echo "${STUB_COMPOSE_VERSION:-2.29.1}"; fi
EOF
chmod +x "$work/bin/docker" "$work/bin/sha256"
export PATH="$work/bin:$PATH"

new_bundle() {  # a fresh bundle directory, checksummed as bundle.sh does; prints its path
  # $1: the scorer build its manifest.json names (cpu or cuda); without it, no manifest
  local b
  b=$(mktemp -d "$work/bundle-XXXXXX")
  mkdir -p "$b/images" "$b/config"
  cp "$deploy/install.sh" "$b/"
  echo app >"$b/images/app.tar"
  echo scorer >"$b/images/scorer.tar"
  cp "$deploy/config/coglossa.example.yaml" "$b/config/"
  cp "$deploy/../engine/coglossa/scoring/tiers.yaml" "$b/config/tiers.example.yaml"
  echo 0.0.0-test >"$b/VERSION"
  if [ -n "${1:-}" ]; then printf '{\n  "scorer": {\n    "build": "%s",\n    "torch_index": "x"\n  }\n}\n' "$1" >"$b/manifest.json"; fi
  # shellcheck disable=SC2094  # find skips SHA256SUMS, so it is not read while written
  (cd "$b" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256 >SHA256SUMS)
  echo "$b"
}

install() {  # runs install.sh in bundle $1 with args; output in $work/out, docker calls in $work/log
  local b=$1; shift
  : >"$work/log"
  DOCKER_LOG="$work/log" "$b/install.sh" "$@" >"$work/out" 2>&1
}

# a fresh install loads every image and creates the config and .env
b=$(new_bundle)
install "$b" || fail "fresh install: $(cat "$work/out")"
{ grep -qx "load -i images/app.tar" "$work/log" && grep -qx "load -i images/scorer.tar" "$work/log"; } \
  || fail "fresh install: images not loaded: $(cat "$work/log")"
cmp -s "$b/config/coglossa.yaml" "$b/config/coglossa.example.yaml" || fail "config not created"
cmp -s "$b/config/tiers.yaml" "$b/config/tiers.example.yaml" || fail "config/tiers.yaml not created"
grep -Eq '^COGLOSSA_DB_PASSWORD=[0-9a-f]{48}$' "$b/.env" || fail ".env has no 48-hex password"
[ "$(stat -c %a "$b/.env" 2>/dev/null || stat -f %Lp "$b/.env")" = 600 ] || fail ".env must be mode 600"
{ grep -q "config/coglossa.yaml" "$work/out" && grep -q "docker compose up -d --wait" "$work/out"; } \
  || fail "the next steps are not printed"
grep -q "docker compose up -d --wait --force-recreate migrate api worker" "$work/out" \
  || fail "how to apply a config change is not printed"
echo "ok - a fresh install"

# Review Focus 1: a second run keeps the config, the tiers and .env; the edited tiers are not
# checked against SHA256SUMS (an upgrade copies them in, then runs install.sh)
echo "# edited" >>"$b/config/coglossa.yaml"
echo 'pairs: { "en>yo": B }' >>"$b/config/tiers.yaml"
cp "$b/config/coglossa.yaml" "$work/config.before"
cp "$b/config/tiers.yaml" "$work/tiers.before"
cp "$b/.env" "$work/env.before"
install "$b" || fail "second run: $(cat "$work/out")"
cmp -s "$b/config/coglossa.yaml" "$work/config.before" || fail "second run changed the config"
cmp -s "$b/config/tiers.yaml" "$work/tiers.before" || fail "second run changed config/tiers.yaml"
cmp -s "$b/.env" "$work/env.before" || fail "second run changed .env"
echo "ok - a second run keeps the config, the tiers and .env"

# Review Focus 2: a damaged or incomplete bundle is refused before anything is loaded
b=$(new_bundle)
echo x >>"$b/images/scorer.tar"
if install "$b"; then fail "a damaged bundle was installed"; fi
grep -q "^error: the bundle is damaged or incomplete (.*images/scorer.tar" "$work/out" \
  || fail "damaged: $(cat "$work/out")"
grep -q "^load" "$work/log" && fail "damaged: an image was loaded"
[ -e "$b/.env" ] && fail "damaged: .env was created"
echo "ok - a damaged bundle is refused"

b=$(new_bundle)
rm "$b/images/app.tar"
if install "$b"; then fail "an incomplete bundle was installed"; fi
grep -q "^error: the bundle is damaged or incomplete (.*images/app.tar" "$work/out" \
  || fail "incomplete: $(cat "$work/out")"
echo "ok - an incomplete bundle is refused"

b=$(new_bundle)
rm "$b/SHA256SUMS"
if install "$b"; then fail "a bundle without SHA256SUMS was installed"; fi
grep -q "^error: SHA256SUMS is missing" "$work/out" || fail "no SHA256SUMS: $(cat "$work/out")"
echo "ok - a bundle without checksums is refused"

b=$(new_bundle)
if STUB_COMPOSE_VERSION=2.20.3 install "$b"; then fail "an old Compose was accepted"; fi
grep -q "^error: Docker Compose 2.20.3 is too old; Coglossa needs 2.24.0 or newer" "$work/out" \
  || fail "old compose: $(cat "$work/out")"
STUB_COMPOSE_VERSION=v5.1.3 install "$b" || fail "Compose v5.1.3 was refused: $(cat "$work/out")"
echo "ok - the Compose version is checked"

b=$(new_bundle)
install "$b" --gpu || fail "--gpu: $(cat "$work/out")"
install "$b" --gpu || fail "--gpu twice: $(cat "$work/out")"
[ "$(grep -c '^COMPOSE_FILE=compose.yaml:compose.gpu.yaml$' "$b/.env")" = 1 ] || fail "--gpu: COMPOSE_FILE once"
echo "ok - --gpu adds the GPU compose file once"

# an edited .env without its last newline: COMPOSE_FILE goes on a line of its own
b=$(new_bundle)
install "$b" || fail "no newline: first run: $(cat "$work/out")"
printf 'GPU_A_KEY=secret' >>"$b/.env"
install "$b" --gpu || fail "no newline: --gpu: $(cat "$work/out")"
{ grep -qx 'GPU_A_KEY=secret' "$b/.env" && grep -qx 'COMPOSE_FILE=compose.yaml:compose.gpu.yaml' "$b/.env"; } \
  || fail "no newline: COMPOSE_FILE joined the last line: $(cat "$b/.env")"
echo "ok - --gpu after a last line without a newline"

# --gpu over a COMPOSE_FILE the operator chose: stop and say what to change, before loading
b=$(new_bundle)
install "$b" || fail "other COMPOSE_FILE: first run: $(cat "$work/out")"
echo 'COMPOSE_FILE=compose.yaml:compose.site.yaml' >>"$b/.env"
cp "$b/.env" "$work/env.before"
if install "$b" --gpu; then fail "--gpu was accepted over another COMPOSE_FILE"; fi
grep -q "^error: .env has COMPOSE_FILE=compose.yaml:compose.site.yaml.*COMPOSE_FILE=compose.yaml:compose.site.yaml:compose.gpu.yaml" "$work/out" \
  || fail "other COMPOSE_FILE: $(cat "$work/out")"
if grep -q "^load" "$work/log"; then fail "other COMPOSE_FILE: an image was loaded"; fi
cmp -s "$b/.env" "$work/env.before" || fail "other COMPOSE_FILE: .env changed"
echo "ok - --gpu stops over another COMPOSE_FILE"

# ... but carries on when that COMPOSE_FILE already has the GPU file, and sets the plain default
b=$(new_bundle)
install "$b" || fail "gpu in COMPOSE_FILE: first run: $(cat "$work/out")"
echo 'COMPOSE_FILE="compose.yaml:compose.gpu.yaml:compose.site.yaml"' >>"$b/.env"
cp "$b/.env" "$work/env.before"
install "$b" --gpu || fail "gpu in COMPOSE_FILE: $(cat "$work/out")"
cmp -s "$b/.env" "$work/env.before" || fail "gpu in COMPOSE_FILE: .env changed"
b=$(new_bundle)
install "$b" || fail "default COMPOSE_FILE: first run: $(cat "$work/out")"
echo 'COMPOSE_FILE=compose.yaml' >>"$b/.env"
install "$b" --gpu || fail "default COMPOSE_FILE: $(cat "$work/out")"
[ "$(grep '^COMPOSE_FILE=' "$b/.env")" = "COMPOSE_FILE=compose.yaml:compose.gpu.yaml" ] \
  || fail "default COMPOSE_FILE: not set to the GPU files: $(cat "$b/.env")"
grep -Eq '^COGLOSSA_DB_PASSWORD=[0-9a-f]{48}$' "$b/.env" || fail "default COMPOSE_FILE: password lost"
[ "$(stat -c %a "$b/.env" 2>/dev/null || stat -f %Lp "$b/.env")" = 600 ] || fail "default COMPOSE_FILE: .env must stay mode 600"
echo "ok - --gpu keeps a COMPOSE_FILE with the GPU file and replaces the plain default"

# compose also reads "export COMPOSE_FILE=" and indented lines: --gpu must not add a second
# line that overrides them
b=$(new_bundle)
install "$b" || fail "export COMPOSE_FILE: first run: $(cat "$work/out")"
echo 'export COMPOSE_FILE=compose.yaml:compose.site.yaml' >>"$b/.env"
cp "$b/.env" "$work/env.before"
if install "$b" --gpu; then fail "--gpu was accepted over an exported COMPOSE_FILE"; fi
grep -q "^error: .env has COMPOSE_FILE=compose.yaml:compose.site.yaml," "$work/out" \
  || fail "export COMPOSE_FILE: $(cat "$work/out")"
cmp -s "$b/.env" "$work/env.before" || fail "export COMPOSE_FILE: .env changed"
# the last line is the one compose uses: a plain default after a GPU line is still replaced
b=$(new_bundle)
install "$b" || fail "two COMPOSE_FILE lines: first run: $(cat "$work/out")"
printf '%s\n' 'COMPOSE_FILE=./compose.yaml:./compose.gpu.yaml' ' COMPOSE_FILE = "compose.yaml" # default' >>"$b/.env"
install "$b" --gpu || fail "two COMPOSE_FILE lines: $(cat "$work/out")"
[ "$(grep -c 'COMPOSE_FILE=compose.yaml:compose.gpu.yaml$' "$b/.env")" = 2 ] \
  || fail "two COMPOSE_FILE lines: the last is not the GPU files: $(cat "$b/.env")"
echo "ok - --gpu reads COMPOSE_FILE as compose does (export, blanks, quotes, the last line)"

# only the archives SHA256SUMS lists are loaded; an extra one is named and skipped
b=$(new_bundle)
echo extra >"$b/images/extra.tar"
install "$b" || fail "unlisted archive: $(cat "$work/out")"
if grep -q "extra.tar" "$work/log"; then fail "unlisted archive: images/extra.tar was loaded"; fi
{ grep -qx "load -i images/app.tar" "$work/log" && grep -qx "load -i images/scorer.tar" "$work/log"; } \
  || fail "unlisted archive: the listed images were not loaded: $(cat "$work/log")"
grep -q "^warning: images/extra.tar is not in SHA256SUMS" "$work/out" \
  || fail "unlisted archive: no warning: $(cat "$work/out")"
echo "ok - an archive SHA256SUMS does not list is not loaded"

# a SHA256SUMS line that leaves images/ is checked but never loaded
b=$(new_bundle)
echo outside >"$b/outside.tar"
(cd "$b" && sha256 outside.tar | sed 's#  outside#  ./images/../outside#' >>SHA256SUMS)
install "$b" || fail "path outside images/: $(cat "$work/out")"
if grep -q "outside" "$work/log"; then fail "path outside images/: it was loaded: $(cat "$work/log")"; fi
echo "ok - a SHA256SUMS entry outside images/ is not loaded"

# SHA256SUMS written in binary mode (`*` before the name) and without the ./ prefix
b=$(new_bundle)
sed -E 's#^([0-9a-f]+)  \./#\1 *#' "$b/SHA256SUMS" >"$work/sums" && cp "$work/sums" "$b/SHA256SUMS"
grep -q ' \*images/app.tar$' "$b/SHA256SUMS" || fail "binary mode: the test's SHA256SUMS was not rewritten"
install "$b" || fail "binary mode: $(cat "$work/out")"
{ grep -qx "load -i images/app.tar" "$work/log" && grep -qx "load -i images/scorer.tar" "$work/log"; } \
  || fail "binary mode: images not loaded: $(cat "$work/log")"
if grep -q "^warning" "$work/out"; then fail "binary mode: $(cat "$work/out")"; fi
echo "ok - a SHA256SUMS in binary mode lists the archives too"

# --gpu needs the CUDA scorer; a bundle built with the CPU one says so before anything else
b=$(new_bundle cpu)
if install "$b" --gpu; then fail "--gpu was accepted with the CPU scorer"; fi
grep -qx "error: this bundle's scorer is the CPU build; build the bundle with SCORER=cuda for --gpu" "$work/out" \
  || fail "cpu scorer: $(cat "$work/out")"
grep -q "^load" "$work/log" && fail "cpu scorer: an image was loaded"
install "$b" || fail "cpu scorer without --gpu: $(cat "$work/out")"
b=$(new_bundle cuda)
install "$b" --gpu || fail "cuda scorer: $(cat "$work/out")"
echo "ok - --gpu is refused on a bundle with the CPU scorer"

# an interrupted install (config, no .env) is completed without touching the config
b=$(new_bundle)
cp "$b/config/coglossa.example.yaml" "$b/config/coglossa.yaml"
echo "# mine" >>"$b/config/coglossa.yaml"
cp "$b/config/coglossa.yaml" "$work/config.before"
install "$b" || fail "interrupted: $(cat "$work/out")"
cmp -s "$b/config/coglossa.yaml" "$work/config.before" || fail "interrupted: config changed"
grep -Eq '^COGLOSSA_DB_PASSWORD=[0-9a-f]{48}$' "$b/.env" || fail "interrupted: no .env"
echo "ok - an interrupted install keeps the config and creates .env"

# a hardened host's umask 077 must not leave the config unreadable by the containers' uid 10001
b=$(new_bundle)
(umask 077 && install "$b") || fail "umask 077: $(cat "$work/out")"
[ "$(find "$b/config" ! -perm -o=r)" = "" ] || fail "umask 077: config/coglossa.yaml is not world-readable"
[ "$(stat -c %a "$b/.env" 2>/dev/null || stat -f %Lp "$b/.env")" = 600 ] || fail "umask 077: .env must stay mode 600"
echo "ok - under umask 077 the config stays readable by the containers"

# bench data extracted under umask 077 (tar applies it) must become readable by the worker
b=$(new_bundle)
(umask 077 && mkdir -p "$b/bench/flores200/flores200_dataset/devtest" \
  && echo hello >"$b/bench/flores200/flores200_dataset/devtest/eng_Latn.devtest")
(umask 077 && install "$b") || fail "bench, umask 077: $(cat "$work/out")"
[ "$(find "$b/bench" ! -perm -o=r)" = "" ] || fail "umask 077: a file under bench/ is not world-readable"
[ "$(find "$b/bench" -type d ! -perm -o=x)" = "" ] || fail "umask 077: a directory under bench/ is not world-searchable"
echo "ok - under umask 077 the bench data becomes readable by the worker"

b=$(new_bundle)
(cd / && "$b/install.sh" --help) >"$work/out" 2>&1 || fail "--help failed: $(cat "$work/out")"
grep -q "Usage: ./install.sh" "$work/out" || fail "--help text: $(cat "$work/out")"
echo "ok - --help works from any directory"

b=$(new_bundle)
if install "$b" --bogus; then fail "an unknown option was accepted"; fi
grep -q "^error: unknown option --bogus" "$work/out" || fail "usage: $(cat "$work/out")"
echo "ok - unknown options are refused"

echo "install.sh: all tests passed"
