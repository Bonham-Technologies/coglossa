"""Checks every wheel in the scorer image comes from scorer/uv.lock, hash-checked and from one
index (CGL-50), and so does the build backend that builds the scorer's own wheel (CGL-130). For
an air-gapped client the bundle is the supply chain, so the CPU image, the CUDA image (bundle)
and the hosted scorer install only what the lock names, at the sha256 it records, from the one
index it names for each package. Offline; it builds nothing.

- The installs (deploy/docker/scorer.Dockerfile's RUN, ENV and ARG instructions, and every
  command and setting on the Modal app's `scorer_image`) run only `uv sync --frozen
  --no-install-project --no-build`, `uv export --frozen` and `uv pip install`, never
  `--no-hashes`, an index flag or variable, a constraints file, another uv command (`uv add`,
  `uv run`, `uv tool`, `uv pip compile`, `uvx`) or pip, except pip installing only a hashed
  requirements file (`--require-hashes --only-binary :all: --no-deps -r FILE`, uv itself on
  Modal). `uv pip install` takes either a pylock.toml from `uv export --frozen` with
  `--no-build`, or the project itself (`--no-deps .`) with `--require-build-hashes` and the
  `build` group exported from the lock as its `--build-constraints`. `scorer_image` is built
  only with Modal methods that install nothing besides those commands.
- scorer/pyproject.toml: every extra index is explicit (it supplies only the packages pinned to
  it); torch comes from download.pytorch.org/whl/cpu in the `cpu` group (a default group) and
  from whl/<CUDA_INDEX> in the extra of that name (the Makefile's CUDA_INDEX); the two conflict,
  list the same requirements, and torch is not a base dependency; the `build` group lists every
  `build-system.requires` package.
- scorer/uv.lock: every package but the project comes from an index (no git, URL or path
  source) with a sha256 on each wheel and sdist; only torch comes
  from download.pytorch.org; no other package comes from two indexes; it holds a hashed cp312
  Linux x86_64 torch wheel for both builds, and PyPI's torch only for macOS; it locks the build
  backend.

A self-test runs first on fixtures, so a check that stops catching anything fails too.
Usage: python3 deploy/tests/check_scorer_lock.py"""

import ast
import re
import shlex
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTORCH = "https://download.pytorch.org/whl/"
CUDA_INDEX = re.compile(r"^CUDA_INDEX \?= (\S+)$", re.M)
INDEX_FLAG = re.compile(r"--(?:default-index|index|index-url|extra-index-url|find-links)\b")
# uv and pip settings that open another source, skip the hashes or change what a build uses,
# as flags or environment variables
UNCHECKED = re.compile(
    r"--no-verify-hashes\b"
    r"|\bUV_(?:DEFAULT_INDEX|INDEX|INDEX_URL|EXTRA_INDEX_URL|FIND_LINKS|NO_VERIFY_HASHES"
    r"|INSECURE_HOST|CONSTRAINT|OVERRIDE|BUILD_CONSTRAINT|NO_BUILD_ISOLATION)\b"
    r"|\bPIP_(?:INDEX_URL|EXTRA_INDEX_URL|FIND_LINKS|TRUSTED_HOST|CONSTRAINT|NO_BUILD_ISOLATION)\b"
)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
LINUX_WHEEL = re.compile(r"-cp312-cp312-manylinux[\w.]*_x86_64\.whl$")
PYLOCK = re.compile(r"(?:^|/)pylock(?:\.[\w-]+)?\.toml$")
BUILD_GROUP = "build"  # the dependency group that locks the build backend (CGL-130)
# the uv commands the installs may run; anything else (uv add, uv run, uv tool, uv pip sync,
# uv pip compile, uv build, ...) is reported
UV_COMMANDS = (["sync"], ["export"], ["pip", "install"])
UV_GLOBAL_VALUE_FLAGS = {"--directory", "--project", "--cache-dir", "--config-file", "--color"}
UV_PIP_FLAGS = {
    "--system",
    "--no-deps",
    "--no-build",
    "--require-build-hashes",
    "--require-hashes",
    "--verify-hashes",
    "--compile-bytecode",
    "--no-cache",
    "--strict",
    "--break-system-packages",
    "--quiet",
    "-q",
    "--verbose",
    "-v",
}
UV_PIP_VALUE_FLAGS = {
    "--python",
    "-p",
    "-r",
    "--requirements",
    "--requirement",
    "-b",
    "--build-constraints",
    "--build-constraint",
}
UV_EXPORT_VALUE_FLAGS = {
    "-o",
    "--output-file",
    "--format",
    "--only-group",
    "--group",
    "--no-group",
    "--extra",
    "--package",
    "--prune",
    "--python",
    "-p",
}
PIP_FLAGS = {"--require-hashes", "--no-deps", "--quiet", "-q", "--no-cache-dir"}
PIP_VALUE_FLAGS = {"-r", "--requirement", "--only-binary"}
# Modal Image methods that install nothing; run_commands and env are read, any other is reported
MODAL_NO_INSTALL = {"debian_slim", "add_local_dir", "add_local_file", "add_local_python_source"}


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


