"""CLI commands for queued runs (spec §7): database migration, the worker, and the run
lifecycle. They talk to Postgres; `coglossa run` does not need any of them."""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from coglossa import catalog, service
from coglossa.config import CoglossaConfig, ConfigError, default_config_path, load_config
from coglossa.glossary_import import GlossaryFileError
from coglossa.ingest.base import IngestError
from coglossa.logs import configure_json_logging, remove_json_logging
from coglossa.prompts.check import validate_prompts
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import CheckStatus, PipelineProfile, RunState
from coglossa.schema.views import GlossaryImportPreview
from coglossa.storage import StorageError, build_storage
from coglossa.store.db import Database, database_url, error_reason, redact
from coglossa.store.migrate import connect_error, ensure_migrated, migration_state, upgrade
from coglossa.store.runs import NotFoundError, find_project
from coglossa.worker.app import ProcrastinateQueue, open_app, run_worker
from coglossa.worker.execute import DB_ERRORS

EXIT_OK, EXIT_FAILED, EXIT_INTERRUPTED = 0, 1, 130
USER_ERRORS = (
    ConfigError,
    IngestError,
    StorageError,
    NotFoundError,
    service.InvalidRunStateError,
    service.ConflictError,
    service.InvalidRequestError,
)


def _config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=default_config_path())


def add_commands(sub: argparse._SubParsersAction) -> None:
    db = sub.add_parser("db", help="database maintenance")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    _config_arg(db_sub.add_parser("upgrade", help="create or migrate the database schema"))

    worker = sub.add_parser("worker", help="process queued runs")
    _config_arg(worker)
    worker.add_argument("--burst", action="store_true", help="exit when the queue is empty")

    submit = sub.add_parser("submit", help="queue a document for translation")
    _config_arg(submit)
    submit.add_argument("input", type=Path)
    submit.add_argument("--target-lang", required=True)
    submit.add_argument("--source-lang")
    submit.add_argument("--profile", choices=[p.value for p in PipelineProfile])
    submit.add_argument(
        "--project",
        default="default",
        help="documents in a project share one translation guide per language",
    )
    checkpoint = submit.add_mutually_exclusive_group()
    checkpoint.add_argument("--checkpoint", dest="checkpoint", action="store_const", const=True)
    checkpoint.add_argument("--no-checkpoint", dest="checkpoint", action="store_const", const=False)
    submit.add_argument(
        "--use-project-guide",
        action="store_true",
        help="use the project guide as-is (no guide generation)",
    )

    for name, help_text in (
        ("status", "show a run's progress"),
        ("cancel", "cancel a run (finished passages are kept)"),
        ("retry", "retry failed chunks, or resume a run whose job was lost"),
    ):
        p = sub.add_parser(name, help=help_text)
        _config_arg(p)
        p.add_argument("run_id")

    guide = sub.add_parser("guide", help="print or save a run's guide")
    _config_arg(guide)
    guide.add_argument("run_id")
    guide.add_argument("--out", type=Path)

    approve = sub.add_parser("approve-guide", help="approve a run's guide and start translating")
    _config_arg(approve)
    approve.add_argument("run_id")
    approve.add_argument("--guide", type=Path, help="edited guide JSON (default: as generated)")

    glossary = sub.add_parser(
        "import-glossary",
        help="add the terms of a CSV, TSV or TBX glossary to a project guide",
        description="Compares the file with the project guide in one language and adds its new "
        "terms as a new guide version. Terms the guide already has with another translation "
        "are reported and kept as they are unless --overwrite-conflicts is given.",
    )
    _config_arg(glossary)
    glossary.add_argument("input", type=Path, help="CSV or TSV (term, translation) or TBX file")
    glossary.add_argument("--project", default="default", help="the project whose guide it is")
    glossary.add_argument(
        "--target-lang", help="the guide's language (default: the project's first)"
    )
    glossary.add_argument(
        "--source-lang", help="TBX: the language of the terms (default: the project's)"
    )
    glossary.add_argument("--lock-all", action="store_true", help="lock every imported term")
    glossary.add_argument(
        "--overwrite-conflicts",
        action="store_true",
        help="replace the guide's translation where the file differs, locked ones too",
    )
    glossary.add_argument(
        "--dry-run", action="store_true", help="only show what the import would do"
    )

    report = sub.add_parser("report", help="write a finished run's report.json")
    _config_arg(report)
    report.add_argument("run_id")
    report.add_argument("--out", type=Path)

    delete = sub.add_parser("delete-project", help="delete a project and everything in it")
    _config_arg(delete)
    delete.add_argument("name")
    delete.add_argument("--yes", action="store_true", help="confirm the deletion")

    doctor = sub.add_parser("doctor", help="check the installation")
    _config_arg(doctor)
    doctor.add_argument("--json", action="store_true", help="print the result as JSON")
    doctor.add_argument("--no-probe", action="store_true", help="skip the context-window probe")


