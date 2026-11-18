"""The two kinds of system a bench run translates with: the full pipeline, and a single-model
one-pass baseline (one model, one call per chunk, no guide, no reconciliation, no scoring)."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from coglossa.bench.config import Pair
from coglossa.bench.units import (
    Unit,
    fill_missing,
    group_units,
    joiner,
    write_source_document,
)
from coglossa.config import CoglossaConfig, ConfigError, TranslatorConfig
from coglossa.ingest.registry import ingest_file
from coglossa.llm.agent import AGENT_FAILURES, AgentRunner, CallLog
from coglossa.llm.types import OVERSIZE_ERRORS, Backend
from coglossa.pipeline import GuideCheckpoint, RunOptions, run_document
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import Candidate, DocumentProfile, Genre, Guide, PipelineProfile
from coglossa.scoring.client import Scorer
from coglossa.segment.chunking import chunk_segments, compute_budget
from coglossa.segment.segmenter import segment_document
from coglossa.stages.context import StageContext
from coglossa.stages.translate import build_chunk_inputs, translate_with


@dataclass(frozen=True)
class SystemOutput:
    units: list[Unit]
    tokens_in: int
    tokens_out: int
    calls: int
    errors: list[str]


async def run_pipeline_system(
    cfg: CoglossaConfig,
    pair: Pair,
    sentences: list[str],
    workdir: Path,
    *,
    backends: dict[str, Backend],
    scorer: Scorer | None,
    prompts: PromptLibrary | None = None,
) -> SystemOutput:
    path = write_source_document(sentences, workdir)
    opts = RunOptions(target_lang=pair.tgt, source_lang=pair.src, checkpoint=False)
    report = await run_document(path, cfg, backends, opts, prompts=prompts, scorer=scorer)
    assert not isinstance(report, GuideCheckpoint)  # checkpoint=False never stops at the guide
    units = group_units(
        [(s.start, s.end, s.text) for s in report.spans],
        report.segments,
        report.blocks,
        len(sentences),
        joiner(pair.tgt),
    )
    return SystemOutput(
        units=fill_missing(units, len(sentences)),
        tokens_in=report.usage.tokens_in,
        tokens_out=report.usage.tokens_out,
        calls=report.usage.calls,
        errors=[f"{e.stage}: {e.message}" for e in report.errors],
    )


async def run_baseline_system(
    cfg: CoglossaConfig,
    model_key: str,
    pair: Pair,
    sentences: list[str],
    workdir: Path,
    *,
    backends: dict[str, Backend],
    prompts: PromptLibrary | None = None,
) -> SystemOutput:
    if model_key not in cfg.models:
        raise ConfigError(f"model '{model_key}' is not defined in this config")
    path = write_source_document(sentences, workdir)
    doc = ingest_file(path)
    segments = segment_document(doc)
    chunks = chunk_segments(doc, segments, compute_budget(cfg))
    profile = DocumentProfile(
        source_lang=pair.src,
        target_lang=pair.tgt,
        mixed_language=False,
        genre=Genre.OTHER,
        tone_register="neutral",
        word_count=sum(len(s.text.split()) for s in segments),
        pipeline_profile=PipelineProfile.SHORT,
    )
    guide = Guide(summary="", style_guide="")
    log = CallLog(store_io=cfg.audit.store_llm_io)
    runner = AgentRunner(cfg, backends, prompts or PromptLibrary(cfg.prompts_dir), log)
    ctx = StageContext(cfg=cfg, runner=runner, target_lang=pair.tgt)
    translator = TranslatorConfig(name="baseline", prompt="contextual", model=model_key)
    inputs = build_chunk_inputs(chunks, segments)
    # chunks in parallel, as the pipeline translates them, so wall times compare
    results = await asyncio.gather(
        *(translate_with(ctx, profile, guide, ci, translator) for ci in inputs),
        return_exceptions=True,
    )
    candidates: list[Candidate] = []
    errors: list[str] = []
    for ci, result in zip(inputs, results, strict=True):
        if isinstance(result, (*AGENT_FAILURES, *OVERSIZE_ERRORS)):
            errors.append(f"translate: {ci.chunk.id}: {result}")
        elif isinstance(result, BaseException):
            raise result
        else:
            candidates.extend(result)
    units = group_units(
        [(c.start, c.end, c.text) for c in candidates],
        segments,
        doc.blocks,
        len(sentences),
        joiner(pair.tgt),
    )
    usage = log.usage()
    return SystemOutput(
        units=fill_missing(units, len(sentences)),
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        calls=usage.calls,
        errors=errors,
    )
