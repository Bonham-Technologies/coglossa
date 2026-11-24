"""Turn per-span evidence into scores, reliability and flags (spec §6.2–§6.4).
Pure functions: the pipeline gathers evidence; this module decides."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from coglossa.config import CoglossaConfig, ScoringConfig, SignalWeights
from coglossa.schema import (
    ACCURACY_CATEGORIES,
    ErrorCategory,
    ErrorSeverity,
    Flag,
    FlagCode,
    Genre,
    JudgeError,
    Reliability,
    Span,
    SpanScore,
)
from coglossa.scoring.numbers import NumberMismatch
from coglossa.scoring.rules import GuideViolation, judge_penalties
from coglossa.scoring.tiers import downgrade
from coglossa.scoring.untranslated import Untranslated

SIGNALS = ("qe", "xsim", "roundtrip", "judge", "agreement", "guide_check")
MODEL_SIGNALS = ("qe", "xsim", "roundtrip", "judge")
LITERARY = frozenset({Genre.LITERARY_PROSE, Genre.POETRY})


@dataclass(frozen=True)
class SpanEvidence:
    source_text: str
    final_text: str
    genre: Genre
    qe_raw: float | None = None
    xsim_cos: float | None = None
    roundtrip_qe_raw: float | None = None
    roundtrip_cos: float | None = None
    judge_errors: list[JudgeError] | None = None  # None = judge unavailable
    agreement: float | None = None
    guide_signal: float | None = None
    guide_violations: tuple[GuideViolation, ...] = ()
    length_ratio: float | None = None
    number_mismatch: NumberMismatch | None = None
    untranslated: Untranslated | None = None


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def normalize_qe(raw: float | None) -> float | None:
    return _clamp01(1 - raw / 25) if _finite(raw) else None  # NaN/inf: no score, not 0


def normalize_sim(cos: float | None, floor: float) -> float | None:
    return _clamp01((cos - floor) / (1 - floor)) if _finite(cos) else None


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _weighted_meaning(
    signals: dict[str, float | None], weights: SignalWeights, roundtrip_factor: float
) -> float | None:
    pairs = [
        (signals["qe"], weights.qe),
        (signals["xsim"], weights.xsim),
        (signals["roundtrip"], weights.roundtrip * roundtrip_factor),
        (signals["judge"], weights.judge),
        (signals["agreement"], weights.agreement),
    ]
    available = [(v, w) for v, w in pairs if v is not None and w > 0]
    total = sum(w for _, w in available)
    if total == 0:
        return None
    return 100 * sum(v * w for v, w in available) / total


def score_span(
    ev: SpanEvidence, *, tier: Reliability, cfg: ScoringConfig, diverse: bool
) -> tuple[SpanScore, list[Flag]]:
    if not ev.final_text.strip():
        return SpanScore(signals=dict.fromkeys(SIGNALS)), []
    judge_acc = judge_style = None
    if ev.judge_errors is not None:
        acc_pen, sty_pen = judge_penalties(ev.judge_errors, cfg.mqm_penalties)
        judge_acc = _clamp01(1 - acc_pen / cfg.judge_penalty_scale)
        judge_style = _clamp01(1 - sty_pen / cfg.judge_penalty_scale)
    signals: dict[str, float | None] = {
        "qe": normalize_qe(ev.qe_raw),
        "xsim": normalize_sim(ev.xsim_cos, cfg.xsim_floor),
        "roundtrip": _mean(
            [normalize_qe(ev.roundtrip_qe_raw), normalize_sim(ev.roundtrip_cos, cfg.xsim_floor)]
        ),
        "judge": judge_acc,
        "agreement": ev.agreement,
        "guide_check": ev.guide_signal,
    }
    factor = cfg.literary_roundtrip_factor if ev.genre in LITERARY else 1.0
    has_model_signal = any(signals[name] is not None for name in MODEL_SIGNALS)
    meaning = (
        _weighted_meaning(signals, cfg.weights_by_tier[tier.value], factor)
        if has_model_signal
        else None
    )
    style = None if judge_style is None else 100 * judge_style
    missing = sum(signals[name] is None for name in MODEL_SIGNALS)
    if meaning is None and style is None:
        reliability = Reliability.UNSCORED
    elif missing >= 2 or not diverse:
        reliability = downgrade(tier)
    else:
        reliability = tier
    score = SpanScore(
        signals=signals,
        meaning=None if meaning is None else round(meaning, 1),
        style=None if style is None else round(style, 1),
        reliability=reliability,
    )
    return score, _flags(ev, score, tier, cfg)


def terminology_explanation(locked: Sequence[GuideViolation]) -> str:
    """A terminology flag's explanation: the locked entries the passage does not follow."""
    rules = "; ".join(f"'{v.term}' should be rendered as '{v.expected}'" for v in locked)
    return f"Locked guide entries were not followed: {rules}."


