"""Checks every wheel in the scorer image comes from scorer/uv.lock, hash-checked and from one
index (CGL-50). For an air-gapped client the bundle is the supply chain, so the CPU image, the
CUDA image (bundle) and the hosted scorer install only what the lock names, at the sha256 it
records, from the one index it names for each package. Offline; it builds nothing.

- The installs (deploy/docker/scorer.Dockerfile's RUN, ENV and ARG instructions and the Modal
  app's `_SCORER_INSTALL`) use `uv sync --frozen` or a pylock.toml from `uv export --frozen`,
  never `--no-hashes`, an index flag or variable, a constraints file, `uv pip compile` or pip
  itself; `uv pip install` only adds the project itself (`--no-deps .`).
- scorer/pyproject.toml: every extra index is explicit (it supplies only the packages pinned to
  it); torch comes from download.pytorch.org/whl/cpu in the `cpu` group (a default group) and
  from whl/<CUDA_INDEX> in the extra of that name (the Makefile's CUDA_INDEX); the two conflict,
  list the same requirements, and torch is not a base dependency.
- scorer/uv.lock: every package but the project comes from an index (no git, URL or path
  source) with a sha256 on each wheel and sdist; only torch comes
  from download.pytorch.org; no other package comes from two indexes; it holds a hashed cp312
  Linux x86_64 torch wheel for both builds, and PyPI's torch only for macOS.

A self-test runs first on fixtures, so a check that stops catching anything fails too.
Usage: python3 deploy/tests/check_scorer_lock.py"""

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTORCH = "https://download.pytorch.org/whl/"
CUDA_INDEX = re.compile(r"^CUDA_INDEX \?= (\S+)$", re.M)
INDEX_FLAG = re.compile(r"--(?:default-index|index|index-url|extra-index-url|find-links)\b")
# uv settings that open another source or skip the hashes, as flags or environment variables
UNCHECKED = re.compile(
    r"--no-verify-hashes\b"
    r"|\bUV_(?:DEFAULT_INDEX|INDEX|INDEX_URL|EXTRA_INDEX_URL|FIND_LINKS|NO_VERIFY_HASHES)\b"
)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
LINUX_WHEEL = re.compile(r"-cp312-cp312-manylinux[\w.]*_x86_64\.whl$")


def dockerfile_installs(text: str) -> list[str]:
    """The Dockerfile's RUN, ENV and ARG instructions, each joined into one line, comments left
    out (an ENV or ARG can point uv at another index)."""
    joined = re.sub(
        r"\\\n",
        " ",
        "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")),
    )
    return [
        line.strip().split(" ", 1)[1]
        for line in joined.splitlines()
        if line.strip().startswith(("RUN ", "ENV ", "ARG "))
    ]


def modal_installs(text: str) -> list[str]:
    """The Modal app's scorer install command (`_SCORER_INSTALL`), its pieces joined."""
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_SCORER_INSTALL"
            for target in node.targets
        ):
            value = node.value
            parts = value.values if isinstance(value, ast.JoinedStr) else [value]
            return [
                "".join(
                    part.value
                    if isinstance(part, ast.Constant)
                    else "{" + ast.unparse(part.value) + "}"
                    for part in parts
                )
            ]
    return []


def install_problems(name: str, commands: list[str]) -> list[str]:
    found = []
    if not any("uv sync" in c or "pylock" in c for c in commands):
        found.append(f"{name}: installs nothing from scorer/uv.lock (uv sync or a pylock.toml)")
    steps = [" ".join(step.split()) for c in commands for step in re.split(r"&&|;", c)]
    exported = any("uv export" in step and "pylock.toml" in step for step in steps)
    for step in steps:
        if "--no-hashes" in step:
            found.append(f"{name}: --no-hashes skips the lock's hashes: {step}")
        if INDEX_FLAG.search(step):
            found.append(f"{name}: an index flag opens a second source: {step}")
        if UNCHECKED.search(step):
            found.append(f"{name}: a uv setting opens a second source or skips hashes: {step}")
        if re.search(r"(?<!uv )\bpip3? install\b", step):
            found.append(f"{name}: pip installs without the lock's hashes: {step}")
        if "uv pip compile" in step:
            found.append(f"{name}: uv pip compile resolves afresh, not from the lock: {step}")
        for command in ("uv sync", "uv export"):
            if command in step and not re.search(r"--(frozen|locked)\b", step):
                found.append(f"{name}: {command} without --frozen or --locked: {step}")
        if "uv pip install" in step:
            pylock = exported and re.search(r"-r \S*pylock\.toml\b", step)
            project = step.endswith("--no-deps .")
            if not (pylock or project) or re.search(r"\s-c\s|--constraint", step):
                found.append(
                    f"{name}: uv pip install may take only a pylock.toml or the project "
                    f"(--no-deps .): {step}"
                )
    return found


