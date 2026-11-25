import pytest

from coglossa.ingest.base import IngestError
from coglossa.ingest.html import HtmlIngestor, collect_blocks, parse_html
from coglossa.ingest.registry import check_supported, ingest_file
from coglossa.schema import BlockKind, KeepReason, MarkKind

H, P, LI, TD, FN, BR = (
    BlockKind.HEADING,
    BlockKind.PARAGRAPH,
    BlockKind.LIST_ITEM,
    BlockKind.TABLE_CELL,
    BlockKind.FOOTNOTE,
    BlockKind.BREAK,
)


def _write(tmp_path, html: str, name: str = "page.html"):
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    return path


def _kinds(doc):
    return [(b.kind, b.text) for b in doc.blocks]


def test_blocks_in_document_order(tmp_path):
    doc = HtmlIngestor().ingest(
        _write(
            tmp_path,
            "<!doctype html><html><head><title>T</title><style>p {}</style></head><body>"
            "<h1>Le  titre</h1><p>Un <em>beau</em>\n   jour.</p>"
            "<ul><li>Pommes</li><li>Poires</li></ul>"
            "<table><tr><th>Nom</th><td>Valeur</td></tr></table>"
            "<script>var x = 1;</script></body></html>",
        )
    )
    assert _kinds(doc) == [
        (H, "Le titre"),
        (P, "Un beau jour."),
        (LI, "Pommes"),
        (LI, "Poires"),
        (TD, "Nom"),
        (TD, "Valeur"),
    ]
    assert doc.format == "html"
    assert [b.id for b in doc.blocks] == [f"b{i}" for i in range(1, 7)]
    assert [b.provenance.index for b in doc.blocks] == list(range(6))
    assert all(b.provenance.locator is None for b in doc.blocks)


def test_loose_text_around_blocks_becomes_paragraphs(tmp_path):
    doc = HtmlIngestor().ingest(
        _write(tmp_path, "<body><div>Avant<p>Milieu</p>Après <b>tout</b></div>Fin</body>")
    )
    assert _kinds(doc) == [(P, "Avant"), (P, "Milieu"), (P, "Après tout"), (P, "Fin")]


def test_line_breaks_rules_and_scene_breaks(tmp_path):
    doc = HtmlIngestor().ingest(
        _write(tmp_path, "<p>Ligne un<br>  Ligne deux<br/></p><hr><p>* * *</p><p>Suite.</p>")
    )
    assert _kinds(doc) == [
        (P, "Ligne un\nLigne deux"),
        (BR, "* * *"),
        (BR, "* * *"),
        (P, "Suite."),
    ]


def test_nesting_footnotes_and_note_references(tmp_path):
    doc = HtmlIngestor().ingest(
        _write(
            tmp_path,
            '<body><p>Texte<a epub:type="noteref" href="#n1">1</a> suite.</p>'
            '<aside epub:type="footnote" id="n1"><p>Une note.</p></aside>'
            '<div role="doc-endnote">Fin.</div>'
            '<p>Voir<sup role="doc-noteref">2</sup>.</p>'
            "<ul><li><p>Point</p><ul><li>Sous-point</li></ul></li></ul>"
            "<table><tr><td><p>Cellule</p></td></tr></table></body>",
        )
    )
    assert _kinds(doc) == [
        (P, "Texte suite."),
        (FN, "Une note."),
        (FN, "Fin."),
        (P, "Voir."),
        (LI, "Point"),
        (LI, "Sous-point"),
        (TD, "Cellule"),
    ]


def test_pre_is_left_as_written_and_comments_are_skipped(tmp_path):
    # CGL-111: a <pre> block keeps its spacing and is never translated
    doc = HtmlIngestor().ingest(
        _write(tmp_path, "<body><pre>a   b\n  c\n</pre><p>x<!-- hidden -->y</p></body>")
    )
    assert _kinds(doc) == [(P, "a   b\n  c"), (P, "xy")]
    assert [b.provenance.keep for b in doc.blocks] == [KeepReason.CODE, None]


def test_private_use_character_in_text_does_not_split_lines(tmp_path):
    doc = HtmlIngestor().ingest(_write(tmp_path, "<p>a\ue000b</p>"))
    assert _kinds(doc) == [(P, "ab")]


