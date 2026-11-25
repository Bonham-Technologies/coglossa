"""run_document: ingest → segment → profile → guide → [checkpoint] → per-chunk
(translate → reconcile → score) in parallel → report (spec §4). Scores come from the
scorer service, the judge and the roundtrip stages (spec §6)."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import coglossa
from coglossa.config import CoglossaConfig, ConfigError, config_hash
from coglossa.ingest.registry import ingest_file
from coglossa.llm.agent import AgentRunner, CallLog
from coglossa.llm.types import Backend
from coglossa.prompts.check import run_prompt_ids, validate_prompts
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import (
    Candidate,
    Chunk,
    ConfigSnapshot,
    DocumentInfo,
    DocumentProfile,
    GlossaryEntry,
    Guide,
    PipelineProfile,
    Reliability,
    Report,
    Revision,
    RunError,
    RunStatus,
    ScorerHealth,
    Segment,
    SourceDocument,
    Span,
)
from coglossa.scoring.aggregate import any_scored, document_notices, model_diversity
from coglossa.scoring.client import Scorer
from coglossa.scoring.tiers import LanguageTiers
from coglossa.segment.chunking import chunk_segments, compute_budget
from coglossa.segment.keep import translatable
from coglossa.segment.segmenter import segment_document
from coglossa.stages.chunk import ChunkOutcome, process_chunk
from coglossa.stages.context import StageContext
from coglossa.stages.guide import guide_was_built, run_guide
from coglossa.stages.profile import run_profile
from coglossa.stages.sweep import resolve_proposals, run_sweep
from coglossa.stages.translate import ChunkInput, build_chunk_inputs


@dataclass
class RunOptions:
    target_lang: str
    source_lang: str | None = None
    pipeline_profile: PipelineProfile | None = None
    guide: Guide | None = None
    guide_approved: bool = False  # use `guide` verbatim; skip generation and checkpoint
    checkpoint: bool | None = None  # None = per-profile default from config
    profile: DocumentProfile | None = (
        None  # use as-is (e.g. a reviewed profile.json); skip profiling
    )


@dataclass
class _Collected:
    spans: list[Span]
    candidates: list[Candidate]
    proposals: list[GlossaryEntry]
    errors: list[RunError]
    engine_error: bool


@dataclass
class GuideCheckpoint:
    profile: DocumentProfile
    guide: Guide
    errors: list[RunError]


async def run_document(
    path: Path,
    cfg: CoglossaConfig,
    backends: dict[str, Backend],
    opts: RunOptions,
    *,
    prompts: PromptLibrary | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    progress: Callable[[str], None] | None = None,
    scorer: Scorer | None = None,
    scorer_health: ScorerHealth | None = None,  # recorded in the report's config snapshot
) -> Report | GuideCheckpoint:
    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    budget = compute_budget(cfg)
    prompts = prompts or PromptLibrary(cfg.prompts_dir)
    validate_prompts(cfg, prompts)
    tiers = LanguageTiers.load(cfg.scoring.language_tiers_file)
    doc = ingest_file(path)
    segments = segment_document(doc)
    chunks = chunk_segments(doc, segments, budget)
    # the profile and the guide never see text left as written (CGL-111)
    readable, readable_chunks = translatable(segments, chunks, doc.blocks)
    log = CallLog(store_io=cfg.audit.store_llm_io)
    runner = AgentRunner(cfg, backends, prompts, log, sleep=sleep)
    ctx = StageContext(cfg=cfg, runner=runner, target_lang=opts.target_lang)

    if opts.profile is not None:
        if opts.profile.target_lang != opts.target_lang:
            raise ConfigError(
                f"profile target language '{opts.profile.target_lang}' does not match "
                f"--target-lang '{opts.target_lang}'"
            )
        profile = opts.profile
        say("profile: using supplied profile")
    else:
        profile = await run_profile(
            ctx, readable, source_lang=opts.source_lang, pipeline_profile=opts.pipeline_profile
        )
        say(
            f"profile: {profile.source_lang} -> {profile.target_lang}, {profile.genre.value}, "
            f"{profile.word_count} words, {profile.pipeline_profile.value}"
        )
    tier = tiers.tier(profile.source_lang, profile.target_lang)
    diverse = model_diversity(cfg)
    errors: list[RunError] = []
    if opts.guide is not None and opts.guide_approved:
        guide = opts.guide
        say("guide: using approved guide")
    else:
        guide, guide_errors = await run_guide(
            ctx, profile, readable, readable_chunks, prior=opts.guide
        )
        errors.extend(guide_errors)
        checkpoint = guide_was_built(guide_errors) and (
            opts.checkpoint
            if opts.checkpoint is not None
            else cfg.profiles.checkpoint.get(profile.pipeline_profile, False)
        )
        if checkpoint:
            say("guide: ready for review")
            return GuideCheckpoint(profile=profile, guide=guide, errors=errors)
        say("guide: ready")

    done = 0

    async def process(ci: ChunkInput) -> ChunkOutcome:
        nonlocal done
        outcome = await process_chunk(ctx, scorer, profile, guide, ci, tier=tier, diverse=diverse)
        done += 1
        say(f"chunk {done}/{len(chunks)} done")
        return outcome

    outcomes = await asyncio.gather(
        *(process(ci) for ci in build_chunk_inputs(chunks, segments, doc.blocks))
    )
    collected = _collect(list(outcomes))
    errors.extend(collected.errors)
    spans = collected.spans
    revisions: list[Revision] = []
    if cfg.profiles.sweep.get(profile.pipeline_profile, True):
        swept = await run_sweep(ctx, profile, guide, spans, segments)
        spans, revisions = swept.spans, swept.revisions
        errors.extend(swept.errors)
        say(f"sweep: {len(swept.changes)} passage(s) revised")
    else:
        say(f"sweep: off for the {profile.pipeline_profile.value} profile")
    report = _assemble(
        doc,
        segments,
        chunks,
        profile,
        guide,
        tier,
        spans,
        collected.candidates,
        resolve_proposals(guide, collected.proposals),
        revisions,
        collected.engine_error,
        errors,
        cfg,
        prompts,
        log,
        now(),
        scorer_health,
    )
    flagged = sum(1 for s in report.spans if s.flags)
    say("report: assembled")
    if any_scored(report.spans):
        say(f"scoring: tier {tier.value}, {flagged} of {len(report.spans)} spans flagged")
    else:
        say("scoring: unscored (no scorer or judge produced scores)")
    return report


def _collect(outcomes: list[ChunkOutcome]) -> _Collected:
    collected = _Collected([], [], [], [], False)
    for outcome in outcomes:
        collected.candidates.extend(outcome.candidates)
        collected.errors.extend(outcome.errors)
        collected.engine_error = collected.engine_error or outcome.engine_error
        for span, terms in zip(outcome.spans, outcome.proposed_terms, strict=True):
            collected.spans.append(span.model_copy(update={"id": f"p{len(collected.spans) + 1}"}))
            collected.proposals.extend(terms)
    return collected


def _assemble(
    doc: SourceDocument,
    segments: list[Segment],
    chunks: list[Chunk],
    profile: DocumentProfile,
    guide: Guide,
    tier: Reliability,
    spans: list[Span],
    candidates: list[Candidate],
    proposals: list[GlossaryEntry],
    revisions: list[Revision],
    engine_error: bool,
    errors: list[RunError],
    cfg: CoglossaConfig,
    prompts: PromptLibrary,
    log: CallLog,
    created_at: datetime,
    scorer_health: ScorerHealth | None,
) -> Report:
    prompt_ids = run_prompt_ids(cfg)
    return Report(
        coglossa_version=coglossa.__version__,
        created_at=created_at,
        status=(RunStatus.COMPLETE_WITH_ERRORS if errors or engine_error else RunStatus.COMPLETE),
        document=DocumentInfo(source_path=doc.source_path, format=doc.format, sha256=doc.sha256),
        profile=profile,
        guide=guide,
        guide_proposals=proposals,
        blocks=doc.blocks,
        segments=segments,
        chunks=chunks,
        candidates=candidates,
        spans=spans,
        revisions=revisions,
        errors=errors,
        usage=log.usage(),
        config=ConfigSnapshot(
            hash=config_hash(cfg),
            roles=dict(cfg.roles.all_roles()),
            prompt_versions=prompts.versions(prompt_ids),
            scorer=scorer_health,
        ),
        llm_calls=log.records,
        language_tier=tier,
        notices=document_notices(
            tier,
            profile.source_lang,
            profile.target_lang,
            scored=any_scored(spans),
            unread=doc.notices,
        ),
    )