def _string(node: ast.expr, strings: dict[str, str]) -> str | None:
    """A string literal, f-string (its fields kept as `{name}`) or module-level string name."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "{" + ast.unparse(part.value) + "}"
            for part in node.values
        )
    if isinstance(node, ast.Name):
        return strings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _string(node.left, strings), _string(node.right, strings)
        return None if left is None or right is None else left + right
    return None


def modal_installs(text: str) -> tuple[list[str], list[str]]:
    """Every command `scorer_image` runs (its run_commands, and each env as an ENV line), and
    the problems: a method that installs some other way, or a command the check cannot read."""
    name = "deploy/hosted/modal/app.py"
    tree = ast.parse(text)
    strings: dict[str, str] = {}
    images = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "scorer_image":
                images.append(node.value)
            elif isinstance(target, ast.Name) and (value := _string(node.value, strings)):
                strings[target.id] = value
    if len(images) != 1:
        return [], [f"{name}: {len(images)} `scorer_image = ...` assignments; the check reads one"]
    image, calls = images[0], []
    while isinstance(image, ast.Call) and isinstance(image.func, ast.Attribute):
        calls.append(image)
        image = image.func.value
    commands, found = [], []
    if ast.unparse(image) != "modal.Image":
        found.append(f"{name}: scorer_image must start from modal.Image, not {ast.unparse(image)}")
    # a later scorer_image.<method>(...) (a function's image=) is another image the check misses
    found += [
        f"{name}: scorer_image.{node.attr} outside its definition makes an unchecked image"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "scorer_image"
    ]
    for call in reversed(calls):
        method = call.func.attr
        arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
        if method == "run_commands":
            for argument in call.args:
                command = _string(argument, strings)
                if command is None:
                    found.append(
                        f"{name}: scorer_image runs a command the check cannot read: "
                        f"{ast.unparse(argument)}"
                    )
                else:
                    commands.append(command)
        elif method == "env":
            commands.append("ENV " + " ".join(ast.unparse(a) for a in arguments))
        elif method not in MODAL_NO_INSTALL:
            found.append(
                f"{name}: scorer_image.{method}() installs outside the checked commands "
                "(only run_commands, env and " + ", ".join(sorted(MODAL_NO_INSTALL)) + ")"
            )
    return commands, found


def _words(step: str) -> list[str]:
    try:
        return shlex.split(step)
    except ValueError:
        return step.split()


def _uv_command(words: list[str]) -> tuple[list[str], list[str]] | None:
    """The uv subcommand a step runs (["pip", "install"], ["sync"], ...) and its arguments, if
    uv comes before any pip install in it."""
    for i, word in enumerate(words):
        if _is_pip_install(words, i):
            return None
        if word.rsplit("/", 1)[-1] == "uv":
            rest, j = words[i + 1 :], 0
            while j < len(rest) and rest[j].startswith("-"):
                j += 2 if rest[j] in UV_GLOBAL_VALUE_FLAGS else 1
            command = rest[j : j + 2] if rest[j : j + 1] in (["pip"], ["tool"]) else rest[j : j + 1]
            return command, rest[j + len(command) :]
    return None


def _is_pip_install(words: list[str], i: int) -> bool:
    return (
        bool(re.fullmatch(r"pip[\d.]*", words[i].rsplit("/", 1)[-1]))
        and words[i + 1 : i + 2] == ["install"]
        and not (i and words[i - 1].rsplit("/", 1)[-1] == "uv")
    )


def _pip_install(words: list[str]) -> list[str] | None:
    """The arguments of a pip install (`pip install`, `python -m pip install`), not uv's."""
    for i in range(len(words)):
        if _is_pip_install(words, i):
            return words[i + 2 :]
    return None