def pyproject_problems(text: str, cuda: str) -> list[str]:
    found = []
    project = tomllib.loads(text)
    uv = project.get("tool", {}).get("uv", {})
    indexes = {index.get("name"): index for index in uv.get("index", [])}
    found += [
        f"scorer/pyproject.toml: index {name} is not explicit, so uv may take any package from it"
        for name, index in indexes.items()
        if not index.get("explicit")
    ]
    sources = uv.get("sources", {}).get("torch", [])
    sources = sources if isinstance(sources, list) else [sources]

    def url(where: dict) -> str | None:
        source = next((s for s in sources if all(s.get(k) == v for k, v in where.items())), None)
        return indexes.get(source.get("index"), {}).get("url") if source else None

    for where, index in (({"group": "cpu"}, "cpu"), ({"extra": cuda}, cuda)):
        if url(where) != PYTORCH + index:
            found.append(
                f"scorer/pyproject.toml: torch for {where} must come from {PYTORCH}{index}"
            )
    cpu = project.get("dependency-groups", {}).get("cpu")
    gpu = project.get("project", {}).get("optional-dependencies", {}).get(cuda)
    if not cpu or sorted(cpu) != sorted(gpu or []):
        found.append(f"scorer/pyproject.toml: the `cpu` group and the `{cuda}` extra must match")
    if "cpu" not in uv.get("default-groups", []):
        found.append("scorer/pyproject.toml: `cpu` must be a default group (uv sync, uv run)")
    pair = [{"group": "cpu"}, {"extra": cuda}]
    if not any(sorted(map(str, c)) == sorted(map(str, pair)) for c in uv.get("conflicts", [])):
        found.append(f"scorer/pyproject.toml: the `cpu` group and `{cuda}` extra must conflict")
    base = [
        NAME.match(r).group(0).lower() for r in project.get("project", {}).get("dependencies", [])
    ]
    if "torch" in base:
        found.append("scorer/pyproject.toml: torch is a base dependency, so it has no one index")
    return found


def lock_problems(text: str, cuda: str) -> list[str]:
    found = []
    lock = tomllib.loads(text)
    registries: dict[str, set[str]] = {}
    builds: dict[str, list[str]] = {}
    for package in lock.get("package", []):
        name, registry = package["name"], package["source"].get("registry")
        if not registry:
            if package["source"] in ({"editable": "."}, {"virtual": "."}):
                continue  # the project itself
            found.append(
                f"scorer/uv.lock: {name} comes from {package['source']}, not an index, so the "
                "image would install it without one index and a sha256 to check"
            )
            continue
        registries.setdefault(name, set()).add(registry)
        artifacts = [
            *package.get("wheels", []),
            *([package["sdist"]] if "sdist" in package else []),
        ]
        if not artifacts or not all(a.get("hash", "").startswith("sha256:") for a in artifacts):
            found.append(f"scorer/uv.lock: {name} {package['version']} has a file without a sha256")
        if registry.startswith(PYTORCH) and name != "torch":
            found.append(f"scorer/uv.lock: {name} comes from {registry}; only torch may")
        if name == "torch" and registry.startswith(PYTORCH):
            index = registry.removeprefix(PYTORCH).strip("/")
            builds[index] = [
                w["url"] for w in package.get("wheels", []) if LINUX_WHEEL.search(w["url"])
            ]
        if name == "torch" and not registry.startswith(PYTORCH):
            markers = package.get("resolution-markers", [])
            if not markers or not all("sys_platform == 'darwin'" in m for m in markers):
                found.append(f"scorer/uv.lock: torch from {registry} may install on Linux")
    found += [
        f"scorer/uv.lock: {name} comes from more than one index: {sorted(where)}"
        for name, where in registries.items()
        if len(where) > 1 and name != "torch"
    ]
    for index in ("cpu", cuda):
        if not builds.get(index):
            found.append(f"scorer/uv.lock: no cp312 Linux x86_64 torch wheel from {PYTORCH}{index}")
    conflicts = [sorted(str(sorted(item.items())) for item in c) for c in lock.get("conflicts", [])]
    pair = sorted(
        str(sorted(item.items()))
        for item in (
            {"package": "coglossa-scorer", "group": "cpu"},
            {"package": "coglossa-scorer", "extra": cuda},
        )
    )
    if pair not in conflicts:
        found.append(f"scorer/uv.lock: no conflict between the `cpu` group and the `{cuda}` extra")
    return found


