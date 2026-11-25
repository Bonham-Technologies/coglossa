"""Checks the scorer's locked torch can be installed for every build the repo makes (CGL-76):
the CPU image (download.pytorch.org/whl/cpu) and the CUDA image, bundle and hosted preview
(download.pytorch.org/whl/<CUDA_INDEX>). The scorer Dockerfile installs
`torch==<version in scorer/uv.lock>+<index>`, so a torch release that has no wheel on the CUDA
index fails only when someone builds the GPU image, which CI does not. This check needs network:
it reads the index pages, not the wheels, and builds nothing.

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
CUDA_INDEX_SOURCES = {
    "Makefile": re.compile(r"^CUDA_INDEX \?= (\S+)$", re.M),
    "deploy/bundle.sh": re.compile(r"^CUDA_INDEX=\$\{CUDA_INDEX:-([^}]+)\}", re.M),
    "deploy/hosted/modal/app.py": re.compile(r'^TORCH_INDEX = "([^"]+)"', re.M),
}


def locked_torch(lock_text: str) -> str:
    """The torch version scorer/uv.lock pins for Linux (its entry from download.pytorch.org),
    without the local `+cpu` part, as the scorer Dockerfile reads it."""
    lock = tomllib.loads(lock_text)
    versions = [
        package["version"].split("+")[0]
        for package in lock["package"]
        if package["name"] == "torch"
        and urllib.parse.urlparse(package["source"].get("registry", "")).hostname
        == "download.pytorch.org"
    ]
    if not versions:
        raise ValueError("scorer/uv.lock has no Linux torch entry from download.pytorch.org")
    return versions[0]


def cuda_indexes(texts: dict[str, str]) -> dict[str, str | None]:
    """The CUDA index each source names, or None where its line is missing."""
    found = {}
    for name, pattern in CUDA_INDEX_SOURCES.items():
        match = pattern.search(texts[name])
        found[name] = match.group(1) if match else None
    return found


def has_wheel(listing: str, version: str, index: str) -> bool:
    pattern = WHEEL.format(version=re.escape(version), index=re.escape(index))
    return re.search(pattern, listing) is not None


def problems(lock_text: str, texts: dict[str, str], fetch) -> list[str]:
    found = []
    named = cuda_indexes(texts)
    found += [f"{name}: no CUDA index line found" for name, index in named.items() if not index]
    indexes = {index for index in named.values() if index}
    if len(indexes) > 1:
        found.append(f"the CUDA index differs between files: {named}")
    try:
        version = locked_torch(lock_text)
    except ValueError as error:
        return [*found, str(error)]
    for index in ["cpu", *sorted(indexes)]:
        if not has_wheel(fetch(index), version, index):
            found.append(
                f"torch {version} (scorer/uv.lock) has no cp312 Linux x86_64 wheel on "
                f"{INDEX_URL.format(index=index)}: hold torch below it in scorer/pyproject.toml "
                "or move the CUDA index"
            )
    return found


def fetch_listing(index: str) -> str:
    with urllib.request.urlopen(INDEX_URL.format(index=index), timeout=60) as response:
        return response.read().decode("utf-8")


FIXTURE_LOCK = """
[[package]]
name = "torch"
version = "2.15.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "torch"
version = "{version}+cpu"
source = { registry = "https://download.pytorch.org/whl/cpu" }
"""
FIXTURE_LISTING = """
<a href="/whl/{index}/torch-2.14.1%2B{index}-cp312-cp312-manylinux_2_28_x86_64.whl#sha256=0">x</a>
<a href="/whl/{index}/torch-2.14.0%2B{index}-cp311-cp311-manylinux_2_28_x86_64.whl#sha256=0">x</a>
<a href="/whl/{index}/torch-2.15.0%2B{index}-cp312-cp312-win_amd64.whl#sha256=0">x</a>
"""


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
    cases = [
        # (what, lock version, texts, expect a problem)
        ("a version with a wheel", "2.14.1", same, False),
        ("a version with no cp312 Linux wheel", "2.15.0", same, True),
        ("a version with wheels for another Python only", "2.14.0", same, True),
        (
            "files naming different CUDA indexes",
            "2.14.1",
            fixture_texts("cu126", "cu126", "cu132"),
            True,
        ),
        (
            "a file with no CUDA index line",
            "2.14.1",
            {**same, "Makefile": "CUDA_INDEX = x\n"},
            True,
        ),
    ]
    found = []
    for what, version, texts, expected in cases:
        lock = FIXTURE_LOCK.replace("{version}", version)
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
        print("ok: the locked torch has a wheel on every index the scorer builds use")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