def _options(
    args: list[str], value_flags: set[str]
) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """Flags without a value, flags with their values, and positional arguments."""
    flags: list[str] = []
    values: dict[str, list[str]] = {}
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("-") and arg != "-":
            flag, equals, value = arg.partition("=") if arg.startswith("--") else (arg, "", "")
            if flag in value_flags:
                if not equals:
                    i += 1
                    value = args[i] if i < len(args) else ""
                values.setdefault(flag, []).append(value)
            else:
                flags.append(flag)
        else:
            positional.append(arg)
        i += 1
    return flags, values, positional


def _get(values: dict[str, list[str]], *flags: str) -> list[str]:
    return [value for flag in flags for value in values.get(flag, [])]


def install_problems(name: str, commands: list[str]) -> list[str]:
    found = []
    if not any("uv sync" in c or "pylock" in c for c in commands):
        found.append(f"{name}: installs nothing from scorer/uv.lock (uv sync or a pylock.toml)")
    steps = [" ".join(step.split()) for c in commands for step in re.split(r"&&|\|\||;|\|", c)]
    # what each frozen, hashed `uv export -o FILE` wrote: a pylock.toml or the build group
    exported: dict[str, str] = {}
    for step in steps:
        uv = _uv_command(_words(step))
        if not uv or uv[0] != ["export"] or "--no-hashes" in uv[1]:
            continue
        flags, values, _ = _options(uv[1], UV_EXPORT_VALUE_FLAGS)
        if not {"--frozen", "--locked"} & set(flags):
            continue
        if _get(values, "--format") == ["pylock.toml"]:
            kind = "pylock"
        elif _get(values, "--only-group") == [BUILD_GROUP] and not _get(values, "--format"):
            kind = "build"
        else:
            kind = "other"
        for output in _get(values, "-o", "--output-file"):
            exported[output] = kind
    for step in steps:
        words = _words(step)
        if "--no-hashes" in step:
            found.append(f"{name}: --no-hashes skips the lock's hashes: {step}")
        if INDEX_FLAG.search(step):
            found.append(f"{name}: an index flag opens a second source: {step}")
        if UNCHECKED.search(step):
            found.append(f"{name}: a setting opens a second source or skips hashes: {step}")
        if "uv pip compile" in step:
            found.append(f"{name}: uv pip compile resolves afresh, not from the lock: {step}")
        if any(word.rsplit("/", 1)[-1] == "uvx" for word in words):
            found.append(f"{name}: uvx installs a tool without the lock's hashes: {step}")
        uv = _uv_command(words)
        if uv is None:
            args = _pip_install(words)
            if args is not None:
                flags, values, positional = _options(args, PIP_VALUE_FLAGS)
                if (
                    positional
                    or set(flags) - PIP_FLAGS
                    or not {"--require-hashes", "--no-deps"} <= set(flags)
                    or len(_get(values, "-r", "--requirement")) != 1
                    or _get(values, "--only-binary") != [":all:"]
                ):
                    found.append(
                        f"{name}: pip may install only a hashed requirements file "
                        f"(--require-hashes --only-binary :all: --no-deps -r FILE): {step}"
                    )
            continue
        command, args = uv
        if command not in UV_COMMANDS:
            found.append(
                f"{name}: uv {' '.join(command)} is not one of the checked installs "
                f"(uv sync, uv export, uv pip install): {step}"
            )
            continue
        if command in (["sync"], ["export"]) and not {"--frozen", "--locked"} & set(args):
            found.append(f"{name}: uv {command[0]} without --frozen or --locked: {step}")
        if command == ["sync"] and not {"--no-install-project", "--no-build"} <= set(args):
            found.append(
                f"{name}: uv sync must not build anything (--no-install-project --no-build): "
                f"a build runs a backend the lock does not check: {step}"
            )
        if command != ["pip", "install"]:
            continue
        flags, values, positional = _options(args, UV_PIP_VALUE_FLAGS)
        if unknown := sorted(set(flags) - UV_PIP_FLAGS):
            found.append(f"{name}: uv pip install with an unchecked option {unknown}: {step}")
        requirements = _get(values, "-r", "--requirements", "--requirement")
        if any(not PYLOCK.search(r) or exported.get(r) != "pylock" for r in requirements):
            found.append(
                f"{name}: uv pip install -r takes only a pylock.toml from `uv export --frozen`: "
                f"{step}"
            )
        if requirements and "--no-build" not in flags:
            found.append(
                f"{name}: a pylock.toml install without --no-build may build a package with a "
                f"backend the lock does not check: {step}"
            )
        if positional and (positional != ["."] or requirements or "--no-deps" not in flags):
            found.append(
                f"{name}: uv pip install may add only the project itself (--no-deps .), "
                f"apart from a pylock.toml: {step}"
            )
        constraints = _get(values, "-b", "--build-constraints", "--build-constraint")
        if "." in positional and (
            "--require-build-hashes" not in flags
            or not constraints
            or any(exported.get(c) != "build" for c in constraints)
        ):
            found.append(
                f"{name}: the project's build backend must be the lock's `{BUILD_GROUP}` group, "
                f"hash-checked (--build-constraints <uv export --frozen --only-group "
                f"{BUILD_GROUP} -o FILE> --require-build-hashes): {step}"
            )
        if not requirements and not positional:
            found.append(f"{name}: uv pip install of nothing the check knows: {step}")
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
    group = project.get("dependency-groups", {}).get(BUILD_GROUP, [])
    locked = {_name(r) for r in group if isinstance(r, str)}
    if not locked:
        found.append(
            f"scorer/pyproject.toml: no `{BUILD_GROUP}` dependency group, so the build backend "
            "has no hash in the lock"
        )
    elif missing := [r for r in build_requires(text) if r not in locked]:
        found.append(
            f"scorer/pyproject.toml: the `{BUILD_GROUP}` group must list every "
            f"build-system.requires package; it misses {missing}"
        )
    return found