def _load(args: argparse.Namespace) -> CoglossaConfig:
    cfg, warnings = load_config(args.config)
    for warning in warnings:
        print(f"warning: {warning.message}", file=sys.stderr)
    return cfg


def _run_id(text: str) -> uuid.UUID:
    try:
        return uuid.UUID(text)
    except ValueError:
        raise ConfigError(f"'{text}' is not a run id") from None


def _with_db[T](cfg: CoglossaConfig, fn: Callable[[Database], Awaitable[T]]) -> T:
    url = database_url(cfg)
    ensure_migrated(url)  # one clear line instead of a missing-table error

    async def go() -> T:
        db = Database(url)
        try:
            return await fn(db)
        finally:
            await db.dispose()

    return asyncio.run(go())


def _with_queue[T](
    cfg: CoglossaConfig, fn: Callable[[Database, service.JobQueue], Awaitable[T]]
) -> T:
    async def go(db: Database) -> T:
        async with open_app(db.url) as opened:
            return await fn(db, ProcrastinateQueue(opened))

    return _with_db(cfg, go)


def _db(args: argparse.Namespace) -> int:
    cfg = _load(args)
    url = database_url(cfg)
    try:
        upgrade(url)
    except DB_ERRORS as e:
        raise connect_error(url, e) from e
    print(f"database at {redact(url)} is at revision {migration_state(url).head}")
    return EXIT_OK


def _worker(args: argparse.Namespace) -> int:
    cfg = _load(args)
    handler = configure_json_logging()
    try:
        asyncio.run(run_worker(cfg, burst=args.burst))
    finally:
        remove_json_logging(handler)
    return EXIT_OK


def _submit(args: argparse.Namespace) -> int:
    cfg = _load(args)
    prompts = PromptLibrary(cfg.prompts_dir)
    validate_prompts(cfg, prompts)
    storage = build_storage(cfg.storage)
    opts = service.SubmitOptions(
        project=args.project,
        target_lang=args.target_lang,
        source_lang=args.source_lang,
        pipeline_profile=PipelineProfile(args.profile) if args.profile else None,
        checkpoint=args.checkpoint,
        use_project_guide=args.use_project_guide,
    )
    run_id = _with_queue(
        cfg, lambda db, q: service.submit_run(db, storage, q, cfg, prompts, args.input, opts)
    )
    print(
        f"run {run_id} queued\n"
        f"  progress: coglossa status {run_id}\n"
        "  a worker must be running: coglossa worker"
    )
    return EXIT_OK


_NEXT = {
    RunState.AWAITING_GUIDE_APPROVAL: (
        "review the guide: coglossa guide {id} --out guide.json, then "
        "coglossa approve-guide {id} [--guide guide.json]"
    ),
    RunState.COMPLETE: "coglossa report {id}",
    RunState.COMPLETE_WITH_ERRORS: "coglossa report {id} (or coglossa retry {id})",
    RunState.FAILED: "fix the cause, then coglossa retry {id}",
}


def _status(args: argparse.Namespace) -> int:
    cfg = _load(args)
    run_id = _run_id(args.run_id)
    p = _with_db(cfg, lambda db: service.run_progress(db, run_id))
    lines = [f"run {run_id}: {p.state.value}"]
    if p.chunks_total:
        done = ", ".join(f"{stage} {n}/{p.chunks_total}" for stage, n in p.chunks_done.items())
        lines.append(f"  chunks: {done} ({p.chunks_with_errors} with errors)")
    if p.error:
        lines.append(f"  error: {p.error}")
    if p.state is RunState.COMPLETE_WITH_ERRORS and not p.retryable:
        # only errors of earlier stages, which a retry does not re-run
        lines.append(f"  next: coglossa report {run_id}")
    elif p.state in _NEXT:
        lines.append(f"  next: {_NEXT[p.state].format(id=run_id)}")
    print("\n".join(lines))
    return EXIT_OK


def _guide(args: argparse.Namespace) -> int:
    cfg = _load(args)
    run_id = _run_id(args.run_id)
    guide = _with_db(cfg, lambda db: service.run_guide(db, run_id))
    text = guide.model_dump_json(indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"guide written to {args.out}")
    else:
        print(text)
    return EXIT_OK


def _approve(args: argparse.Namespace) -> int:
    from coglossa.cli import _load_guide

    cfg = _load(args)
    run_id = _run_id(args.run_id)
    guide = _load_guide(args.guide) if args.guide else None
    version = _with_queue(cfg, lambda db, q: service.approve_guide(db, q, run_id, guide))
    print(f"guide version {version} approved; translation queued")
    return EXIT_OK


