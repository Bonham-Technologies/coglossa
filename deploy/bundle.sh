#!/usr/bin/env bash
# Build the offline bundle (spec §10) on a machine with Docker and internet access.
#   deploy/bundle.sh --weights DIR        the full bundle (make weights fetches DIR once)
#   deploy/bundle.sh --no-weights         without scorer weights (tests; the demo still works)
# Options: --version V (default: the tag at HEAD, else <coglossa version>-g<sha>), --out DIR
# (default: dist), --scorer cuda|cpu (default: cuda, which also runs on CPU), --flores DIR
# (FLORES-200 for bench, as `make flores` fetches it; shipped in bench/flores200/), --allow-dirty,
# --keep-stage (keep the unpacked directory next to the tarball).
# The CUDA scorer image is tagged coglossa/scorer:<version>-cuda, the CPU one
# coglossa/scorer:<version>; the bundle's compose files name the one it ships.
# Disk: the weights (about 25 GB) are held three times at the peak: in --weights, copied into the
# stage under --out, and in the tarball. A failed build removes the stage, so it leaves nothing
# under --out but an earlier build's tarball.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
die() { echo "error: $*" >&2; exit 1; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

PLATFORM=linux/amd64
POSTGRES_IMAGE=postgres:16-alpine
SYFT_IMAGE=anchore/syft:v1.18.1
CUDA_INDEX=${CUDA_INDEX:-cu126} # keep it in step with the Makefile

version="" out="$repo/dist" weights="" no_weights=false scorer=cuda flores="" allow_dirty=false keep=false
while [ $# -gt 0 ]; do
  case $1 in
    --version) version=${2:?--version needs a value}; shift ;;
    --out) out=${2:?--out needs a directory}; shift ;;
    --weights) weights=${2:?--weights needs a directory}; shift ;;
    --no-weights) no_weights=true ;;
    --scorer) scorer=${2:?--scorer needs cpu or cuda}; shift ;;
    --flores) flores=${2:?--flores needs a directory}; shift ;;
    --allow-dirty) allow_dirty=true ;;
    --keep-stage) keep=true ;;
    *) die "unknown option $1 (see the comment at the top of deploy/bundle.sh)" ;;
  esac
  shift
done
case $scorer in cpu) torch_index=cpu ;; cuda) torch_index=$CUDA_INDEX ;; *) die "--scorer is cpu or cuda" ;; esac
if $no_weights; then
  [ -z "$weights" ] || die "pass --weights DIR or --no-weights, not both"
else
  [ -n "$weights" ] || die "pass --weights DIR (make weights fetches them) or --no-weights"
  weights=$(cd "$weights" && pwd)
  (cd "$repo/scorer" && uv run coglossa-scorer-weights verify "$weights") \
    || die "the weights in $weights are incomplete (see above)"
fi
if [ -n "$flores" ]; then # CC BY-SA 4.0: the data ships only with its licence and attribution
  for f in flores200_dataset/dev flores200_dataset/devtest LICENSE.txt ATTRIBUTION.txt; do
    [ -e "$flores/$f" ] || die "no $f in $flores (make flores fetches FLORES-200 there)"
  done
  flores=$(cd "$flores" && pwd)
fi
if ! $allow_dirty && [ -n "$(git -C "$repo" status --porcelain)" ]; then
  die "the working tree has uncommitted changes; commit them or pass --allow-dirty"
