"""Coglossa hosted preview on Modal (spec §6): the API, a burst worker, the GPU scorer and a
cron, all scaling to zero. Deploy: modal deploy deploy/hosted/modal/app.py"""

import subprocess
from pathlib import Path

import modal
from fastapi import Header  # in the app image through the api's dependencies
from fastapi.responses import JSONResponse

from settings import HOSTED_TEMPLATE, check_kick, render_config, repo_root

# A Modal container imports this file as /root/app.py, which has no repository around it.
REPO = repo_root(__file__, modal.is_local())
CONFIG = Path("/etc/coglossa/coglossa.hosted.yaml")  # the template, baked into the image
RENDERED = Path("/tmp/coglossa.yaml")
IGNORE = [".venv", "**/__pycache__", "tests", "node_modules", "dist", "coverage"]
API_LABEL = "coglossa-api"  # the web endpoint URLs are https://<workspace>--<label>.modal.run
KICK_LABEL = "coglossa-kick"
SCORER_LABEL = "coglossa-scorer"
TORCH_INDEX = "cu126"  # the repo's tested CUDA build (Makefile CUDA_INDEX); runs on the L4
# uv, the version the Dockerfiles copy from ghcr.io/astral-sh/uv, from PyPI with its hashes
UV_REQUIREMENTS = REPO / "deploy" / "hosted" / "modal" / "uv-requirements.txt"
_UV_INSTALL = (
    "python -m pip install --require-hashes --only-binary :all: --no-deps"
    " -r /tmp/uv-requirements.txt"
)

app = modal.App("coglossa")
secret = modal.Secret.from_name("coglossa-hosted")
scorer_secret = modal.Secret.from_name("coglossa-scorer")  # COGLOSSA_SCORER_TOKEN only
weights = modal.Volume.from_name("coglossa-weights", create_if_missing=True)

app_image = (
    modal.Image.debian_slim(python_version="3.12")
    .add_local_file(UV_REQUIREMENTS, "/tmp/uv-requirements.txt", copy=True)
    .run_commands(_UV_INSTALL)
    .add_local_dir(REPO / "engine", "/src/engine", copy=True, ignore=IGNORE)
    .add_local_dir(REPO / "api", "/src/api", copy=True, ignore=IGNORE)
    .run_commands(
        # the locked dependencies, each checked against the lock's sha256, then the two projects,
        # into the system Python Modal runs
        # --extra s3: boto3 for Supabase Storage (the air-gapped app image leaves it out)
        "cd /src/api && uv export --frozen --no-dev --extra s3 --no-emit-project"
        " | grep -v '^-e ' > /tmp/req.txt",
        "uv pip install --system -r /tmp/req.txt /src/engine /src/api",
    )
    .add_local_file(HOSTED_TEMPLATE, str(CONFIG), copy=True)
    .env({"COGLOSSA_CONFIG": str(RENDERED)})
    .add_local_python_source("settings")  # not copied: must stay the last step
)

def _config():
    from coglossa.config import load_config

    render_config(CONFIG, RENDERED)
    cfg, _ = load_config(RENDERED)
    return cfg


# Every web endpoint requires Modal proxy auth (Modal-Key/Modal-Secret headers): a request
# without them is refused by Modal and never starts a container. One API container: the
# database connection budget in README.md assumes it.
@app.function(
    image=app_image, secrets=[secret], cpu=1.0, memory=2048, scaledown_window=600, max_containers=1
)
@modal.concurrent(max_inputs=50)
@modal.asgi_app(label=API_LABEL, requires_proxy_auth=True)
def api():
    from coglossa_api.app import create_app

    return create_app(_config(), RENDERED)


@app.function(image=app_image, secrets=[secret], cpu=2.0, memory=4096, timeout=86400, max_containers=2)
def worker():
    import asyncio

    from coglossa.store.db import database_url
    from coglossa.worker.app import queue_status, run_worker

    cfg = _config()
    status = asyncio.run(queue_status(database_url(cfg), cfg.worker.stalled_after_s))
    if not status.has_work:
        return  # a late kick or a second spawn: exit before waking the scorer
    asyncio.run(run_worker(cfg, burst=True, install_signal_handlers=False))


