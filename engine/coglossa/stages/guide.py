"""Guide stage (spec §4.1): build the translation guide, single pass or map-reduce."""

import asyncio
import json
from typing import Any

from coglossa.llm.agent import AGENT_FAILURES, UNUSABLE_OUTPUT, StructuredOutputError
from coglossa.llm.types import OutputTruncatedError
from coglossa.schema import Chunk, DocumentProfile, Guide, PipelineProfile, RunError, Segment
from coglossa.segment.text_metrics import estimate_tokens
from coglossa.stages.context import StageContext, StageError
from coglossa.text import entry_key


async def run_guide(
    ctx: StageContext,
    profile: DocumentProfile,
    segments: list[Segment],
    chunks: list[Chunk],
    *,
    prior: Guide | None,
) -> tuple[Guide, list[RunError]]:
    model_key = ctx.cfg.roles.profiler_guide.model
    model = ctx.cfg.models[model_key]
    budget = model.context_window - ctx.cfg.chunking.reserve_tokens
    text = "\n".join(s.text for s in segments)
    errors: list[RunError] = []

    def note(message: str, chunk_id: str | None = None) -> None:
        errors.append(RunError(stage="guide", chunk_id=chunk_id, role="guide", message=message))

    # The guide helps translation but is not needed for it: when the model's answer is unusable
    # (CGL-14: a model that loops cuts off even the light guide), the run goes on without one.
    def without_guide(reason: Exception) -> Guide:
        earlier = prior is not None and has_content(prior)
        instead = WITH_EARLIER if earlier else "translating without one"
        note(f"{NOT_BUILT} ({reason}); {instead}")
        return prior if prior is not None and earlier else Guide(summary="", style_guide="")

    single_pass = (
        profile.pipeline_profile is not PipelineProfile.LONG_FORM
        and estimate_tokens(text, model.bytes_per_token) * 2 <= budget
    )
    try:
        if single_pass:
            depth = "light" if profile.pipeline_profile is PipelineProfile.SHORT else "full"
            try:
                guide = await _guide_call(ctx, profile, text, depth, prior)
            except (StructuredOutputError, OutputTruncatedError) as e:
                if depth == "light":
                    raise
                # A text with many names can outgrow the model's output limit; a light guide
                # still carries the terms that matter, where failing would stop the whole run.
                note(f"the full guide could not be built ({e}); {LIGHT_INSTEAD}")
                guide = await _guide_call(ctx, profile, text, "light", prior)
        else:
            if not chunks:
                raise StageError("guide failed: document has no excerpts")
            by_id = {s.id: s for s in segments}
            texts = ["\n".join(by_id[i].text for i in c.segment_ids) for c in chunks]
            results = await asyncio.gather(
                *(_guide_call(ctx, profile, t, "partial", prior) for t in texts),
                return_exceptions=True,
            )
            partials: list[Guide] = []
            failures: list[Exception] = []
            for chunk, result in zip(chunks, results, strict=True):
                if isinstance(result, Guide):
                    partials.append(result)
                elif isinstance(result, AGENT_FAILURES):
                    failures.append(result)
                    note(str(result), chunk.id)
                else:
                    raise result
            if not partials and not all(isinstance(f, UNUSABLE_OUTPUT) for f in failures):
                raise StageError(f"guide failed: every excerpt failed ({errors[0].message})")
            if not partials:
                guide = without_guide(failures[0])
            else:
                try:
                    guide = await _merge(
                        ctx, profile, partials, prior, budget, model.bytes_per_token
                    )
                except UNUSABLE_OUTPUT as e:
                    note(f"{NOT_MERGED} ({e}); their entries were combined as they are")
                    guide = combine_partials(partials)
    except UNUSABLE_OUTPUT as e:
        guide = without_guide(e)
    except AGENT_FAILURES as e:
        raise StageError(f"guide failed: {e}") from e
    return apply_locked(prior, _unlocked(guide)), errors


# The notes' fixed words: coglossa.problems tells the app which kind of guide a run got by them.
NOT_BUILT = "the translation guide could not be built"
WITH_EARLIER = "translating with the earlier guide"
LIGHT_INSTEAD = "a light guide was built instead"
NOT_MERGED = "the partial guides could not be merged"


def guide_was_built(errors: list[RunError]) -> bool:
    """False when run_guide went on without a guide: there is nothing to stop and review."""
    return not any(e.stage == "guide" and e.message.startswith(NOT_BUILT) for e in errors)


def has_content(guide: Guide) -> bool:
    return any(
        (
            guide.summary.strip(),
            guide.style_guide.strip(),
            guide.glossary,
            guide.entities,
            guide.cultural_references,
        )
    )


def combine_partials(partials: list[Guide]) -> Guide:
    """The partial guides side by side, when the model could not merge them: the first entry for
    each term and name wins, as a merge would keep one rendering for each."""
    terms: set[str] = set()
    names: set[str] = set()
    references: list[str] = []
    glossary, entities = [], []
    for p in partials:
        for g in p.glossary:
            if entry_key(g.term) not in terms:
                terms.add(entry_key(g.term))
                glossary.append(g)
        for e in p.entities:
            if entry_key(e.name) not in names:
                names.add(entry_key(e.name))
                entities.append(e)
        references += [r for r in p.cultural_references if r not in references]
    return Guide(
        summary=" ".join(p.summary for p in partials if p.summary.strip()),
        style_guide=partials[0].style_guide,
        glossary=glossary,
        entities=entities,
        cultural_references=references,
    )


