import hashlib
from datetime import UTC, datetime

import pytest

from coglossa.schema import (
    Agreement,
    Block,
    BlockKind,
    Chunk,
    ConfigSnapshot,
    DocumentInfo,
    DocumentProfile,
    Flag,
    FlagCode,
    FlagStatus,
    Genre,
    Guide,
    PipelineProfile,
    Provenance,
    Reliability,
    Report,
    RunStatus,
    Segment,
    Span,
    SpanScore,
    Usage,
)


def _block(n: int, kind: BlockKind, text: str) -> Block:
    return Block(
        id=f"b{n}", kind=kind, text=text, provenance=Provenance(source_path="story.txt", index=n)
    )


def _seg(n: int, block: str, text: str) -> Segment:
    return Segment(id=f"s{n}", block_id=block, start=0, end=len(text), text=text)


def make_span(sid, start, end, text, *, meaning=None, style=None, flags=()):
    return Span(
        id=sid,
        chunk_id="c1",
        start=start,
        end=end,
        text=text,
        agreement=Agreement.AGREE,
        chosen_from="contextual",
        score=SpanScore(
            signals={"qe": None},
            meaning=meaning,
            style=style,
            reliability=Reliability.B if meaning is not None else Reliability.UNSCORED,
        ),
        flags=list(flags),
    )


def make_report(target_lang: str = "en", spans: list[Span] | None = None) -> Report:
    blocks = [
        _block(1, BlockKind.HEADING, "Chapitre un"),
        _block(2, BlockKind.PARAGRAPH, "Le chat dort. Il pleut."),
        _block(3, BlockKind.BREAK, "***"),
        _block(4, BlockKind.LIST_ITEM, "Fin <du> & monde."),
    ]
    segments = [
        _seg(1, "b1", "Chapitre un"),
        _seg(2, "b2", "Le chat dort."),
        _seg(3, "b2", "Il pleut."),
        _seg(4, "b4", "Fin <du> & monde."),
    ]
    if spans is None:
        spans = [
            make_span("p1", "s1", "s1", "Chapter One", meaning=92.4, style=88),
            make_span(
                "p2",
                "s2",
                "s3",
                "The cat sleeps.\nIt rains.",
                meaning=55.4,
                style=70,
                flags=[
                    Flag(code=FlagCode.LOW_MEANING, explanation="Meaning score 55 is below 70."),
                    Flag(
                        code=FlagCode.STYLE_DRIFT,
                        explanation="Style drifts.",
                        status=FlagStatus.DISMISSED,
                        dismiss_reason="intended",
                    ),
                ],
            ),
            make_span("p3", "s4", "s4", "The end <of> & world."),
        ]
    return Report(
        coglossa_version="0.1.0",
        run_id="r1",
        created_at=datetime(2026, 10, 11, tzinfo=UTC),
        status=RunStatus.COMPLETE,
        document=DocumentInfo(source_path="/data/story.txt", format="txt", sha256="x"),
        profile=DocumentProfile(
            source_lang="fr",
            target_lang=target_lang,
            mixed_language=False,
            genre=Genre.LITERARY_PROSE,
            tone_register="neutral",
            word_count=12,
            pipeline_profile=PipelineProfile.SHORT,
        ),
        guide=Guide(summary="A cat sleeps while it rains.", style_guide="Plain."),
        blocks=blocks,
        segments=segments,
        chunks=[Chunk(id="c1", segment_ids=["s1", "s2", "s3", "s4"])],
        candidates=[],
        spans=spans,
        notices=[
            Flag(
                code=FlagCode.LOW_RELIABILITY_LANGUAGE,
                explanation="Scores for fr→en are less reliable — review more broadly.",
            )
        ],
        usage=Usage(calls=0, tokens_in=0, tokens_out=0, by_role={}),
        config=ConfigSnapshot(hash="h", roles={}, prompt_versions={}),
    )


@pytest.fixture
def report() -> Report:
    return make_report()


def translated_report(
    blocks: list[Block],
    original: bytes,
    *,
    fmt: str,
    target_lang: str = "en",
    name: str = "source",
) -> Report:
    """A report of `original` with one passage per block, translated as "[en] <source>"
    (CGL-110)."""
    segments = [
        Segment(id=f"s{n}", block_id=b.id, start=0, end=len(b.text), text=b.text)
        for n, b in enumerate(blocks, start=1)
    ]
    spans = [
        make_span(f"p{n}", seg.id, seg.id, f"[en] {seg.text}")
        for n, seg in enumerate(segments, start=1)
    ]
    document = DocumentInfo(
        source_path=f"{name}.{fmt}", format=fmt, sha256=hashlib.sha256(original).hexdigest()
    )
    return make_report(target_lang=target_lang).model_copy(
        update={
            "document": document,
            "blocks": blocks,
            "segments": segments,
            "spans": spans,
            "chunks": [Chunk(id="c1", segment_ids=[s.id for s in segments])],
        }
    )
