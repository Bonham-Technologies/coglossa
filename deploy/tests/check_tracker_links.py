"""Checks no tracked file links the internal Jira or Confluence site (CGL-78). The public
repository is published as a fresh-history snapshot of this tree, so such links must not be in
it; ticket keys stay as plain text (`Ticket: CGL-12`). `.claude/` is vendored from bonham-ops and
changed there, not here.
Usage: python3 deploy/tests/check_tracker_links.py"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACKER_URL = re.compile(r"https?://[a-z0-9-]+\.atlassian\.net\b")


def offenders() -> list[str]:
    files = (
        subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        )
        .stdout.decode()
        .split("\0")
    )
    found = []
    for rel in filter(None, files):
        if rel.startswith(".claude/"):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if TRACKER_URL.search(line):
                found.append(f"{rel}:{number}")
    return found


def main() -> int:
    bad = offenders()
    for where in bad:
        print(f"internal tracker link: {where}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