fi
if [ -z "$version" ]; then
  if tag=$(git -C "$repo" describe --tags --exact-match 2>/dev/null); then
    version=${tag#v}
  else
    base=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$repo/engine/coglossa/__init__.py")
    version="$base-g$(git -C "$repo" rev-parse --short HEAD)"
  fi
fi

name="coglossa-$version"
mkdir -p "$out"
out=$(cd "$out" && pwd)
stage="$out/$name"
sums="$out/SHA256SUMS.$$"
scratch="" tarball="" built=false
cleanup() { # on failure, nothing half-built is left under --out
  rm -rf "$sums" ${scratch:+"$scratch"}
  if ! $built; then
    rm -rf "$stage"
    if [ -n "$tarball" ]; then rm -f "$out/$tarball" "$out/$tarball.sha256"; fi
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
rm -rf "$stage"
mkdir -p "$stage/images" "$stage/config" "$stage/sbom"
app="coglossa/app:$version"
scorer_image="coglossa/scorer:$version"
if [ "$scorer" = cuda ]; then scorer_image+="-cuda"; fi

echo "== images ($PLATFORM, scorer torch: $torch_index)"
docker build --platform "$PLATFORM" -f "$repo/deploy/docker/app.Dockerfile" \
  --build-arg VERSION="$version" -t "$app" "$repo"
docker build --platform "$PLATFORM" -f "$repo/deploy/docker/scorer.Dockerfile" \
  --build-arg VERSION="$version" --build-arg TORCH_INDEX="$torch_index" -t "$scorer_image" "$repo"
"$repo/deploy/tests/check-app-image.sh" "$app"
"$repo/deploy/tests/check-scorer-image.sh" "$scorer_image" "$torch_index"
docker pull --platform "$PLATFORM" "$POSTGRES_IMAGE"
docker save -o "$stage/images/app.tar" "$app"
docker save -o "$stage/images/scorer.tar" "$scorer_image"
docker save -o "$stage/images/postgres.tar" "$POSTGRES_IMAGE"

echo "== files"
# every compose file names the one scorer image the bundle ships, compose.gpu.yaml's -cuda included
for f in compose.yaml compose.gpu.yaml compose.demo.yaml; do
  sed -e "s|coglossa/scorer:\${COGLOSSA_VERSION:-dev}\(-cuda\)\{0,1\}|$scorer_image|" \
    -e "s/\${COGLOSSA_VERSION:-dev}/$version/g" "$repo/deploy/$f" >"$stage/$f"
done
cp "$repo/deploy/config/coglossa.example.yaml" "$repo/deploy/config/coglossa.demo.yaml" \
  "$repo/deploy/config/bench.example.yaml" "$stage/config/"
# install.sh copies it to config/tiers.yaml once: an edited copy is not in SHA256SUMS
cp "$repo/engine/coglossa/scoring/tiers.yaml" "$stage/config/tiers.example.yaml"
cp "$repo/deploy/install.sh" "$repo/deploy/backup.sh" "$repo/deploy/restore.sh" "$stage/"
chmod 755 "$stage"/*.sh
cp "$repo/docs/operator-guide.md" "$stage/operator-guide.md"
echo "$version" >"$stage/VERSION"
if ! $no_weights; then
  mkdir "$stage/models"
  for model in metricx-24-hybrid-xl-v2p6 metricx-24-hybrid-large-v2p6 mt5-xl labse; do
    cp -R "$weights/$model" "$stage/models/"
  done
  cp "$weights/weights.json" "$stage/models/"
  # the scorer container runs as uid 10001 and reads these through a read-only bind mount
  chmod -R a+rX "$stage/models"
fi
if [ -n "$flores" ]; then
  mkdir -p "$stage/bench/flores200"
  cp -R "$flores/flores200_dataset" "$flores/LICENSE.txt" "$flores/ATTRIBUTION.txt" "$stage/bench/flores200/"
  chmod -R a+rX "$stage/bench" # the worker (uid 10001) reads it through a read-only bind mount
fi

echo "== SBOMs (CycloneDX)"
# syft runs as the caller (so the SBOMs are theirs). It unpacks every layer into /tmp, which is
# 11 GB for the CUDA scorer, so /tmp is a disk-backed directory under --out, not a tmpfs (RAM).
scratch=$(mktemp -d "$out/.syft-tmp.XXXXXX")
syft=(docker run --rm --user "$(id -u):$(id -g)" -v "$scratch:/tmp" -e HOME=/tmp -e XDG_CACHE_HOME=/tmp
  -v "$stage/sbom:/sbom")
# sbom NAME SOURCE MOUNT [SYFT-ARG...]: no -q, which would hide syft's errors; an empty file is an error too
sbom() {
  local name=$1 source=$2 mount=$3
  shift 3
  if ! "${syft[@]}" -v "$mount" "$SYFT_IMAGE" "$source" "$@" -o "cyclonedx-json=/sbom/$name.cdx.json"; then
    die "the SBOM for $name failed; see the message above"
  fi
  if [ ! -s "$stage/sbom/$name.cdx.json" ]; then
    die "the SBOM for $name is empty; see the message above"
  fi
}
for image in app scorer postgres; do
  sbom "$image" "docker-archive:/images/$image.tar" "$stage/images:/images:ro"
done
sbom web dir:/src "$repo/web:/src:ro" --exclude './node_modules/**'

echo "== manifest and checksums"
python3 - "$stage" "$version" "$(git -C "$repo" rev-parse HEAD)" "$PLATFORM" "$scorer" "$torch_index" \
  "$app" "$scorer_image" "$POSTGRES_IMAGE" <<'PY'
import datetime, json, subprocess, sys
from pathlib import Path
stage, version, commit, platform, scorer, torch_index, *images = sys.argv[1:]
def inspect(image):
    out = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, check=True)
    info = json.loads(out.stdout)[0]
    return {"id": info["Id"], "repo_digests": info.get("RepoDigests") or []}
files = ["images/app.tar", "images/scorer.tar", "images/postgres.tar"]
weights = Path(stage, "models", "weights.json")
flores = Path(stage, "bench", "flores200")
manifest = {
    "version": version, "commit": commit, "platform": platform,
    "built_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    "scorer": {"build": scorer, "torch_index": torch_index},
    "images": [{"file": f, "tag": i, **inspect(i)} for f, i in zip(files, images)],
    "weights": json.loads(weights.read_text()) if weights.is_file() else None,
    "bench": {"flores200": flores.is_dir()},
}
Path(stage, "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
PY
(cd "$stage" && find . -type f -print0 | sort -z | xargs -0 bash -c \
  'if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi' _ >"$sums")
mv "$sums" "$stage/SHA256SUMS"

echo "== tarball"
tarball="$name-linux-amd64.tar"
# Every member is owned by root (uid and gid 0, no names): `sudo tar -xf` keeps the archive's
# owners, and the builder's uid may be some other account on the target host.
if tar --version 2>/dev/null | grep -q 'GNU tar'; then
  owner=(--owner=0 --group=0 --numeric-owner)
else # bsdtar (macOS)
  owner=(--uid 0 --gid 0 --numeric-owner)
fi
(cd "$out" && COPYFILE_DISABLE=1 tar "${owner[@]}" -cf "$tarball" "$name" && sha256 "$tarball" >"$tarball.sha256")
built=true
$keep || rm -rf "$stage"
echo "Built $out/$tarball ($(du -h "$out/$tarball" | cut -f1))"
