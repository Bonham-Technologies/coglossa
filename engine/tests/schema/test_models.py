from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from coglossa.schema import (
    Agreement,
    Block,
    BlockKind,
    ConfigSnapshot,
    DocumentInfo,
    DocumentProfile,
    Flag,
    FlagCode,
    Genre,
    GlossaryEntry,
    Guide,
    PipelineProfile,
    Provenance,
    Reliability,
    Report,
    RunStatus,
    Span,
    SpanScore,
    Usage,
)


def _profile() -> DocumentProfile:
    return DocumentProfile(
        source_lang="fr",
        target_lang="en",
        mixed_language=False,
        genre=Genre.LITERARY_PROSE,
        tone_register="formal, melancholic",
        word_count=12,
        pipeline_profile=PipelineProfile.SHORT,
    )


def test_guide_defaults_are_independent_lists():
    a = Guide(summary="s", style_guide="g")
    b = Guide(summary="s", style_guide="g")
    a.glossary.append(GlossaryEntry(term="x", rendering="y"))
    assert b.glossary == []
    assert a.glossary[0].locked is False


def test_pipeline_profile_values_match_spec():
    assert [p.value for p in PipelineProfile] == ["short", "standard", "long-form"]


def test_flag_codes_match_spec():
    assert {c.value for c in FlagCode} == {
        "low_meaning",
        "style_drift",
        "agent_disagreement",
        "terminology_violation",
        "possible_omission",
        "cultural_or_wordplay",
        "engine_error",
        "low_reliability_language",
        "unscored",
        "number_mismatch",
        "unread_content",
    }


def test_span_score_defaults_unscored():
    score = SpanScore(signals={"qe": None})
    assert score.meaning is None and score.style is None
    assert score.reliability is Reliability.UNSCORED


def test_block_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        Block(id="b1", kind="sidebar", text="x", provenance=Provenance(source_path="a", index=0))


def test_report_round_trips_through_json():
    span = Span(
        id="p1",
        chunk_id="c1",
        start="s1",
        end="s1",
        text="Hello",
        agreement=Agreement.AGREE,
        chosen_from="contextual",
        score=SpanScore(signals={"qe": None}),
        flags=[Flag(code=FlagCode.ENGINE_ERROR, explanation="x")],
    )
    report = Report(
        coglossa_version="0.1.0",
        created_at=datetime(2026, 10, 9, tzinfo=UTC),
        status=RunStatus.COMPLETE,
        document=DocumentInfo(source_path="a.txt", format="txt", sha256="0" * 64),
        profile=_profile(),
        guide=Guide(summary="s", style_guide="g"),
        guide_proposals=[],
        blocks=[
            Block(
                id="b1",
                kind=BlockKind.PARAGRAPH,
                text="Bonjour",
                provenance=Provenance(source_path="a.txt", index=0),
            )
        ],
        segments=[],
        chunks=[],
        candidates=[],
        spans=[span],
        errors=[],
        usage=Usage(calls=0, tokens_in=0, tokens_out=0, by_role={}),
        config=ConfigSnapshot(hash="abc", roles={}, prompt_versions={}),
        llm_calls=[],
    )
    again = Report.model_validate_json(report.model_dump_json())
    assert again == report
    assert again.schema_version == "1"
