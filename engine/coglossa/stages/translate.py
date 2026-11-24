"""Translate stage (spec §4.1, §4.3, §4.4): every translator renders every chunk, keyed by spans."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from coglossa.config import TranslatorConfig
from coglossa.llm.agent import AGENT_FAILURES
from coglossa.llm.types import OVERSIZE_ERRORS
from coglossa.schema import (
    Block,
    Candidate,
    Chunk,
    DocumentProfile,
    Guide,
    RunError,
    Segment,
    TranslationOutput,
)
from coglossa.segment.markup import ChunkMarkup, chunk_markup
from coglossa.stages.context import StageContext
from coglossa.stages.guide import guide_slice

CONTEXT_CHARS = 800


class _SpanLike(Protocol):
    start: str
    end: str


class _TextSpanLike(_SpanLike, Protocol):
    text: str


def check_coverage(spans: Sequence[_SpanLike], segment_ids: list[str]) -> list[str]:
    """Spans must cover segment_ids exactly once, in order, each a contiguous range."""
    pos = {sid: i for i, sid in enumerate(segment_ids)}
    expected = 0
    for k, span in enumerate(spans):
        if span.start not in pos or span.end not in pos:
            return [f"spans[{k}]: unknown segment id in {span.start}..{span.end}"]
        a, b = pos[span.start], pos[span.end]
        if b < a:
            return [f"spans[{k}]: end {span.end} is before start {span.start}"]
        if expected >= len(segment_ids):
            return [f"spans[{k}]: span {span.start}..{span.end} is after the last segment"]
        if a != expected:
            return [
                f"spans[{k}]: expected a span starting at {segment_ids[expected]}, got {span.start}"
            ]
        expected = b + 1
    if expected < len(segment_ids):
        return [f"segments {segment_ids[expected]}..{segment_ids[-1]} not covered by any span"]
    return []


# A model that types a plain " inside a JSON string under constrained decoding ends the string
# there, mid-sentence, usually right after the space before an opening quotation mark.
CUT_OFF = (
    "the text ends with a space, as if it was cut off; give the whole text, and write "
    'quotation marks as typographic marks (“ ” « »), never as the plain " character'
)


def looks_cut_off(text: str) -> bool:
    """Ends with a space, as a text cut off before a quotation mark does; a line break is fine."""
    return bool(text.strip()) and text[-1].isspace() and text[-1] not in "\n\r"


def check_cut_off(spans: Sequence[_TextSpanLike]) -> list[str]:
    return [f"spans[{k}]: {CUT_OFF}" for k, span in enumerate(spans) if looks_cut_off(span.text)]


def drop_repeated_spans[S: _TextSpanLike](spans: list[S], segment_ids: list[str]) -> list[S]:
    """Spans after full coverage that add nothing (blank, or an earlier span written again) go.

    A model sometimes writes its whole list twice, or a blank span past the last segment
    (CGL-21). If any span after the end says something new, all are kept for check_coverage to
    refuse: it may be a restart of a span that was cut off."""
    for k in range(1, len(spans)):
        if not check_coverage(spans[:k], segment_ids):
            head, tail = spans[:k], spans[k:]
            written = {(s.start, s.end, s.text) for s in head}
            if all(not s.text.strip() or (s.start, s.end, s.text) in written for s in tail):
                return head
            return spans
    return spans


@dataclass(frozen=True)
class ChunkInput:
    chunk: Chunk
    segments: list[Segment]
    context_before: str
    context_after: str
    markup: ChunkMarkup = field(default_factory=ChunkMarkup)  # inline formatting (CGL-108)


def build_chunk_inputs(
    chunks: list[Chunk], segments: list[Segment], blocks: Sequence[Block] = ()
) -> list[ChunkInput]:
    """`blocks` give the segments their inline formatting; without them prompts are plain."""
    by_id = {s.id: s for s in segments}
    by_block = {b.id: b for b in blocks}
    texts = ["\n".join(by_id[i].text for i in c.segment_ids) for c in chunks]
    inputs = []
    for k, chunk in enumerate(chunks):
        own = [by_id[i] for i in chunk.segment_ids]
        inputs.append(
            ChunkInput(
                chunk=chunk,
                segments=own,
                context_before=texts[k - 1][-CONTEXT_CHARS:] if k > 0 else "",
                context_after=texts[k + 1][:CONTEXT_CHARS] if k + 1 < len(texts) else "",
                markup=chunk_markup(own, by_block) if by_block else ChunkMarkup(),
            )
        )
    return inputs


async def translate_with(
    ctx: StageContext,
    profile: DocumentProfile,
    guide: Guide,
    ci: ChunkInput,
    t: TranslatorConfig,
) -> list[Candidate]:
    """One translator renders one chunk (spec §4.3). The candidates' text keeps the markers
    the model wrote (CGL-108): the reviewer reads them, and `unmark_spans` takes them out."""
    segment_ids = [s.id for s in ci.segments]
    sliced = guide_slice(guide, "\n".join(s.text for s in ci.segments))
    literal = t.prompt == "literal"
    payload = {
        "task": "translate",
        "translator": t.name,
        "source_lang": profile.source_lang,
        "target_lang": profile.target_lang,
        "genre": profile.genre.value,
        "register": profile.tone_register,
        "guide": (
            {"glossary": [g.model_dump() for g in sliced.glossary]}
            if literal
            else sliced.model_dump()
        ),
        "context_before": "" if literal else ci.context_before,
        "context_after": "" if literal else ci.context_after,
        "segments": [{"id": s.id, "text": ci.markup.text(s)} for s in ci.segments],
    }
    out = await ctx.runner.run(
        role=f"translator:{t.name}",
        model_key=t.model,
        max_output_tokens=ctx.cfg.output_limit(t),
        prompt_id=t.prompt,
        prompt_vars={
            "source_lang": profile.source_lang,
            "target_lang": profile.target_lang,
            "genre": profile.genre.value,
            "register": profile.tone_register,
        },
        payload=payload,
        output_type=TranslationOutput,
        normalize=lambda o: o.model_copy(
            update={"spans": drop_repeated_spans(o.spans, segment_ids)}
        ),
        validate=lambda o: check_coverage(o.spans, segment_ids) + check_cut_off(o.spans),
    )
    served = ctx.cfg.models[t.model].name or t.model
    return [
        Candidate(
            chunk_id=ci.chunk.id,
            translator=t.name,
            model=served,
            start=sp.start,
            end=sp.end,
            text=sp.text,
            notes=sp.notes,
        )
        for sp in out.spans
    ]


async def translate_chunk(
    ctx: StageContext, profile: DocumentProfile, guide: Guide, ci: ChunkInput
) -> tuple[list[Candidate], list[RunError]]:
    translators = ctx.cfg.roles.translators()

    results = await asyncio.gather(
        *(translate_with(ctx, profile, guide, ci, t) for t in translators),
        return_exceptions=True,
    )
    oversize = next((r for r in results if isinstance(r, OVERSIZE_ERRORS)), None)
    if oversize is not None:
        raise oversize  # the caller splits the chunk (spec §7.2)
    candidates: list[Candidate] = []
    errors: list[RunError] = []
    for t, result in zip(translators, results, strict=True):
        if isinstance(result, list):
            candidates.extend(result)
        elif isinstance(result, AGENT_FAILURES):
            errors.append(
                RunError(
                    stage="translate",
                    chunk_id=ci.chunk.id,
                    role=f"translator:{t.name}",
                    message=str(result),
                )
            )
        else:
            raise result
    return candidates, errors
