import pytest
from pydantic import ValidationError

from coglossa.schema import (
    ACCURACY_CATEGORIES,
    SCORER_MAX_BATCH,
    Agreement,
    ErrorCategory,
    ErrorSeverity,
    JudgeError,
    JudgeOutput,
    QEItem,
    QERequest,
    Reliability,
    Report,
    ScorerHealth,
    SimilarityPair,
    SimilarityRequest,
    Span,
    SpanScore,
)


def test_judge_error_defaults_and_categories():
    err = JudgeError(category="accuracy/omission", severity="major", explanation="drops a clause")
    assert err.excerpt == ""
    assert err.category is ErrorCategory.OMISSION and err.severity is ErrorSeverity.MAJOR
    assert {
        ErrorCategory.MISTRANSLATION,
        ErrorCategory.OMISSION,
        ErrorCategory.ADDITION,
        ErrorCategory.TERMINOLOGY,
    } == ACCURACY_CATEGORIES
    out = JudgeOutput.model_validate({"spans": [{"start": "s1", "end": "s1"}]})
    assert out.spans[0].errors == []


def test_unknown_severity_rejected():
    with pytest.raises(ValidationError):
        JudgeError(category="fluency", severity="catastrophic", explanation="x")


def test_span_and_report_new_defaults():
    span = Span(
        id="p1",
        chunk_id="c1",
        start="s1",
        end="s1",
        text="t",
        agreement=Agreement.AGREE,
        chosen_from="literal",
        score=SpanScore(signals={}),
    )
    assert span.judge_errors is None and span.back_translation is None  # None = not run
    fields = Report.model_fields
    assert fields["language_tier"].default is Reliability.UNSCORED
    assert fields["notices"].default_factory() == []


def test_scorer_api_batch_limits():
    assert SCORER_MAX_BATCH == 256
    with pytest.raises(ValidationError):
        QERequest(items=[])
    with pytest.raises(ValidationError):
        QERequest(items=[QEItem(source="a", hypothesis="b")] * 257)
    assert QERequest(items=[QEItem(source="a", hypothesis="b")]).items[0].reference is None
    with pytest.raises(ValidationError):
        SimilarityRequest(pairs=[])
    assert SimilarityRequest(pairs=[SimilarityPair(a="x", b="y")]).pairs[0].b == "y"


def test_scorer_health():
    health = ScorerHealth(device="cpu")
    assert health.status == "ok" and health.qe_model is None
    with pytest.raises(ValidationError):
        ScorerHealth(status="down", device="cpu")
