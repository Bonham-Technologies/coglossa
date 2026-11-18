from coglossa.schema import BlockKind, Segment, SourceDocument
from coglossa.segment.sentences import split_sentences


def segment_document(doc: SourceDocument) -> list[Segment]:
    segments: list[Segment] = []
    for block in doc.blocks:
        if block.kind is BlockKind.BREAK:
            continue
        for start, end in split_sentences(block.text):
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