def _name(requirement: str) -> str:
    return re.sub(r"[-_.]+", "-", NAME.match(requirement).group(0)).lower()


def build_requires(pyproject: str) -> list[str]:
    """The names of the packages that build the project (build-system.requires)."""
    return [
        _name(r) for r in tomllib.loads(pyproject).get("build-system", {}).get("requires", [])
    ]


def lock_problems(text: str, cuda: str, backend: list[str]) -> list[str]:
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
    found += [
        f"scorer/uv.lock: the build backend's {name} is not locked from an index"
        for name in backend
        if name not in {_name(n) for n in registries}
    ]
    return found


def problems(texts: dict[str, str]) -> list[str]:
    match = CUDA_INDEX.search(texts["Makefile"])
    if not match:
        return ["Makefile: no `CUDA_INDEX ?= <index>` line"]
    cuda = match.group(1)
    modal, modal_found = modal_installs(texts["app.py"])
    return [
        *install_problems(
            "deploy/docker/scorer.Dockerfile", dockerfile_installs(texts["Dockerfile"])
        ),
        *modal_found,
        *install_problems("deploy/hosted/modal/app.py", modal),
        *pyproject_problems(texts["pyproject.toml"], cuda),
        *lock_problems(texts["uv.lock"], cuda, build_requires(texts["pyproject.toml"])),
    ]


