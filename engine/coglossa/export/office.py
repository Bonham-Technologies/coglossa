"""What the PowerPoint and Excel downloads share (CGL-110): they write the translation back into
a copy of the original file, so they need the file the run translated, unchanged."""

import hashlib
import io
import zipfile

from coglossa.export.rows import TranslatedBlock, source_name, translated_blocks
from coglossa.ingest.base import IngestError, check_office_zip
from coglossa.schema import Block, Report


def a_file(app: str) -> str:
    """ "a PowerPoint file", "an Excel file" """
    return f"{'an' if app[0] in 'AEIOU' else 'a'} {app} file"


class ExportError(ValueError):
    """The download cannot be made; the message is for the person asking for it."""


def check_original(report: Report, original: bytes, fmt: str, app: str, ext: str) -> None:
    """Refuse a run of another format, and a file that is not the one the run translated."""
    name = source_name(report).name
    if report.document.format != fmt:
        raise ExportError(f"only a translation of {a_file(app)} can be downloaded as one")
    if hashlib.sha256(original).hexdigest() != report.document.sha256:
        raise ExportError(f"the stored copy of {name} is not the file that was translated")
    try:
        check_office_zip(name, original[:8], zipfile.is_zipfile(io.BytesIO(original)), app, ext)
    except IngestError as e:
        raise ExportError(str(e)) from e


def blocks_in_order(report: Report) -> list[Block]:
    return sorted(report.blocks, key=lambda b: b.provenance.index)


def translations(report: Report) -> dict[str, TranslatedBlock]:
    """Each block's translation by block id. A block left out had its sentences translated in
    an earlier block's passage, so its place is left empty."""
    return {item.block.id: item for item in translated_blocks(report)}


def mismatch(report: Report, app: str) -> ExportError:
    return ExportError(
        f"{source_name(report).name} could not be written back as {a_file(app)}: it does "
        "not match the text that was translated"
    )