async def _guide_call(
    ctx: StageContext, profile: DocumentProfile, text: str, depth: str, prior: Guide | None
) -> Guide:
    return await ctx.runner.run(
        role="guide",
        model_key=ctx.cfg.roles.profiler_guide.model,
        max_output_tokens=ctx.cfg.output_limit(ctx.cfg.roles.profiler_guide),
        prompt_id="guide",
        prompt_vars={
            "source_lang": profile.source_lang,
            "target_lang": profile.target_lang,
            "depth": depth,
        },
        payload={
            "task": "guide",
            "depth": depth,
            "source_lang": profile.source_lang,
            "target_lang": profile.target_lang,
            "genre": profile.genre.value,
            "register": profile.tone_register,
            "text": text,
            "prior_guide": _dump(prior),
        },
        output_type=Guide,
    )


async def _merge(
    ctx: StageContext,
    profile: DocumentProfile,
    partials: list[Guide],
    prior: Guide | None,
    budget: int,
    bytes_per_token: float,
) -> Guide:
    if len(partials) == 1:
        return partials[0]
    size = estimate_tokens(
        json.dumps([p.prompt_dump() for p in partials], ensure_ascii=False), bytes_per_token
    )
    if size * 2 > budget and len(partials) > 2:
        mid = len(partials) // 2
        left, right = await asyncio.gather(
            _merge(ctx, profile, partials[:mid], prior, budget, bytes_per_token),
            _merge(ctx, profile, partials[mid:], prior, budget, bytes_per_token),
        )
        partials = [left, right]
    return await ctx.runner.run(
        role="guide",
        model_key=ctx.cfg.roles.profiler_guide.model,
        max_output_tokens=ctx.cfg.output_limit(ctx.cfg.roles.profiler_guide),
        prompt_id="guide_merge",
        prompt_vars={"source_lang": profile.source_lang, "target_lang": profile.target_lang},
        payload={
            "task": "guide_merge",
            "source_lang": profile.source_lang,
            "target_lang": profile.target_lang,
            "partials": [p.prompt_dump() for p in partials],
            "prior_guide": _dump(prior),
        },
        output_type=Guide,
    )


def _dump(guide: Guide | None) -> dict[str, Any] | None:
    return guide.prompt_dump() if guide is not None else None


# Only people lock entries and list their forms (CGL-109): a model's guide has neither.
_ENGINE_ENTRY = {"locked": False, "forms": [], "source_forms": []}


def _unlocked(guide: Guide) -> Guide:
    glossary = [g.model_copy(update=_ENGINE_ENTRY) for g in guide.glossary]
    entities = [e.model_copy(update=_ENGINE_ENTRY) for e in guide.entities]
    return guide.model_copy(update={"glossary": glossary, "entities": entities})


def apply_locked(prior: Guide | None, guide: Guide) -> Guide:
    """Human-locked entries (glossary terms and entities) from the prior guide are kept
    verbatim and override engine entries with the same term or name."""
    if prior is None:
        return guide
    locked_terms = [g for g in prior.glossary if g.locked]
    term_keys = {entry_key(g.term) for g in locked_terms}
    locked_names = [e for e in prior.entities if e.locked]
    name_keys = {entry_key(e.name) for e in locked_names}
    return guide.model_copy(
        update={
            "glossary": [
                *locked_terms,
                *(g for g in guide.glossary if entry_key(g.term) not in term_keys),
            ],
            "entities": [
                *locked_names,
                *(e for e in guide.entities if entry_key(e.name) not in name_keys),
            ],
        }
    )


def carry_over(base: Guide | None, guide: Guide) -> Guide:
    """A run's draft merged onto the set guide (CGL-23). Every term and name the set guide has
    stays exactly as it is, so the files of one set agree on them; the draft adds only the terms
    and names the set guide lacks, and never a locked one: only people lock entries and the set
    guide keeps every lock, so a locked entry it lacks is one a person removed. The summary and
    style guide stay the draft's (they describe its own document) unless the draft has none, as
    when its guide could not be built.

    Keys are matched trimmed and casefolded (text.entry_key, as the guide editor matches them)
    and across both lists (CGL-28): a draft term the set guide has as a name, or a draft name
    it has as a term, is dropped like any other entry the set guide already has, so one file
    filing "Assembly" as a term and another filing it as a name leaves a single rendering, the
    set guide's."""
    if base is None:
        return guide
    known = {entry_key(g.term) for g in base.glossary} | {entry_key(e.name) for e in base.entities}
    return guide.model_copy(
        update={
            "summary": guide.summary if guide.summary.strip() else base.summary,
            "style_guide": guide.style_guide if guide.style_guide.strip() else base.style_guide,
            "glossary": [
                *base.glossary,
                *(g for g in guide.glossary if not g.locked and entry_key(g.term) not in known),
            ],
            "entities": [
                *base.entities,
                *(e for e in guide.entities if not e.locked and entry_key(e.name) not in known),
            ],
            "cultural_references": [
                *base.cultural_references,
                *(r for r in guide.cultural_references if r not in base.cultural_references),
            ],
        }
    )


def guide_slice(guide: Guide, text: str) -> Guide:
    """The part of the guide relevant to `text`, keeping prompts small for long books: the
    entries whose term or name, or one of its source forms (CGL-109), is in it."""
    folded = text.casefold()

    def found(words: list[str]) -> bool:
        return any(w.casefold() in folded for w in words)

    return guide.model_copy(
        update={
            "glossary": [g for g in guide.glossary if found(g.source_words)],
            "entities": [e for e in guide.entities if found(e.source_words)],
        }
    )
