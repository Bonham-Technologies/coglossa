from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from coglossa.schema import (
    Agreement,
    FlagCode,
    FlagStatus,
    Reliability,
    RunProgress,
    RunState,
    SpanScore,
)
from coglossa.schema.views import (
    LanguageCode,
    ReviewAction,
    ReviewFlag,
    ReviewSpan,
    RunSummary,
    SpanDetail,
    SpanPage,
)

NOW = datetime(2026, 10, 11, tzinfo=UTC)


@pytest.mark.parametrize("code", ["en", "pt-BR", "zh-Hant-TW", "fil", "es_419", "sr-Latn"])
def test_language_codes_accepted(code):
    assert TypeAdapter(LanguageCode).validate_python(code) == code


@pytest.mark.parametrize("code", ["", "e", "english!", "en--US", "12", "en-" + "x" * 40])
def test_language_codes_rejected(code):
    with pytest.raises(ValidationError):
        TypeAdapter(LanguageCode).validate_python(code)


def _span(**kw) -> ReviewSpan:
    data = {
        "id": "7b0c8a52-6f0e-4c55-9b55-0c7d7b0b1a10",
        "number": 3,
        "chunk_id": "c1",
        "start": "s4",
        "end": "s5",
        "source": "Le chat dort.",
        "text": "The cat sleeps.",
        "agreement": Agreement.MINOR,
        "score": SpanScore(signals={"qe": 0.8}, meaning=81.0, reliability=Reliability.B),
        "flags": [
            ReviewFlag(id=12, code=FlagCode.LOW_MEANING, explanation="x", status=FlagStatus.OPEN)
        ],
        "edited": False,
    }
    data.update(kw)
    return ReviewSpan(**data)


def test_span_detail_extends_review_span():
    base = _span()
    detail = SpanDetail(
        **base.model_dump(),
        chosen_from="contextual",
        rationale="",
        issues=[],
        judge_errors=None,
        back_translation=None,
        candidates=[],
        revisions=[],
    )
    assert detail.flags[0].evidence == {}
    assert SpanPage(total=1, offset=0, limit=50, spans=[base]).spans[0].number == 3


def test_run_summary_round_trips():
    summary = RunSummary(
        id="r",
        project_id="p",
        document_id="d",
        filename="story.txt",
        state=RunState.TRANSLATING,
        target_lang="en",
        source_lang=None,
        pipeline_profile=None,
        created_at=NOW,
        started_at=None,
        finished_at=None,
        error=None,
    )
    assert RunSummary.model_validate_json(summary.model_dump_json()) == summary
    progress = RunProgress(
        run_id="r", state=RunState.TRANSLATING, chunks_total=2, chunks_done={}, chunks_with_errors=0
    )
    assert progress.retryable is False


def test_review_actions_are_the_feedback_log_vocabulary():
    assert {a.value for a in ReviewAction} == {
        "accept",
        "edit",
        "use_candidate",
        "accept_flag",
        "dismiss_flag",
        "reopen_flag",
        "add_to_guide",
    }
