"""Checks the GitHub Actions workflows are hardened (CGL-37): each one declares a top-level
`permissions:` block, and every action is pinned to a full commit SHA with its version in a
comment (`uses: owner/repo@<40 hex> # v1.2.3`), so a retagged action cannot change what runs.
Dependabot's github-actions ecosystem keeps the pins current. A job that reads `secrets.*` must
name an `environment:`, so its secrets sit behind the owner's approval, and if the workflow can be
run by hand its `if:` must require `refs/heads/main` (CGL-77, checklist GA-7 and GA-3).
Every `runs-on:` is a GitHub-hosted label or the one expression that reads `vars.RUNS_ON` (the
self-hosted runner) only for a push or a pull request whose head is this repo, so a fork PR and
any other event fall back to `ubuntu-24.04`; and every `actions/checkout` step sets
`persist-credentials: false` under its `with:` (CGL-102). A self-test feeds both checks bad and
good fixtures first, so a check that stops catching anything fails too.
Usage: python3 deploy/tests/check_workflows.py [workflow ...]"""

import re
import sys
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S*)(.*)$")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
VERSION = re.compile(r"^\s+# v\d+\.\d+\.\d+\s*$")
COMMENT = re.compile(r"(?:^|\s)#.*$")
TOP_KEY = re.compile(r"""^["']?([\w-]+)["']?:""")
JOB = re.compile(r"^( +)([\w-]+):$")
DISPATCH = re.compile(r"\bworkflow_dispatch\b")


def sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """The top-level `on:` block's lines, and each job's lines by name (the keys at the first
    indent under the top-level `jobs:`), all with comments and blank lines dropped."""
    triggers: list[str] = []
    found: dict[str, list[str]] = {}
    top = current = indent = None
    for line in lines:
        line = COMMENT.sub("", line).rstrip()
        if not line.strip():
            continue
        if not line[0].isspace():
            key = TOP_KEY.match(line)
            top, current = (key.group(1) if key else None), None
            if top == "on":
                triggers.append(line)
            continue
        if top == "on":
            triggers.append(line)
        elif top == "jobs":
            match = JOB.match(line)
            if indent is None and match:
                indent = match.group(1)
            if match and match.group(1) == indent:
                current = match.group(2)
                found[current] = []
            elif current is not None:
                found[current].append(line)
    return triggers, found


# The one runner expression that may read vars.RUNS_ON (the self-hosted runner): a push, or a pull
# request whose head is this repo. Every other event, a fork PR among them, gets a hosted runner.
GUARDED_RUNNER = (
    "${{ (vars.RUNS_ON && (github.event_name == 'push'"
    " || github.event.pull_request.head.repo.full_name == github.repository))"
    " && fromJSON(vars.RUNS_ON) || 'ubuntu-24.04' }}"
)
HOSTED = re.compile(r"""^["']?(?:ubuntu|windows|macos)-[\w.-]+["']?$""")
RUNS_ON_KEY = re.compile(r"^(\s*)runs-on:(.*)$")
RUNS_ON_VAR = re.compile(r"runs_on", re.I)  # contexts are case-insensitive: vars.runs_on too
BLOCK = re.compile(r"^[>|][+-]?\d*")
STEP = re.compile(r"^\s*-(?:\s|$)")
CHECKOUT = re.compile(r"^actions/checkout(?:@|$)", re.I)
NO_CREDENTIALS = re.compile(r"""^persist-credentials:\s*(["']?)false\1$""")
NO_CREDENTIALS_FLOW = re.compile(r"""[{,]\s*persist-credentials:\s*(["']?)false\1\s*[,}]""")


def indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def runner_problems(name: str, lines: list[str]) -> list[str]:
    """Every `runs-on:` is a GitHub-hosted label or exactly GUARDED_RUNNER (on one line or
    several), and RUNS_ON appears nowhere else: fork code never reaches the self-hosted runner."""
    code = [COMMENT.sub("", line).rstrip() for line in lines]
    found, guarded = [], set()
    for i, line in enumerate(code):
        if not (match := RUNS_ON_KEY.match(line)):
            continue
        end = i + 1
        while end < len(code) and (not code[end].strip() or indent(code[end]) > len(match[1])):
            end += 1
        value = " ".join(" ".join([BLOCK.sub("", match[2].strip()), *code[i + 1 : end]]).split())
        if value == GUARDED_RUNNER:
            guarded.update(range(i, end))
        elif not HOSTED.match(value):
            found.append(
                f"{name}:{i + 1}: runs-on must be a GitHub-hosted label or the same-repo"
                f" vars.RUNS_ON expression with an 'ubuntu-24.04' fallback: {value}"
            )
            guarded.update(range(i, end))  # reported once, not again below
    for i, line in enumerate(code):
        if i not in guarded and RUNS_ON_VAR.search(line):
            found.append(f"{name}:{i + 1}: vars.RUNS_ON must be read only by a guarded runs-on")
    return found


def with_sets_no_credentials(step: list[str], key: int) -> bool:
    """Whether the step's `with:` (a key at column `key`) has `persist-credentials: false`."""
    for j, line in enumerate(step):
        if indent(line) != key or not line.strip().startswith("with:"):
            continue
        rest = line.strip()[len("with:") :].strip()
        if rest.startswith("{"):
            return bool(NO_CREDENTIALS_FLOW.search(rest))
        children = []
        for child in step[j + 1 :]:
            if child.strip() and indent(child) <= key:
                break
            if child.strip():
                children.append(child)
        if children:
            first = indent(children[0])
            return any(indent(c) == first and NO_CREDENTIALS.match(c.strip()) for c in children)
    return False


