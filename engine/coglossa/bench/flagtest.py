"""`coglossa bench flags`: run clean and error-injected reference translations through the real
scoring stage, and measure how often the flags catch each injected error (recall) and how often
they fire on clean text (false positives and precision), per language-pair tier."""

import asyncio
import hashlib
import json
import random
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from coglossa.bench.config import BenchConfig, DatasetConfig, InjectorConfig, Pair
from coglossa.bench.fingerprint import prompt_hashes
from coglossa.bench.flores import FLAGS_MODIFIED, FloresData, write_attribution
from coglossa.bench.inject import (
    STYLE_KINDS,
    Injection,
    InjectionKind,
    inject_with_model,
    leave_untranslated,
    omit,
    swap_name,
    swap_number,
    violate_locked_term,
)
from coglossa.bench.run import load_pair, pick_indexes, write_atomic
from coglossa.bench.units import write_source_document
from coglossa.config import CoglossaConfig, ConfigError, load_config
from coglossa.ingest.registry import ingest_file
from coglossa.llm.agent import AGENT_FAILURES, AgentRunner, CallLog
from coglossa.llm.registry import build_backends
from coglossa.llm.types import Backend
from coglossa.prompts.library import RENDER_VERSION, PromptLibrary
from coglossa.schema import (
    ACCURACY_CATEGORIES,
    Agreement,
    Chunk,
    DocumentProfile,
    ErrorSeverity,
    Flag,
    Genre,
    GlossaryEntry,
    Guide,
    JudgeError,
    PipelineProfile,
    ReconciledSpan,
    Reliability,
    RunError,
    Segment,
    SpanScore,
)
from coglossa.scoring.aggregate import model_diversity
from coglossa.scoring.client import Scorer, build_scorer
from coglossa.scoring.rules import term_in
from coglossa.scoring.tiers import LanguageTiers
from coglossa.segment.chunking import chunk_segments, compute_budget
from coglossa.segment.segmenter import segment_document
from coglossa.stages.context import StageContext
from coglossa.stages.reconcile import ReconcileResult
from coglossa.stages.score import ChunkScores, score_chunk
from coglossa.stages.translate import ChunkInput, build_chunk_inputs

CLEAN = "clean"
KINDS = [k.value for k in InjectionKind]
MEANING_FLAGS = frozenset(
    {
        "low_meaning",
        "possible_omission",
        "terminology_violation",
        "number_mismatch",
        "untranslated",
        "literal_mismatch",
    }
)
STYLE_FLAGS = frozenset({"style_drift"})
NOTHING_TO_FLAG = "the scoring config has no judge and no scorer; nothing can raise a flag"
NO_INJECTOR = "no injector configured: negation and register are not tested"
MODEL_KINDS = (InjectionKind.NEGATION, InjectionKind.REGISTER)
MD_FOOTER = (
    "Recall counts low_meaning, possible_omission, terminology_violation, number_mismatch, "
    "untranslated or literal_mismatch for meaning errors and style_drift for register; a clean "
    "span with any of them is a false positive.\n\n"
    "The clean false-positive rate (flagged clean spans over clean spans) is the number that "
    "transfers to real use. This rate counts every flag; `bench calibrate` fits each threshold "
    "to the rate of its own flag, so this one is higher. "
    "Precision is caught injected spans over caught injected spans plus flagged clean "
    "spans; precision reflects this bench's mix of injected and clean spans "
    "(several injected spans per clean one), while real translations have far fewer errors, so "
    "real precision is lower.\n\n"
    "Negation and register errors are written by a model and not checked, so some may not be "
    "errors at all: their recall is a lower bound. Sample injections of each kind are below, "
    "for a person to check.\n\n"
    "A span with a scoring error (a judge, back-translation or scorer call that failed) lost "
    "some of its signals, so a flag it should have had may be missing; `bench calibrate` leaves "
    "such spans out.\n\n"
    "Injected spans carry Agreement.UNKNOWN (a reference has no translators to agree or "
    "disagree), so meaning is computed without the agreement signal. Thresholds calibrated "
    "this way slightly understate its contribution in real runs."
)
SAMPLES_PER_KIND = 3
# Bump when a pair's records would mean something else: saved pairs are then scored again. The
# fingerprint sees settings, prompts and the tiers file, not code: bump it when the injections
# (inject.py), the scoring stage or the flags it raises change, or the built-in tiers do.
# 2: the numbers check (number_mismatch, CGL-86); 3: by language (CGL-114); 4: the untranslated
# check and its injected kind (CGL-107); 5: the literals check (literal_mismatch, CGL-111)
RECORDS_VERSION = 5
# Settings that never change a pair's records: how fast, how often and how many calls are made,
# credentials, and where the deployment keeps its data. Raising a timeout so that a shared CPU
# scorer keeps up must not cost the pairs already finished. The prompts and the tiers file are
# hashed by content instead of by path.
_NOT_SHAPING: dict[str, Any] = {
    **dict.fromkeys(
        ("retry", "audit", "database", "storage", "worker", "api", "auth", "prompts_dir"), True
    ),
    "backends": {"__all__": {"max_concurrency", "timeout_s", "api_key_env", "stream"}},
    "scorer": {
        "timeout_s",
        "connect_timeout_s",
        "max_batch",
        "max_concurrency",
        "api_key_env",
        "extra_headers_env",
    },
    "scoring": {"language_tiers_file"},
}


