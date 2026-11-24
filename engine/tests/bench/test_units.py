import pytest

from coglossa.bench.units import Unit, fill_missing, group_units, write_source_document
from coglossa.ingest.registry import ingest_file
from coglossa.segment.segmenter import segment_document

SENTENCES = ["One. Two.", "Three.", "Four."]


def _doc(tmp_path):
    path = write_source_document(SENTENCES, tmp_path)
    doc = ingest_file(path)
    return doc, segment_document(doc)


def test_one_unit_per_sentence(tmp_path):
    doc, segs = _doc(tmp_path)
    assert len(doc.blocks) == 3
    by_block = {}
    for s in segs:
        by_block.setdefault(s.block_id, []).append(s.id)
    spans = [(ids[0], ids[-1], f"T{k}") for k, ids in enumerate(by_block.values())]
    assert group_units(spans, segs, doc.blocks, 3) == [
        Unit(0, 0, "T0"),
        Unit(1, 1, "T1"),
        Unit(2, 2, "T2"),
    ]


def test_two_spans_in_one_sentence_join(tmp_path):
    doc, segs = _doc(tmp_path)  # "One. Two." is two segments of block 1
    spans = [
        (segs[0].id, segs[0].id, "Uno."),
        (segs[1].id, segs[1].id, "Dos."),
        (segs[2].id, segs[2].id, "Tres."),
        (segs[3].id, segs[3].id, "Cuatro."),
    ]
    assert group_units(spans, segs, doc.blocks, 3) == [
        Unit(0, 0, "Uno. Dos."),
        Unit(1, 1, "Tres."),
        Unit(2, 2, "Cuatro."),
    ]


def test_merged_paragraphs_form_one_unit(tmp_path):  # Review Focus 2
    doc, segs = _doc(tmp_path)
    spans = [(segs[0].id, segs[2].id, "Uno dos tres."), (segs[3].id, segs[3].id, "Cuatro.")]
    assert group_units(spans, segs, doc.blocks, 3) == [
        Unit(0, 1, "Uno dos tres."),
        Unit(2, 2, "Cuatro."),
    ]


def test_block_count_must_match(tmp_path):
    doc, segs = _doc(tmp_path)
    with pytest.raises(ValueError, match="3 paragraphs for 4 sentences"):
        group_units([], segs, doc.blocks, 4)


def test_sentences_with_blank_lines_are_flattened(tmp_path):
    path = write_source_document(["a\n\nb", "c"], tmp_path)
    assert path.read_text(encoding="utf-8") == "a b\n\nc\n"


def test_fill_missing_adds_empty_units_for_uncovered_sentences():
    units = [Unit(1, 2, "x")]
    assert fill_missing(units, 5) == [
        Unit(0, 0, ""),
        Unit(1, 2, "x"),
        Unit(3, 3, ""),
        Unit(4, 4, ""),
    ]
    assert fill_missing([Unit(0, 0, "a")], 1) == [Unit(0, 0, "a")]


def test_a_formatted_bench_document_marks_every_other_sentence(tmp_path):
    """CGL-108: the longest word of every other sentence of three words or more, in bold, in
    italics or linked in turn; the text read back is the sentences unchanged."""
    sentences = ["One two three.", "Four five six.", "Seven <eight> & nine.", "a b", "x yy z", "x"]
    path = write_source_document(sentences, tmp_path, formatting=True)
    assert path.suffix == ".html"
    doc = ingest_file(path)
    assert [b.text for b in doc.blocks] == sentences
    marked = [
        [(m.kind.value, m.href, b.text[m.start : m.end]) for m in b.provenance.marks]
        for b in doc.blocks
    ]
    assert marked == [
        [("bold", None, "three.")],
        [],
        [("italic", None, "<eight>")],
        [],
        [("link", "https://example.org/", "yy")],
        [],
    ]