def checkout_problems(name: str, lines: list[str]) -> list[str]:
    """Every actions/checkout step sets `persist-credentials: false` under its `with:`, wherever
    `uses:` and `with:` sit in the step."""
    code = [COMMENT.sub("", line).rstrip() for line in lines]
    found = []
    for i, line in enumerate(code):
        match = USES.match(line)
        if not match or not CHECKOUT.match(match[1].strip("'\"")):
            continue
        key = line.index("uses:")
        start = i
        while start >= 0 and not (STEP.match(code[start]) and indent(code[start]) < key):
            if code[start].strip() and indent(code[start]) < key:
                start = -1  # not inside a step's mapping
                break
            start -= 1
        end = i + 1
        while end < len(code) and (not code[end].strip() or indent(code[end]) >= key):
            end += 1
        if start >= 0:
            step = [code[start].replace("-", " ", 1), *code[start + 1 : end]]
            if with_sets_no_credentials(step, key):
                continue
        found.append(f"{name}:{i + 1}: actions/checkout must set persist-credentials: false")
    return found


def problems(path: Path, text: str | None = None) -> list[str]:
    lines = (path.read_text() if text is None else text).splitlines()
    found = runner_problems(path.name, lines) + checkout_problems(path.name, lines)
    if not any(line.startswith("permissions:") for line in lines):
        found.append(f"{path.name}: no top-level permissions: block")
    for number, line in enumerate(lines, 1):
        match = USES.match(line)
        if match is None:
            continue
        action, rest = match.groups()
        action = action.strip("'\"")
        if not action:  # the value on the next line: write it on the same line to be checked
            found.append(f"{path.name}:{number}: write the action on the uses: line")
            continue
        if action.startswith("./"):
            continue  # an action in this repository is pinned by the checkout itself
        if not PINNED.match(action) or not VERSION.match(rest):
            found.append(f"{path.name}:{number}: not pinned as owner/repo@<sha> # vX.Y.Z: {action}")
    triggers, jobs = sections(lines)
    dispatch = any(DISPATCH.search(line) for line in triggers)
    for name, code in jobs.items():
        if not any("secrets." in line for line in code):
            continue
        if not any(line.strip().startswith("environment:") for line in code):
            found.append(f"{path.name}: job {name} reads secrets without an environment:")
        if dispatch and not any("refs/heads/main" in line for line in code):
            found.append(f"{path.name}: job {name} reads secrets on a manual run of any branch")
    return found


# Self-test: each bad runs-on and each bad checkout step must be reported, the good ones not.
CO = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"
BAD_RUNNERS = [
    "${{ vars.RUNS_ON && fromJSON(vars.RUNS_ON) || 'ubuntu-latest' }}",
    "${{ vars.runs_on && fromJSON(vars.runs_on) || 'ubuntu-latest' }}",
    "${{ vars['RUNS_ON'] && fromJSON(vars['RUNS_ON']) || 'ubuntu-latest' }}",
    ">-\n      ${{ vars.RUNS_ON && fromJSON(vars.RUNS_ON) || 'ubuntu-latest' }}",
    GUARDED_RUNNER.replace(") && fromJSON", ") || fromJSON"),
    GUARDED_RUNNER.replace("'push'", "'pull_request'").replace(" == 'pull", " != 'pull"),
    "[self-hosted, linux]",
    "\n      - self-hosted",
]
GOOD_RUNNERS = [
    "ubuntu-latest",
    "'ubuntu-24.04'",
    GUARDED_RUNNER,
    ">-\n      " + GUARDED_RUNNER.replace(" || github.event", "\n        || github.event"),
]
BAD_CHECKOUTS = [
    f"- uses: {CO}",
    f"- if: true\n  uses: {CO}\n  with:\n    ref: x",
    '- uses: "' + CO.replace(" #", '" #'),
    f"- uses: {CO.replace('actions/checkout', 'Actions/Checkout')}",
    f"- uses: {CO}\n  env:\n    persist-credentials: false",
    f"- uses: {CO}\n  with:\n    persist-credentials: true",
    f"- uses: {CO}\n  with:\n    ref: x\n    x:\n      persist-credentials: false",
    f"-\n  uses: {CO}",
]
GOOD_CHECKOUTS = [
    f"- uses: {CO}\n  with:\n    persist-credentials: false",
    f"- name: x\n  with:\n    ref: x\n    persist-credentials: false\n  uses: {CO}",
    f"-\n  uses: {CO}\n  with: {{ ref: x, persist-credentials: false }}",
]


def fixture(runners: list[str], steps: list[str]) -> str:
    jobs = "".join(f"  j{n}:\n    runs-on: {r}\n    steps: []\n" for n, r in enumerate(runners))
    body = "".join("\n".join("      " + s for s in step.split("\n")) + "\n" for step in steps)
    return f"permissions:\n  contents: read\njobs:\n{jobs}  steps:\n    steps:\n{body}"


def self_test() -> list[str]:
    found = [
        f"self-test: bad {kind} not reported: {case!r}"
        for kind, cases, make in (
            ("runs-on", BAD_RUNNERS, lambda c: fixture([c], [])),
            ("checkout", BAD_CHECKOUTS, lambda c: fixture([], [c])),
        )
        for case in cases
        if not problems(Path("fixture.yml"), make(case))
    ]
    good = problems(Path("fixture.yml"), fixture(GOOD_RUNNERS, GOOD_CHECKOUTS))
    return found + [f"self-test: good fixture reported: {problem}" for problem in good]


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or sorted(WORKFLOWS.glob("*.y*ml"))
    found = self_test() + [p for path in paths for p in problems(path)]
    for problem in found:
        print(f"FAIL: {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
