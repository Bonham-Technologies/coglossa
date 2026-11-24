"""`coglossa bench run`: translate the same FLORES sentences with every system, score the
units, and keep one resumable result file per (system, pair)."""

import hashlib
import json
import random
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from coglossa.bench.config import BenchConfig, DatasetConfig, Pair, SystemConfig
from coglossa.bench.fingerprint import output_hash
from coglossa.bench.flores import FloresData, write_attribution
from coglossa.bench.metrics import (
    Fingerprint,
    SummaryRow,
    SystemPairResult,
    score_units,
    shared_metricx,
    summarise,
)
from coglossa.bench.systems import SystemOutput, run_baseline_system, run_pipeline_system
from coglossa.bench.units import Unit
from coglossa.config import CoglossaConfig, ConfigError, load_config
from coglossa.llm.registry import build_backends
from coglossa.llm.types import Backend
from coglossa.scoring.client import Scorer, ScorerUnavailableError, build_scorer

MD_HEADER = (
    "| system | pair | MetricX ↓ | scored/units | too long src/out | chrF ↑ | markers kept "
    "| tokens in | tokens out | seconds | errors |"
)
MD_FOOTER = (
    "MetricX-24 reference mode: error 0-25, lower is better. chrF: 0-100, higher is better.\n\n"
    "So that systems compare, a pair's MetricX is the mean over the units every system of the "
    "pair scored (matched by their sentences), weighted by sentence count; summary.json also "
    "holds each system's own mean (`metricx_mean`). scored/units is each system's own count. "
    'A unit too long for MetricX has no score: "src" counts units too long by their source '
    'and reference, "out" those too long by the system\'s output. "markers kept" counts the '
    "passages with formatting in the source (a bold, italic or linked word) whose numbered "
    "formatting markers came back whole, out of all of them."
)


def pick_indexes(n: int, k: int, seed: int) -> list[int]:
    """A reproducible sample of k of n line indexes, in reading order."""
    return sorted(random.Random(seed).sample(range(n), min(k, n)))