def _preview_lines(name: str, p: GlossaryImportPreview) -> list[str]:
    locked = f" ({p.locked_conflicts} locked)" if p.locked_conflicts else ""
    lines = [
        f"{name} ({p.target_lang} guide): {p.new} new, {p.identical} already in the guide, "
        f"{p.conflicts} different from the guide{locked}, {p.duplicates} repeated in the file"
    ]
    if p.skipped_entries:
        lines.append(f"  entries left out, without a term in both languages: {p.skipped_entries}")
    for t in p.terms:
        if t.status == "conflict":
            lock = ", locked" if t.current_locked else ""
            lines.append(
                f"  line {t.line}: {t.term} → {t.rendering} (the guide has {t.current_rendering}"
                f"{lock})"
            )
        elif t.status == "duplicate":
            lines.append(f"  line {t.line}: {t.term} repeats line {t.first_line}, which is used")
    return lines


def _import_glossary(args: argparse.Namespace) -> int:
    cfg = _load(args)
    try:
        data = args.input.read_bytes()
    except OSError as e:
        raise GlossaryFileError(f"cannot read {args.input}: {e.strerror}") from None
    file = catalog.GlossaryFile(data, args.input.name, args.source_lang)

    async def go(db: Database) -> list[str]:
        async with db.session() as s:
            project_id = (await find_project(s, args.project)).id
        preview = await catalog.preview_glossary_import(
            db, project_id, file, target_lang=args.target_lang
        )
        lines = _preview_lines(args.input.name, preview)
        if args.dry_run:
            return [*lines, "dry run: nothing was saved"]
        r = await catalog.import_glossary(
            db,
            project_id,
            file,
            base_version=preview.base_version,
            target_lang=args.target_lang,
            lock_all=args.lock_all,
            overwrite_conflicts=args.overwrite_conflicts,
        )
        done = f"{r.added} added, {r.updated} replaced, {r.locked} newly locked"
        if not r.created_version:
            lines.append(f"nothing to change: the guide stays at version {r.view.info.version}")
        else:
            lines.append(f"guide version {r.view.info.version} saved: {done}")
        if r.kept:
            lines.append(
                f"{r.kept} different translations kept as the guide has them "
                "(--overwrite-conflicts replaces them)"
            )
        return lines

    print("\n".join(_with_db(cfg, go)))
    return EXIT_OK


def _cancel(args: argparse.Namespace) -> int:
    cfg = _load(args)
    run_id = _run_id(args.run_id)
    state = _with_db(cfg, lambda db: service.cancel_run(db, run_id))
    if state is RunState.CANCELLED:
        print(f"run {run_id}: cancelled")
    else:
        print(
            f"run {run_id}: cancel requested; running work stops after the current chunk or stage"
        )
    return EXIT_OK


def _retry(args: argparse.Namespace) -> int:
    cfg = _load(args)
    run_id = _run_id(args.run_id)
    message = _with_queue(cfg, lambda db, q: service.retry_run(db, q, run_id))
    print(f"run {run_id}: {message}")
    return EXIT_OK


def _report(args: argparse.Namespace) -> int:
    from coglossa.cli import _prepare_out_dir, _write_report

    cfg = _load(args)
    run_id = _run_id(args.run_id)
    out_dir = _prepare_out_dir(args.out or Path("coglossa-out") / str(run_id))
    report = _with_db(cfg, lambda db: service.build_report(db, run_id))
    return _write_report(report, out_dir)


def _delete_project(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ConfigError(
            f"deleting project '{args.name}' removes all of its documents, runs and files; "
            "pass --yes to confirm"
        )
    cfg = _load(args)
    storage = build_storage(cfg.storage)
    _with_db(cfg, lambda db: service.delete_project(db, storage, args.name))
    print(f"project {args.name} deleted")
    return EXIT_OK


def _doctor(args: argparse.Namespace) -> int:
    from coglossa.doctor import run_doctor

    report = asyncio.run(run_doctor(args.config, probe_context=not args.no_probe))
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        for c in report.checks:
            print(f"{c.status.value.upper():<5} {c.name}: {c.message}")
            if c.fix and c.status is not CheckStatus.PASS:
                print(f"      fix: {c.fix}")
    return EXIT_OK if report.ok else EXIT_FAILED


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "db": _db,
    "worker": _worker,
    "submit": _submit,
    "status": _status,
    "guide": _guide,
    "approve-guide": _approve,
    "import-glossary": _import_glossary,
    "cancel": _cancel,
    "retry": _retry,
    "report": _report,
    "delete-project": _delete_project,
    "doctor": _doctor,
}


def run_command(args: argparse.Namespace) -> int:
    try:
        return COMMANDS[args.command](args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except USER_ERRORS as e:
        print(f"error: {e}", file=sys.stderr)
    except DB_ERRORS as e:
        print(f"error: cannot reach the database: {error_reason(e)}", file=sys.stderr)
    except Exception as e:  # last resort: never show a traceback to the user
        print(f"error: internal error: {type(e).__name__}: {e}", file=sys.stderr)
    return EXIT_FAILED
