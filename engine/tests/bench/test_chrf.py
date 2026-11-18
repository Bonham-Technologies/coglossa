import pytest

from coglossa.bench.chrf import corpus_chrf, sentence_chrf

# Expected values computed with sacrebleu 2.4.3: CHRF().sentence_score / corpus_score.
CASES = [
    ("The cat sat on the mat.", "The cat sat on the mat.", 100.0),
    ("The cat sat on the mat.", "A cat was sitting on the mat.", 39.7849),
    ("", "The cat sat on the mat.", 0.0),
    ("猫が座った", "猫が座っていた", 40.1856),
]


@pytest.mark.parametrize(("hyp", "ref", "expected"), CASES)
def test_sentence_chrf_matches_sacrebleu(hyp, ref, expected):
    assert sentence_chrf(hyp, ref) == pytest.approx(expected, abs=1e-4)


def test_corpus_chrf_sums_statistics_like_sacrebleu():
    hyps = [CASES[1][0], CASES[3][0]]
    refs = [CASES[1][1], CASES[3][1]]
    assert corpus_chrf(hyps, refs) == pytest.approx(39.3810, abs=1e-4)


def test_hypothesis_ngrams_vanish_when_reference_has_none_of_that_order():
    # sacrebleu drops a segment's hypothesis n-gram count for orders the reference lacks.
    assert sentence_chrf("abcdefgh", "abc") == pytest.approx(65.5660, abs=1e-4)
    assert corpus_chrf(["abcdefgh", "猫が座った"], ["abc", "猫が座っていた"]) == pytest.approx(
        42.2924, abs=1e-4
    )
    assert corpus_chrf(["abc", "hello there"], ["", "hello world"]) == pytest.approx(
        31.1971, abs=1e-4
    )


def test_edge_cases():
    assert sentence_chrf("", "") == 0.0
    assert corpus_chrf([], []) == 0.0
    with pytest.raises(ValueError, match="same length"):
        corpus_chrf(["a"], [])
