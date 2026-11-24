"""`coglossa bench calibrate`: turn the bench results into a suggested `scoring:` section (meaning
thresholds per tier and one style threshold) and a calibrated language-tiers file."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from pydantic import ValidationError

from coglossa.bench.config import TierCuts
from coglossa.bench.flagtest import CLEAN, SpanRecord
from coglossa.bench.inject import MEANING_KINDS, STYLE_KINDS, InjectionKind
from coglossa.bench.metrics import SummaryRow
from coglossa.bench.run import META_FILE, write_atomic
from coglossa.config import ConfigError, ScoringConfig, load_config
from coglossa.scoring.tiers import LanguageTiers

MIN_SAMPLES = 30  # positives and negatives needed per tier before a threshold is fitted
MIN_KIND_SAMPLES = 10  # injected spans a kind needs to count in the recall average
MAX_FALSE_POSITIVE_RATE = 0.10  # default share of clean spans a threshold may flag
TIER_NAMES = ("A", "B", "C")
# Kinds only rules are meant to catch: their detection does not depend on the threshold, and
# would inflate recall.
_RULE_ONLY = (InjectionKind.LOCKED_TERM, InjectionKind.UNTRANSLATED)
_FITTED_MEANING = [k.value for k in InjectionKind if k in MEANING_KINDS and k not in _RULE_ONLY]
_STYLE = [k.value for k in InjectionKind if k in STYLE_KINDS]

HEADER = (
    "Suggested by `coglossa bench calibrate` from the bench run in {run}, on {today}.\n"
    "Each threshold is the highest that flags at most {target} of clean spans\n"
    "with its own flag (low_meaning for meaning, style_drift for style) or a critical error\n"
    "found by the judge: the clean false-positive rate, which carries over to real use.\n"
    "Other flags (terminology, omission) also fire on some clean spans; flag-summary.md\n"
    "gives the rate for all flags together. Recall is the share of injected errors caught,\n"
    "averaged over the kinds of error with at least {min_kind} examples, so that each kind\n"
    "weighs the same.\n"
    "Paste the `scoring:` section below into coglossa.yaml.\n"
    "{scores}\n"
    "Read these thresholds with two caveats:\n"
    "- Injected spans carry no agreement signal (a reference has no translators to agree or\n"
    "  disagree), so meaning was fitted without it; real runs add it.\n"
    "- The meaning threshold that applies to a pair depends on its tier. If you also apply\n"
    "  tiers.calibrated.yaml, rerun `bench flags` and `bench calibrate` with the new tiers\n"
    "  before trusting these thresholds.\n"
)

Item = tuple[float | None, bool]  # (score, critical error)


@dataclass(frozen=True)
class Fit:
    threshold: int
    false_positive_rate: float  # flagged clean spans over clean spans, at the threshold
    recall: float | None  # averaged over the kinds with MIN_KIND_SAMPLES spans; None if none
    kinds: dict[str, float]  # recall per kind
    counts: dict[str, tuple[int, int]]  # (caught, total) per kind


@dataclass
class Calibration:
    meaning: dict[str, float]
    style: float
    max_false_positive_rate: float = MAX_FALSE_POSITIVE_RATE
    notes: list[str] = field(default_factory=list)
    scores: dict[str, Fit] = field(default_factory=dict)  # "tier A", "tier B", "tier C", "style"
    tier_system: str = ""


def _flagged(item: Item, t: int) -> bool:
    score, critical = item
    return critical or (score is not None and score < t)


def _rate(items: list[Item], t: int) -> float:
    return sum(_flagged(i, t) for i in items) / len(items)


def _fit(positives: dict[str, list[Item]], negatives: list[Item], max_fpr: float) -> Fit | None:
    """The highest integer t in 0..100 at which "score < t or critical" flags at most `max_fpr`
    of the clean spans, with its recall per kind, the counts, and the average over the kinds
    with MIN_KIND_SAMPLES spans. None when even t = 0 flags more (critical errors alone). Unlike
    F1, this does not depend on how many injected spans the bench has per clean one."""
    for t in range(100, -1, -1):  # the rate only grows with t, so the first fit is the highest
        fpr = _rate(negatives, t)
        if fpr <= max_fpr:
            kinds = {k: _rate(items, t) for k, items in positives.items() if items}
            counts = {
                k: (sum(_flagged(i, t) for i in items), len(items))
                for k, items in positives.items()
                if items
            }
            enough = [kinds[k] for k, (_, total) in counts.items() if total >= MIN_KIND_SAMPLES]
            recall = sum(enough) / len(enough) if enough else None
            return Fit(t, fpr, recall, kinds, counts)
    return None


def calibrate(
    records: list[SpanRecord],
    current: ScoringConfig,
    max_false_positive_rate: float = MAX_FALSE_POSITIVE_RATE,
) -> Calibration:
    cal = Calibration(
        meaning=dict(current.thresholds.meaning),
        style=current.thresholds.style,
        max_false_positive_rate=max_false_positive_rate,
    )
    errored = sum(bool(r.errors) for r in records)
    if errored:
        cal.notes.append(
            f"{errored} span{_s(errored)} had a scoring error (a failed judge, back-translation "
            "or scorer call), so their flags may be missing; left out"
        )
    records = [r for r in records if not r.errors]
    unscored = sum(
        r.meaning is None for r in records if r.kind == CLEAN or r.kind in _FITTED_MEANING
    )

    def fit(label: str, rows: list[SpanRecord], style: bool, kept: float) -> float:
        def item(r: SpanRecord) -> Item:
            if style:
                return r.style, r.critical_style
            return r.meaning, r.critical_accuracy

        kinds = _STYLE if style else _FITTED_MEANING
        positives = {k: [item(r) for r in rows if r.kind == k] for k in kinds}
        negatives = [item(r) for r in rows if r.kind == CLEAN]
        injected = sum(map(len, positives.values()))
        stays = f"so its threshold stays {kept:g}"
        if min(injected, len(negatives)) < MIN_SAMPLES:
            cal.notes.append(
                f"{label}: {injected} injected and {len(negatives)} clean spans; at least "
                f"{MIN_SAMPLES} of each are needed, {stays}"
            )
            return kept
        if all(score is None for items in positives.values() for score, _ in items):
            cal.notes.append(f"{label}: no injected span has a score, {stays}")
            return kept
        result = _fit(positives, negatives, max_false_positive_rate)
        if result is None:
            cal.notes.append(
                f"{label}: critical errors alone flag {_percent(_rate(negatives, 0))} of clean "
                f"spans, above the {_percent(max_false_positive_rate)} target, {stays}"
            )
            return kept
        if not any(caught for caught, _ in result.counts.values()):
            cal.notes.append(
                f"{label}: at threshold {result.threshold} (the highest within the "
                f"{_percent(max_false_positive_rate)} target) no injected error is caught, {stays}"
            )
            return kept
        cal.scores[label] = result
        return result.threshold

    for tier in TIER_NAMES:
        rows = [r for r in records if r.tier == tier]
        cal.meaning[tier] = fit(f"tier {tier}", rows, False, cal.meaning[tier])
    cal.style = fit("style", records, True, cal.style)
    if unscored:
        cal.notes.append(
            f"{unscored} span{_s(unscored)} had no meaning score; only a critical error flags them"
        )
    return cal


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def result_lines(cal: Calibration) -> list[str]:
    """One line per fitted threshold, then the notes: for calibration.yaml and the CLI."""
    lines = []
    for label, f in cal.scores.items():
        kinds = ", ".join(_kind_text(k, f) for k in f.kinds)
        lines.append(
            f"{label}: threshold {f.threshold} flags {_percent(f.false_positive_rate)} of clean "
            f"spans; {_recall_text(f)} ({kinds})"
        )
    return lines + cal.notes


def _kind_text(kind: str, f: Fit) -> str:
    caught, total = f.counts[kind]
    few = ", too few to average" if total < MIN_KIND_SAMPLES else ""
    return f"{kind} {_percent(f.kinds[kind])} ({caught}/{total}{few})"


def _recall_text(f: Fit) -> str:
    if f.recall is None:
        return "recall: too few examples of each kind to average"
    n = sum(total >= MIN_KIND_SAMPLES for _, total in f.counts.values())
    return f"recall averaged over {n} kind{_s(n)}: {_percent(f.recall)}"


def suggest_tiers(rows: list[SummaryRow], system: str, cuts: TierCuts) -> dict[str, str]:
    """Pair label to tier, from the system's mean MetricX (lower is better)."""
    out: dict[str, str] = {}
    for r in rows:
        if r.system != system or r.status != "complete" or r.metricx_mean is None:
            continue
        out[r.pair] = (
            "A" if r.metricx_mean <= cuts.a_max else "B" if r.metricx_mean <= cuts.b_max else "C"
        )
    return out