class SpanRecord(BaseModel):
    pair: str  # label, e.g. "en>es"
    tier: str
    unit: int  # 0-based index of the sentence in the sample
    kind: str  # "clean" or an InjectionKind value
    detail: str
    meaning: float | None
    style: float | None
    signals: dict[str, float | None]
    critical_accuracy: bool
    critical_style: bool
    flags: list[str]
    errors: list[str] = []  # the scoring errors of the call that scored this span


class KindMetrics(BaseModel):
    caught: int
    total: int
    recall: float | None  # null when no span of this kind was tested


class TierMetrics(BaseModel):
    tier: str
    kinds: dict[str, KindMetrics]
    clean_total: int
    false_positives: int
    false_positive_rate: float | None
    precision: float | None  # null when nothing was flagged; depends on the bench's mix
    scoring_errors: int = 0  # spans whose scoring call failed in part (lost signals)


class Sample(BaseModel):
    pair: str
    unit: int
    original: str
    injected: str


class FlagSummary(BaseModel):
    tiers: list[TierMetrics]
    overall: TierMetrics
    samples: dict[str, list[Sample]] = {}  # a few injections per kind, for a person to check


class PairMeta(BaseModel):
    """flags/<pair>.meta.json, written after flags/<pair>.jsonl: what made the pair's records,
    so a later run keeps them, and the pair's samples, so its summary can be rebuilt."""

    fingerprint: str
    spans: int
    samples: dict[str, list[Sample]] = {}


def counted_flags(kind: str) -> frozenset[str]:
    if kind == CLEAN:
        return MEANING_FLAGS | STYLE_FLAGS
    return STYLE_FLAGS if InjectionKind(kind) in STYLE_KINDS else MEANING_FLAGS


def _caught(record: SpanRecord) -> bool:
    return not counted_flags(record.kind).isdisjoint(record.flags)


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _tier_metrics(tier: str, records: list[SpanRecord]) -> TierMetrics:
    kinds: dict[str, KindMetrics] = {}
    for kind in KINDS:
        of_kind = [r for r in records if r.kind == kind]
        caught = sum(map(_caught, of_kind))
        kinds[kind] = KindMetrics(
            caught=caught, total=len(of_kind), recall=_ratio(caught, len(of_kind))
        )
    clean = [r for r in records if r.kind == CLEAN]
    false_positives = sum(map(_caught, clean))
    caught = sum(m.caught for m in kinds.values())
    return TierMetrics(
        tier=tier,
        kinds=kinds,
        clean_total=len(clean),
        false_positives=false_positives,
        false_positive_rate=_ratio(false_positives, len(clean)),
        precision=_ratio(caught, caught + false_positives),
        scoring_errors=sum(bool(r.errors) for r in records),
    )


