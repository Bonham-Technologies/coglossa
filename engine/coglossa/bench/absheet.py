"""The blind A/B sheet (spec §9): two systems' translations side by side in a random order for
human judges, plus a separate key that says which system is which."""

import csv
import json
import random
from collections.abc import Callable
from pathlib import Path

from coglossa.bench.config import parse_pair
from coglossa.bench.flores import write_attribution
from coglossa.bench.metrics import SystemPairResult, UnitResult
from coglossa.bench.run import META_FILE
from coglossa.config import ConfigError

SHEET_HEADER = [
    "item",
    "pair",
    "source",
    "translation_1",
    "translation_2",
    "preferred (1, 2 or same)",
    "comment",
]
KEY_HEADER = ["item", "translation_1", "translation_2"]


def _summary(out: Path) -> dict[tuple[str, str], str]:
    """Each (system, pair label) of `out/summary.json` to its status: the results `bench run`
    reported."""
    try:
        rows = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        status = {(r["system"], r["pair"]): r["status"] for r in rows}
        for _, label in status:
            parse_pair(label)  # a label that does not parse is a damaged summary
        return status
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ConfigError(f"no summary in {out} (run coglossa bench run first)") from e


def _load(out: Path, system: str, slug: str) -> SystemPairResult | None:
    path = out / "runs" / system / f"{slug}.json"
    if not path.is_file():
        return None
    return SystemPairResult.model_validate_json(path.read_text(encoding="utf-8"))


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def _from_flores(out: Path) -> bool:
    """Whether `bench run` read FLORES-200, from its bench-meta.json; when that cannot be read,
    assume so (an attribution too many is harmless)."""
    try:
        meta = json.loads((out / META_FILE).read_text(encoding="utf-8"))
        return meta["dataset"]["kind"] == "flores200"
    except (OSError, ValueError, KeyError, TypeError):
        return True


def export_ab(
    out: Path,
    system_a: str,
    system_b: str,
    *,
    pairs: list[str] | None = None,
    seed: int = 1,
    log: Callable[[str], None] | None = None,
) -> tuple[Path, Path]:
    """Write `ab-sheet.csv` and `ab-key.csv` under `out`. The pairs are those of `out/summary.json`
    (the results `bench run` reported); other result files on disk are never read. Units are
    matched by `(first, last)`; identical outputs, unmatched units and failed or missing pairs
    are left out (and counted through `log`)."""
    status = _summary(out)
    for system in (system_a, system_b):
        if not any(s == system for s, _ in status):
            raise ConfigError(
                f"no results for system '{system}' in {out / 'summary.json'} "
                "(run coglossa bench run first)"
            )
    if pairs is None:
        labels = sorted(
            {p for s, p in status if s == system_a} & {p for s, p in status if s == system_b},
            key=lambda label: parse_pair(label).slug,
        )
    else:
        try:
            labels = [parse_pair(p).label for p in pairs]
        except ValueError as e:
            raise ConfigError(str(e)) from e

    rng = random.Random(seed)
    sheet: list[list[str]] = []
    key: list[list[str]] = []
    failed = missing = unmatched = identical = 0
    for label in labels:
        seen = [status.get((system_a, label)), status.get((system_b, label))]
        if None in seen:
            missing += 1
            continue
        if "failed" in seen:
            failed += 1
            continue
        slug = parse_pair(label).slug
        res_a, res_b = _load(out, system_a, slug), _load(out, system_b, slug)
        if res_a is None or res_b is None:
            missing += 1
            continue
        if "failed" in (res_a.status, res_b.status):
            failed += 1
            continue
        by_a: dict[tuple[int, int], UnitResult] = {(u.first, u.last): u for u in res_a.units}
        by_b: dict[tuple[int, int], UnitResult] = {(u.first, u.last): u for u in res_b.units}
        unmatched += len(by_a.keys() ^ by_b.keys())
        for span in sorted(by_a.keys() & by_b.keys()):
            ua, ub = by_a[span], by_b[span]
            if ua.output == ub.output:
                identical += 1
                continue
            first, second = (system_a, system_b) if rng.random() < 0.5 else (system_b, system_a)
            texts = {system_a: ua.output, system_b: ub.output}
            item = str(len(sheet) + 1)
            sheet.append([item, res_a.pair, ua.source, texts[first], texts[second], "", ""])
            key.append([item, first, second])

    sheet_path, key_path = out / "ab-sheet.csv", out / "ab-key.csv"
    _write(sheet_path, SHEET_HEADER, sheet)
    _write(key_path, KEY_HEADER, key)
    if _from_flores(out):  # CC BY-SA 4.0: the sheet quotes FLORES source sentences
        write_attribution(out)
    if log is not None:
        log(
            f"ab: {len(sheet)} items; left out {identical} identical, {unmatched} unmatched "
            f"unit(s), {failed} failed pair(s), {missing} missing pair(s)"
        )
    return sheet_path, key_path
