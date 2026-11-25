from coglossa.schema import Block, BlockKind, KeepReason, Provenance, SourceDocument
from coglossa.segment.segmenter import segment_document


def _block(i, kind, text):
    return Block(
        id=f"b{i}", kind=kind, text=text, provenance=Provenance(source_path="x", index=i - 1)
    )


def test_segments_are_global_and_point_back_to_blocks():
    doc = SourceDocument(
        source_path="x",
        format="txt",
        sha256="0",
        blocks=[
            _block(1, BlockKind.PARAGRAPH, "  One. Two.  "),
            _block(2, BlockKind.BREAK, "* * *"),
            _block(3, BlockKind.HEADING, "Title"),
        ],
    )
    segs = segment_document(doc)
    assert [(s.id, s.block_id, s.text) for s in segs] == [
        ("s1", "b1", "One."),
        ("s2", "b1", "Two."),
        ("s3", "b3", "Title"),
    ]
    blocks = {b.id: b for b in doc.blocks}
    assert all(blocks[s.block_id].text[s.start : s.end] == s.text for s in segs)


def test_a_block_left_as_written_is_one_segment():
    code = "  a = 1. b = 2.\n  c = 3."
    block = _block(1, BlockKind.PARAGRAPH, code)
    block.provenance.keep = KeepReason.CODE
    doc = SourceDocument(source_path="x", format="md", sha256="0", blocks=[block])
    assert [(s.start, s.end, s.text) for s in segment_document(doc)] == [(0, len(code), code)]