def _read_records(out: Path) -> list[SpanRecord]:
    files = sorted((out / "flags").glob("*.jsonl"))
    if not files:
        raise ConfigError(f"no flag records in {out / 'flags'}; run `coglossa bench flags` first")
    try:
        return [
            SpanRecord.model_validate_json(line)
            for f in files
            for line in f.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except ValidationError as e:
        raise ConfigError(
            f"{out / 'flags'}: not a flag record file: {e.error_count()} errors"
        ) from e


def _read_summary(out: Path) -> list[SummaryRow]:
    path = out / "summary.json"
    if not path.is_file():
        raise ConfigError(f"{path} not found; run `coglossa bench run` first")
    try:
        return [SummaryRow.model_validate(r) for r in json.loads(path.read_text(encoding="utf-8"))]
    except ValidationError as e:
        raise ConfigError(f"{path}: not a bench summary: {e.error_count()} errors") from e
    except (ValueError, TypeError) as e:
        raise ConfigError(f"{path}: not a bench summary: {_one_line(str(e))}") from e


def _read_meta(out: Path, log: Callable[[str], None]) -> tuple[TierCuts, list[dict]]:
    """The tier cuts and the systems (in bench-config order) from bench-meta.json; defaults,
    with a note, when there is no such file."""
    path = out / META_FILE
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        cuts = TierCuts.model_validate(meta["tier_cuts"])
        systems = meta.get("systems", [])
        if not isinstance(systems, list) or not all(
            isinstance(s, dict) and {"name", "kind"} <= s.keys() for s in systems
        ):
            raise ValueError("systems must be a list of names and kinds")
        return cuts, systems
    except FileNotFoundError:
        log(f"{META_FILE} not found in {out}; using the default tier cuts")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise ConfigError(f"{path}: not a bench meta file: {_one_line(str(e))}") from e
    return TierCuts(), []


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def _calibration_yaml(cal: Calibration, run: str, today: date) -> str:
    scores = "\n".join(f"  {line}" for line in result_lines(cal))
    header = HEADER.format(
        run=run,
        today=today.isoformat(),
        target=_percent(cal.max_false_positive_rate),
        scores=scores,
        min_kind=MIN_KIND_SAMPLES,
    )
    body = {
        "scoring": {
            "thresholds": {
                "meaning": {
                    k: int(v) if float(v).is_integer() else v for k, v in cal.meaning.items()
                },
                "style": int(cal.style) if float(cal.style).is_integer() else cal.style,
            }
        }
    }
    comment = "".join(f"# {line}".rstrip() + "\n" for line in header.splitlines())
    return comment + yaml.safe_dump(body, sort_keys=False)


def _tiers_yaml(current: LanguageTiers, suggested: dict[str, str]) -> str:
    languages: dict[str, list[str]] = {}
    for code, tier in sorted(current.languages.items()):
        languages.setdefault(str(tier.value), []).append(code)
    pairs = {f"{s}>{t}": str(tier.value) for (s, t), tier in sorted(current.pairs.items())}
    pairs.update(suggested)
    body = {"default": str(current.default.value), "languages": dict(sorted(languages.items())),
            "pairs": dict(sorted(pairs.items()))}  # fmt: skip
    return (
        "# Calibrated by `coglossa bench calibrate`: pairs are tiered from the bench's MetricX.\n"
        + yaml.safe_dump(body, sort_keys=False)
    )


def run_calibrate(
    out: Path,
    scoring_config: Path,
    tier_system: str | None,
    *,
    max_false_positive_rate: float = MAX_FALSE_POSITIVE_RATE,
    log: Callable[[str], None] = print,
    today: Callable[[], date] = date.today,
) -> Calibration:
    if not 0 < max_false_positive_rate < 1:
        raise ConfigError(
            f"--max-false-positive-rate must be between 0 and 1, not {max_false_positive_rate:g}"
        )
    scoring = load_config(scoring_config)[0].scoring
    rows = _read_summary(out)
    records = _read_records(out)
    cuts, meta_systems = _read_meta(out, log)
    systems = list(dict.fromkeys(r.system for r in rows))
    # the bench config's order (bench-meta.json); older results only have the summary's
    order = meta_systems or [{"name": r.system, "kind": r.kind} for r in rows]
    system = tier_system or next((s["name"] for s in order if s["kind"] == "pipeline"), "")
    if not system:
        raise ConfigError(
            f"no pipeline system in {out / 'summary.json'}; pass --tier-system to pick one"
        )
    if system not in systems:
        raise ConfigError(f"no system '{system}' in {out / 'summary.json'}")
    cal = calibrate(records, scoring, max_false_positive_rate)
    cal.tier_system = system
    write_atomic(out / "calibration.yaml", _calibration_yaml(cal, str(out), today()))
    tiers = LanguageTiers.load(scoring.language_tiers_file)
    write_atomic(
        out / "tiers.calibrated.yaml", _tiers_yaml(tiers, suggest_tiers(rows, system, cuts))
    )
    return cal
