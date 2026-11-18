"""Checks the compose files as Docker Compose resolves them (docker compose config). Needs
Docker with the Compose plugin; no images and no daemon state are needed.
Usage: python3 deploy/tests/check_compose.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
PASSWORD = "check-password"


def config(*files: str, env: dict[str, str] | None = None) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".env") as empty:  # ignore a local deploy/.env
        cmd = ["docker", "compose", "--env-file", empty.name]
        for f in files:
            cmd += ["-f", str(DEPLOY / f)]
        full_env = {**os.environ, "COGLOSSA_DB_PASSWORD": PASSWORD, **(env or {})}
        for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME"):
            full_env.pop(name, None)
        out = subprocess.run(
            [*cmd, "config", "--format", "json"], env=full_env, capture_output=True, text=True
        )
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def check(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    base = config("compose.yaml")
    s = base["services"]
    check(base["name"] == "coglossa", "the project is named coglossa")
    check(set(s) == {"postgres", "migrate", "scorer", "worker", "api"}, f"services: {sorted(s)}")
    check(set(base["volumes"]) == {"pgdata", "files"}, "named volumes pgdata and files")
    for name, svc in s.items():
        check(":" in svc["image"] and not svc["image"].endswith(":latest"), f"{name}: pinned image")
        check(svc["logging"]["driver"] == "json-file", f"{name}: json-file logs")
        check(
            svc["logging"]["options"] == {"max-size": "50m", "max-file": "5"}, f"{name}: rotation"
        )
        want = "no" if name == "migrate" else "unless-stopped"
        check(svc.get("restart") == want, f"{name}: restart {want}")
    check(
        s["api"]["image"] == "coglossa/app:dev" and s["scorer"]["image"] == "coglossa/scorer:dev",
        "images default to the dev tag",
    )
    for name in ("migrate", "worker", "api"):
        svc = s[name]
        url = svc["environment"]["COGLOSSA_DATABASE_URL"]
        check(
            url == f"postgresql://coglossa:{PASSWORD}@postgres:5432/coglossa",
            f"{name}: database URL",
        )
        mounts = {(v["target"], v.get("read_only", False)) for v in svc["volumes"]}
        check(
            ("/etc/coglossa", True) in mounts and ("/data/files", False) in mounts,
            f"{name}: mounts",
        )
    # Review Focus 4: a model server on the Docker host is host.docker.internal
    for name in ("worker", "api"):
        hosts = json.dumps(s[name].get("extra_hosts"))
        check(
            "host.docker.internal" in hosts and "host-gateway" in hosts,
            f"{name}: host.docker.internal",
        )
    # Review Focus 5: no run starts before the scorer has loaded its weights
    check(
        s["worker"]["depends_on"]["scorer"]["condition"] == "service_healthy",
        "worker waits for the scorer",
    )
    check(
        s["worker"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully",
        "worker waits for the migrations",
    )
    check(
        s["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully",
        "api waits for the migrations",
    )
    check(
        s["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy",
        "migrate waits for postgres",
    )
    check(
        s["migrate"]["command"] == ["coglossa", "db", "upgrade"], "migrate runs coglossa db upgrade"
    )
    check(s["worker"]["command"] == ["coglossa", "worker"], "worker runs coglossa worker")
    # bench reads the bundle's FLORES-200 data; a bundle without it still starts. Compose 5
    # prints create_host_path: true as `bind: {}` (true is its default) and an unset one as no
    # `bind` at all; older versions, which need it set, print it.
    bench = [v for v in s["worker"]["volumes"] if v["target"] == "/opt/coglossa/bench"]
    check(
        len(bench) == 1
        and bench[0]["type"] == "bind"
        and bench[0]["source"] == str(DEPLOY / "bench")
        and bench[0].get("read_only") is True
        and "bind" in bench[0]
        and bench[0]["bind"].get("create_host_path", True) is True,
        f"worker: ./bench read-only at /opt/coglossa/bench, created if missing: {bench}",
    )
    for name in ("postgres", "scorer", "api"):
        check("test" in s[name].get("healthcheck", {}), f"{name}: health check")
    # CGL-48: a slow CPU host loads MetricX for longer than ten minutes. The start period is
    # the scorer's whole loading budget (failures in it don't count); interval and retries stay
    # short, so a healthy scorer is seen within 15 s and a crashed one within about 75 s.
    hc = s["scorer"]["healthcheck"]
    check(
        hc.get("start_period") in ("15m0s", "900s", "15m"),
        f"the scorer's health check allows 15 minutes to load the weights: {hc}",
    )
    check(
        (hc.get("interval"), hc.get("retries")) == ("15s", 5),
        f"the scorer's health check probes every 15 s and fails after 5 misses: {hc}",
    )
    slow = config("compose.yaml", env={"COGLOSSA_SCORER_START_PERIOD": "30m"})
    check(
        slow["services"]["scorer"]["healthcheck"].get("start_period") in ("30m0s", "1800s", "30m"),
        "COGLOSSA_SCORER_START_PERIOD in .env raises the scorer's start period",
    )
    check(
        s["scorer"]["volumes"][0]["target"] == "/models" and s["scorer"]["volumes"][0]["read_only"],
        "the scorer mounts models/ read-only at /models",
    )
    check(
        "/models/metricx-24-hybrid-large-v2p6" in s["scorer"]["command"]
        and s["scorer"]["command"][s["scorer"]["command"].index("--device") + 1] == "cpu",
        "the scorer defaults to MetricX large on CPU",
    )
    port = s["api"]["ports"][0]
    check(
        (port["host_ip"], port["published"], port["target"]) == ("0.0.0.0", "8080", 8080),
        f"api port: {port}",
    )
    bound = config(
        "compose.yaml", env={"COGLOSSA_HTTP_BIND": "127.0.0.1", "COGLOSSA_HTTP_PORT": "9090"}
    )
    port = bound["services"]["api"]["ports"][0]
    check((port["host_ip"], port["published"]) == ("127.0.0.1", "9090"), "COGLOSSA_HTTP_BIND/PORT")
    # CGL-61: the api container sees where it is published, to warn about an open port
    check(
        s["api"]["environment"]["COGLOSSA_HTTP_BIND"] == "0.0.0.0"
        and bound["services"]["api"]["environment"]["COGLOSSA_HTTP_BIND"] == "127.0.0.1",
        "the api is told COGLOSSA_HTTP_BIND",
    )
    check(
        s["worker"].get("stop_grace_period") in ("2m0s", "120s", "2m"),
        "the worker gets 2 minutes to stop",
    )

    gpu = config("compose.yaml", "compose.gpu.yaml")["services"]["scorer"]
    devices = gpu["deploy"]["resources"]["reservations"]["devices"]
    check(
        devices[0]["driver"] == "nvidia" and "gpu" in devices[0]["capabilities"],
        "gpu: an NVIDIA device",
    )
    check(gpu["image"] == "coglossa/scorer:dev-cuda", "gpu: the CUDA scorer image, its own tag")
    check(
        "/models/metricx-24-hybrid-xl-v2p6" in gpu["command"]
        and gpu["command"][gpu["command"].index("--device") + 1] == "cuda",
        "gpu: MetricX XL on cuda",
    )

    demo_project = config("compose.yaml", "compose.demo.yaml")
    check(demo_project["name"] == "coglossa-demo", "demo: its own project, coglossa-demo")
    demo = demo_project["services"]
    check("fake-openai" in demo, "demo: a fake model server")
    check(
        demo["scorer"]["image"] == "coglossa/app:dev"
        and demo["scorer"]["command"][:2] == ["coglossa", "fake-scorer"],
        "demo: the fake scorer",
    )
    check(not demo["scorer"].get("volumes"), "demo: the fake scorer needs no weights")
    check(
        demo["scorer"]["healthcheck"].get("start_period") in ("0s", "0"),
        "demo: the fake scorer has no start period",
    )
    for name in ("migrate", "worker", "api"):
        check(
            demo[name]["environment"]["COGLOSSA_CONFIG"] == "/etc/coglossa/coglossa.demo.yaml",
            f"demo: {name} reads the demo config",
        )
    # the overlay's depends_on merges with compose.yaml's: the worker still waits for both
    waits = {k: v["condition"] for k, v in demo["worker"]["depends_on"].items()}
    check(
        waits
        == {
            "fake-openai": "service_healthy",
            "scorer": "service_healthy",
            "migrate": "service_completed_successfully",
        },
        f"demo: the worker waits for the fake model server, the scorer and migrate: {waits}",
    )

    missing = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(DEPLOY / "compose.yaml"),
            "config",
        ],
        env={k: v for k, v in os.environ.items() if k != "COGLOSSA_DB_PASSWORD"},
        capture_output=True,
        text=True,
    )
    check(
        missing.returncode != 0 and "run ./install.sh first" in missing.stderr,
        "without COGLOSSA_DB_PASSWORD, compose says to run install.sh",
    )
    print("ok: compose files")


if __name__ == "__main__":
    main()
