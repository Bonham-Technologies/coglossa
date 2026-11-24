"""Checks the scorer's locked torch can be installed for every build the repo makes (CGL-76):
the CPU image (download.pytorch.org/whl/cpu) and the CUDA image, bundle and hosted preview
(download.pytorch.org/whl/<CUDA_INDEX>). scorer/uv.lock holds a torch entry from each of those
indexes (CGL-50), both at the same version, and the index must still serve that entry's cp312
Linux x86_64 wheel with the sha256 the lock records: the images install only that file, and CI
builds only the CPU one. This check needs network: it reads the index pages, not the wheels, and
builds nothing.

It also checks the three places that name the CUDA index agree: the Makefile's `CUDA_INDEX`,
deploy/bundle.sh's default and the Modal app's `TORCH_INDEX`. A self-test runs first on fixtures,
so a check that stops catching anything fails too.
Usage: python3 deploy/tests/check_torch_wheel.py"""

import re
import sys
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX_URL = "https://download.pytorch.org/whl/{index}/torch/"
# The scorer image's Python and platform (deploy/docker/scorer.Dockerfile, linux/amd64).
WHEEL = r"torch-{version}(?:\+|%2B){index}-cp312-cp312-manylinux[\w.]*_x86_64\.whl"
LINK = WHEEL + r"#sha256={sha256}"
CUDA_INDEX_SOURCES = {
    "Makefile": re.compile(r"^CUDA_INDEX \?= (\S+)$", re.M),
    "deploy/bundle.sh": re.compile(r"^CUDA_INDEX=\$\{CUDA_INDEX:-([^}]+)\}", re.M),
    "deploy/hosted/modal/app.py": re.compile(r'^TORCH_INDEX = "([^"]+)"', re.M),
}


def locked_builds(lock_text: str) -> dict[str, tuple[str, str | None]]:
    """Each torch scorer/uv.lock takes from download.pytorch.org, by index (cpu, cu126, ...): its
    version without the local `+<index>` part, and the sha256 the lock records for its cp312
    Linux x86_64 wheel (None when it has no such wheel)."""
    lock = tomllib.loads(lock_text)
    builds = {}
    for package in lock["package"]:
        registry = package["source"].get("registry", "")
        if (
            package["name"] != "torch"
            or urllib.parse.urlparse(registry).hostname != "download.pytorch.org"
        ):
            continue
        index = registry.rstrip("/").rsplit("/", 1)[-1]
        version = package["version"].split("+")[0]
        pattern = re.compile(WHEEL.format(version=re.escape(version), index=re.escape(index)))
        hashes = [
            wheel.get("hash", "").removeprefix("sha256:")
            for wheel in package.get("wheels", [])
            if pattern.search(wheel["url"])
        ]
        builds[index] = (version, hashes[0] if hashes and hashes[0] else None)
    return builds


def cuda_indexes(texts: dict[str, str]) -> dict[str, str | None]:
    """The CUDA index each source names, or None where its line is missing."""
    found = {}
    for name, pattern in CUDA_INDEX_SOURCES.items():
        match = pattern.search(texts[name])
        found[name] = match.group(1) if match else None
    return found


def has_wheel(listing: str, version: str, index: str, sha256: str) -> bool:
    pattern = LINK.format(version=re.escape(version), index=re.escape(index), sha256=sha256)
    return re.search(pattern, listing) is not None


def problems(lock_text: str, texts: dict[str, str], fetch) -> list[str]:
    found = []
    named = cuda_indexes(texts)
    found += [f"{name}: no CUDA index line found" for name, index in named.items() if not index]
    indexes = {index for index in named.values() if index}
    if len(indexes) > 1:
        found.append(f"the CUDA index differs between files: {named}")
    builds = locked_builds(lock_text)
    for index in ["cpu", *sorted(indexes)]:
        if index not in builds:
            found.append(
                f"scorer/uv.lock has no torch from download.pytorch.org/whl/{index}: the "
                f"scorer/pyproject.toml group or extra `{index}` and its torch source must "
                "match the CUDA index, then run uv lock"
            )
            continue
        version, sha256 = builds[index]
        if sha256 is None:
            found.append(f"scorer/uv.lock has no hashed cp312 Linux x86_64 torch wheel for {index}")
        elif not has_wheel(fetch(index), version, index, sha256):
            found.append(
                f"torch {version}+{index} (scorer/uv.lock) has no cp312 Linux x86_64 wheel with "
                f"the locked sha256 on {INDEX_URL.format(index=index)}: hold torch below it in "
                "scorer/pyproject.toml or move the CUDA index, then run uv lock"
            )
    versions = {version for version, _ in builds.values()}
    if len(versions) > 1:
        found.append(f"scorer/uv.lock locks different torch versions per index: {builds}")
    return found


