import zipfile

import pytest

from coglossa.ingest import epub as epub_module
from coglossa.ingest.base import IngestError
from coglossa.ingest.epub import EpubIngestor
from coglossa.ingest.registry import ingest_file
from coglossa.schema import BlockKind

CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    '<rootfile full-path="{opf}" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>"
)


def _chapter(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>t</title></head><body>{body}</body></html>"
    )


def make_epub(
    path,
    chapters: dict[str, str],
    *,
    spine: list[str] | None = None,
    encrypted: tuple[str, ...] = (),
    extra_items: str = "",
    container: str | None = None,
):
    """chapters: href (relative to OEBPS/) -> body HTML. spine: hrefs in reading order."""
    spine = spine if spine is not None else list(chapters)
    ids = {href: f"i{n}" for n, href in enumerate(chapters)}
    manifest = "".join(
        f'<item id="{ids[h]}" href="{h.replace(" ", "%20")}" media-type="application/xhtml+xml"/>'
        for h in chapters
    )
    itemrefs = "".join(f'<itemref idref="{ids[h]}"/>' for h in spine)
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        f"<manifest>{manifest}{extra_items}</manifest><spine>{itemrefs}</spine></package>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container or CONTAINER.format(opf="OEBPS/content.opf"))
        z.writestr("OEBPS/content.opf", opf)
        for href, body in chapters.items():
            z.writestr(f"OEBPS/{href}", _chapter(body))
        if encrypted:
            refs = "".join(
                f'<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData>'
                f'<CipherReference URI="{uri}"/></CipherData></EncryptedData>'
                for uri in encrypted
            )
            z.writestr(
                "META-INF/encryption.xml",
                f'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">{refs}'
                "</encryption>",
            )
    return path


def test_chapters_follow_the_spine(tmp_path):
    path = make_epub(
        tmp_path / "book.epub",
        {
            "ch2.xhtml": "<h1>Deux</h1><p>Le second.</p>",
            "chapter 1.xhtml": "<h1>Un</h1><p>Le premier.</p>",
            "notes.xhtml": '<aside epub:type="footnote"><p>Une note.</p></aside>',
        },
        spine=["chapter 1.xhtml", "ch2.xhtml", "notes.xhtml"],
        extra_items='<item id="css" href="style.css" media-type="text/css"/>',
    )
    doc = EpubIngestor().ingest(path)
    assert [(b.kind, b.text, b.provenance.locator) for b in doc.blocks] == [
        (BlockKind.HEADING, "Un", "OEBPS/chapter 1.xhtml"),
        (BlockKind.PARAGRAPH, "Le premier.", "OEBPS/chapter 1.xhtml"),
        (BlockKind.HEADING, "Deux", "OEBPS/ch2.xhtml"),
        (BlockKind.PARAGRAPH, "Le second.", "OEBPS/ch2.xhtml"),
        (BlockKind.FOOTNOTE, "Une note.", "OEBPS/notes.xhtml"),
    ]
    assert doc.format == "epub"
    assert [b.id for b in doc.blocks] == ["b1", "b2", "b3", "b4", "b5"]


def test_items_outside_the_spine_and_empty_chapters_are_skipped(tmp_path):
    path = make_epub(
        tmp_path / "book.epub",
        {"nav.xhtml": "<p>Table des matières</p>", "cover.xhtml": "", "a.xhtml": "<p>Texte.</p>"},
        spine=["cover.xhtml", "a.xhtml"],
    )
    assert [b.text for b in EpubIngestor().ingest(path).blocks] == ["Texte."]


def test_font_obfuscation_is_accepted(tmp_path):
    path = make_epub(
        tmp_path / "book.epub", {"a.xhtml": "<p>Texte.</p>"}, encrypted=("OEBPS/fonts/f.otf",)
    )
    assert EpubIngestor().ingest(path).blocks[0].text == "Texte."


def test_encrypted_chapters_are_refused(tmp_path):
    path = make_epub(
        tmp_path / "book.epub", {"a.xhtml": "<p>Texte.</p>"}, encrypted=("OEBPS/a.xhtml",)
    )
    with pytest.raises(IngestError, match="book.epub is DRM-protected"):
        EpubIngestor().ingest(path)


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda p: p.write_bytes(b"not a zip"), "File is not a zip file"),
        (lambda p: zipfile.ZipFile(p, "w").close(), "META-INF/container.xml"),
        (
            lambda p: make_epub(p, {"a.xhtml": "<p>x</p>"}, container="<container/>"),
            "no package document",
        ),
        (lambda p: make_epub(p, {"a.xhtml": "<p>x</p>"}, spine=[]), "lists no chapters"),
        (
            lambda p: make_epub(p, {"a.xhtml": "<p>x</p>"}, container="<container><oops"),
            "container.xml",
        ),
    ],
)
def test_damaged_books_name_the_problem(tmp_path, build, reason):
    path = tmp_path / "book.epub"
    build(path)
    with pytest.raises(IngestError, match="book.epub is not a readable EPUB file") as e:
        EpubIngestor().ingest(path)
    assert reason in str(e.value)


