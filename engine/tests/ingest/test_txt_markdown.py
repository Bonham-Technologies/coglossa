import pytest

from coglossa.ingest.base import IngestError, is_scene_break, split_paragraphs
from coglossa.ingest.markdown import MarkdownIngestor
from coglossa.ingest.txt import TextIngestor
from coglossa.schema import BlockKind, KeepReason


@pytest.mark.parametrize("line", ["***", "* * *", "---", "___", "#", "~~~", "§", "  * * *  "])
def test_scene_breaks(line):
    assert is_scene_break(line)


@pytest.mark.parametrize("line", ["# Title", "Hello", "**bold**", "--", "- item"])
def test_not_scene_breaks(line):
    assert not is_scene_break(line)


def test_split_paragraphs_keeps_inner_line_breaks():
    text = "Roses are red,\r\nviolets are blue.\r\n\r\n\n  Second para.  \n\n\n"
    assert split_paragraphs(text) == ["Roses are red,\nviolets are blue.", "Second para."]


def test_txt_ingest(tmp_path):
    path = tmp_path / "story.txt"
    path.write_text("﻿First paragraph.\n\n* * *\n\nSecond, after a break.\n", encoding="utf-8")
    doc = TextIngestor().ingest(path)
    assert doc.format == "txt"
    assert len(doc.sha256) == 64
    assert [(b.id, b.kind, b.text) for b in doc.blocks] == [
        ("b1", BlockKind.PARAGRAPH, "First paragraph."),
        ("b2", BlockKind.BREAK, "* * *"),
        ("b3", BlockKind.PARAGRAPH, "Second, after a break."),
    ]
    assert doc.blocks[2].provenance.index == 2
    assert doc.blocks[0].provenance.source_path == str(path)


def test_txt_rejects_non_utf8(tmp_path):
    path = tmp_path / "latin1.txt"
    path.write_bytes("café".encode("latin-1"))
    with pytest.raises(IngestError, match=r"latin1\.txt is not valid UTF-8"):
        TextIngestor().ingest(path)


def test_markdown_ingest(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(
        "# Chapter One\n\nIt was night.\n\n- milk\n- 2. bread\n\n1. first\n2. second\n\n"
        "---\n\n## Part Two\nThe heading has a body.\n\n* * *\n",
        encoding="utf-8",
    )
    doc = MarkdownIngestor().ingest(path)
    assert [(b.kind, b.text) for b in doc.blocks] == [
        (BlockKind.HEADING, "Chapter One"),
        (BlockKind.PARAGRAPH, "It was night."),
        (BlockKind.LIST_ITEM, "milk"),
        (BlockKind.LIST_ITEM, "2. bread"),
        (BlockKind.LIST_ITEM, "first"),
        (BlockKind.LIST_ITEM, "second"),
        (BlockKind.BREAK, "---"),
        (BlockKind.HEADING, "Part Two"),
        (BlockKind.PARAGRAPH, "The heading has a body."),
        (BlockKind.BREAK, "* * *"),
    ]
    assert doc.format == "md"


# --- CGL-111: code is left as written -------------------------------------------------------


def _md(tmp_path, text):
    path = tmp_path / "doc.md"
    path.write_text(text, encoding="utf-8")
    return MarkdownIngestor().ingest(path)


def test_a_fenced_code_block_is_one_block_left_as_written(tmp_path):
    code = "def f(x):\n\n    return x  # see https://example.com\n"
    doc = _md(
        tmp_path,
        f"Run this:\n\n```python\n{code}```\n\n~~~js\nlet a = 1;\n~~~\n\nThen stop.\n",
    )
    assert [(b.text, b.provenance.keep) for b in doc.blocks] == [
        ("Run this:", None),
        ("def f(x):\n\n    return x  # see https://example.com", KeepReason.CODE),
        ("let a = 1;", KeepReason.CODE),
        ("Then stop.", None),
    ]
    assert doc.blocks[1].kind is BlockKind.PARAGRAPH


def test_an_indented_fence_loses_only_its_own_indent(tmp_path):
    doc = _md(tmp_path, "  ```\n  a\n    b\nc\n  ```\n")
    assert [(b.text, b.provenance.keep) for b in doc.blocks] == [("a\n  b\nc", KeepReason.CODE)]


def test_a_bare_tilde_line_is_still_a_scene_break_and_an_unclosed_fence_is_text(tmp_path):
    doc = _md(tmp_path, "One.\n\n~~~\n\nTwo.\n\n~~~\n\n```\nThree.\n")
    assert [(b.kind, b.text, b.provenance.keep) for b in doc.blocks] == [
        (BlockKind.PARAGRAPH, "One.", None),
        (BlockKind.BREAK, "~~~", None),
        (BlockKind.PARAGRAPH, "Two.", None),
        (BlockKind.BREAK, "~~~", None),
        (BlockKind.PARAGRAPH, "```\nThree.", None),
    ]


def test_many_unclosed_fences_are_read_in_linear_time(tmp_path):
    """Each unclosed fence used to scan the rest of the file for its closing line."""
    lines = [f"~~~py {i}" for i in range(50_000)]
    doc = _md(tmp_path, "\n".join(lines) + "\n\n```\nx\n```\n")
    assert doc.blocks[0].text.split("\n") == lines
    assert [(b.text, b.provenance.keep) for b in doc.blocks[1:]] == [("x", KeepReason.CODE)]


def test_a_fence_with_many_blank_lines_is_read_in_linear_time(tmp_path):
    """Blank lines at the ends of a block were trimmed one list copy at a time."""
    doc = _md(tmp_path, "```\n" + "\n" * 200_000 + "x = 1\n" + "\n" * 200_000 + "```\n\nFin.\n")
    assert [(b.text, b.provenance.keep) for b in doc.blocks] == [
        ("x = 1", KeepReason.CODE),
        ("Fin.", None),
    ]


def test_a_fence_indented_under_a_list_item_is_left_as_written(tmp_path):
    doc = _md(
        tmp_path,
        "1. Install it:\n\n    ```bash\n    npm install\n\n      --save x\n    ```\n\n"
        "2. Then:\n\n\t```\n\tmake\n\t```\n",
    )
    assert [(b.text, b.provenance.keep) for b in doc.blocks] == [
        ("Install it:", None),
        ("npm install\n\n  --save x", KeepReason.CODE),
        ("Then:", None),
        ("make", KeepReason.CODE),
    ]


def test_a_fence_inside_a_blockquote_is_left_as_written(tmp_path):
    doc = _md(tmp_path, "> Run:\n>\n> ```\n> code\n>\n>   more\n> ```\n\nFin.\n")
    assert [(b.text, b.provenance.keep, b.provenance.literals) for b in doc.blocks] == [
        ("> Run:\n>", None, []),
        ("code\n\n  more", KeepReason.CODE, []),
        ("Fin.", None, []),
    ]


def test_a_fence_the_blockquote_does_not_close_is_text(tmp_path):
    doc = _md(tmp_path, "> ```\n> code\n\nNot quoted.\n\n```\n")
    assert all(b.provenance.keep is None for b in doc.blocks)


def test_code_spans_stay_in_the_sentence_and_are_recorded(tmp_path):
    doc = _md(tmp_path, "Run `make test` and ``a`b`` now.\n\n`npm install`\n\n- use `x`\n")
    assert [(b.text, b.provenance.keep, b.provenance.literals) for b in doc.blocks] == [
        ("Run `make test` and ``a`b`` now.", None, ["make test", "a`b"]),
        ("`npm install`", KeepReason.CODE, []),
        ("use `x`", None, ["x"]),
    ]
