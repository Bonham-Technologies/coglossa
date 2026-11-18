"""Opt-in sanity checks against real weights. Set COGLOSSA_REAL_MODELS=1 and
COGLOSSA_METRICX_DIR, COGLOSSA_MT5_TOKENIZER_DIR, COGLOSSA_LABSE_DIR to local directories."""

import os

import pytest

pytestmark = [
    pytest.mark.realmodels,
    pytest.mark.skipif(
        os.environ.get("COGLOSSA_REAL_MODELS") != "1",
        reason="set COGLOSSA_REAL_MODELS=1 to run against real weights",
    ),
]


@pytest.fixture(scope="module")
def metricx():
    from coglossa_scorer.qe import MetricXQE

    return MetricXQE.from_pretrained(
        os.environ["COGLOSSA_METRICX_DIR"], os.environ["COGLOSSA_MT5_TOKENIZER_DIR"]
    )


def test_metricx_prefers_the_correct_translation(metricx):
    good, bad = metricx.score(
        [
            ("Il pleut à Paris.", "It is raining in Paris.", None),
            ("Il pleut à Paris.", "The cat ate my homework.", None),
        ]
    )
    assert good < bad


def test_metricx_reference_mode_prefers_the_faithful_back_translation(metricx):
    # the roundtrip signal: back-translation as hypothesis, the source as reference
    good, bad = metricx.score(
        [
            ("", "It's raining in Paris.", "It is raining in Paris."),
            ("", "It is sunny in Lyon.", "It is raining in Paris."),
        ]
    )
    assert good < bad


def test_metricx_score_does_not_depend_on_the_batch(metricx):
    items = [
        ("Il pleut à Paris.", "It is raining in Paris.", None),
        ("我每天早上喝咖啡。", "I drink coffee every morning, every single day of the week.", None),
    ]
    batched = metricx.score(items)
    alone = [metricx.score([item])[0] for item in items]
    assert batched == pytest.approx(alone, abs=1e-3)


def test_labse_aligns_translations():
    from coglossa_scorer.embed import LabseEmbedder

    emb = LabseEmbedder.from_pretrained(os.environ["COGLOSSA_LABSE_DIR"])
    same, different = emb.similarity(
        [("Il pleut.", "It is raining."), ("Il pleut.", "Stock prices fell sharply.")]
    )
    assert same > different