_ROOTFILE_DEFAULT = '<!ATTLIST rootfile full-path CDATA "OEBPS/content.opf">'
_NO_PATH = (
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile media-type="application/oebps-package+xml"/></rootfiles></container>'
)


@pytest.mark.parametrize("via", ["external DTD", "external parameter entity"])
def test_package_xml_never_loads_external_definitions(tmp_path, via):
    """A DTD would give rootfile its full-path; the book is readable only if it is loaded."""
    dtd = tmp_path / "defaults.dtd"
    dtd.write_text(_ROOTFILE_DEFAULT, encoding="utf-8")
    doctype = (
        f'<!DOCTYPE container SYSTEM "{dtd.as_uri()}">'
        if via == "external DTD"
        else f'<!DOCTYPE container [<!ENTITY % ext SYSTEM "{dtd.as_uri()}"> %ext;]>'
    )
    path = make_epub(tmp_path / "book.epub", {"a.xhtml": "<p>x</p>"}, container=doctype + _NO_PATH)
    with pytest.raises(IngestError, match="names no package document"):
        EpubIngestor().ingest(path)


def test_package_xml_refuses_entity_expansion_bombs(tmp_path):
    entities = '<!ENTITY a0 "lol">' + "".join(
        f'<!ENTITY a{i} "{f"&a{i - 1};" * 10}">' for i in range(1, 10)
    )
    container = (
        CONTAINER.format(opf="&a9;")
        .replace("<container", f"<!DOCTYPE container [{entities}]><container", 1)
        .replace('<?xml version="1.0"?>', "", 1)
    )
    path = make_epub(tmp_path / "book.epub", {"a.xhtml": "<p>x</p>"}, container=container)
    with pytest.raises(IngestError, match="not a readable EPUB file .*amplification"):
        EpubIngestor().ingest(path)


def test_unreadable_reasons_are_one_line_without_extra_quotes(tmp_path, monkeypatch):
    empty = tmp_path / "empty.epub"
    zipfile.ZipFile(empty, "w").close()
    with pytest.raises(IngestError) as e:
        EpubIngestor().ingest(empty)
    assert str(e.value) == (
        "empty.epub is not a readable EPUB file (There is no item named "
        "'META-INF/container.xml' in the archive)"
    )

    def broken(z):
        raise ValueError("first line\n   second   line")

    monkeypatch.setattr(epub_module, "_chapters", broken)
    path = make_epub(tmp_path / "book.epub", {"a.xhtml": "<p>x</p>"})
    with pytest.raises(IngestError) as e:
        EpubIngestor().ingest(path)
    assert str(e.value) == "book.epub is not a readable EPUB file (first line second line)"


def test_oversized_archives_are_refused_before_reading(tmp_path, monkeypatch):
    path = make_epub(tmp_path / "book.epub", {"a.xhtml": "<p>" + "x" * 5000 + "</p>"})
    monkeypatch.setattr(epub_module, "MAX_UNCOMPRESSED_BYTES", 1000)
    with pytest.raises(IngestError, match="expands to more than"):
        EpubIngestor().ingest(path)


def test_registry_reads_epub(tmp_path):
    path = make_epub(tmp_path / "book.epub", {"a.xhtml": "<p>Bonjour.</p>"})
    assert ingest_file(path).format == "epub"


def test_inline_formatting_is_recorded_in_each_chapter(tmp_path):
    """CGL-108."""
    path = make_epub(
        tmp_path / "book.epub",
        {"a.xhtml": "<p>Un <em>beau</em> jour.</p>", "b.xhtml": "<p>Le <b>soir</b>.</p>"},
    )
    blocks = EpubIngestor().ingest(path).blocks
    assert [
        (b.text[m.start : m.end], m.kind.value) for b in blocks for m in b.provenance.marks
    ] == [
        ("beau", "italic"),
        ("soir", "bold"),
    ]
