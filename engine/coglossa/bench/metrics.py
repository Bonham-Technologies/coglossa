"""Per-unit and per-system metrics (spec §9.2): MetricX-24 in reference mode through the scorer
service (raw error 0-25, lower is better; null when the unit is too long or the scorer is
unavailable, never truncated) and chrF."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from coglossa.bench.chrf import corpus_chrf, sentence_chrf
from coglossa.bench.config import Pair
from coglossa.bench.units import Unit, joiner
from coglossa.schema import QEItem
from coglossa.scoring.client import Scorer, ScorerUnavailableError


class UnitResult(BaseModel):
    first: int
    last: int
    source: str
    reference: str
    output: str
    chrf: float
    metricx: float | None = None  # raw MetricX-24 reference-mode error, 0-25, lower is better
    # Why MetricX was not asked: the unit is too long, by its source and reference ("source")
    # or by its output ("output": the unit would fit with an output as long as its reference).
    too_long: Literal["source", "output"] | None = None

    @property
    def sentences(self) -> int:
        return self.last - self.first + 1


class Fingerprint(BaseModel):
    """The settings a result was produced with; a result made with others is not reused."""

    dataset: str  # kind
    split: str
    sentences: int
    seed: int
    sample: str  # SHA-256 of the sampled line indexes, sources and references
    output: str  # output_hash: what shapes the system's translated text
    model: str | None = None  # the model key a baseline translates with


class SystemPairResult(BaseModel):
    system: str
    pair: str  # label, e.g. "en>es"
    status: Literal["complete", "failed"]
    error: str | None = None
    units: list[UnitResult] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0
    seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)
    fingerprint: Fingerprint | None = None  # None: written before fingerprints, never reused
    # Which scoring made the numbers: "<QE model>@<qe_max_chars>"; None when never scored. A
    # result written before this field also reads None, and is scored again.
    metricx_by: str | None = None
    # Outputs but no scoring (metricx_by is None): the scorer failed, had no QE model or was not
    # set; the next run with a working scorer scores the stored outputs.
    metricx_pending: bool = False
    # Marker retention (CGL-108): passages whose source has formatting, and how many of them
    # came back with their markers whole. 0 and 0 in results written before.
    markers_kept: int = 0
    markers_total: int = 0

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _old_fingerprint(cls, value: Any) -> Any:
        """A fingerprint of an older shape (no `output`) reads as None: redo the pair."""
        if isinstance(value, dict) and "output" not in value:
            return None
        return value

    @model_validator(mode="after")
    def _unidentified_scores_are_pending(self) -> "SystemPairResult":
        """Units with no `metricx_by` come from an older version (or were never scored): their
        values are not trusted, so the result reads as pending."""
        if self.status == "complete" and self.units and self.metricx_by is None:
            self.metricx_pending = True
        return self


class SummaryRow(BaseModel):
    system: str
    kind: Literal["pipeline", "baseline"] = "pipeline"  # default: older summaries
    pair: str
    status: Literal["complete", "failed"]
    units: int
    scored_units: int
    metricx_mean: float | None  # this system's own mean over the units it scored
    # The pair's comparable mean: over the units every complete system of the pair scored
    # (matched by first and last sentence), weighted by sentence count.
    metricx_shared: float | None = None
    shared_units: int = 0
    long_sources: int = 0  # units too long for MetricX by their source and reference
    long_outputs: int = 0  # units too long for MetricX by their output
    metricx_pending: bool = False  # not scored; a run with a working scorer will score it
    chrf_corpus: float | None  # null when no unit was produced (a failed pair)
    tokens_in: int
    tokens_out: int
    seconds: float
    errors: int
    markers_kept: int = 0  # passages that kept their formatting markers (CGL-108)
    markers_total: int = 0  # passages whose source has formatting


async def score_units(
    units: list[Unit],
    sentences: list[str],
    references: list[str],
    scorer: Scorer | None,
    *,
    pair: Pair,
    qe_max_chars: int,
) -> tuple[list[UnitResult], list[str]]:
    """Score each unit against its sentences' references. Returns the results and any
    scorer error strings (`scorer: <message>`)."""
    source_sep, target_sep = joiner(pair.src), joiner(pair.tgt)
    results = []
    for u in units:
        reference = target_sep.join(references[u.first : u.last + 1])
        results.append(
            UnitResult(
                first=u.first,
                last=u.last,
                source=source_sep.join(sentences[u.first : u.last + 1]),
                reference=reference,
                output=u.text,
                chrf=sentence_chrf(u.text, reference),
            )
        )
    if scorer is None or not scorer.supports_qe:
        return results, []
    fits = []
    for r in results:
        if not r.output.strip():
            continue
        if len(r.source) + len(r.output) + len(r.reference) <= qe_max_chars:
            fits.append(r)
        else:
            fits_with_reference = len(r.source) + 2 * len(r.reference) <= qe_max_chars
            r.too_long = "output" if fits_with_reference else "source"
    if not fits:
        return results, []
    items = [QEItem(source=r.source, hypothesis=r.output, reference=r.reference) for r in fits]
    try:
        scores = await scorer.qe(items)
    except ScorerUnavailableError as e:
        return results, [f"scorer: {e}"]
    for r, score in zip(fits, scores, strict=True):
        r.metricx = score
    return results, []


def summarise(
    result: SystemPairResult, kind: Literal["pipeline", "baseline"] = "pipeline"
) -> SummaryRow:
    scores = [u.metricx for u in result.units if u.metricx is not None]
    failed = result.status == "failed"
    units = result.units
    return SummaryRow(
        system=result.system,
        kind=kind,
        pair=result.pair,
        status=result.status,
        units=len(result.units),
        scored_units=len(scores),
        metricx_mean=sum(scores) / len(scores) if scores else None,
        chrf_corpus=(
            corpus_chrf([u.output for u in units], [u.reference for u in units]) if units else None
        ),
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        seconds=result.seconds,
        errors=len(result.errors) + (1 if failed else 0),
        long_sources=sum(u.too_long == "source" for u in units),
        long_outputs=sum(u.too_long == "output" for u in units),
        metricx_pending=result.metricx_pending,
        markers_kept=result.markers_kept,
        markers_total=result.markers_total,
    )


def shared_metricx(results: list[SystemPairResult]) -> dict[str, tuple[float | None, int]]:
    """For one pair's results: system name to (mean MetricX, unit count) over the units every
    complete system scored, matched by (first, last) and weighted by sentence count. Failed
    results take no part (they have no units)."""
    complete = [r for r in results if r.status == "complete"]
    scored = [{(u.first, u.last): u for u in r.units if u.metricx is not None} for r in complete]
    shared = set.intersection(*(set(s) for s in scored)) if scored else set()
    out: dict[str, tuple[float | None, int]] = {}
    for r, by_span in zip(complete, scored, strict=True):
        units = [by_span[k] for k in sorted(shared)]
        weight = sum(u.sentences for u in units)
        total = sum(u.metricx * u.sentences for u in units if u.metricx is not None)
        out[r.system] = (total / weight if weight else None, len(units))
    return out
