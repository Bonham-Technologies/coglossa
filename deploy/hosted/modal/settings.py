"""Hosted preview settings shared by the Modal app and its tests; no Modal import here."""

import hmac
import os
from collections.abc import Mapping
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
HOSTED_TEMPLATE = HERE.parent / "coglossa.hosted.yaml"


def repo_root(app_file: str | Path, local: bool) -> Path:
    """The repository root, from deploy/hosted/modal/app.py. Inside a Modal container app.py
    is /root/app.py and nothing is read from the local tree (the image is already built), so
    any path will do there: "/" stands in for it."""
    return Path(app_file).resolve().parents[3] if local else Path("/")


def render_config(template: Path, out: Path, env: Mapping[str, str] = os.environ) -> Path:
    """The hosted config with ${NAME} filled from the environment (the Modal Secret). A
    missing value raises KeyError naming it, so the container fails at start, not mid-run."""
    out.write_text(Template(template.read_text(encoding="utf-8")).substitute(env), encoding="utf-8")
    return out


def check_kick(authorization: str | None, token: str) -> bool:
    """True when the Authorization header is exactly "Bearer <token>" (constant-time compare)."""
    return hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode())
