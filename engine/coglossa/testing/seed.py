"""A run with known passages, flags, candidates and a guide, written straight to the
database. Tests of the catalog, the review actions and the API use it; the product never
does. The values are documented in the M4a plan (Task 8)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from coglossa.config import CoglossaConfig, config_hash
from coglossa.schema import (
    TERMINAL_STATES,
    Agreement,
    Block,
    BlockKind,
    Candidate,
    Chunk,
    DocumentProfile,
    ErrorCategory,
    ErrorSeverity,
    Flag,
    FlagCode,
    Genre,
    GlossaryEntry,
    Guide,
    JudgeError,
    PipelineProfile,
    Provenance,
    Reliability,
    RunState,
    Segment,
    Span,
    SpanScore,
)
from coglossa.stages.chunk import ChunkOutcome
from coglossa.storage import Storage
from coglossa.store import outputs, runs
from coglossa.store.db import Database
from coglossa.store.models import FlagRow, SpanRow

SEED_CONFIG: dict = {
    "backends": {"fake": {"base_url": "http://127.0.0.1:9/v1"}},
    "models": {
        "model-a": {"backend": "fake", "context_window": 32768},
        "model-b": {"backend": "fake", "context_window": 32768},
    },
    "roles": {
        "literal_translator": {"model": "model-a"},
        "contextual_translator": {"model": "model-b"},
        "reviewer": {"model": "model-a"},
        "profiler_guide": {"model": "model-b"},
    },
}
SOURCE_TEXT = "Chapitre un\n\nLe chat dort. Il pleut.\n\nFin."
GUIDE = Guide(
    summary="A cat sleeps while it rains.",
    style_guide="Plain, modern English.",
    glossary=[GlossaryEntry(term="chat", rendering="cat")],
)


def _block(n: int, kind: BlockKind, text: str, filename: str) -> Block:
    return Block(
        id=f"b{n}", kind=kind, text=text, provenance=Provenance(source_path=filename, index=n - 1)
    )


def _source(filename: str) -> tuple[list[Block], list[Segment]]:
    blocks = [
        _block(1, BlockKind.HEADING, "Chapitre un", filename),
        _block(2, BlockKind.PARAGRAPH, "Le chat dort. Il pleut.", filename),
        _block(3, BlockKind.PARAGRAPH, "Fin.", filename),
    ]
    segments = [
        Segment(id="s1", block_id="b1", start=0, end=11, text="Chapitre un"),
        Segment(id="s2", block_id="b2", start=0, end=13, text="Le chat dort."),
        Segment(id="s3", block_id="b2", start=14, end=23, text="Il pleut."),
        Segment(id="s4", block_id="b3", start=0, end=4, text="Fin."),
    ]
    return blocks, segments


def _candidates() -> list[Candidate]:
    rows = [
        ("literal", "model-a", "s1", "s1", "Chapter one"),
        ("literal", "model-a", "s2", "s2", "The cat sleeps."),
        ("literal", "model-a", "s3", "s3", "It is raining."),
        ("literal", "model-a", "s4", "s4", "End."),
        ("contextual", "model-b", "s1", "s1", "Chapter One"),
        ("contextual", "model-b", "s2", "s3", "The cat is asleep. It rains."),
        ("contextual", "model-b", "s4", "s4", "The End."),
    ]
    return [
        Candidate(chunk_id="c1", translator=t, model=m, start=a, end=b, text=x)
        for t, m, a, b, x in rows
    ]


def _spans() -> list[Span]:
    return [
        Span(
            id="c1.1",
            chunk_id="c1",
            start="s1",
            end="s1",
            text="Chapter One",
            agreement=Agreement.AGREE,
            chosen_from="contextual",
            score=SpanScore(
                signals={"qe": 0.95, "xsim": 0.9},
                meaning=92.0,
                style=88.0,
                reliability=Reliability.B,
            ),
            judge_errors=[],
            back_translation="Chapitre un",
        ),
        Span(
            id="c1.2",
            chunk_id="c1",
            start="s2",
            end="s3",
            text="The cat sleeps. It rains.",
            agreement=Agreement.MAJOR,
            chosen_from="merged",
            rationale="Merged the literal tense with the contextual rhythm.",
            issues=["tense"],
            score=SpanScore(
                signals={"qe": 0.5, "xsim": 0.7},
                meaning=55.0,
                style=70.0,
                reliability=Reliability.B,
            ),
            flags=[
                Flag(code=FlagCode.LOW_MEANING, explanation="Meaning score 55 is below 72."),
                Flag(
                    code=FlagCode.AGENT_DISAGREEMENT,
                    explanation="The translators disagree on this passage.",
                ),
            ],
            judge_errors=[
                JudgeError(
                    category=ErrorCategory.MISTRANSLATION,
                    severity=ErrorSeverity.MAJOR,
                    excerpt="sleeps",
                    explanation="The tense differs from the source.",
                )
            ],
            back_translation="Le chat dort. Il pleut.",
        ),
        Span(
            id="c1.3",
            chunk_id="c1",
            start="s4",
            end="s4",
            text="End.",
            agreement=Agreement.AGREE,
            chosen_from="literal",
            score=SpanScore(signals={"qe": None, "xsim": None}, reliability=Reliability.UNSCORED),
            flags=[
                Flag(
                    code=FlagCode.TERMINOLOGY_VIOLATION,
                    explanation="'Fin' should be rendered 'The End'.",
                    evidence={"term": "Fin", "expected": "The End"},
                )
            ],
        ),
    ]


@dataclass(frozen=True)
class SeededRun:
    project_id: uuid.UUID
    document_id: uuid.UUID
    run_id: uuid.UUID
    span_ids: list[uuid.UUID]  # reading order: p1, p2, p3
    flag_ids: dict[str, int]  # "p2:low_meaning" -> flag id
    guide_version_id: uuid.UUID


async def seed_run(
    db: Database,
    *,
    project: str = "demo",
    state: RunState = RunState.COMPLETE,
    target_lang: str = "en",
    filename: str = "story.txt",
    storage: Storage | None = None,
) -> SeededRun:
    cfg = CoglossaConfig.model_validate(SEED_CONFIG)
    profile = DocumentProfile(
        source_lang="fr",
        target_lang=target_lang,
        mixed_language=False,
        genre=Genre.LITERARY_PROSE,
        tone_register="neutral",
        word_count=8,
        pipeline_profile=PipelineProfile.STANDARD,
    )
    blocks, segments = _source(filename)
    now = datetime.now(UTC)
    finished = state in TERMINAL_STATES
    async with db.session() as s, s.begin():
        row = await runs.get_or_create_project(s, project)
        if row.target_lang is None:
            row.target_lang = target_lang
        document_id = uuid.uuid4()
        key = f"projects/{row.id}/documents/{document_id}/{filename}"
        if storage is not None:
            storage.put(key, SOURCE_TEXT.encode("utf-8"))
        doc = await runs.add_document(
            s,
            row.id,
            document_id=document_id,
            filename=filename,
            format=filename.rsplit(".", 1)[-1],
            sha256="0" * 64,
            storage_key=key,
        )
        await runs.save_source(s, doc.id, blocks, segments)
        run = await runs.create_run(
            s,
            doc.id,
            target_lang=target_lang,
            config_hash=config_hash(cfg),
            config_snapshot=cfg.model_dump(mode="json"),
            prompt_versions={},
        )
        await runs.save_chunks(s, run.id, [Chunk(id="c1", segment_ids=["s1", "s2", "s3", "s4"])])
        version = await runs.add_guide_version(
            s,
            row.id,
            GUIDE,
            target_lang=target_lang,
            status=runs.DRAFT,
            author=runs.ENGINE,
            source_run_id=run.id,
        )
        for stage in ("ingest", "profile", "guide"):
            runs.finish_stage(await runs.begin_stage(s, run.id, stage), {})
        outcome = ChunkOutcome(
            chunk_id="c1",
            candidates=_candidates(),
            spans=_spans(),
            proposed_terms=[[], [GlossaryEntry(term="pluie", rendering="rain")], []],
        )
        await outputs.save_chunk_outcome(s, run.id, 0, outcome)
        for stage in ("translate", "reconcile", "score"):
            runs.finish_stage(await runs.begin_stage(s, run.id, stage, "c1"), {"errors": []})
        if finished:
            runs.finish_stage(await runs.begin_stage(s, run.id, "sweep"), {"errors": []})
        run.state = state.value
        run.profile = profile.model_dump(mode="json")
        run.language_tier = Reliability.B.value
        run.guide_version_id = version.id
        run.started_at = now
        run.finished_at = now if finished else None
        span_ids = list(
            await s.scalars(
                select(SpanRow.id).where(SpanRow.run_id == run.id).order_by(SpanRow.ordinal)
            )
        )
        flags = await s.execute(
            select(SpanRow.ordinal, FlagRow.code, FlagRow.id)
            .join(FlagRow, FlagRow.span_id == SpanRow.id)
            .where(SpanRow.run_id == run.id)
        )
        flag_ids = {f"p{ordinal + 1}:{code}": flag_id for ordinal, code, flag_id in flags}
        return SeededRun(
            project_id=row.id,
            document_id=doc.id,
            run_id=run.id,
            span_ids=span_ids,
            flag_ids=flag_ids,
            guide_version_id=version.id,
        )
