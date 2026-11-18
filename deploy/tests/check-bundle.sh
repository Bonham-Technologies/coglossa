#!/usr/bin/env bash
# Checks a built bundle: its transfer checksum, its own SHA256SUMS, every file the operator
# guide promises, the versions in the compose files, the images' tags and platforms (read from
# each image), the SBOMs, and that compose.yaml resolves alone and with each shipped overlay and
# names only shipped images. Usage: deploy/tests/check-bundle.sh coglossa-V-linux-amd64.tar
set -euo pipefail
tar_file=$(cd "$(dirname "${1:?usage: check-bundle.sh TAR}")" && pwd)/$(basename "$1")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "FAIL: $(basename "$tar_file"): $*" >&2; exit 1; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

(cd "$(dirname "$tar_file")" && sha256 -c "$(basename "$tar_file").sha256" >/dev/null) \
  || fail "the .sha256 file does not match"
tar -xf "$tar_file" -C "$work"
dirs=("$work"/coglossa-*)
if [ ${#dirs[@]} -ne 1 ] || [ ! -d "${dirs[0]}" ]; then fail "must extract to one coglossa-<version>/ directory"; fi
b=${dirs[0]}
version=$(cat "$b/VERSION")
[ "$(basename "$b")" = "coglossa-$version" ] || fail "the directory and VERSION disagree"
[ "$(basename "$tar_file")" = "coglossa-$version-linux-amd64.tar" ] || fail "the file name and VERSION disagree"
(cd "$b" && sha256 -c SHA256SUMS >/dev/null) || fail "SHA256SUMS does not verify"
for f in install.sh backup.sh restore.sh; do [ -x "$b/$f" ] || fail "$f is missing or not executable"; done
for f in compose.yaml compose.gpu.yaml compose.demo.yaml config/coglossa.example.yaml \
  config/coglossa.demo.yaml config/tiers.example.yaml config/bench.example.yaml \
  operator-guide.md manifest.json images/app.tar images/scorer.tar images/postgres.tar \
  sbom/app.cdx.json sbom/scorer.cdx.json sbom/postgres.cdx.json sbom/web.cdx.json; do
  [ -s "$b/$f" ] || fail "$f is missing or empty"
done
# the files install.sh creates and the operator edits are not shipped, so SHA256SUMS never names them
for f in config/coglossa.yaml config/tiers.yaml config/bench.yaml .env; do [ ! -e "$b/$f" ] || fail "$f must not be in the bundle"; done
if grep -q 'COGLOSSA_VERSION' "$b"/compose*.yaml; then fail "a compose file still names COGLOSSA_VERSION"; fi
grep -q "coglossa/app:$version" "$b/compose.yaml" || fail "compose.yaml does not name coglossa/app:$version"
[ -z "$(find "$b" -name '._*' -o -name '.DS_Store')" ] || fail "macOS metadata files are in the bundle"
if [ -d "$b/models" ]; then
  [ -z "$(find "$b/models" ! -perm -o=r)" ] || fail "a file under models/ is not world-readable"
  [ -z "$(find "$b/models" -type d ! -perm -o=x)" ] || fail "a directory under models/ is not world-searchable"
fi
if [ -d "$b/bench" ]; then # the worker (uid 10001) reads the FLORES-200 data through a bind mount
  [ -z "$(find "$b/bench" ! -perm -o=r)" ] || fail "a file under bench/ is not world-readable"
  [ -z "$(find "$b/bench" -type d ! -perm -o=x)" ] || fail "a directory under bench/ is not world-searchable"
fi
cmp -s "$b/config/coglossa.example.yaml" "$(dirname "$0")/../config/coglossa.example.yaml" \
  || fail "config/coglossa.example.yaml differs from deploy/config/"
python3 - "$b" "$version" "$tar_file" "$work/shipped" <<'PY' || fail "owners, manifest, image tags or platforms, SBOMs or bench data (see above)"
import json, sys, tarfile
from pathlib import Path
b, version = Path(sys.argv[1]), sys.argv[2]
with tarfile.open(sys.argv[3]) as bundle:  # root-owned, so `sudo tar -xf` maps no local account
    for m in bundle:
        assert (m.uid, m.gid) == (0, 0) and m.uname in ("", "root") and m.gname in ("", "root"), (
            m.name, m.uid, m.gid, m.uname, m.gname)
manifest = json.loads((b / "manifest.json").read_text())
assert manifest["version"] == version, manifest["version"]
assert manifest["platform"] == "linux/amd64"
scorer_tag = {"cpu": f"coglossa/scorer:{version}", "cuda": f"coglossa/scorer:{version}-cuda"}[
    manifest["scorer"]["build"]]
expected = {"app.tar": f"coglossa/app:{version}", "scorer.tar": scorer_tag,
            "postgres.tar": "postgres:16-alpine"}
shipped = []
for archive, tag in expected.items():
    with tarfile.open(b / "images" / archive) as t:
        entries = json.load(t.extractfile("manifest.json"))
        named = [e for e in entries if {tag, f"docker.io/library/{tag}"} & set(e.get("RepoTags") or [])]
        assert named, (archive, [e.get("RepoTags") for e in entries])
        # the platform each image itself records; a multi-platform archive has an entry for each
        configs = [json.load(t.extractfile(e["Config"])) for e in named]
    platforms = sorted({f"{c.get('os')}/{c.get('architecture')}" for c in configs})
    assert manifest["platform"] in platforms, f"{archive}: the image is {', '.join(platforms)}, not {manifest['platform']}"
    shipped += [tag, f"docker.io/library/{tag}"]
    assert any(i["file"] == f"images/{archive}" and i["tag"] == tag for i in manifest["images"]), archive
Path(sys.argv[4]).write_text("\n".join(shipped) + "\n")
for sbom in (b / "sbom").glob("*.cdx.json"):
    assert json.loads(sbom.read_text())["bomFormat"] == "CycloneDX", sbom
if manifest["weights"] is not None:
    for name in ("metricx-24-hybrid-xl-v2p6", "metricx-24-hybrid-large-v2p6", "mt5-xl", "labse"):
        assert (b / "models" / name).is_dir(), name
        assert name in manifest["weights"], name
else:
    assert not (b / "models").exists()
flores = b / "bench" / "flores200"
if manifest["bench"] == {"flores200": True}:  # CC BY-SA 4.0: the attribution travels with the data
    for split in ("dev", "devtest"):
        assert (flores / "flores200_dataset" / split).is_dir(), f"bench/flores200: no {split}"
    for name in ("LICENSE.txt", "ATTRIBUTION.txt"):
        assert (flores / name).is_file() and (flores / name).stat().st_size, f"bench/flores200/{name}"
else:
    assert manifest["bench"] == {"flores200": False}, manifest["bench"]
    assert not (b / "bench").exists(), "bench/ is shipped, but the manifest says flores200: false"
PY
for overlay in "" compose.gpu.yaml compose.demo.yaml; do
  what="compose.yaml${overlay:+ with $overlay}"
  files=(-f compose.yaml)
  if [ -n "$overlay" ]; then files+=(-f "$overlay"); fi
  images=$(cd "$b" && COGLOSSA_DB_PASSWORD=check docker compose --env-file /dev/null "${files[@]}" config --images) \
    || fail "$what does not resolve"
  [ -n "$images" ] || fail "$what names no images"
  for image in $images; do
    grep -qxF "$image" "$work/shipped" || fail "$what names $image, which is not in images/"
  done
done
echo "ok: $(basename "$tar_file")"
