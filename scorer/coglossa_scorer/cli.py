"""coglossa-scorer: serve MetricX-24 QE and LaBSE similarity from local model directories."""

import argparse
import os
import sys

import uvicorn
from fastapi import FastAPI

from coglossa_scorer.app import create_app
from coglossa_scorer.embed import LabseEmbedder
from coglossa_scorer.qe import MetricXQE


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coglossa-scorer", description=__doc__)
    p.add_argument("--qe-model", help="local directory of a MetricX-24 checkpoint")
    p.add_argument(
        "--qe-tokenizer", help="local directory of the mT5 tokenizer (default: --qe-model)"
    )
    p.add_argument("--embed-model", help="local directory of sentence-transformers/LaBSE")
    p.add_argument("--device", default="cpu", help="cpu, cuda, cuda:1, …")
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    return p


def build_app(argv: list[str] | None = None) -> FastAPI:
    """Parse args, load the models, return the app. Exits (as argparse does) on bad arguments;
    prints the error and exits 1 when a model cannot be loaded."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.qe_model and not args.embed_model:
        parser.error("load at least one model: --qe-model and/or --embed-model")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        qe = (
            MetricXQE.from_pretrained(
                args.qe_model,
                args.qe_tokenizer or args.qe_model,
                device=args.device,
                batch_size=args.batch_size,
            )
            if args.qe_model
            else None
        )
        embedder = (
            LabseEmbedder.from_pretrained(args.embed_model, device=args.device)
            if args.embed_model
            else None
        )
    except (OSError, ValueError, ImportError) as e:
        print(f"error: cannot load model: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    token = os.environ.get("COGLOSSA_SCORER_TOKEN") or None  # unset or empty: no auth
    return create_app(qe, embedder, device=args.device, token=token)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        app = build_app(argv)
    except SystemExit as e:
        if e.code == 1:  # model-load failure; argument errors (2) keep propagating
            return 1
        raise
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