def problems(texts: dict[str, str]) -> list[str]:
    match = CUDA_INDEX.search(texts["Makefile"])
    if not match:
        return ["Makefile: no `CUDA_INDEX ?= <index>` line"]
    cuda = match.group(1)
    return [
        *install_problems(
            "deploy/docker/scorer.Dockerfile", dockerfile_installs(texts["Dockerfile"])
        ),
        *install_problems("deploy/hosted/modal/app.py", modal_installs(texts["app.py"])),
        *pyproject_problems(texts["pyproject.toml"], cuda),
        *lock_problems(texts["uv.lock"], cuda),
    ]


FIXTURE_DOCKERFILE = """# a comment: uv pip install --index x is fine here
RUN case "$TORCH_INDEX" in cpu) build="--group cpu" ;; *) build="--extra $TORCH_INDEX" ;; esac \\
 && uv sync --frozen --no-default-groups $build --no-install-project
COPY scorer/coglossa_scorer coglossa_scorer
RUN uv pip install --python /opt/venv/bin/python --no-deps .
"""
FIXTURE_MODAL = """_SCORER_INSTALL = (
    "cd /src/scorer"
    f" && uv export --frozen --extra {TORCH_INDEX} --format pylock.toml -o /tmp/pylock.toml"
    " && uv pip install --system -r /tmp/pylock.toml"
    " && uv pip install --system --no-deps ."
)
"""
FIXTURE_PYPROJECT = """
[project]
dependencies = ["fastapi>=0.115"]
[project.optional-dependencies]
cu126 = ["torch<2.15", "sentence-transformers"]
[dependency-groups]
cpu = ["torch<2.15", "sentence-transformers"]
[tool.uv.sources]
torch = [
  { index = "pytorch-cpu", group = "cpu", marker = "sys_platform == 'linux'" },
  { index = "pytorch-cu126", extra = "cu126", marker = "sys_platform == 'linux'" },
]
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true
[[tool.uv.index]]
name = "pytorch-cu126"
url = "https://download.pytorch.org/whl/cu126"
explicit = true
[tool.uv]
default-groups = ["dev", "cpu"]
conflicts = [[{ group = "cpu" }, { extra = "cu126" }]]
"""
FIXTURE_LOCK = """
conflicts = [[
    { package = "coglossa-scorer", extra = "cu126" },
    { package = "coglossa-scorer", group = "cpu" },
]]
[[package]]
name = "coglossa-scorer"
version = "0.1.0"
source = { editable = "." }
[[package]]
name = "numpy"
version = "2.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://x/numpy-2.0.tar.gz", hash = "sha256:aa" }
wheels = [{ url = "https://x/numpy-2.0-cp312-cp312-manylinux_2_28_x86_64.whl", hash = "sha256:ab" }]
[[package]]
name = "torch"
version = "2.14.1"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["platform_machine == 'arm64' and sys_platform == 'darwin'"]
wheels = [{ url = "https://x/torch-2.14.1-cp312-cp312-macosx_14_0_arm64.whl", hash = "sha256:ac" }]
[[package]]
name = "torch"
version = "2.14.1+cpu"
source = { registry = "https://download.pytorch.org/whl/cpu" }
wheels = [{ url = "https://x/torch-2.14.1%2Bcpu-cp312-cp312-manylinux_2_28_x86_64.whl", hash = "sha256:ad" }]
[[package]]
name = "torch"
version = "2.14.1+cu126"
source = { registry = "https://download.pytorch.org/whl/cu126" }
wheels = [{ url = "https://x/torch-2.14.1%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl", hash = "sha256:ae" }]
"""
FIXTURES = {
    "Makefile": "CUDA_INDEX ?= cu126\n",
    "Dockerfile": FIXTURE_DOCKERFILE,
    "app.py": FIXTURE_MODAL,
    "pyproject.toml": FIXTURE_PYPROJECT,
    "uv.lock": FIXTURE_LOCK,
}
CU126_WHEEL = '"https://x/torch-2.14.1%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl"'
# (what, file, old text, new text): each must be reported
BREAKS = [
    ("--no-hashes", "Dockerfile", "--no-install-project", "--no-install-project --no-hashes"),
    ("a second index", "Dockerfile", "--no-deps .", "--no-deps . --index https://x"),
    ("uv pip install of torch", "Dockerfile", "--no-deps .", "torch==2.14.1"),
    ("uv sync without --frozen", "Dockerfile", "uv sync --frozen", "uv sync"),
    ("pip itself", "Dockerfile", "RUN uv pip install", "RUN pip install"),
    (
        "an index in the environment",
        "Dockerfile",
        "COPY scorer/coglossa_scorer",
        "ENV UV_EXTRA_INDEX_URL=https://x\nCOPY scorer/coglossa_scorer",
    ),
    ("an export without --frozen", "app.py", "uv export --frozen", "uv export"),
    ("a fresh resolve", "app.py", "uv export --frozen", "uv pip compile pyproject.toml"),
    ("no lock install", "Dockerfile", "uv sync --frozen", "uv pip compile"),
    ("a constraints file", "app.py", "-r /tmp/pylock.toml", "-c c.txt -r /tmp/pylock.toml"),
    ("an export without hashes", "app.py", "--format pylock.toml", "--no-hashes"),
    (
        "a non-explicit index",
        "pyproject.toml",
        'cu126"\nexplicit = true',
        'cu126"\nexplicit = false',
    ),
    ("torch in the base", "pyproject.toml", '["fastapi>=0.115"]', '["fastapi>=0.115", "torch"]'),
    (
        "the CUDA source",
        "pyproject.toml",
        'index = "pytorch-cu126", extra',
        'index = "pytorch-cpu", extra',
    ),
    ("lists that differ", "pyproject.toml", 'cpu = ["torch<2.15", ', 'cpu = ["torch<2.16", '),
    ("cpu not by default", "pyproject.toml", '["dev", "cpu"]', '["dev"]'),
    ("no conflict", "pyproject.toml", 'conflicts = [[{ group = "cpu" }, { extra = "cu126" }]]', ""),
    ("an unhashed wheel", "uv.lock", f'{CU126_WHEEL}, hash = "sha256:ae"', CU126_WHEEL),
    (
        "another package from PyTorch",
        "uv.lock",
        'name = "numpy"\nversion = "2.0"\nsource = { registry = "https://pypi.org/simple" }',
        'name = "numpy"\nversion = "2.0"\nsource = { registry = "https://download.pytorch.org/whl/cpu" }',
    ),
    (
        "PyPI's torch on Linux",
        "uv.lock",
        "\"platform_machine == 'arm64' and sys_platform == 'darwin'\"",
        "\"sys_platform == 'linux'\"",
    ),
    (
        "no CUDA build",
        "uv.lock",
        'cp312-cp312-manylinux_2_28_x86_64.whl", hash = "sha256:ae"',
        'cp313-cp313-manylinux_2_28_x86_64.whl", hash = "sha256:ae"',
    ),
    (
        "a git source",
        "uv.lock",
        'version = "2.0"\nsource = { registry = "https://pypi.org/simple" }',
        'version = "2.0"\nsource = { git = "https://x/numpy?rev=v2.0#abc" }',
    ),
    ("no lock conflict", "uv.lock", '{ package = "coglossa-scorer", group = "cpu" },', ""),
    ("another CUDA index", "Makefile", "cu126", "cu128"),
]


def self_test() -> list[str]:
    found = [f"self-test: good fixtures reported: {p}" for p in problems(FIXTURES)]
    for what, name, old, new in BREAKS:
        if FIXTURES[name].count(old) != 1:
            found.append(f"self-test: {what}: the fixture text to replace is not there once")
        elif not problems({**FIXTURES, name: FIXTURES[name].replace(old, new)}):
            found.append(f"self-test: {what} not reported")
    return found


def main() -> int:
    found = self_test()
    if not found:
        paths = {
            "Makefile": "Makefile",
            "Dockerfile": "deploy/docker/scorer.Dockerfile",
            "app.py": "deploy/hosted/modal/app.py",
            "pyproject.toml": "scorer/pyproject.toml",
            "uv.lock": "scorer/uv.lock",
        }
        found = problems(
            {key: (ROOT / path).read_text(encoding="utf-8") for key, path in paths.items()}
        )
    for problem in found:
        print(f"FAIL: {problem}", file=sys.stderr)
    if not found:
        print(
            "ok: the scorer installs only hash-checked wheels from scorer/uv.lock, one index each"
        )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
