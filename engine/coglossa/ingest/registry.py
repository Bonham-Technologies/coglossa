"""Pick an ingestor by file extension; third parties add formats via coglossa.ingestors
(entry point value: an Ingestor class). OCR and speech-to-text arrive this way later."""

from importlib.metadata import entry_points
from pathlib import Path, PurePosixPath

from coglossa.ingest.base import IngestError, Ingestor, UnsupportedFormatError
from coglossa.ingest.docx import DocxIngestor
from coglossa.ingest.epub import EpubIngestor
from coglossa.ingest.html import HtmlIngestor
from coglossa.ingest.markdown import MarkdownIngestor
from coglossa.ingest.pdf import PdfIngestor
from coglossa.ingest.txt import TextIngestor
from coglossa.schema import BlockKind, SourceDocument
from coglossa.text import clean_text


def available_ingestors() -> list[Ingestor]:
    ingestors: list[Ingestor] = [
        TextIngestor(),
        MarkdownIngestor(),
        DocxIngestor(),
        HtmlIngestor(),
        EpubIngestor(),
        PdfIngestor(),
    ]
    ingestors += [ep.load()() for ep in entry_points(group="coglossa.ingestors")]
    return ingestors


def check_extension(name: str) -> None:
    """Fail fast on a file name no ingestor handles (uploads have no path to check)."""
    ext = PurePosixPath(name).suffix.lower()
    ingestors = available_ingestors()
    if not any(ext in i.extensions for i in ingestors):
        supported = sorted({e for i in ingestors for e in i.extensions})
        raise UnsupportedFormatError(
            f"unsupported file type '{ext or name}'; supported: {', '.join(supported)}"
        )


def check_supported(path: Path) -> None:
    """Fail fast on a missing file or an extension no ingestor handles."""
    if not path.is_file():
        raise IngestError(f"input file not found: {path}")
    check_extension(path.name)


def ingest_file(path: Path) -> SourceDocument:
    check_supported(path)
    ext = path.suffix.lower()
    for ingestor in available_ingestors():
        if ext in ingestor.extensions:
            try:
                doc = ingestor.ingest(path)
            except OSError as e:
                raise IngestError(f"{path.name} cannot be read: {e.strerror or e}") from e
            if any(clean_text(b.text) != b.text for b in doc.blocks):  # NUL, form feeds, ...
                blocks = [b.model_copy(update={"text": clean_text(b.text)}) for b in doc.blocks]
                doc = doc.model_copy(update={"blocks": blocks})
            if not any(b.kind is not BlockKind.BREAK and b.text.strip() for b in doc.blocks):
                raise IngestError(f"{path.name} contains no translatable text")
            return doc
    raise AssertionError("unreachable")  # pragma: no cover - check_supported guarantees a match