def test_utf8_without_charset_and_declared_legacy_charset(tmp_path):
    plain = tmp_path / "a.html"
    plain.write_bytes("<p>café</p>".encode())
    assert HtmlIngestor().ingest(plain).blocks[0].text == "café"
    legacy = tmp_path / "b.html"
    legacy.write_bytes(
        '<html><head><meta charset="windows-1252"></head><body><p>caf\xe9</p></body></html>'.encode(
            "cp1252"
        )
    )
    assert HtmlIngestor().ingest(legacy).blocks[0].text == "café"


def test_xhtml_with_xml_declaration_and_namespace(tmp_path):
    doc = HtmlIngestor().ingest(
        _write(
            tmp_path,
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><h2>Titre</h2>'
            "<p>Corps.</p></body></html>",
            name="chapter.xhtml",
        )
    )
    assert _kinds(doc) == [(H, "Titre"), (P, "Corps.")]


def test_parse_html_returns_none_for_empty_documents():
    assert parse_html(b"") is None
    assert parse_html(b"   \n") is None
    assert collect_blocks(parse_html(b"<p>a</p>"), "ch1.xhtml") == [(P, "a", "ch1.xhtml")]


@pytest.mark.parametrize("html", ["", "<html><head><title>x</title></head><body>  </body></html>"])
def test_documents_without_text_are_refused(tmp_path, html):
    with pytest.raises(IngestError, match="contains no translatable text"):
        ingest_file(_write(tmp_path, html))


def test_registry_reads_html(tmp_path):
    for name in ("a.html", "b.htm", "c.xhtml"):
        assert ingest_file(_write(tmp_path, "<p>Bonjour.</p>", name=name)).format == "html"
    with pytest.raises(IngestError) as e:
        check_supported(_write(tmp_path, "x", name="a.rtf"))
    assert ".html" in str(e.value)


def test_nested_blocks_keep_the_outer_kind(tmp_path):
    doc = HtmlIngestor().ingest(
        _write(
            tmp_path,
            "<body><table><tr><td><ul><li>x</li></ul></td></tr></table>"
            '<aside epub:type="footnote"><ol><li>note</li></ol></aside>'
            "<ul><li><h3>titre</h3></li></ul></body>",
        )
    )
    assert _kinds(doc) == [(TD, "x"), (FN, "note"), (LI, "titre")]


def test_inline_formatting_is_recorded_as_marks(tmp_path):
    """CGL-108: <b>, <em>, <a href> and <sup> in one paragraph, across collapsed whitespace."""
    doc = HtmlIngestor().ingest(
        _write(
            tmp_path,
            "<p>  Le <b>chat</b>\n   <em>dort <a href='https://example.org/x'>ici</a></em>"
            "<sup>2</sup>, H<sub>2</sub>O, <u>souligné</u> <strong> fort </strong>"
            "<i>et</i> <a href='#note'>renvoi</a> <a href='chapitre2.xhtml'>suite</a>.</p>"
            "<p>Sans rien.</p><p><b>* * *</b></p><pre><b>a</b>  b</pre>",
        )
    )
    first = doc.blocks[0]
    assert first.text == "Le chat dort ici2, H2O, souligné fort et renvoi suite."
    marked = [(m.kind, m.href, first.text[m.start : m.end]) for m in first.provenance.marks]
    assert marked == [
        (MarkKind.BOLD, None, "chat"),
        (MarkKind.ITALIC, None, "dort ici"),
        (MarkKind.LINK, "https://example.org/x", "ici"),
        (MarkKind.SUPERSCRIPT, None, "2"),
        (MarkKind.SUBSCRIPT, None, "2"),
        (MarkKind.UNDERLINE, None, "souligné"),
        (MarkKind.BOLD, None, "fort"),
        (MarkKind.ITALIC, None, "et"),
    ]  # links within the book or page have no address a Word file could open
    assert doc.blocks[1].provenance.marks == []
    assert doc.blocks[2].kind is BR and doc.blocks[2].provenance.marks == []
    assert doc.blocks[3].text == "a  b" and doc.blocks[3].provenance.marks == []
    assert doc.blocks[3].provenance.keep is KeepReason.CODE


