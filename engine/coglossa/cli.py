"""Command-line interface.

Exit codes: 0 complete (or stopped at the guide checkpoint), 1 failed, 2 usage error,
3 complete with errors, 130 interrupted."""

import argparse
import asyncio
import contextlib
import json
import shlex
import sys
from pathlib import Path

from pydantic import ValidationError

from coglossa import __version__
from coglossa.bench.cli import add_bench_commands, run_bench_command
from coglossa.cli_ops import COMMANDS, add_commands, run_command
from coglossa.config import (
    CoglossaConfig,
    ConfigError,
    _first_error,
    default_config_path,
    load_config,
)
from coglossa.ingest.base import IngestError
from coglossa.llm.registry import build_backends
from coglossa.pipeline import GuideCheckpoint, RunOptions, run_document
from coglossa.schema import (
    DocumentProfile,
    FlagCode,
    FlagStatus,
    Guide,
    PipelineProfile,
    Report,
    RunStatus,
    ScorerHealth,
)
from coglossa.scoring.client import Scorer, build_scorer, probe_scorer
from coglossa.stages.context import StageError
from coglossa.testing.fake_openai import FakeOpenAIServer
from coglossa.testing.fake_scorer import FakeScorerServer

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_WITH_ERRORS = 0, 1, 2, 3
EXIT_INTERRUPTED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coglossa", description="Multi-agent translation with verifiable, scored output."
    )
    parser.add_argument("--version", action="version", version=f"coglossa {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="translate a document")
    run.add_argument(
        "input",
        type=Path,
        help="TXT, MD, DOCX, PPTX, XLSX, HTML, EPUB or PDF (with a text layer) file",
    )
    run.add_argument("--config", type=Path, default=default_config_path())
    run.add_argument("--target-lang", required=True, help="BCP-47 code, e.g. en, pt-BR")
    run.add_argument("--source-lang", help="override language detection")
    run.add_argument("--profile", choices=[p.value for p in PipelineProfile])
    run.add_argument("--guide", type=Path, help="guide JSON from a previous run")
    run.add_argument(
        "--approved-guide",
        action="store_true",
        help="use --guide as-is: skip guide generation and the checkpoint",
    )
    run.add_argument(
        "--profile-file",
        type=Path,
        help="profile.json from a previous run (edit genre/register there); skips profiling",
    )
    run.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="do not stop for guide review, even for long-form documents",
    )
    run.add_argument("--out", type=Path, help="output directory (default: coglossa-out/<name>)")

    fake = sub.add_parser(
        "fake-openai", help="serve a synthetic OpenAI-compatible endpoint (demos, tests)"
    )
    fake.add_argument("--host", default="127.0.0.1")
    fake.add_argument("--port", type=int, default=8000)
    fake.add_argument("--model", action="append", dest="models", help="model id to advertise")

    fake_scorer = sub.add_parser(
        "fake-scorer", help="serve a synthetic scorer service (demos, tests)"
    )
    fake_scorer.add_argument("--host", default="127.0.0.1")
    fake_scorer.add_argument("--port", type=int, default=8080)
    add_commands(sub)
    add_bench_commands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in COMMANDS:
        return run_command(args)
    if args.command == "bench":
        return run_bench_command(args)
    if args.command == "fake-openai":
        return _fake_openai(args)
    if args.command == "fake-scorer":
        return _fake_scorer(args)
    if args.approved_guide and args.guide is None:
        parser.error("--approved-guide requires --guide")
    return _run(args)


def _fake_openai(args: argparse.Namespace) -> int:
    models = tuple(args.models or ["fake-model"])
    server = FakeOpenAIServer(args.host, args.port, models, keep_requests=False)
    print(f"fake OpenAI-compatible server at {server.base_url} (Ctrl+C to stop)", flush=True)
    try:
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    finally:
        server.stop()
    return EXIT_OK


