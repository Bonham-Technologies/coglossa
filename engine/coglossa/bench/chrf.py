"""chrF (Popović 2015) with sacrebleu's defaults: character n-grams of order 1-6, beta 2,
whitespace ignored, no word n-grams. Implemented here so the engine needs no extra dependency;
the tests pin it to sacrebleu 2.4.3's values (spec §9.2).

Matches sacrebleu 2.4.3 exactly, including two details: precision and recall are averaged over
the orders where both sides have n-grams (its "effective order"), and a segment whose reference
has no n-grams of some order contributes no hypothesis n-grams of that order to the totals."""

from collections import Counter
from collections.abc import Sequence

ORDER = 6
BETA = 2.0


def _ngrams(text: str, n: int) -> Counter[str]:
    chars = "".join(text.split())
    return Counter(chars[i : i + n] for i in range(len(chars) - n + 1))


def _stats(hyp: str, ref: str) -> list[tuple[int, int, int]]:
    """Per order: (matching n-grams, hypothesis n-grams, reference n-grams)."""
    out = []
    for n in range(1, ORDER + 1):
        h, r = _ngrams(hyp, n), _ngrams(ref, n)
        ref_total = sum(r.values())
        hyp_total = sum(h.values()) if ref_total else 0
        out.append((sum((h & r).values()), hyp_total, ref_total))
    return out


def _score(stats: Sequence[tuple[int, int, int]]) -> float:
    precisions, recalls = [], []
    for match, hyp_total, ref_total in stats:
        if hyp_total > 0 and ref_total > 0:
            precisions.append(match / hyp_total)
            recalls.append(match / ref_total)
    if not precisions:
        return 0.0
    p, r = sum(precisions) / len(precisions), sum(recalls) / len(recalls)
    if p + r == 0:
        return 0.0
    b2 = BETA**2
    return 100 * (1 + b2) * p * r / (b2 * p + r)


def sentence_chrf(hypothesis: str, reference: str) -> float:
    return _score(_stats(hypothesis, reference))


def corpus_chrf(hypotheses: Sequence[str], references: Sequence[str]) -> float:
    if len(hypotheses) != len(references):
        raise ValueError("hypotheses and references must have the same length")
    totals = [(0, 0, 0)] * ORDER
    for hyp, ref in zip(hypotheses, references, strict=True):
        totals = [
            (t[0] + m, t[1] + h, t[2] + r)
            for t, (m, h, r) in zip(totals, _stats(hyp, ref), strict=True)
        ]
    return _score(totals)