def test_unsafe_or_malformed_link_targets_are_no_links(tmp_path):
    doc = HtmlIngestor().ingest(
        _write(
            tmp_path,
            "<p>A <a href='javascript:alert(1)'>x</a> <a href='http://[bad'>y</a> "
            "<a href='file:///etc/passwd'>z</a>.</p>",
        )
    )
    assert doc.blocks[0].text == "A x y z." and doc.blocks[0].provenance.marks == []


def test_line_breaks_keep_marks_on_their_lines(tmp_path):
    doc = HtmlIngestor().ingest(_write(tmp_path, "<p>un <b>deux<br>  trois</b> quatre</p>"))
    block = doc.blocks[0]
    assert block.text == "un deux\ntrois quatre"
    assert [block.text[m.start : m.end] for m in block.provenance.marks] == ["deux", "trois"]


# --- CGL-111: code and translate="no" are left as written -------------------------------------


def _protected(doc):
    return [(b.text, b.provenance.keep, b.provenance.literals) for b in doc.blocks]


def test_code_blocks_are_left_as_written(tmp_path):
    doc = HtmlIngestor().ingest(
        _write(
            tmp_path,
            "<p>Install it:</p><pre><code>npm  install\n  --save x</code></pre>"
            "<p><code>make test</code></p><ul><li><kbd>Ctrl</kbd> <kbd>C</kbd></li></ul>"
            "<p>* * *</p><pre>* * *</pre>",
        )
    )
    assert _protected(doc) == [
        ("Install it:", None, []),
        ("npm  install\n  --save x", KeepReason.CODE, []),
        ("make test", KeepReason.CODE, []),
        ("Ctrl C", None, []),  # key names are localised: <kbd> is translated
        ("* * *", None, []),
        ("* * *", KeepReason.CODE, []),
    ]
    assert doc.blocks[4].kind is BR and doc.blocks[5].kind is P


def test_translate_no_is_left_as_written_and_inherited(tmp_path):
    doc = HtmlIngestor().ingest(
        _write(
            tmp_path,
            "<h2 translate='no'>ACME Turbo</h2><p translate='NO'>Gardez <b>ceci</b>.</p>"
            "<div translate='no'><p>Pas ceci</p><p translate='yes'>Mais ceci.</p>"
            "<p translate=''>Et ceci.</p></div><p class='lead notranslate'>Marque</p>"
            "<pre translate='yes'>Un   poème</pre>",
        )
    )
    assert _protected(doc) == [
        ("ACME Turbo", KeepReason.NO_TRANSLATE, []),
        ("Gardez ceci.", KeepReason.NO_TRANSLATE, []),
        ("Pas ceci", KeepReason.NO_TRANSLATE, []),
        ("Mais ceci.", None, []),
        ("Et ceci.", None, []),
        ("Marque", KeepReason.NO_TRANSLATE, []),
        ("Un poème", None, []),
    ]
    assert doc.blocks[0].kind is H
    # the formatting of a block left as written is kept
    assert [(m.kind, m.start, m.end) for m in doc.blocks[1].provenance.marks] == [
        (MarkKind.BOLD, 7, 11)
    ]


def test_code_and_translate_no_inside_a_sentence_stay_in_it_as_literals(tmp_path):
    doc = HtmlIngestor().ingest(
        _write(
            tmp_path,
            "<p>Run <code>make  test</code>, then open <span translate='no'>ACME Box</span> "
            "and <code>x</code>.</p>",
        )
    )
    assert _protected(doc) == [
        ("Run make test, then open ACME Box and x.", None, ["make test", "ACME Box", "x"])
    ]


def test_a_key_name_inside_a_sentence_may_be_localised(tmp_path):
    doc = HtmlIngestor().ingest(_write(tmp_path, "<p>Appuyez sur <kbd>Entrée</kbd>.</p>"))
    assert _protected(doc) == [("Appuyez sur Entrée.", None, [])]


def test_a_file_that_is_all_code_is_refused(tmp_path):
    with pytest.raises(IngestError, match="all of it is code or marked not to translate"):
        ingest_file(_write(tmp_path, "<pre>x = 1</pre><p translate='no'>ACME</p>"))
