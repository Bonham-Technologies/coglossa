"""Rule-based scoring signals (spec §6.1): reviewer agreement, guide consistency, length
ratio, and MQM-style judge penalties. Pure functions; no I/O."""

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Literal

from coglossa.schema import ACCURACY_CATEGORIES, Agreement, ErrorSeverity, Guide, JudgeError
from coglossa.segment.text_metrics import SPACELESS, SPACELESS_RANGES

# Hangul syllables, Jamo and compatibility Jamo: word characters (\w) that nevertheless
# do not need a space to separate a Latin term from the next word.
_HANGUL_RANGES = r"\u1100-\u11ff\u3130-\u318f\uac00-\ud7af"
# a "word character" for boundary purposes: \w minus the spaceless scripts and Hangul
_WORD_CHAR = rf"[^\W{SPACELESS_RANGES}{_HANGUL_RANGES}]"

_AGREEMENT_VALUE = {Agreement.AGREE: 1.0, Agreement.MINOR: 0.7, Agreement.MAJOR: 0.2}


def agreement_signal(agreement: Agreement, candidate_similarity: float | None) -> float | None:
    parts = [p for p in (_AGREEMENT_VALUE.get(agreement), candidate_similarity) if p is not None]
    return sum(parts) / len(parts) if parts else None


@dataclass(frozen=True)
class GuideViolation:
    term: str
    expected: str
    kind: Literal["glossary", "entity"]
    locked: bool
    forms: tuple[str, ...] = ()  # the entry's other accepted forms of `expected` (CGL-109)

    def as_evidence(self) -> dict[str, object]:
        evidence = asdict(self)
        if self.forms:
            evidence["forms"] = list(self.forms)
        else:
            del evidence["forms"]  # as before forms existed
        return evidence


def term_in(term: str, text: str) -> bool:
    """Case-insensitive term search that does not match inside other words: a left word
    boundary (and a right one for terms of 3 characters or fewer, e.g. "UE" vs "que").
    Terms in spaceless scripts (CJK, kana, Thai, Lao, Khmer, Myanmar) match as substrings.
    Characters of those scripts and Hangul do not count as word characters, so a Latin term
    touching them ("新しいCoglossaを", "EU는") is still found."""
    needle = term.strip().casefold()
    if not needle:
        return False
    haystack = text.casefold()
    if SPACELESS.search(needle):
        return needle in haystack
    pattern = (
        f"(?<!{_WORD_CHAR})" + re.escape(needle) + (f"(?!{_WORD_CHAR})" if len(needle) <= 3 else "")
    )
    return re.search(pattern, haystack) is not None


def any_in(terms: Iterable[str], text: str) -> bool:
    """term_in for any of `terms`: an entry's words and their listed forms (CGL-109)."""
    return any(term_in(t, text) for t in terms)


def guide_check(
    source_text: str, final_text: str, guide: Guide
) -> tuple[float | None, list[GuideViolation]]:
    applicable = 0
    violations: list[GuideViolation] = []
    # An entry applies where its term (or name), or one of its source forms, is in the source,
    # and holds where its rendering, or one of its accepted forms, is in the translation.
    for g in guide.glossary:
        if any_in(g.source_words, source_text):
            applicable += 1
            if not any_in(g.target_words, final_text):
                violations.append(
                    GuideViolation(g.term, g.rendering, "glossary", g.locked, tuple(g.forms))
                )
    for e in guide.entities:
        if any_in(e.source_words, source_text):
            applicable += 1
            if not any_in(e.target_words, final_text):
                violations.append(
                    GuideViolation(e.name, e.rendering, "entity", e.locked, tuple(e.forms))
                )
    if applicable == 0:
        return None, []
    return (applicable - len(violations)) / applicable, violations


def length_ratio(source_text: str, final_text: str) -> float | None:
    src = len(source_text.strip())
    return len(final_text.strip()) / src if src else None


def judge_penalties(
    errors: list[JudgeError], penalties: dict[ErrorSeverity, float]
) -> tuple[float, float]:
    accuracy = sum(penalties[e.severity] for e in errors if e.category in ACCURACY_CATEGORIES)
    style = sum(penalties[e.severity] for e in errors if e.category not in ACCURACY_CATEGORIES)
    return accuracy, style