def _fake_scorer(args: argparse.Namespace) -> int:
    server = FakeScorerServer(args.host, args.port, keep_requests=False)
    print(f"fake scorer at {server.base_url} (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return EXIT_OK


def _run(args: argparse.Namespace) -> int:
    try:
        return _run_checked(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as e:  # last resort: never show a traceback to the user
        print(f"error: internal error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_FAILED


def _progress(message: str) -> None:
    print(f"coglossa: {message}", file=sys.stderr, flush=True)


def _run_checked(args: argparse.Namespace) -> int:
    try:
        cfg, warnings = load_config(args.config)
        for warning in warnings:
            print(f"warning: {warning.message}", file=sys.stderr)
        opts = RunOptions(
            target_lang=args.target_lang,
            source_lang=args.source_lang,
            pipeline_profile=PipelineProfile(args.profile) if args.profile else None,
            guide=_load_guide(args.guide) if args.guide else None,
            guide_approved=args.approved_guide,
            profile=_load_profile(args.profile_file) if args.profile_file else None,
            checkpoint=False if args.no_checkpoint else None,
        )
        out_dir = _prepare_out_dir(args.out or Path("coglossa-out") / args.input.stem)
        result = asyncio.run(_execute(args.input, cfg, opts))
    except (ConfigError, IngestError, StageError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_FAILED
    try:
        if isinstance(result, GuideCheckpoint):
            return _write_checkpoint(result, out_dir, args)
        return _write_report(result, out_dir)
    except OSError as e:
        print(f"error: cannot write results to {out_dir}: {e}", file=sys.stderr)
        return EXIT_FAILED


def _prepare_out_dir(out_dir: Path) -> Path:
    """Create the output directory up front so a typo does not cost a multi-hour run."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ConfigError(f"cannot write output directory {out_dir}: {e}") from e
    return out_dir


async def _execute(path: Path, cfg: CoglossaConfig, opts: RunOptions) -> Report | GuideCheckpoint:
    backends = build_backends(cfg)
    scorer: Scorer | None = None
    try:
        scorer = build_scorer(cfg)
        health = await _check_scorer(scorer, cfg) if scorer is not None else None
        return await run_document(
            path, cfg, backends, opts, progress=_progress, scorer=scorer, scorer_health=health
        )
    finally:
        for backend in backends.values():
            await backend.aclose()
        if scorer is not None:
            await scorer.aclose()


async def _check_scorer(scorer: Scorer, cfg: CoglossaConfig) -> ScorerHealth | None:
    health, warnings = await probe_scorer(scorer, cfg)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return health


def _load_guide(path: Path) -> Guide:
    if not path.is_file():
        raise ConfigError(f"guide file not found: {path}")
    try:
        return Guide.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(f"{path} is not a valid guide: {_first_error(e)}") from e


def _load_profile(path: Path) -> DocumentProfile:
    if not path.is_file():
        raise ConfigError(f"profile file not found: {path}")
    try:
        return DocumentProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(f"{path} is not a valid profile file: {_first_error(e)}") from e
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path} is not a valid profile file: {e}") from e


def _resume_command(args: argparse.Namespace, out_dir: Path) -> str:
    argv = ["coglossa", "run", str(args.input.resolve())]
    argv += ["--config", str(args.config.resolve()), "--target-lang", args.target_lang]
    if args.source_lang:
        argv += ["--source-lang", args.source_lang]
    if args.profile:
        argv += ["--profile", args.profile]
    argv += ["--out", str(out_dir.resolve())]
    argv += ["--guide", str((out_dir / "guide.json").resolve()), "--approved-guide"]
    argv += ["--profile-file", str((out_dir / "profile.json").resolve())]
    return shlex.join(argv)


def _write_checkpoint(result: GuideCheckpoint, out_dir: Path, args: argparse.Namespace) -> int:
    guide_path = out_dir / "guide.json"
    guide_path.write_text(result.guide.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "profile.json").write_text(
        result.profile.model_dump_json(indent=2), encoding="utf-8"
    )
    if result.errors:
        errors_path = out_dir / "guide_errors.json"
        errors_path.write_text(
            json.dumps([e.model_dump() for e in result.errors], indent=2), encoding="utf-8"
        )
        print(
            f"warning: {len(result.errors)} excerpt(s) failed while building the guide; "
            f"see {errors_path}",
            file=sys.stderr,
        )
    print(
        f"Guide ready for review: {guide_path}\n"
        'Edit it (set "locked": true on glossary entries and entities that must be used as\n'
        "written: locked entries are mandatory, the sweep enforces them and flags any it cannot\n"
        "fix), then run:\n"
        f"  {_resume_command(args, out_dir)}"
    )
    return EXIT_OK


def _write_report(report: Report, out_dir: Path) -> int:
    report_path = out_dir / "report.json"
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    flags = sum(1 for s in report.spans for f in s.flags if f.status is FlagStatus.OPEN)
    tokens = report.usage.tokens_in + report.usage.tokens_out
    for notice in report.notices:
        print(f"notice: {notice.explanation}", file=sys.stderr)
    unscored = any(n.code is FlagCode.UNSCORED for n in report.notices)
    tier = "unscored" if unscored else f"tier {report.language_tier.value}"
    revised = f", {len(report.revisions)} revised by the sweep" if report.revisions else ""
    print(
        f"{report.status.value}: {len(report.spans)} spans, {flags} flags, "
        f"{len(report.errors)} errors, {tokens} tokens ({tier}){revised}\n"
        f"report: {report_path}"
    )
    return EXIT_OK if report.status is RunStatus.COMPLETE else EXIT_WITH_ERRORS
