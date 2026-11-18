import json

import pytest

from coglossa.llm.payload import INPUT_MARKER, parse_input, render_input
from coglossa.schema import (
    Guide,
    JudgeOutput,
    ProfileAssessment,
    ReconcileOutput,
    TranslationOutput,
)
from coglossa.testing.synthetic import chat_completion, synthetic_response

SEGS = [{"id": "s1", "text": "Bonjour."}, {"id": "s2", "text": "Ça va ?"}]


def test_render_and_parse_round_trip():
    payload = {"task": "profile", "sample": "héllo"}
    text = render_input(payload)
    assert text.startswith(INPUT_MARKER)
    assert "héllo" in text  # not ASCII-escaped
    messages = [{"role": "system", "content": "x"}, {"role": "user", "content": text}]
    assert parse_input(messages) == payload


def test_parse_input_without_marker_raises():
    with pytest.raises(ValueError, match="INPUT_JSON"):
        parse_input([{"role": "user", "content": "hello"}])


def test_profile_response_is_valid():
    out = synthetic_response(
        {"task": "profile", "sample": "x", "source_lang_hint": "fr", "target_lang": "en"}
    )
    assert ProfileAssessment.model_validate(out).source_lang == "fr"
    out = synthetic_response(
        {"task": "profile", "sample": "x", "source_lang_hint": None, "target_lang": "en"}
    )
    assert out["source_lang"] == "und"


def test_guide_and_merge_responses_are_valid():
    guide = Guide.model_validate(synthetic_response({"task": "guide", "text": "a b c"}))
    assert "3 words" in guide.summary
    merged = synthetic_response(
        {"task": "guide_merge", "partials": [guide.model_dump(), guide.model_dump()]}
    )
    assert Guide.model_validate(merged).summary.count("3 words") == 2


def test_translate_response_covers_segments():
    out = synthetic_response({"task": "translate", "target_lang": "en", "segments": SEGS})
    parsed = TranslationOutput.model_validate(out)
    assert [(s.start, s.end, s.text) for s in parsed.spans] == [
        ("s1", "s1", "[en] Bonjour."),
        ("s2", "s2", "[en] Ça va ?"),
    ]


def test_reconcile_picks_first_candidate():
    spans = [{"start": "s1", "end": "s2", "text": "Hi. OK?", "notes": ""}]
    out = synthetic_response(
        {
            "task": "reconcile",
            "segments": SEGS,
            "candidates": [{"translator": "literal", "spans": spans}],
        }
    )
    parsed = ReconcileOutput.model_validate(out)
    assert parsed.spans[0].chosen_from == "literal"
    assert parsed.spans[0].text == "Hi. OK?"


def test_unknown_task_raises():
    with pytest.raises(ValueError, match="unknown task"):
        synthetic_response({"task": "dance"})


def test_chat_completion_shape():
    body = chat_completion('{"a": 1}', finish_reason="length")
    assert body["choices"][0]["message"]["content"] == '{"a": 1}'
    assert body["choices"][0]["finish_reason"] == "length"
    assert json.dumps(body)


def test_judge_response_has_no_errors():
    out = synthetic_response(
        {"task": "judge", "spans": [{"start": "s1", "end": "s2", "text": "x"}]}
    )
    assert out == {"spans": [{"start": "s1", "end": "s2", "errors": []}]}


def test_back_translate_response():
    out = synthetic_response(
        {
            "task": "back_translate",
            "to_lang": "fr",
            "spans": [{"start": "s1", "end": "s1", "text": "Hi"}],
        }
    )
    assert out == {"spans": [{"start": "s1", "end": "s1", "text": "[fr] Hi", "notes": ""}]}


def test_judge_marks_the_mistranslated_marker():
    out = synthetic_response(
        {
            "task": "judge",
            "spans": [
                {"start": "s1", "end": "s1", "text": "[en] Fine."},
                {"start": "s2", "end": "s2", "text": "[en] It is MISTRANSLATED here."},
            ],
        }
    )
    judged = JudgeOutput.model_validate(out)
    assert judged.spans[0].errors == []
    [error] = judged.spans[1].errors
    assert (error.category.value, error.severity.value, error.excerpt) == (
        "accuracy/mistranslation",
        "critical",
        "MISTRANSLATED",
    )
    assert error.explanation == "Synthetic critical error: the text is marked MISTRANSLATED."
