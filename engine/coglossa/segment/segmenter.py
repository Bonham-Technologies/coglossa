from coglossa.schema import BlockKind, Segment, SourceDocument
from coglossa.segment.sentences import split_sentences


def segment_document(doc: SourceDocument) -> list[Segment]:
    segments: list[Segment] = []
    for block in doc.blocks:
        if block.kind is BlockKind.BREAK:
            continue
        # a block left as written (CGL-111) is one segment: code is never split into sentences
        spans = (
            [(0, len(block.text))]
            if block.provenance.keep is not None
            else split_sentences(block.text)
        )
        for start, end in spans:
            segments.append(
                Segment(
                    id=f"s{len(segments) + 1}",
                    block_id=block.id,
                    start=start,
                    end=end,
                    text=block.text[start:end],
                )
            )
    return segments
