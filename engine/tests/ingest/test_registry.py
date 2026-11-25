from pathlib import Path

import pytest

from coglossa.ingest import registry
from coglossa.ingest.base import IngestError, UnsupportedFormatError
from coglossa.schema import SourceDocument


def test_dispatches_by_extension_case_insensitively(tmp_path):
    path = tmp_path / "A.TXT"
    path.write_text("Hello.", encoding="utf-8")
    assert registry.ingest_file(path).format == "txt"


def test_unsupported_extension(tmp_path):
    path = tmp_path / "scan.odt"
    path.write_bytes(b"x")
    with pytest.raises(UnsupportedFormatError, match=r"unsupported file type '\.odt'") as e:
        registry.ingest_file(path)
    assert ".pptx" in str(e.value) and ".xlsx" in str(e.value)  # CGL-110


def test_powerpoint_and_excel_files_are_accepted():
    registry.check_extension("Slides.PPTX")
    registry.check_extension("Budget.xlsx")
    with pytest.raises(UnsupportedFormatError):
        registry.check_extension("old.ppt")


def test_missing_file(tmp_path):
    with pytest.raises(IngestError, match="not found"):
        registry.ingest_file(tmp_path / "nope.txt")


def test_document_without_text_is_rejected(tmp_path):
    path = tmp_path / "breaks.txt"
    path.write_text("* * *\n\n---\n", encoding="utf-8")
    with pytest.raises(IngestError, match="no translatable text"):
        registry.ingest_file(path)


def test_plugin_ingestors(tmp_path, monkeypatch):
    class OdtIngestor:
        format = "odt"
        extensions = (".odt",)

        def ingest(self, path):
            return SourceDocument(source_path=str(path), format="odt", sha256="0", blocks=[])

    class EP:
        name = "odt"

        def load(self):
            return OdtIngestor

    monkeypatch.setattr(registry, "entry_points", lambda group: [EP()])
    path = tmp_path / "x.odt"
    path.write_bytes(b"")
    with pytest.raises(IngestError, match="no translatable text"):
        registry.ingest_file(path)


def test_unreadable_file_is_ingest_error(tmp_path, monkeypatch):
    path = tmp_path / "locked.txt"
    path.write_text("Hello.", encoding="utf-8")

    def deny(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", deny)
    with pytest.raises(IngestError, match="cannot be read: Permission denied") as exc_info:
        registry.ingest_file(path)
    assert isinstance(exc_info.value.__cause__, PermissionError)


def test_nul_characters_are_removed_from_text_files(tmp_path):
    path = tmp_path / "nul.txt"
    path.write_text("Le chat\x00 dort.\n\n\x00\n\nIl pleut.", encoding="utf-8")
    doc = registry.ingest_file(path)
    assert [b.text for b in doc.blocks] == ["Le chat dort.", "Il pleut."]


def test_control_characters_are_cleaned_from_every_block(tmp_path):
    path = tmp_path / "controls.txt"
    path.write_text("Le chat\x0cdort.\x01\n\nIl\x0bpleut.\x1f", encoding="utf-8")
    doc = registry.ingest_file(path)
    assert [b.text for b in doc.blocks] == ["Le chat\ndort.", "Il\npleut."]


def test_nul_characters_are_removed_from_plugin_ingestor_blocks(tmp_path, monkeypatch):
    from coglossa.ingest.base import make_blocks
    from coglossa.schema import BlockKind

    class OdtIngestor:
        format = "odt"
        extensions = (".odt",)

        def ingest(self, path):
            blocks = make_blocks(str(path), [(BlockKind.PARAGRAPH, "Bon\x00jour\x0c.", None)])
            return SourceDocument(source_path=str(path), format="odt", sha256="0", blocks=blocks)

    class EP:
        name = "odt"

        def load(self):
            return OdtIngestor

    monkeypatch.setattr(registry, "entry_points", lambda group: [EP()])
    path = tmp_path / "x.odt"
    path.write_bytes(b"")
    assert [b.text for b in registry.ingest_file(path).blocks] == ["Bonjour\n."]


def test_cleaning_a_blocks_text_keeps_its_marks_on_their_words(tmp_path, monkeypatch):
    """CGL-108: characters the registry removes shift the marks after them."""
    from coglossa.ingest.base import make_blocks
    from coglossa.schema import BlockKind, InlineMark, MarkKind

    class OdtIngestor:
        format = "odt"
        extensions = (".odt",)

        def ingest(self, path):
            text = "Bon\x00jour \x01chat."
            mark = InlineMark(start=10, end=14, kind=MarkKind.BOLD)
            blocks = make_blocks(str(path), [(BlockKind.PARAGRAPH, text, None)], [[mark]])
            return SourceDocument(source_path=str(path), format="odt", sha256="0", blocks=blocks)

    class EP:
        name = "odt"

        def load(self):
            return OdtIngestor

    monkeypatch.setattr(registry, "entry_points", lambda group: [EP()])
    path = tmp_path / "x.odt"
    path.write_bytes(b"")
    block = registry.ingest_file(path).blocks[0]
    assert block.text == "Bonjour chat."
    assert [block.text[m.start : m.end] for m in block.provenance.marks] == ["chat"]
