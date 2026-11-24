"""Score stage: M1 helpers (null scores, review-derived flags) plus M2 per-chunk scoring
(spec §6)."""

import asyncio
import math
from dataclasses import dataclass
from itertools import combinations

from coglossa.schema import (
    Agreement,
    Candidate,
    DocumentProfile,
    Flag,
    FlagCode,
    Guide,
    JudgeError,
    QEItem,
    ReconciledSpan,
    Reliability,
    RunError,
    SimilarityPair,
    SpanScore,
)
from coglossa.scoring.aggregate import SpanEvidence, normalize_sim, score_span
from coglossa.scoring.client import Scorer, ScorerUnavailableError
from coglossa.scoring.numbers import number_check
from coglossa.scoring.rules import agreement_signal, guide_check, length_ratio
from coglossa.scoring.untranslated import kept_names, untranslated_check
from coglossa.stages.context import StageContext
from coglossa.stages.judge import judge_chunk
from coglossa.stages.reconcile import ReconcileResult
from coglossa.stages.roundtrip import back_translate_chunk
from coglossa.stages.translate import ChunkInput

SIGNALS = ("qe", "xsim", "roundtrip", "judge", "agreement", "guide_check")


def unscored() -> SpanScore:
    return SpanScore(signals=dict.fromkeys(SIGNALS))


def competing_candidates(
    candidates: list[Candidate], start: str, end: str, positions: dict[str, int]
) -> list[Candidate]:
    a, b = positions[start], positions[end]
    return [c for c in candidates if positions[c.start] <= b and positions[c.end] >= a]


def initial_flags(
    span: ReconciledSpan, *, engine_error: str | None, competing: list[Candidate]
) -> list[Flag]:
    alternatives = [
        {"translator": c.translator, "model": c.model, "text": c.text} for c in competing
    ]
    flags: list[Flag] = []
    if engine_error is not None:
        flags.append(
            Flag(
                code=FlagCode.ENGINE_ERROR,
                explanation=(
                    "This passage was not reviewed because an engine step failed. The text "
                    "shown is an unreviewed translator draft, or empty if none was produced."
                ),
                evidence={"error": engine_error, "candidates": alternatives},
            )
        )
    if span.agreement is Agreement.MAJOR:
        flags.append(
            Flag(
                code=FlagCode.AGENT_DISAGREEMENT,
                explanation=(
                    span.rationale or "The translators disagreed on the meaning of this passage."
                ),
                evidence={"chosen_from": span.chosen_from, "candidates": alternatives},
            )
        )
    if span.cultural_or_wordplay:
        flags.append(
            Flag(
                code=FlagCode.CULTURAL_OR_WORDPLAY,
                explanation=span.rationale
                or (
                    "This passage contains a cultural reference, idiom or "
                    "wordplay that may not fully carry over."
                ),
                evidence={"candidates": alternatives},
            )
        )
    return flags


@dataclass
class ChunkScores:
    scores: list[SpanScore]
    flags: list[list[Flag]]
    judge_errors: list[list[JudgeError] | None]
    back_translations: list[str | None]
    run_errors: list[RunError]


def span_source_text(ci: ChunkInput, start: str, end: str) -> str:
    ids = [s.id for s in ci.segments]
    a, b = ids.index(start), ids.index(end)
    return " ".join(s.text for s in ci.segments[a : b + 1])


class _ScorerCalls:
    """Wraps the optional scorer; remembers the first failure, returns None for failed calls."""

    def __init__(self, scorer: Scorer | None) -> None:
        self.scorer = scorer
        self.error: str | None = None

    async def qe(self, items: list[QEItem]) -> list[float] | None:
        if self.scorer is None or not items or not getattr(self.scorer, "supports_qe", True):
            return None
        try:
            return self._checked(await self.scorer.qe(items), len(items))
        except ScorerUnavailableError as e:
            self.error = self.error or str(e)
            return None

    async def similarity(self, pairs: list[SimilarityPair]) -> list[float] | None:
        if (
            self.scorer is None
            or not pairs
            or not getattr(self.scorer, "supports_similarity", True)
        ):
            return None
        try:
            return self._checked(await self.scorer.similarity(pairs), len(pairs))
        except ScorerUnavailableError as e:
            self.error = self.error or str(e)
            return None

    @staticmethod
    def _checked(values: list[float], expected: int) -> list[float]:
        if len(values) != expected:
            raise ScorerUnavailableError(
                f"scorer returned {len(values)} values for {expected} inputs"
            )
        return values


def _keyed[K](keys: list[K], values: list[float] | None) -> dict[K, float]:
    return dict(zip(keys, values, strict=True)) if values is not None else {}


def _candidate_pairs(
    ci: ChunkInput, candidates: list[Candidate], start: str, end: str
) -> list[SimilarityPair]:
    positions = {s.id: k for k, s in enumerate(ci.segments)}
    texts: dict[str, list[str]] = {}
    for c in competing_candidates(candidates, start, end, positions):
        texts.setdefault(c.translator, []).append(c.text)
    joined = [" ".join(parts) for parts in texts.values()]
    return [SimilarityPair(a=a, b=b) for a, b in combinations(joined, 2)]


