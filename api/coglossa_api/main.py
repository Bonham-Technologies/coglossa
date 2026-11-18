"""`coglossa-api`: serve the REST API. It refuses to start, with one line, on an invalid
config, prompts that do not render, an unreachable or unmigrated database, or a storage path
it cannot write (spec §7.3)."""

import argparse
import sys
from pathlib import Path

import uvicorn
from coglossa.config import (
    ConfigError,
    default_config_path,
    load_config,
    proxy_bypass_address,
    proxy_bypass_warning,
)
from coglossa.logs import configure_json_logging, remove_json_logging
from coglossa.prompts.check import validate_prompts
from coglossa.prompts.library import PromptLibrary
from coglossa.storage import StorageError, build_storage
from coglossa.store.db import database_url
from coglossa.store.migrate import ensure_migrated

from coglossa_api.app import create_app
from coglossa_api.web import missing_index

EXIT_OK, EXIT_FAILED = 0, 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coglossa-api", description="Serve the Coglossa REST API."
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 inside a container")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--web-dir", type=Path, help="the built web app (web/dist); overrides api.web_dir"
    )
    args = parser.parse_args(argv)
    try:
        cfg, warnings = load_config(args.config)
        for warning in warnings:
            print(f"warning: {warning.message}", file=sys.stderr)
        if (address := proxy_bypass_address(cfg.auth, args.host)) is not None:
            message, fix = proxy_bypass_warning(address)
            print(f"warning: {message}; {fix}", file=sys.stderr)
        if args.web_dir is not None:
            api = cfg.api.model_copy(update={"web_dir": args.web_dir.resolve()})
            cfg = cfg.model_copy(update={"api": api})
        validate_prompts(cfg, PromptLibrary(cfg.prompts_dir))
        ensure_migrated(database_url(cfg))
        build_storage(cfg.storage).check_writable()
        if cfg.api.web_dir is not None and (problem := missing_index(cfg.api.web_dir)):
            raise ConfigError(problem)
    except (ConfigError, StorageError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_FAILED
    handler = configure_json_logging()
    try:
        uvicorn.run(create_app(cfg, args.config.resolve()), host=args.host, port=args.port)
    finally:
        remove_json_logging(handler)
    return EXIT_OK