def flag_metrics(
    records: list[SpanRecord], samples: dict[str, list[Sample]] | None = None
) -> FlagSummary:
    tiers = sorted({r.tier for r in records})
    return FlagSummary(
        tiers=[_tier_metrics(t, [r for r in records if r.tier == t]) for t in tiers],
        overall=_tier_metrics("all", records),
        samples=samples or {},
    )


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def summary_markdown(summary: FlagSummary) -> str:
    lines = ["# Flag recall and precision", ""]
    for t in summary.tiers:
        lines += [
            f"## Tier {t.tier}",
            "",
            "| kind | caught | total | recall |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {k} | {m.caught} | {m.total} | {_fmt(m.recall)} |" for k, m in t.kinds.items()
        ]
        lines += [
            f"| clean (false positives) | {t.false_positives} | {t.clean_total} | "
            f"{_fmt(t.false_positive_rate)} |",
            "",
            f"Clean false-positive rate (any flag): {_fmt(t.false_positive_rate)}",
            f"Precision (at this bench's mix): {_fmt(t.precision)}",
            f"Spans with a scoring error: {t.scoring_errors}",
            "",
        ]
    lines += [MD_FOOTER, ""]
    if summary.samples:
        lines += ["## Sample injections", ""]
    for kind, samples in summary.samples.items():
        lines += [f"### {kind}", ""]
        for n, sample in enumerate(samples, 1):
            lines += [
                f"{n}. {sample.pair}, sentence {sample.unit + 1}",
                f"   - original: {sample.original}",
                f"   - injected: {sample.injected}",
            ]
        lines.append("")
    return "\n".join(lines)


@dataclass(frozen=True)
class _Variant:
    unit: int
    text: str
    detail: str
    locked_term: GlossaryEntry | None = None


@dataclass(frozen=True)
class _Injector:
    runner: AgentRunner
    model: str


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _runner(cfg: CoglossaConfig, backends: dict[str, Backend]) -> AgentRunner:
    return AgentRunner(cfg, backends, PromptLibrary(cfg.prompts_dir), CallLog(store_io=False))


def _injector_config(spec: InjectorConfig) -> CoglossaConfig:
    cfg = load_config(spec.config)[0]
    if spec.model not in cfg.models:
        raise ConfigError(f"injector model '{spec.model}' is not defined in {spec.config}")
    return cfg


async def run_flag_test(
    cfg: BenchConfig,
    scoring_config: Path,
    out: Path,
    *,
    pairs: int = 1,
    force: bool = False,
    backends_factory: Callable = build_backends,
    scorer: Scorer | None = None,
    log: Callable[[str], None] = print,
) -> FlagSummary:
    """Score up to `pairs` language pairs at the same time. A pair whose records were written
    with the same settings is kept, unless `force`; a crash loses only the pairs in flight."""
    if pairs < 1:
        raise ConfigError("--pairs must be at least 1")
    scoring_cfg = load_config(scoring_config)[0]
    own_scorer = build_scorer(scoring_cfg) if scorer is None else None
    scorer = scorer or own_scorer
    if scoring_cfg.roles.judge is None and scorer is None:
        raise ConfigError(NOTHING_TO_FLAG)
    opened: list[dict[str, Backend]] = []
    try:
        ds = cfg.dataset
        flores = FloresData(ds.path) if ds.kind == "flores200" else None
        if flores is not None:  # CC BY-SA 4.0: flags/*.jsonl holds altered FLORES references
            write_attribution(out, modified=FLAGS_MODIFIED)
        tiers = LanguageTiers.load(scoring_cfg.scoring.language_tiers_file)
        injector = None
        injector_cfg = None
        if cfg.injector is None:
            log(NO_INJECTOR)
        else:
            injector_cfg = _injector_config(cfg.injector)
            opened.append(backends_factory(injector_cfg))
            injector = _Injector(_runner(injector_cfg, opened[-1]), cfg.injector.model)
        opened.append(backends_factory(scoring_cfg))
        runner = _runner(scoring_cfg, opened[-1])
        settings = {
            "version": RECORDS_VERSION,
            "render_version": RENDER_VERSION,
            "scoring": _shaping(scoring_cfg),
            "injector": None
            if cfg.injector is None or injector_cfg is None
            else {"model": cfg.injector.model, **_shaping(injector_cfg)},
        }
        limit = asyncio.Semaphore(pairs)

        async def one(pair: Pair) -> tuple[list[SpanRecord], dict[str, list[Sample]]]:
            rows = load_pair(ds, flores, pair)
            picked = pick_indexes(len(rows), ds.sentences, ds.seed)
            sources = [rows[i][0] for i in picked]
            references = [rows[i][1] for i in picked]
            fingerprint = _pair_fingerprint(settings, ds, pair, sources, references)
            jsonl = out / "flags" / f"{pair.slug}.jsonl"
            meta_path = out / "flags" / f"{pair.slug}.meta.json"
            if not force:
                kept = _kept(jsonl, meta_path, fingerprint, pair, log)
                if kept is not None:
                    return kept
            async with limit:
                variants = await _variants(pair, sources, references, injector, ds.seed, log)
                ctx = StageContext(cfg=scoring_cfg, runner=runner, target_lang=pair.tgt)
                pair_records = await _score_pair(
                    ctx, scorer, pair, sources, variants, tiers.tier(pair.src, pair.tgt), log
                )
            pair_samples = _pair_samples(pair, references, variants)
            meta_path.unlink(missing_ok=True)  # until the new meta is written, nothing is kept
            write_atomic(jsonl, "".join(r.model_dump_json() + "\n" for r in pair_records))
            meta = PairMeta(fingerprint=fingerprint, spans=len(pair_records), samples=pair_samples)
            write_atomic(meta_path, meta.model_dump_json(indent=2))
            log(f"flags: {pair.label}: {len(pair_records)} spans scored")
            return pair_records, pair_samples

        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(one(pair)) for pair in ds.parsed_pairs()]
        except ExceptionGroup as e:  # the first failure; the other pairs were cancelled
            raise e.exceptions[0] from None
    finally:
        for backends in opened:
            for backend in backends.values():
                await backend.aclose()
        if own_scorer is not None:
            await own_scorer.aclose()
    records: list[SpanRecord] = []
    samples: dict[str, list[Sample]] = {}
    for task in tasks:  # in the bench config's pair order, however the pairs finished
        pair_records, pair_samples = task.result()
        records += pair_records
        _add_samples(samples, pair_samples)
    summary = flag_metrics(records, {k: samples[k] for k in KINDS if k in samples})
    write_atomic(out / "flag-summary.json", summary.model_dump_json(indent=2))
    write_atomic(out / "flag-summary.md", summary_markdown(summary))
    return summary


