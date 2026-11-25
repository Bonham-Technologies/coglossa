"""Text left as written (CGL-111). Ingest marks a block whose text is all code, or all marked not
to translate, as kept (`Provenance.keep`): it is one segment, no model ever sees it, and its
passage is its own text. Code or no-translate text inside a translated block is recorded as the
block's literals, which the literals check expects back in the translation as written."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from coglossa.schema import Block, Chunk, InlineMark, KeepReason, Segment
from coglossa.segment.markup import clip_marks


@dataclass(frozen=True)
class ChunkKeep:
    """What a chunk's segments keep as written; empty when nothing is."""

    kept: dict[str, KeepReason] = field(default_factory=dict)  # segment id -> why
    marks: dict[str, list[InlineMark]] = field(default_factory=dict)  # a kept segment's formatting
    literals: dict[str, tuple[str, ...]] = field(default_factory=dict)  # segment id -> literals

    def literals_of(self, segment_ids: Sequence[str]) -> tuple[str, ...]:
        return tuple(t for sid in segment_ids for t in self.literals.get(sid, ()))


def chunk_keep(segments: Sequence[Segment], blocks: Mapping[str, Block]) -> ChunkKeep:
    kept: dict[str, KeepReason] = {}
    marks: dict[str, list[InlineMark]] = {}
    literals: dict[str, list[str]] = {}
    by_block: dict[str, list[Segment]] = {}
    for segment in segments:
        by_block.setdefault(segment.block_id, []).append(segment)
    for block_id, own in by_block.items():
        block = blocks.get(block_id)
        if block is None:
            continue
        if block.provenance.keep is not None:
            for segment in own:
                kept[segment.id] = block.provenance.keep
                found = clip_marks(block.provenance.marks, segment.start, segment.end)
                if found:
                    marks[segment.id] = found
            continue
        cursor = 0
        for text in block.provenance.literals:
            pos = block.text.find(text, cursor)
            if pos < 0:
                continue
            cursor = pos + len(text)
            for segment in own:
                if segment.start <= pos and cursor <= segment.end:
                    literals.setdefault(segment.id, []).append(text)
    return ChunkKeep(kept=kept, marks=marks, literals={k: tuple(v) for k, v in literals.items()})


def kept_segment_ids(segments: Sequence[Segment], blocks: Sequence[Block]) -> set[str]:
    keep = {b.id for b in blocks if b.provenance.keep is not None}
    return {s.id for s in segments if s.block_id in keep}


def translatable(
    segments: list[Segment], chunks: list[Chunk], blocks: Sequence[Block]
) -> tuple[list[Segment], list[Chunk]]:
    """The segments and chunks without the text left as written, for the stages that read the
    document as a whole (the profile and the guide): no model sees kept text."""
    kept = kept_segment_ids(segments, blocks)
    if not kept:
        return segments, chunks
    own = [
        c.model_copy(update={"segment_ids": [i for i in c.segment_ids if i not in kept]})
        for c in chunks
    ]
    return [s for s in segments if s.id not in kept], [c for c in own if c.segment_ids]
