"""Pick an ingestor by file extension; third parties add formats via coglossa.ingestors
(entry point value: an Ingestor class). OCR and speech-to-text arrive this way later; subtitle
files (CGL-131) are text, read by their own ingestor, not speech."""

from importlib.metadata import entry_points
from pathlib import Path, PurePosixPath

from coglossa.ingest.base import IngestError, Ingestor, UnsupportedFormatError
from coglossa.ingest.docx import DocxIngestor
from coglossa.ingest.epub import EpubIngestor
from coglossa.ingest.html import HtmlIngestor
from coglossa.ingest.markdown import MarkdownIngestor
from coglossa.ingest.pdf import PdfIngestor
from coglossa.ingest.pptx import PptxIngestor
from coglossa.ingest.subtitles import SrtIngestor, VttIngestor
from coglossa.ingest.txt import TextIngestor
from coglossa.ingest.xlsx import XlsxIngestor
from coglossa.schema import Block, BlockKind, SourceDocument
from coglossa.segment.markup import char_keys, marks_from_keys
from coglossa.text import clean_text


def available_ingestors() -> list[Ingestor]:
    ingestors: list[Ingestor] = [
        TextIngestor(),
        MarkdownIngestor(),
        DocxIngestor(),
        HtmlIngestor(),
        EpubIngestor(),
        PdfIngestor(),
        PptxIngestor(),
        XlsxIngestor(),
        SrtIngestor(),
        VttIngestor(),
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


def _cleaned(block: Block) -> Block:
    """The block with clean text; its marks follow the characters they cover."""
    literals = [clean_text(t) for t in block.provenance.literals]
    if not block.provenance.marks:
        provenance = block.provenance.model_copy(update={"literals": literals})
        return block.model_copy(update={"text": clean_text(block.text), "provenance": provenance})
    keys = char_keys(len(block.text), block.provenance.marks)
    kept = [(clean_text(c), k) for c, k in zip(block.text, keys, strict=True)]
    text = "".join(c for c, _ in kept)
    marks = marks_from_keys(text, [k for c, k in kept if c])
    provenance = block.provenance.model_copy(update={"marks": marks, "literals": literals})
    return block.model_copy(update={"text": text, "provenance": provenance})


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
                blocks = [_cleaned(b) for b in doc.blocks]
                doc = doc.model_copy(update={"blocks": blocks})
            texts = [b for b in doc.blocks if b.kind is not BlockKind.BREAK and b.text.strip()]
            if not texts:
                raise IngestError(f"{path.name} contains no translatable text")
            if all(b.provenance.keep is not None for b in texts):
                raise IngestError(
                    f"{path.name} contains no translatable text: all of it is code or marked "
                    "not to translate"
                )
            return doc
    raise AssertionError("unreachable")  # pragma: no cover - check_supported guarantees a match