def _shaping(cfg: CoglossaConfig) -> dict:
    tiers = cfg.scoring.language_tiers_file
    return {
        "config": json.loads(cfg.model_dump_json(exclude=_NOT_SHAPING)),
        "prompts": prompt_hashes(cfg),
        "tiers": hashlib.sha256(tiers.read_bytes()).hexdigest() if tiers else None,
    }


def _pair_fingerprint(
    settings: dict, ds: DatasetConfig, pair: Pair, sources: list[str], references: list[str]
) -> str:
    shaping = {
        **settings,
        "pair": pair.label,
        "dataset": ds.kind,
        "seed": ds.seed,  # seeds the injections
        "sample": [sources, references],
    }
    blob = json.dumps(shaping, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _kept(
    jsonl: Path, meta_path: Path, fingerprint: str, pair: Pair, log: Callable[[str], None]
) -> tuple[list[SpanRecord], dict[str, list[Sample]]] | None:
    """The pair's saved records and samples, when they were written with these settings."""
    if not jsonl.is_file():
        return None
    try:
        meta = PairMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
        lines = jsonl.read_text(encoding="utf-8").splitlines()
        records = [SpanRecord.model_validate_json(line) for line in lines]
    except FileNotFoundError:
        log(f"flags: {pair.label}: scoring again: its settings were not recorded")
        return None
    except (OSError, ValueError, ValidationError):
        log(f"flags: {pair.label}: scoring again: the saved results cannot be read")
        return None
    if meta.fingerprint != fingerprint:
        log(f"flags: {pair.label}: scoring again: settings changed")
        return None
    if meta.spans != len(records):
        log(f"flags: {pair.label}: scoring again: the saved results are incomplete")
        return None
    lost = sum(1 for r in records if r.errors)
    note = f"; {lost} lost a signal, --force scores them again" if lost else ""
    log(f"flags: {pair.label}: kept from an earlier run ({len(records)} spans{note})")
    return records, meta.samples


def _pair_samples(
    pair: Pair, references: list[str], variants: dict[str, list[_Variant]]
) -> dict[str, list[Sample]]:
    """The pair's first few injections of each kind."""
    return {
        kind: [
            Sample(pair=pair.label, unit=v.unit, original=references[v.unit], injected=v.text)
            for v in variants[kind][:SAMPLES_PER_KIND]
        ]
        for kind in KINDS
        if variants.get(kind)
    }


def _add_samples(samples: dict[str, list[Sample]], pair_samples: dict[str, list[Sample]]) -> None:
    """The first few injections of each kind, across pairs, for a person to check."""
    for kind in KINDS:
        have = samples.setdefault(kind, [])
        have += pair_samples.get(kind, [])[: SAMPLES_PER_KIND - len(have)]
        if not have:
            del samples[kind]


async def _variants(
    pair: Pair,
    sources: list[str],
    references: list[str],
    injector: _Injector | None,
    seed: int,
    log: Callable[[str], None],
) -> dict[str, list[_Variant]]:
    rng = random.Random(seed)
    variants: dict[str, list[_Variant]] = {
        CLEAN: [_Variant(i, r, "") for i, r in enumerate(references)]
    }

    def add(unit: int, inj: Injection | None) -> None:
        if inj is not None:
            variants.setdefault(inj.kind.value, []).append(
                _Variant(unit, inj.text, inj.detail, inj.locked_term)
            )

    for i, (src, ref) in enumerate(zip(sources, references, strict=True)):
        others = references[:i] + references[i + 1 :]
        add(i, omit(ref))
        add(i, swap_number(ref, rng))
        add(i, swap_name(src, ref, others, rng))
        add(i, violate_locked_term(src, ref, others, rng))
        add(i, leave_untranslated(src))
    if injector is None:
        return variants

    async def one(kind: InjectionKind, ref: str) -> Injection | Exception | None:
        try:
            return await inject_with_model(injector.runner, injector.model, kind, pair.tgt, ref)
        except AGENT_FAILURES as e:
            return e

    for kind in MODEL_KINDS:
        results = await asyncio.gather(*(one(kind, ref) for ref in references))
        failed = [r for r in results if isinstance(r, Exception)]
        if failed:
            log(
                f"flags: {pair.label}: {kind.value}: {len(failed)} of {len(references)} "
                f"injections failed: {_one_line(str(failed[0]))}"
            )
        for i, result in enumerate(results):
            add(i, None if isinstance(result, Exception) else result)
    return variants


def _whole_blocks(chunks: list[Chunk], segments: list[Segment]) -> list[Chunk]:
    """Chunking may break inside a paragraph; a sentence is scored as one span, so a
    paragraph's segments all go to the chunk it starts in."""
    block_of = {s.id: s.block_id for s in segments}
    owner: dict[str, int] = {}
    groups: list[list[str]] = [[] for _ in chunks]
    for k, chunk in enumerate(chunks):
        for sid in chunk.segment_ids:
            groups[owner.setdefault(block_of[sid], k)].append(sid)
    return [Chunk(id=c.id, segment_ids=g) for c, g in zip(chunks, groups, strict=True) if g]


async def _score_pair(
    ctx: StageContext,
    scorer: Scorer | None,
    pair: Pair,
    sources: list[str],
    variants: dict[str, list[_Variant]],
    tier: Reliability,
    log: Callable[[str], None],
) -> list[SpanRecord]:
    scoring_cfg = ctx.cfg
    with tempfile.TemporaryDirectory() as workdir:
        doc = ingest_file(write_source_document(sources, Path(workdir)))
        segments = segment_document(doc)
    if len(doc.blocks) != len(sources):
        raise ConfigError(
            f"the bench document has {len(doc.blocks)} paragraphs for {len(sources)} sentences"
        )
    chunks = _whole_blocks(chunk_segments(doc, segments, compute_budget(scoring_cfg)), segments)
    block_index = {b.id: k for k, b in enumerate(doc.blocks)}
    bounds: dict[int, tuple[str, str]] = {}  # sentence -> its first and last segment ids
    for seg in segments:
        k = block_index[seg.block_id]
        bounds[k] = (bounds[k][0] if k in bounds else seg.id, seg.id)
    profile = DocumentProfile(
        source_lang=pair.src,
        target_lang=pair.tgt,
        mixed_language=False,
        genre=Genre.OTHER,
        tone_register="neutral",
        word_count=sum(len(s.text.split()) for s in segments),
        pipeline_profile=PipelineProfile.SHORT,
    )
    diverse = model_diversity(scoring_cfg)
    # Rounds (V = clean + the kinds in play): in round r sentence i carries slot (i + r) mod V,
    # or its clean text when that kind does not apply to it. Every call then scores every
    # sentence of its chunk, as a real run does, and mixes kinds, so the judge never sees a
    # batch of one kind. Over V rounds each sentence carries each of its variants once.
    slots = [CLEAN, *(k for k in KINDS if variants.get(k))]
    by_unit = {(kind, v.unit): v for kind, vs in variants.items() for v in vs}
    records: list[SpanRecord] = []
    skipped_locked = 0
    for ci in build_chunk_inputs(chunks, segments):
        ids = {s.id for s in ci.segments}
        here = sorted(k for k, (first, _) in bounds.items() if first in ids)
        rounds: list[list[tuple[str | None, _Variant]]] = []
        for r in range(len(slots)):
            carried: list[tuple[str | None, _Variant]] = []  # (recorded kind or None, variant)
            for i in here:
                kind = slots[(i + r) % len(slots)]
                variant = by_unit.get((kind, i))
                if variant is not None and variant.locked_term is not None:
                    # Its locked entry would also judge any other sentence of the chunk that
                    # has the term; such a test is not clean, so the sentence stays clean here.
                    term = variant.locked_term.term
                    if any(term_in(term, sources[j]) for j in here if j != i):
                        skipped_locked += 1
                        variant = None
                carried.append(
                    (kind, variant) if variant is not None else (None, by_unit[(CLEAN, i)])
                )
            if any(kind is not None for kind, _ in carried):
                rounds.append(carried)
        # The rounds are independent, so they are scored concurrently.
        all_scored = await asyncio.gather(
            *(
                _score_round(ctx, scorer, profile, ci, carried, bounds, tier, diverse)
                for carried in rounds
            )
        )
        for carried, scored in zip(rounds, all_scored, strict=True):
            for e in scored.run_errors:
                log(f"flags: {pair.label}: {e.stage}: {e.chunk_id}: {_one_line(e.message)}")
            # a judge error belongs to the spans of its failed batch, the ones left unjudged;
            # any other error (scorer, back-translation) to every span of the round
            judge_errors = [_error_line(e) for e in scored.run_errors if e.stage == "judge"]
            other_errors = [_error_line(e) for e in scored.run_errors if e.stage != "judge"]
            records += [
                _record(
                    pair,
                    tier,
                    kind,
                    v,
                    score,
                    flags,
                    judged,
                    other_errors + (judge_errors if judged is None else []),
                )
                for (kind, v), score, flags, judged in zip(
                    carried, scored.scores, scored.flags, scored.judge_errors, strict=True
                )
                if kind is not None
            ]
    if skipped_locked:
        log(
            f"flags: {pair.label}: {skipped_locked} locked-term test(s) left out: the term is "
            "also in another sentence of the chunk"
        )
    order = {kind: n for n, kind in enumerate((CLEAN, *KINDS))}
    return sorted(records, key=lambda r: (order[r.kind], r.unit))


def _error_line(e: RunError) -> str:
    return f"{e.stage}: {_one_line(e.message)}"


async def _score_round(
    ctx: StageContext,
    scorer: Scorer | None,
    profile: DocumentProfile,
    ci: ChunkInput,
    carried: list[tuple[str | None, _Variant]],
    bounds: dict[int, tuple[str, str]],
    tier: Reliability,
    diverse: bool,
) -> ChunkScores:
    """Score one round: each sentence of the chunk carrying its variant, as one real chunk."""
    locked = {v.locked_term.term: v.locked_term for _, v in carried if v.locked_term}
    guide = Guide(summary="", style_guide="", glossary=list(locked.values()))
    spans = [
        ReconciledSpan(
            start=bounds[v.unit][0],
            end=bounds[v.unit][1],
            text=v.text,
            agreement=Agreement.UNKNOWN,  # no translators: no agreement signal
            chosen_from="reference",
        )
        for _, v in carried
    ]
    return await score_chunk(
        ctx,
        scorer,
        profile,
        guide,
        ci,
        [],
        ReconcileResult(spans=spans, engine_error=None, errors=[]),
        tier=tier,
        diverse=diverse,
    )


def _record(
    pair: Pair,
    tier: Reliability,
    kind: str,
    variant: _Variant,
    score: SpanScore,
    flags: list[Flag],
    judged: list[JudgeError] | None,
    errors: list[str],
) -> SpanRecord:
    critical = [e for e in judged or [] if e.severity is ErrorSeverity.CRITICAL]
    return SpanRecord(
        pair=pair.label,
        tier=tier.value,
        unit=variant.unit,
        kind=kind,
        detail=variant.detail,
        meaning=score.meaning,
        style=score.style,
        signals=score.signals,
        critical_accuracy=any(e.category in ACCURACY_CATEGORIES for e in critical),
        critical_style=any(e.category not in ACCURACY_CATEGORIES for e in critical),
        flags=[f.code.value for f in flags],
        errors=errors,
    )