async def score_chunk(
    ctx: StageContext,
    scorer: Scorer | None,
    profile: DocumentProfile,
    guide: Guide,
    ci: ChunkInput,
    candidates: list[Candidate],
    reconciled: ReconcileResult,
    *,
    tier: Reliability,
    diverse: bool,
) -> ChunkScores:
    spans = reconciled.spans
    sources = [span_source_text(ci, s.start, s.end) for s in spans]
    live = [i for i, s in enumerate(spans) if s.text.strip()]
    calls = _ScorerCalls(scorer)
    cfg = ctx.cfg.scoring

    def qe_fits(item: QEItem) -> bool:
        size = len(item.source) + len(item.hypothesis) + len(item.reference or "")
        return size <= cfg.qe_max_chars

    def sim_fits(pair: SimilarityPair) -> bool:
        return len(pair.a) <= cfg.sim_max_chars and len(pair.b) <= cfg.sim_max_chars

    # Each request is keyed by the span it belongs to, so a dropped (over-long) input can
    # never shift another span's score.
    qe_req = [(i, QEItem(source=sources[i], hypothesis=spans[i].text)) for i in live]
    qe_req = [(k, item) for k, item in qe_req if qe_fits(item)]
    sim_req: list[tuple[tuple[str, int, int], SimilarityPair]] = [
        (("final", i, 0), SimilarityPair(a=sources[i], b=spans[i].text)) for i in live
    ]
    for i in live:
        pairs = _candidate_pairs(ci, candidates, spans[i].start, spans[i].end)
        sim_req += [(("cand", i, n), p) for n, p in enumerate(pairs)]
    sim_req = [(k, p) for k, p in sim_req if sim_fits(p)]

    judged, roundtrip, qe_raw, sims_raw = await asyncio.gather(
        judge_chunk(ctx, profile, guide, ci, spans),
        back_translate_chunk(ctx, profile, ci, spans),
        calls.qe([item for _, item in qe_req]),
        calls.similarity([p for _, p in sim_req]),
    )
    qe = _keyed([k for k, _ in qe_req], qe_raw)
    sims = _keyed([k for k, _ in sim_req], sims_raw)

    bt_texts = roundtrip.texts or [None] * len(spans)
    # a scorer that just failed is not asked again for this chunk
    bt_live = [(i, bt) for i in live if (bt := bt_texts[i])] if calls.error is None else []
    rt_qe_req = [(i, QEItem(source="", hypothesis=bt, reference=sources[i])) for i, bt in bt_live]
    rt_qe_req = [(k, item) for k, item in rt_qe_req if qe_fits(item)]
    rt_sim_req = [(i, SimilarityPair(a=sources[i], b=bt)) for i, bt in bt_live]
    rt_sim_req = [(k, p) for k, p in rt_sim_req if sim_fits(p)]
    rt_qe_raw, rt_sims_raw = await asyncio.gather(
        calls.qe([item for _, item in rt_qe_req]),
        calls.similarity([p for _, p in rt_sim_req]),
    )
    rt_qe = _keyed([k for k, _ in rt_qe_req], rt_qe_raw)
    rt_sims = _keyed([k for k, _ in rt_sim_req], rt_sims_raw)

    scores: list[SpanScore] = []
    flags: list[list[Flag]] = []
    keep = kept_names(guide)  # names the guide keeps as they are are never "untranslated"
    for i, rs in enumerate(spans):
        cand = [
            v
            for (kind, span, _), v in sims.items()
            if kind == "cand" and span == i and math.isfinite(v)
        ]
        signal, violations = guide_check(sources[i], rs.text, guide)
        evidence = SpanEvidence(
            source_text=sources[i],
            final_text=rs.text,
            genre=profile.genre,
            qe_raw=qe.get(i),
            xsim_cos=sims.get(("final", i, 0)),
            roundtrip_qe_raw=rt_qe.get(i),
            roundtrip_cos=rt_sims.get(i),
            judge_errors=judged.errors_by_span[i] if judged.errors_by_span is not None else None,
            agreement=agreement_signal(
                rs.agreement,
                normalize_sim(sum(cand) / len(cand), cfg.xsim_floor) if cand else None,
            ),
            guide_signal=signal,
            guide_violations=tuple(violations),
            length_ratio=length_ratio(sources[i], rs.text),
            number_mismatch=number_check(sources[i], rs.text, profile.source_lang, ctx.target_lang),
            untranslated=untranslated_check(sources[i], rs.text, ctx.target_lang, keep),
        )
        score, span_flags = score_span(evidence, tier=tier, cfg=cfg, diverse=diverse)
        scores.append(score)
        flags.append(span_flags)

    run_errors = [*judged.run_errors, *roundtrip.run_errors]
    if calls.error is not None:
        run_errors.append(
            RunError(stage="score", chunk_id=ci.chunk.id, role="scorer", message=calls.error)
        )
    return ChunkScores(
        scores=scores,
        flags=flags,
        judge_errors=[
            judged.errors_by_span[i] if judged.errors_by_span is not None and i in live else None
            for i in range(len(spans))
        ],
        back_translations=[(bt_texts[i] or None) if i in live else None for i in range(len(spans))],
        run_errors=run_errors,
    )