def _read_lines(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    if lines and lines[-1] == "":
        lines.pop()
    for n, line in enumerate(lines, 1):
        if not line.strip():
            raise ConfigError(f"{path}: line {n} is blank; every line must hold a sentence")
    return lines


def load_pair(ds: DatasetConfig, flores: FloresData | None, pair: Pair) -> list[tuple[str, str]]:
    if flores is not None:
        return flores.pair(pair.src, pair.tgt, ds.split)
    assert ds.source_file is not None and ds.reference_file is not None
    src, ref = _read_lines(ds.source_file), _read_lines(ds.reference_file)
    if len(src) != len(ref):
        raise ConfigError(
            f"{ds.source_file} and {ds.reference_file} have {len(src)} and {len(ref)} lines"
        )
    return list(zip(src, ref, strict=True))


def load_systems(cfg: BenchConfig) -> dict[str, CoglossaConfig]:
    """Every system's Coglossa config, checked before anything is translated."""
    configs: dict[str, CoglossaConfig] = {}
    for spec in cfg.systems:
        try:
            configs[spec.name] = load_config(spec.config)[0]
        except ConfigError as e:
            raise ConfigError(f"system '{spec.name}': {e}") from e
        if spec.kind == "baseline" and spec.model not in configs[spec.name].models:
            raise ConfigError(
                f"system '{spec.name}': model '{spec.model}' is not defined in {spec.config}"
            )
    return configs


class _System:
    def __init__(self, spec: SystemConfig, cfg: CoglossaConfig, backends_factory: Callable) -> None:
        self.spec = spec
        self.cfg = cfg
        self._backends_factory = backends_factory
        self.backends: dict[str, Backend] | None = None  # built when first needed
        self.scorer: Scorer | None = None

    async def translate(self, pair: Pair, sentences: list[str], workdir: Path) -> SystemOutput:
        if self.backends is None:
            self.backends = self._backends_factory(self.cfg)
        if self.spec.kind == "pipeline":
            if self.scorer is None:
                self.scorer = build_scorer(self.cfg)  # the system's own, as in `coglossa run`
            return await run_pipeline_system(
                self.cfg, pair, sentences, workdir, backends=self.backends, scorer=self.scorer
            )
        assert self.spec.model is not None
        return await run_baseline_system(
            self.cfg, self.spec.model, pair, sentences, workdir, backends=self.backends
        )

    async def aclose(self) -> None:
        for backend in (self.backends or {}).values():
            await backend.aclose()
        if self.scorer is not None:
            await self.scorer.aclose()


def _one_line(text: str) -> str:
    return " ".join(text.split())


META_FILE = "bench-meta.json"
SCORER_ERROR = "scorer: "  # how score_units starts a scorer failure


def _meta_json(cfg: BenchConfig) -> str:
    ds = cfg.dataset
    meta = {
        "tier_cuts": cfg.tiers.model_dump(),
        "dataset": {
            "kind": ds.kind,
            "split": ds.split,
            "sentences": ds.sentences,
            "seed": ds.seed,
            "pairs": ds.pairs,
        },
        # in the bench config's order: calibrate's default tier system is the first pipeline
        "systems": [{"name": s.name, "kind": s.kind} for s in cfg.systems],
    }
    return json.dumps(meta, indent=2)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _load_result(path: Path) -> SystemPairResult | None:
    try:
        return SystemPairResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class _Sample:
    sentences: list[str]
    references: list[str]
    digest: str  # SHA-256 of the indexes, sentences and references


def _sample(rows: list[tuple[str, str]], ds: DatasetConfig) -> _Sample:
    picked = pick_indexes(len(rows), ds.sentences, ds.seed)
    sentences = [rows[i][0] for i in picked]
    references = [rows[i][1] for i in picked]
    blob = json.dumps([picked, sentences, references], ensure_ascii=False)
    return _Sample(sentences, references, hashlib.sha256(blob.encode("utf-8")).hexdigest())


def _fingerprint(
    ds: DatasetConfig, sample: _Sample, spec: SystemConfig, cfg: CoglossaConfig
) -> Fingerprint:
    return Fingerprint(
        dataset=ds.kind,
        split=ds.split,
        sentences=ds.sentences,
        seed=ds.seed,
        sample=sample.digest,
        output=output_hash(cfg, spec),
        model=spec.model if spec.kind == "baseline" else None,
    )


async def run_bench(
    cfg: BenchConfig,
    out: Path,
    *,
    force: bool = False,
    backends_factory: Callable = build_backends,
    scorer: Scorer | None = None,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = print,
) -> list[SummaryRow]:
    ds = cfg.dataset
    configs = load_systems(cfg)
    flores = FloresData(ds.path) if ds.kind == "flores200" else None
    pairs = ds.parsed_pairs()
    samples = {pair.label: _sample(load_pair(ds, flores, pair), ds) for pair in pairs}
    runs = out / "runs"
    write_atomic(out / META_FILE, _meta_json(cfg))
    if flores is not None:  # CC BY-SA 4.0: the results quote FLORES sources and references
        write_attribution(out)

    todo = [(s, p) for s in cfg.systems for p in pairs]
    expected = {
        (s.name, p.label): _fingerprint(ds, samples[p.label], s, configs[s.name]) for s, p in todo
    }
    scorer, qe_model = await _usable_scorer(scorer, log)

    def identity(name: str) -> str | None:  # what scores made now would be marked with
        return None if qe_model is None else f"{qe_model}@{configs[name].scoring.qe_max_chars}"

    finished: set[tuple[str, str]] = set()
    pending: dict[tuple[str, str], SystemPairResult] = {}  # MetricX still to score
    for spec, pair in [] if force else todo:
        key = (spec.name, pair.label)
        old = _load_result(runs / spec.name / f"{pair.slug}.json")
        if old is None or old.status != "complete":
            continue
        if old.fingerprint != expected[key]:
            why = "written by an older version" if old.fingerprint is None else "settings changed"
            log(f"re-running {spec.name} {pair.label}: {why}")
        elif scorer is None or old.metricx_by == identity(spec.name):
            finished.add(key)
        else:
            pending[key] = old
    if finished:
        noun = "system-pair" if len(finished) == 1 else "system-pairs"
        log(f"skipping {len(finished)} finished {noun}")

    systems: dict[str, _System] = {}
    try:
        for spec, pair in todo:
            key = (spec.name, pair.label)
            if key in finished:
                continue
            sample = samples[pair.label]
            if key in pending:
                why = "MetricX pending" if pending[key].metricx_by is None else "scorer changed"
                log(f"re-scoring {spec.name} {pair.label}: {why}")
                result = await _rescore(
                    pending[key],
                    pair,
                    sample,
                    scorer,
                    configs[spec.name].scoring.qe_max_chars,
                    identity(spec.name),
                )
            else:
                if spec.name not in systems:
                    systems[spec.name] = _System(spec, configs[spec.name], backends_factory)
                result = await _run_one(
                    systems[spec.name],
                    pair,
                    sample.sentences,
                    sample.references,
                    scorer,
                    identity(spec.name),
                    clock,
                )
                result.fingerprint = expected[key]
            write_atomic(runs / spec.name / f"{pair.slug}.json", result.model_dump_json(indent=2))
            log(_progress_line(result))
    finally:
        for system in systems.values():
            await system.aclose()
    return _write_summary(cfg, out, log)


async def _usable_scorer(
    scorer: Scorer | None, log: Callable[[str], None]
) -> tuple[Scorer | None, str | None]:
    """The scorer and its QE model id, or (None, None) when MetricX cannot be scored: no scorer,
    it is unreachable, or it has no QE model. The results then stay pending."""
    if scorer is None:
        return None, None
    try:
        health = await scorer.health()
    except ScorerUnavailableError as e:
        log(
            f"scorer unavailable ({e}): MetricX stays pending; the next run with a scorer scores it"
        )
        return None, None
    if health.qe_model is None:
        log("scorer has no QE model: MetricX stays pending")
        return None, None
    return scorer, health.qe_model


async def _rescore(
    result: SystemPairResult,
    pair: Pair,
    sample: _Sample,
    scorer: Scorer | None,
    qe_max_chars: int,
    identity: str | None,
) -> SystemPairResult:
    """Score a finished result's stored outputs again, without translating anything."""
    units, scorer_errors = await score_units(
        [Unit(u.first, u.last, u.output) for u in result.units],
        sample.sentences,
        sample.references,
        scorer,
        pair=pair,
        qe_max_chars=qe_max_chars,
    )
    errors = [e for e in result.errors if not e.startswith(SCORER_ERROR)]
    metricx_by = None if scorer_errors else identity
    return result.model_copy(
        update={
            "units": units,
            "errors": [*errors, *scorer_errors],
            "metricx_by": metricx_by,
            "metricx_pending": metricx_by is None and bool(units),
        }
    )


class _Untranslated(Exception):
    pass


def _untranslated(units: list[Unit], sentences: list[str]) -> int:
    """Sentences whose unit has no output although the source has text."""
    return sum(
        u.last - u.first + 1
        for u in units
        if not u.text.strip() and " ".join(sentences[u.first : u.last + 1]).strip()
    )


def _failed(name: str, pair: Pair, error: str, seconds: float) -> SystemPairResult:
    """A failed pair records no units, so it contributes no metrics (never a made-up 0.0)."""
    return SystemPairResult(
        system=name,
        pair=pair.label,
        status="failed",
        error=_one_line(error),
        seconds=seconds,
    )


async def _run_one(
    system: _System,
    pair: Pair,
    sentences: list[str],
    references: list[str],
    scorer: Scorer | None,
    identity: str | None,
    clock: Callable[[], float],
) -> SystemPairResult:
    name = system.spec.name
    started = clock()
    try:
        with tempfile.TemporaryDirectory() as workdir:
            output = await system.translate(pair, sentences, Path(workdir))
        seconds = clock() - started
        missing = _untranslated(output.units, sentences)
        if output.errors and missing:
            raise _Untranslated(
                f"{missing} of {len(sentences)} sentences untranslated: {output.errors[0]}"
            )
        units, scorer_errors = await score_units(
            output.units,
            sentences,
            references,
            scorer,
            pair=pair,
            qe_max_chars=system.cfg.scoring.qe_max_chars,
        )
    except _Untranslated as e:
        return _failed(name, pair, str(e), clock() - started)
    except Exception as e:
        return _failed(name, pair, f"{type(e).__name__}: {e}", clock() - started)
    metricx_by = None if scorer_errors else identity
    return SystemPairResult(
        system=name,
        pair=pair.label,
        status="complete",
        units=units,
        tokens_in=output.tokens_in,
        tokens_out=output.tokens_out,
        calls=output.calls,
        seconds=seconds,
        errors=[*output.errors, *scorer_errors],
        metricx_by=metricx_by,
        metricx_pending=metricx_by is None and bool(units),
        markers_kept=output.markers_kept,
        markers_total=output.markers_total,
    )


def _fmt(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _progress_line(result: SystemPairResult) -> str:
    if result.status == "failed":
        return f"bench: {result.system} {result.pair}: failed: {result.error}"
    row = summarise(result)
    return (
        f"bench: {row.system} {row.pair}: {row.units} units, MetricX {_fmt(row.metricx_mean)}, "
        f"chrF {_fmt(row.chrf_corpus)}, {row.seconds:.0f} s"
    )


def _write_summary(cfg: BenchConfig, out: Path, log: Callable[[str], None]) -> list[SummaryRow]:
    """Summarise the results of the bench config's systems and pairs; result files of others
    (left from an earlier config) stay on disk but are left out."""
    kinds = {s.name: s.kind for s in cfg.systems}
    labels = {p.slug: p.label for p in cfg.dataset.parsed_pairs()}
    results: list[SystemPairResult] = []
    others = 0
    for path in sorted((out / "runs").glob("*/*.json")):
        if path.parent.name not in kinds or path.stem not in labels:
            others += 1
            continue
        results.append(SystemPairResult.model_validate_json(path.read_text(encoding="utf-8")))
    if others:
        log(
            f"summary: left out {others} result file(s) of systems or pairs that are not in "
            "the bench config"
        )
    rows = []
    for label in labels.values():
        of_pair = [r for r in results if r.pair == label]
        shared = shared_metricx(of_pair)
        for r in of_pair:
            mean, n = shared.get(r.system, (None, 0))
            row = summarise(r, kinds[r.system])
            rows.append(row.model_copy(update={"metricx_shared": mean, "shared_units": n}))
    rows.sort(key=lambda r: (r.pair, r.system))
    write_atomic(out / "summary.json", json.dumps([r.model_dump() for r in rows], indent=2))
    write_atomic(out / "summary.md", _markdown(cfg, rows))
    return rows


def _markdown(cfg: BenchConfig, rows: list[SummaryRow]) -> str:
    ds = cfg.dataset
    lines = [
        f"# Bench: {ds.kind} {ds.split}, {ds.sentences} sentences, seed {ds.seed}",
        "",
        MD_HEADER,
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.system} | {r.pair} | {_fmt(r.metricx_shared, 2)} | "
            f"{r.scored_units}/{r.units} | {r.long_sources}/{r.long_outputs} | "
            f"{_fmt(r.chrf_corpus)} | {r.markers_kept}/{r.markers_total} | "
            f"{r.tokens_in} | {r.tokens_out} | "
            f"{r.seconds:.0f} | {r.errors} |"
        )
    shared = {r.pair: r.shared_units for r in rows if r.status == "complete"}
    counts = ", ".join(f"{pair} {n}" for pair, n in sorted(shared.items()))
    footer = MD_FOOTER + (f" Units every system scored: {counts}." if counts else "")
    pending = sum(r.metricx_pending for r in rows)
    if pending:
        footer += (
            f"\n\nMetricX pending for {pending} system-pair(s): a run with a working scorer "
            "scores them without translating again."
        )
    return "\n".join([*lines, "", footer, ""])
