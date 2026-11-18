"""The `coglossa bench` command group."""

import argparse
import asyncio
import sys
from pathlib import Path

from coglossa.bench.absheet import export_ab
from coglossa.bench.calibrate import MAX_FALSE_POSITIVE_RATE, result_lines, run_calibrate
from coglossa.bench.config import load_bench_config
from coglossa.bench.flagtest import run_flag_test
from coglossa.bench.flores import fetch_flores
from coglossa.bench.metrics import SummaryRow, SystemPairResult
from coglossa.bench.run import run_bench
from coglossa.config import ConfigError
from coglossa.scoring.client import HttpScorer


def add_bench_commands(sub: argparse._SubParsersAction) -> None:
    bench = sub.add_parser("bench", help="evaluate translation systems on FLORES-200")
    bench_sub = bench.add_subparsers(dest="bench_command", required=True)

    fetch = bench_sub.add_parser(
        "fetch-flores", help="download FLORES-200 (needs internet; CC BY-SA 4.0)"
    )
    fetch.add_argument("--out", type=Path, required=True, help="directory to put the data in")

    run = bench_sub.add_parser("run", help="run the systems in a bench config and summarise")
    run.add_argument("bench_config", metavar="BENCH.yaml", type=Path, help="the bench config")
    run.add_argument("--out", type=Path, required=True, help="directory for results")
    run.add_argument(
        "--force", action="store_true", help="re-run system-pairs that already finished"
    )

    flags = bench_sub.add_parser(
        "flags", help="measure how often the flags catch injected errors, per tier"
    )
    flags.add_argument("bench_config", metavar="BENCH.yaml", type=Path, help="the bench config")
    flags.add_argument(
        "--scoring-config",
        type=Path,
        required=True,
        metavar="CONFIG",
        help="the Coglossa config whose judge, roundtrip and scorer are under test",
    )
    flags.add_argument("--out", type=Path, required=True, help="directory for results")
    flags.add_argument(
        "--pairs",
        type=int,
        default=1,
        metavar="N",
        help="score up to N language pairs at the same time (default 1)",
    )
    flags.add_argument(
        "--force", action="store_true", help="score again the pairs that already finished"
    )

    calibrate = bench_sub.add_parser(
        "calibrate", help="suggest score thresholds and language tiers from the bench results"
    )
    calibrate.add_argument("--out", type=Path, required=True, help="the bench results directory")
    calibrate.add_argument(
        "--scoring-config",
        type=Path,
        required=True,
        metavar="CONFIG",
        help="the Coglossa config whose thresholds and language tiers are the starting point",
    )
    calibrate.add_argument(
        "--tier-system",
        metavar="NAME",
        help="the system whose MetricX sets the tiers (default: the first pipeline system)",
    )
    calibrate.add_argument(
        "--max-false-positive-rate",
        type=float,
        default=MAX_FALSE_POSITIVE_RATE,
        metavar="RATE",
        help=(
            "the share of clean sentences a threshold may flag with its own flag, between 0 and 1 "
            f"(default {MAX_FALSE_POSITIVE_RATE:g})"
        ),
    )

    ab = bench_sub.add_parser("ab", help="export a blind A/B sheet and its key for human judges")
    ab.add_argument("--out", type=Path, required=True, help="the bench results directory")
    ab.add_argument("--a", required=True, metavar="SYSTEM", help="the first system")
    ab.add_argument("--b", required=True, metavar="SYSTEM", help="the second system")
    ab.add_argument(
        "--pair", action="append", metavar="SRC>TGT", help="limit to a pair (repeatable)"
    )
    ab.add_argument("--seed", type=int, default=1, help="seed for the random order (default 1)")


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run_bench_command(args: argparse.Namespace) -> int:
    try:
        if args.bench_command == "fetch-flores":
            dest = fetch_flores(args.out, log=_say)
            print(f"FLORES-200 is in {dest} (CC BY-SA 4.0; see {dest}/ATTRIBUTION.txt)")
            return 0
        if args.bench_command == "ab":
            sheet, key = export_ab(
                args.out, args.a, args.b, pairs=args.pair, seed=args.seed, log=_say
            )
            print(f"wrote {sheet} and {key} (keep the key from the judges)")
            return 0
        if args.bench_command == "calibrate":
            cal = run_calibrate(
                args.out,
                args.scoring_config,
                args.tier_system,
                max_false_positive_rate=args.max_false_positive_rate,
                log=_say,
            )
            for line in result_lines(cal):
                print(line)
            print(f"tiers from system '{cal.tier_system}'")
            print(f"wrote {args.out / 'calibration.yaml'} and {args.out / 'tiers.calibrated.yaml'}")
            return 0
        if args.bench_command == "flags":
            cfg = load_bench_config(args.bench_config)
            asyncio.run(
                run_flag_test(
                    cfg,
                    args.scoring_config,
                    args.out,
                    pairs=args.pairs,
                    force=args.force,
                    log=_say,
                )
            )
            print(f"wrote {args.out / 'flag-summary.md'}")
            return 0
        return _run(args)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _failure(out: Path, row: SummaryRow) -> str:
    path = out / "runs" / row.system / f"{row.pair.replace('>', '-')}.json"
    result = SystemPairResult.model_validate_json(path.read_text(encoding="utf-8"))
    return result.error or "failed"


def _run(args: argparse.Namespace) -> int:
    cfg = load_bench_config(args.bench_config)
    scorer = HttpScorer(cfg.scorer_url) if cfg.scorer_url else None

    async def go():
        try:
            return await run_bench(cfg, args.out, force=args.force, scorer=scorer, log=_say)
        finally:
            if scorer is not None:
                await scorer.aclose()

    rows = asyncio.run(go())
    for row in rows:
        if row.status == "failed":
            print(f"error: {row.system} {row.pair}: {_failure(args.out, row)}", file=sys.stderr)
    print(f"wrote {args.out / 'summary.md'}")
    return 1 if any(r.status == "failed" for r in rows) else 0
