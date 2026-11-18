import pytest

from coglossa.export.rows import export_basename, export_rows, is_rtl, translated_blocks
from coglossa.schema import BlockKind, FlagCode
from tests.export.conftest import make_report, make_span


def test_rows_pair_each_span_with_its_source(report):
    rows = export_rows(report)
    assert [(r.number, r.source, r.span.id) for r in rows] == [
        (1, "Chapitre un", "p1"),
        (2, "Le chat dort. Il pleut.", "p2"),
        (3, "Fin <du> & monde.", "p3"),
    ]
    assert [f.code for f in rows[1].open_flags] == [FlagCode.LOW_MEANING]
    assert rows[0].open_flags == []


def test_translated_blocks_keep_structure(report):
    blocks = translated_blocks(report)
    assert [(b.block.kind, b.text) for b in blocks] == [
        (BlockKind.HEADING, "Chapter One"),
        (BlockKind.PARAGRAPH, "The cat sleeps.\nIt rains."),
        (BlockKind.BREAK, "* * *"),
        (BlockKind.LIST_ITEM, "The end <of> & world."),
    ]


def test_a_span_across_blocks_belongs_to_the_block_it_starts_in():
    report = make_report(
        spans=[
            make_span("p1", "s1", "s2", "Chapter One. The cat sleeps."),
            make_span("p2", "s3", "s3", "It rains."),
            make_span("p3", "s4", "s4", "   "),
        ]
    )
    assert [(b.block.id, b.text) for b in translated_blocks(report)] == [
        ("b1", "Chapter One. The cat sleeps."),
        ("b2", "It rains."),
        ("b3", "* * *"),
    ]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/data/story.txt", "story.en"),
        ("C:\\docs\\Mon livre.docx", "Mon livre.en"),
        ("", "document.en"),
    ],
)
def test_export_basename(path, expected):
    report = make_report()
    report.document.source_path = path
    assert export_basename(report) == expected


@pytest.mark.parametrize(
    ("lang", "rtl"),
    [
        ("ar", True),
        ("he-IL", True),
        ("fa", True),
        ("ur", True),
        ("az-Arab", True),
        ("ku-Arab-IQ", True),
        ("en", False),
        ("az-Latn", False),
        ("pt_BR", False),
    ],
)
def test_is_rtl(lang, rtl):
    assert is_rtl(lang) is rtl