FIXTURE_DOCKERFILE = """# a comment: uv pip install --index x is fine here
RUN case "$TORCH_INDEX" in cpu) build="--group cpu" ;; *) build="--extra $TORCH_INDEX" ;; esac \\
 && uv sync --frozen --no-default-groups $build --no-install-project --no-build \\
 && uv export --frozen --only-group build -o /tmp/build.txt
COPY scorer/coglossa_scorer coglossa_scorer
RUN uv pip install --python /opt/venv/bin/python --no-deps \\
    --build-constraints /tmp/build.txt --require-build-hashes .
"""
FIXTURE_MODAL = """_UV_INSTALL = (
    "python -m pip install --require-hashes --only-binary :all: --no-deps -r /tmp/uv.txt"
)
_SCORER_INSTALL = (
    "cd /src/scorer"
    f" && uv export --frozen --extra {TORCH_INDEX} --format pylock.toml -o /tmp/pylock.toml"
    " && uv export --frozen --only-group build -o /tmp/build.txt"
    " && uv pip install --system --no-build -r /tmp/pylock.toml"
    " && uv pip install --system --no-deps --build-constraints /tmp/build.txt"
    " --require-build-hashes ."
)
scorer_image = (
    modal.Image.debian_slim(python_version="3.12")
    .add_local_file(UV_REQUIREMENTS, "/tmp/uv.txt", copy=True)
    .run_commands(_UV_INSTALL)
    .add_local_dir(REPO / "scorer", "/src/scorer", copy=True)
    .run_commands(_SCORER_INSTALL)
    .env({"HF_HUB_OFFLINE": "1"})
    .add_local_python_source("settings")
)
"""
FIXTURE_PYPROJECT = """
[project]
dependencies = ["fastapi>=0.115"]
[project.optional-dependencies]
cu126 = ["torch<2.15", "sentence-transformers"]
[dependency-groups]
cpu = ["torch<2.15", "sentence-transformers"]
build = ["hatchling"]
[build-system]
requires = ["hatchling"]
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
name = "hatchling"
version = "1.33.0"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://x/hatchling-1.33.0-py3-none-any.whl", hash = "sha256:af" }]
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
COPY = "COPY scorer/coglossa_scorer"
END = '    .add_local_python_source("settings")\n)\n'
CU126_WHEEL = '"https://x/torch-2.14.1%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl"'
# (what, file, old text, new text): each must be reported
BREAKS = [
    ("--no-hashes", "Dockerfile", "--no-install-project", "--no-install-project --no-hashes"),
    ("a second index", "Dockerfile", "-hashes .", "-hashes . --index https://x"),
    ("uv pip install of torch", "Dockerfile", "-hashes .", "-hashes torch==2.14.1"),
    ("uv sync without --frozen", "Dockerfile", "uv sync --frozen", "uv sync"),
    ("pip itself", "Dockerfile", "RUN uv pip install", "RUN pip install"),
    (
        "an index in the environment",
        "Dockerfile",
        "COPY scorer/coglossa_scorer",
        "ENV UV_EXTRA_INDEX_URL=https://x\nCOPY scorer/coglossa_scorer",
    ),
    ("an export without --frozen", "app.py", "uv export --frozen --extra", "uv export --extra"),
    ("a fresh resolve", "app.py", "uv export --frozen --extra", "uv pip compile x.in --extra"),
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
    # CGL-130: the commands around the lock install, and the build backend
    (
        "a requirements file before the project",
        "Dockerfile",
        "/opt/venv/bin/python --no-deps",
        "/opt/venv/bin/python -r requirements.txt --no-deps",
    ),
    ("uv add", "Dockerfile", COPY, "RUN uv add requests\n" + COPY),
    ("uv run --with", "Dockerfile", COPY, "RUN uv run --with requests python -V\n" + COPY),
    ("uv tool install", "Dockerfile", COPY, "RUN uv tool install ruff\n" + COPY),
    ("uvx", "Dockerfile", COPY, "RUN uvx ruff --version\n" + COPY),
    ("uv pip sync", "Dockerfile", COPY, "RUN uv pip sync /tmp/r.txt\n" + COPY),
    ("a pip index in the environment", "Dockerfile", COPY, "ENV PIP_INDEX_URL=https://x\n" + COPY),
    ("a Modal pip_install", "app.py", ".add_local_dir(", '.pip_install("x")\n    .add_local_dir('),
    ("a Modal uv_pip_install", "app.py", ".add_local_dir(", '.uv_pip_install("x").add_local_dir('),
    (
        "a Modal requirements install",
        "app.py",
        ".add_local_dir(",
        '.pip_install_from_requirements("r.txt")\n    .add_local_dir(',
    ),
    (
        "another Modal command",
        "app.py",
        ".run_commands(_SCORER_INSTALL)",
        '.run_commands(_SCORER_INSTALL, "pip install x")',
    ),
    ("a Modal command the check cannot read", "app.py", "(_UV_INSTALL)", "(*COMMANDS)"),
    ("an index in the Modal env", "app.py", '{"HF_HUB_OFFLINE": "1"}', '{"UV_INDEX_URL": "x"}'),
    ("another Modal base", "app.py", "Image.debian_slim(", "Image.from_registry("),
    ("no Modal scorer image", "app.py", "scorer_image = (", "other_image = ("),
    ("an unhashed uv bootstrap", "app.py", "--require-hashes ", ""),
    ("a uv bootstrap that may build", "app.py", "--only-binary :all: ", ""),
    ("a uv bootstrap with a package", "app.py", "-r /tmp/uv.txt", "-r /tmp/uv.txt uv"),
    ("a project build without hashes", "Dockerfile", " --require-build-hashes .", " ."),
    ("a project build without constraints", "Dockerfile", "--build-constraints /tmp/build.txt", ""),
    ("build constraints from elsewhere", "Dockerfile", "-o /tmp/build.txt", "-o /tmp/other.txt"),
    ("build constraints from another group", "app.py", "--only-group build", "--only-group dev"),
    ("a build without isolation", "Dockerfile", "-hashes .", "-hashes --no-build-isolation ."),
    ("an editable project", "Dockerfile", "-hashes .", "-hashes -e ."),
    ("a project install with its dependencies", "app.py", "--system --no-deps", "--system"),
    ("uv sync building the project", "Dockerfile", " --no-install-project", ""),
    ("uv sync building sources", "Dockerfile", "-project --no-build", "-project"),
    ("a pylock install building sources", "app.py", "--system --no-build -r", "--system -r"),
    ("a pylock not from the export", "app.py", "-r /tmp/pylock.toml", "-r /tmp/other/pylock.toml"),
    ("no build group", "pyproject.toml", 'build = ["hatchling"]\n', ""),
    (
        "a build requirement outside the group",
        "pyproject.toml",
        'requires = ["hatchling"]',
        'requires = ["hatchling", "editables"]',
    ),
    ("no locked build backend", "uv.lock", 'name = "hatchling"', 'name = "hatchling-x"'),
    ("scorer_image extended later", "app.py", END, END + "scorer_image = scorer_image.env({})\n"),
    (
        "a function on an extended scorer_image",
        "app.py",
        END,
        END + '@app.function(image=scorer_image.pip_install("x"))\ndef f(): ...\n',
    ),
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
            "ok: the scorer installs only hash-checked wheels from scorer/uv.lock, one index each, "
            "and builds its own wheel with a hash-checked backend from the lock"
        )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
