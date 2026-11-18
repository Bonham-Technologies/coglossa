"""A complete local Coglossa for a browser: a throwaway database, the fake model and scorer
servers, a worker, and the API serving the built web app. `make dev-stack` runs it for
development (with `pnpm dev` for live reloading); the Playwright journeys start it as their web
server. Ctrl+C (or SIGTERM) stops everything and drops the database."""

import argparse
import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from types import FrameType

import yaml
from coglossa.store.migrate import upgrade
from coglossa.testing.postgres import TEST_DB_ENV, create_test_database, drop_test_database

COGLOSSA = [sys.executable, "-c", "import sys; from coglossa.cli import main; sys.exit(main())"]
API = [sys.executable, "-c", "import sys; from coglossa_api.main import main; sys.exit(main())"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url: str, timeout_s: float, procs: list[subprocess.Popen[bytes]]) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for p in procs:
            if p.poll() is not None:
                raise RuntimeError(f"{p.args} exited with {p.returncode}")
        with (
            contextlib.suppress(OSError),
            urllib.request.urlopen(url, timeout=1) as response,  # noqa: S310 (local URL)
        ):
            if response.status == 200:
                return
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not answer within {timeout_s:.0f} s")


def config(
    work: Path, llm_port: int, scorer_port: int, web_dir: Path | None, people: Path | None = None
) -> dict:
    roles = {
        role: {"model": "m1"}
        for role in ("literal_translator", "contextual_translator", "reviewer", "profiler_guide")
    }
    roles |= {"judge": {"model": "m2"}, "roundtrip": {"model": "m2"}}
    api: dict = {"progress_poll_s": 0.2}
    if web_dir is not None:
        api["web_dir"] = str(web_dir.resolve())
    extra: dict = {}
    if people is not None:  # CGL-26: sign in by the X-Remote-User header; teams from the file
        extra["auth"] = {"mode": "trusted_header", "people_file": str(people.resolve())}
    return {
        **extra,
        "backends": {
            "local": {"base_url": f"http://127.0.0.1:{llm_port}/v1", "max_concurrency": 4}
        },
        "models": {
            "m1": {"backend": "local", "name": "fake-model", "context_window": 32768},
            "m2": {"backend": "local", "name": "fake-model", "context_window": 32768},
        },
        "roles": roles,
        "scorer": {"url": f"http://127.0.0.1:{scorer_port}"},
        "storage": {"path": str(work / "files")},
        "api": api,
        "profiles": {"sweep": {"short": True}},  # demo documents are short; keep the sweep on
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--web-dir", type=Path, help="the built web app (web/dist)")
    parser.add_argument(
        "--people", type=Path, help="a people file: sign in by the X-Remote-User header (CGL-26)"
    )
    args = parser.parse_args(argv)
    admin = os.environ.get(TEST_DB_ENV)
    if not admin:
        print(
            f"error: set {TEST_DB_ENV} to a Postgres admin URL (make dev-stack does)",
            file=sys.stderr,
        )
        return 1
    url = create_test_database(admin)
    work = Path(tempfile.mkdtemp(prefix="coglossa-dev-"))
    procs: list[subprocess.Popen[bytes]] = []

    def stop(signum: int | None = None, frame: FrameType | None = None) -> None:
        # Ignore further signals so that a repeated one cannot interrupt the cleanup.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        upgrade(url)
        llm, scorer = free_port(), free_port()
        path = work / "coglossa.yaml"
        path.write_text(
            yaml.safe_dump(config(work, llm, scorer, args.web_dir, args.people)), encoding="utf-8"
        )
        # COGLOSSA_HTTP_BIND, as compose sets it: doctor's auth check goes by it (CGL-61)
        env = {**os.environ, "COGLOSSA_DATABASE_URL": url, "COGLOSSA_HTTP_BIND": args.host}
        host = ["--host", "127.0.0.1"]
        procs.append(
            subprocess.Popen([*COGLOSSA, "fake-openai", *host, "--port", str(llm)], env=env)
        )
        procs.append(
            subprocess.Popen([*COGLOSSA, "fake-scorer", *host, "--port", str(scorer)], env=env)
        )
        procs.append(subprocess.Popen([*COGLOSSA, "worker", "--config", str(path)], env=env))
        procs.append(
            subprocess.Popen(
                [*API, "--config", str(path), "--host", args.host, "--port", str(args.port)],
                env=env,
            )
        )
        wait_for(f"http://{args.host}:{args.port}/api/health", 60, procs)
        address = f"http://{args.host}:{args.port}"
        if args.web_dir is not None:
            print(f"Coglossa is running at {address} (Ctrl+C to stop)", flush=True)
        else:  # the API alone: the app comes from `pnpm dev`, which forwards /api here
            print(
                f"The Coglossa API is running at {address}; for the web app, run `pnpm dev` in "
                "web/ and open the address it prints (Ctrl+C to stop)",
                flush=True,
            )
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        print("error: a service stopped; shutting down", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=10)
            if p.poll() is None:
                p.kill()
        drop_test_database(admin, url)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
