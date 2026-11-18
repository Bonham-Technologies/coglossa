#!/usr/bin/env bash
# Tests bundle.sh and check-bundle.sh with a stub `docker` that writes fake image archives (with a
# chosen platform) and SBOMs; its `compose` calls go to the real docker, whose `config` needs no
# daemon. A failed build must leave nothing under --out; CPU and CUDA bundles must pass
# check-bundle with their own scorer tags; an arm64 image must fail it.
# Usage: deploy/tests/test-bundle.sh (Docker with the Compose plugin; no daemon, no images)
set -euo pipefail
deploy=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
REAL_DOCKER=$(command -v docker) || fail "needs docker with the Compose plugin"
REAL_SHA256SUM=$(command -v sha256sum || true)
export REAL_DOCKER REAL_SHA256SUM

# The files bundle.sh reads, in a repository of their own. The image checks are stubs: the images
# are fakes.
repo=$work/repo
mkdir -p "$repo/deploy/tests" "$repo/engine/coglossa/scoring" "$repo/docs" "$repo/web"
cp "$deploy"/*.sh "$deploy"/compose*.yaml "$repo/deploy/"
cp -R "$deploy/config" "$deploy/docker" "$repo/deploy/"
cp "$deploy/../engine/coglossa/scoring/tiers.yaml" "$repo/engine/coglossa/scoring/"
cp "$deploy/../docs/operator-guide.md" "$repo/docs/"
for f in check-app-image.sh check-scorer-image.sh; do
  printf '#!/bin/sh\nexit 0\n' >"$repo/deploy/tests/$f"
  chmod +x "$repo/deploy/tests/$f"
done

mkdir -p "$work/bin"
cat >"$work/bin/git" <<'EOF'
#!/usr/bin/env bash
case "$*" in *"rev-parse HEAD") echo 0123456789abcdef0123456789abcdef01234567 ;; esac
EOF
cat >"$work/bin/sha256sum" <<'EOF'
#!/usr/bin/env bash
if [ -n "${STUB_SHA_FAIL:-}" ]; then echo "partial"; exit 1; fi
if [ -n "$REAL_SHA256SUM" ]; then exec "$REAL_SHA256SUM" "$@"; else exec shasum -a 256 "$@"; fi
EOF
cat >"$work/bin/docker" <<'EOF'
#!/usr/bin/env bash
case $1 in
  compose) exec "$REAL_DOCKER" "$@" ;;
  build | pull) ;;
  image) echo '[{"Id": "sha256:0", "RepoDigests": []}]' ;; # image inspect
  save) # save -o FILE TAG: a docker-archive of TAG whose config says linux/$STUB_ARCH
    [ -z "${STUB_SAVE_FAIL:-}" ] || exit 1
    python3 - "$3" "$4" <<'PY'
import io, json, os, sys, tarfile
out, tag = sys.argv[1:]
config = {"os": "linux", "architecture": os.environ.get("STUB_ARCH", "amd64")}
manifest = [{"Config": "blobs/sha256/c", "RepoTags": [tag], "Layers": []}]
with tarfile.open(out, "w") as t:
    for name, value in (("blobs/sha256/c", config), ("manifest.json", manifest)):
        data = json.dumps(value).encode()
        info = tarfile.TarInfo(name)
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
PY
    ;;
  run) # syft: -v DIR:/sbom ... -o cyclonedx-json=/sbom/NAME
    sbom="" name=""
    while [ $# -gt 0 ]; do
      case $1 in
        -v) case $2 in *:/sbom) sbom=${2%:/sbom} ;; esac; shift ;;
        cyclonedx-json=/sbom/*) name=${1#cyclonedx-json=/sbom/} ;;
      esac
      shift
    done
    { [ -n "$sbom" ] && [ -n "$name" ]; } || exit 1
    echo '{"bomFormat": "CycloneDX"}' >"$sbom/$name" ;;
  *) echo "stub docker: unexpected: $*" >&2; exit 1 ;;
esac
EOF
chmod +x "$work/bin"/*
export PATH="$work/bin:$PATH"

build() { # bundle.sh into the directory $1, with more options; its output in $work/out
  local out=$1
  shift
  "$repo/deploy/bundle.sh" --no-weights --allow-dirty --version 1.0.0-test --out "$out" "$@" \
    >"$work/out" 2>&1
}
check() { "$deploy/tests/check-bundle.sh" "$1/coglossa-1.0.0-test-linux-amd64.tar" >"$work/out" 2>&1; }

# a failed build leaves no stage, no temporary checksum file and no SBOM scratch
if STUB_SAVE_FAIL=1 build "$work/fail-save"; then fail "a failed docker save was not an error"; fi
[ -z "$(ls -A "$work/fail-save")" ] || fail "a failed docker save left: $(ls -A "$work/fail-save")"
if STUB_SHA_FAIL=1 build "$work/fail-sums" --scorer cpu; then fail "a failed checksum was not an error"; fi
[ -z "$(ls -A "$work/fail-sums")" ] || fail "a failed checksum left: $(ls -A "$work/fail-sums")"
echo "ok - a failed build leaves nothing under --out"

build "$work/cpu" --scorer cpu || fail "CPU bundle: $(cat "$work/out")"
[ "$(ls -A "$work/cpu")" = "$(printf '%s\n' coglossa-1.0.0-test-linux-amd64.tar coglossa-1.0.0-test-linux-amd64.tar.sha256)" ] \
  || fail "CPU bundle: --out holds more than the tarball: $(ls -A "$work/cpu")"
check "$work/cpu" || fail "CPU bundle: $(cat "$work/out")"
echo "ok - a CPU bundle passes check-bundle, every overlay naming coglossa/scorer:1.0.0-test"

build "$work/cuda" --scorer cuda || fail "CUDA bundle: $(cat "$work/out")"
check "$work/cuda" || fail "CUDA bundle: $(cat "$work/out")"
tar -xOf "$work/cuda/coglossa-1.0.0-test-linux-amd64.tar" coglossa-1.0.0-test/compose.yaml \
  | grep -qx '    image: coglossa/scorer:1.0.0-test-cuda' || fail "CUDA bundle: compose.yaml does not name the -cuda tag"
echo "ok - a CUDA bundle tags its scorer coglossa/scorer:1.0.0-test-cuda and passes check-bundle"

STUB_ARCH=arm64 build "$work/arm" --scorer cpu || fail "arm64 bundle: $(cat "$work/out")"
if check "$work/arm"; then fail "check-bundle accepted arm64 images"; fi
grep -q "the image is linux/arm64, not linux/amd64" "$work/out" || fail "arm64: $(cat "$work/out")"
echo "ok - check-bundle reads each image's platform and refuses arm64"

echo "bundle.sh: all tests passed"