def _flags(ev: SpanEvidence, score: SpanScore, tier: Reliability, cfg: ScoringConfig) -> list[Flag]:
    errors = ev.judge_errors or []
    accuracy = [e for e in errors if e.category in ACCURACY_CATEGORIES]
    style = [e for e in errors if e.category not in ACCURACY_CATEGORIES]
    base = {"signals": score.signals, "meaning": score.meaning, "style": score.style}
    flags: list[Flag] = []

    threshold = cfg.thresholds.meaning[tier.value]
    flag = _threshold_flag(
        FlagCode.LOW_MEANING,
        "Meaning",
        score.meaning,
        threshold,
        accuracy,
        base,
        fallback="Compare the translation with the source carefully.",
    )
    if flag:
        flags.append(flag)
    flag = _threshold_flag(
        FlagCode.STYLE_DRIFT,
        "Style",
        score.style,
        cfg.thresholds.style,
        style,
        base,
        fallback="Review the wording and register of this passage.",
    )
    if flag:
        flags.append(flag)

    locked = [v for v in ev.guide_violations if v.locked]
    if locked:
        flags.append(
            Flag(
                code=FlagCode.TERMINOLOGY_VIOLATION,
                explanation=terminology_explanation(locked),
                evidence={"violations": [v.as_evidence() for v in locked]},
            )
        )

    if ev.number_mismatch is not None:  # a hard trigger: it never changes the scores
        flags.append(
            Flag(
                code=FlagCode.NUMBER_MISMATCH,
                explanation=ev.number_mismatch.explanation(),
                evidence=ev.number_mismatch.as_evidence(),
            )
        )

    if ev.untranslated is not None:  # a hard trigger: it never changes the scores
        flags.append(
            Flag(
                code=FlagCode.UNTRANSLATED,
                explanation=ev.untranslated.explanation(),
                evidence=ev.untranslated.as_evidence(),
            )
        )

    omissions = [e for e in accuracy if e.category is ErrorCategory.OMISSION]
    low, high = cfg.length_ratio_band
    ratio = ev.length_ratio
    if omissions and ratio is not None and not low <= ratio <= high:
        flags.append(
            Flag(
                code=FlagCode.POSSIBLE_OMISSION,
                explanation=(
                    f"The translation is {ratio:.2f}× the length of the source and the judge "
                    f"reported an omission: {omissions[0].explanation}"
                ),
                evidence={
                    "length_ratio": ratio,
                    "band": [low, high],
                    "judge_errors": [e.model_dump() for e in omissions],
                },
            )
        )
    return flags


def _threshold_flag(
    code: FlagCode,
    label: str,
    value: float | None,
    threshold: float,
    errors: list[JudgeError],
    base: dict[str, object],
    *,
    fallback: str,
) -> Flag | None:
    critical = [e for e in errors if e.severity is ErrorSeverity.CRITICAL]
    below = value is not None and value < threshold
    if not below and not critical:
        return None
    if below:
        head = f"{label} score {value:.1f} is below the {threshold:g} threshold."
    else:
        head = f"A critical {label.lower()} error was found."
    reason = (critical or errors)[0].explanation if (critical or errors) else None
    tail = f" {reason or fallback}"
    return Flag(
        code=code,
        explanation=head + tail,
        evidence={**base, "threshold": threshold, "judge_errors": [e.model_dump() for e in errors]},
    )


def model_diversity(cfg: CoglossaConfig) -> bool:
    """Spec §6.4: the judge and roundtrip models must be independent of the translators (and
    the judge of the reviewer) for a span to keep its language-pair tier."""
    roles = cfg.roles
    translator_models = {t.model for t in roles.translators()}
    judge_ok = (
        roles.judge is not None
        and roles.judge.model not in translator_models
        and roles.judge.model != roles.reviewer.model
    )
    roundtrip_ok = roles.roundtrip is not None and roles.roundtrip.model not in translator_models
    return judge_ok and roundtrip_ok


UNSCORED_EXPLANATION = (
    "No scores were computed: no scorer or judge was configured or reachable. Review every passage."
)


def unread_notices(notices: Sequence[str]) -> list[Flag]:
    """The source file's notices about parts left out (CGL-87). They are document notices, not
    run errors: what could be read is translated in full, and translating it again would leave
    the same parts out."""
    return [Flag(code=FlagCode.UNREAD_CONTENT, explanation=n) for n in dict.fromkeys(notices)]


def document_notices(
    tier: Reliability,
    source_lang: str,
    target_lang: str,
    *,
    scored: bool = True,
    unread: Sequence[str] = (),
) -> list[Flag]:
    notices = unread_notices(unread)
    if not scored:
        return [*notices, Flag(code=FlagCode.UNSCORED, explanation=UNSCORED_EXPLANATION)]
    if tier is not Reliability.C:
        return notices
    return [
        *notices,
        Flag(
            code=FlagCode.LOW_RELIABILITY_LANGUAGE,
            explanation=(
                f"Scores for {source_lang}→{target_lang} are less reliable — review more broadly."
            ),
            evidence={"tier": tier.value},
        ),
    ]


def any_scored(spans: list[Span]) -> bool:
    return any(s.score.meaning is not None or s.score.style is not None for s in spans)