@app.function(image=app_image, secrets=[secret], cpu=0.25)
@modal.fastapi_endpoint(method="POST", label=KICK_LABEL, requires_proxy_auth=True)
def kick(authorization: str | None = Header(None)):
    import os

    if not check_kick(authorization, os.environ["COGLOSSA_KICK_TOKEN"]):
        return JSONResponse({"detail": "wrong kick token"}, status_code=401)
    worker.spawn()
    return {"spawned": True}


@app.function(image=app_image, secrets=[secret], cpu=0.25, schedule=modal.Period(minutes=5))
def cron():
    """Starts a worker when a job is waiting and no worker is alive (one queued as a worker
    decided to exit, a failed kick, a retry whose backoff has passed), or when a worker that
    died left jobs stalled."""
    import asyncio

    from coglossa.store.db import database_url
    from coglossa.worker.app import needs_worker

    cfg = _config()
    if asyncio.run(needs_worker(database_url(cfg), cfg.worker.stalled_after_s)):
        worker.spawn()


@app.function(image=app_image, secrets=[secret], timeout=900)
def migrate():
    render_config(CONFIG, RENDERED)
    subprocess.run(["coglossa", "db", "upgrade"], check=True)


@app.function(image=app_image, secrets=[secret], timeout=900)
def doctor():
    render_config(CONFIG, RENDERED)
    subprocess.run(["coglossa", "doctor"], check=False)


# Mirrors deploy/docker/scorer.Dockerfile (the source of truth) with the venv replaced by the
# system Python. uv exports the CUDA build (the TORCH_INDEX extra) from scorer/uv.lock as a
# pylock.toml, which names every wheel's URL and sha256, and installs exactly those files,
# checking each hash, so each package comes from the one index the lock names for it (CGL-50).
# The scorer's own wheel is built by hatchling from the lock's `build` group, hash-checked too
# (CGL-130). deploy/tests/check_scorer_lock.py reads every step of scorer_image.
_SCORER_INSTALL = (
    "cd /src/scorer"
    f" && uv export --quiet --frozen --no-default-groups --extra {TORCH_INDEX} --no-emit-project"
    " --format pylock.toml -o /tmp/pylock.toml"
    " && uv export --quiet --frozen --only-group build --no-emit-project"
    " -o /tmp/build-constraints.txt"
    " && uv pip install --system --no-build -r /tmp/pylock.toml"
    " && uv pip install --system --no-deps --build-constraints /tmp/build-constraints.txt"
    " --require-build-hashes ."
)

scorer_image = (
    modal.Image.debian_slim(python_version="3.12")
    .add_local_file(UV_REQUIREMENTS, "/tmp/uv-requirements.txt", copy=True)
    .run_commands(_UV_INSTALL)
    .add_local_dir(REPO / "scorer", "/src/scorer", copy=True, ignore=IGNORE)
    .run_commands(_SCORER_INSTALL)
    # MALLOC_ARENA_MAX: see deploy/docker/scorer.Dockerfile (bounds glibc arena memory growth)
    .env(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HOME": "/tmp/huggingface",
            "MALLOC_ARENA_MAX": "2",
            "PYTHONUNBUFFERED": "1",
        }
    )
    .add_local_python_source("settings")  # every container imports app.py, which imports settings
)


@app.function(
    image=scorer_image,
    gpu="L4",
    memory=16384,
    secrets=[scorer_secret],
    volumes={"/models": weights},
    scaledown_window=300,
    timeout=3600,
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app(label=SCORER_LABEL, requires_proxy_auth=True)
def scorer():
    from coglossa_scorer.cli import build_app

    return build_app(
        [
            "--qe-model", "/models/metricx-24-hybrid-large-v2p6",
            "--qe-tokenizer", "/models/mt5-xl",
            "--embed-model", "/models/labse",
            "--device", "cuda",
        ]
    )