def fetch_listing(index: str) -> str:
    with urllib.request.urlopen(INDEX_URL.format(index=index), timeout=60) as response:
        return response.read().decode("utf-8")


FIXTURE_HASH = "ab" * 32
FIXTURE_ENTRY = """
[[package]]
name = "torch"
version = "{version}+{index}"
source = { registry = "https://download.pytorch.org/whl/{index}" }
wheels = [
    { url = "https://download-r2.pytorch.org/whl/{index}/torch-{version}%2B{index}-cp312-cp312-manylinux_2_28_x86_64.whl", hash = "sha256:{hash}" },
]
"""
FIXTURE_PYPI = """
[[package]]
name = "torch"
version = "2.15.0"
source = { registry = "https://pypi.org/simple" }
"""
FIXTURE_LISTING = f"""
<a href="/whl/{{index}}/torch-2.14.1%2B{{index}}-cp312-cp312-manylinux_2_28_x86_64.whl#sha256={FIXTURE_HASH}">x</a>
<a href="/whl/{{index}}/torch-2.14.0%2B{{index}}-cp311-cp311-manylinux_2_28_x86_64.whl#sha256={FIXTURE_HASH}">x</a>
<a href="/whl/{{index}}/torch-2.15.0%2B{{index}}-cp312-cp312-win_amd64.whl#sha256={FIXTURE_HASH}">x</a>
"""


def fixture_lock(*builds: tuple[str, str, str]) -> str:
    """A lock with PyPI's torch and one entry per (index, version, wheel sha256)."""
    entries = [
        FIXTURE_ENTRY.replace("{index}", index)
        .replace("{version}", version)
        .replace("{hash}", sha256)
        for index, version, sha256 in builds
    ]
    return FIXTURE_PYPI + "".join(entries)


def fixture_texts(make: str, bundle: str, modal: str) -> dict[str, str]:
    return {
        "Makefile": f"CUDA_INDEX ?= {make}\n",
        "deploy/bundle.sh": f"CUDA_INDEX=${{CUDA_INDEX:-{bundle}}} # keep it in step\n",
        "deploy/hosted/modal/app.py": f'TORCH_INDEX = "{modal}"  # the tested build\n',
    }


def self_test() -> list[str]:
    def fetch(index: str) -> str:
        return FIXTURE_LISTING.replace("{index}", index)

    same = fixture_texts("cu126", "cu126", "cu126")
    good = fixture_lock(("cpu", "2.14.1", FIXTURE_HASH), ("cu126", "2.14.1", FIXTURE_HASH))
    other = "cd" * 32
    cases = [
        # (what, lock, texts, expect a problem)
        ("both builds with their wheel", good, same, False),
        (
            "a version with no cp312 Linux wheel",
            fixture_lock(("cpu", "2.15.0", FIXTURE_HASH), ("cu126", "2.15.0", FIXTURE_HASH)),
            same,
            True,
        ),
        (
            "a version with wheels for another Python only",
            fixture_lock(("cpu", "2.14.0", FIXTURE_HASH), ("cu126", "2.14.0", FIXTURE_HASH)),
            same,
            True,
        ),
        (
            "a locked hash the index does not serve",
            fixture_lock(("cpu", "2.14.1", FIXTURE_HASH), ("cu126", "2.14.1", other)),
            same,
            True,
        ),
        (
            "a lock without the CUDA build",
            fixture_lock(("cpu", "2.14.1", FIXTURE_HASH)),
            same,
            True,
        ),
        ("a CUDA index the lock has no build for", good, fixture_texts(*["cu128"] * 3), True),
        (
            "files naming different CUDA indexes",
            good,
            fixture_texts("cu126", "cu126", "cu132"),
            True,
        ),
        ("a file with no CUDA index line", good, {**same, "Makefile": "CUDA_INDEX = x\n"}, True),
    ]
    found = []
    for what, lock, texts, expected in cases:
        if bool(problems(lock, texts, fetch)) != expected:
            found.append(f"self-test: {what} {'not ' if expected else ''}reported")
    if not problems("[[package]]\nname = 'numpy'\nversion = '1'\nsource = {}\n", same, fetch):
        found.append("self-test: a lock without torch not reported")
    return found


def main() -> int:
    found = self_test()
    if not found:
        texts = {name: (ROOT / name).read_text(encoding="utf-8") for name in CUDA_INDEX_SOURCES}
        lock = (ROOT / "scorer" / "uv.lock").read_text(encoding="utf-8")
        found = problems(lock, texts, fetch_listing)
    for problem in found:
        print(f"FAIL: {problem}", file=sys.stderr)
    if not found:
        print("ok: every locked torch build is served by its index with the locked hash")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
