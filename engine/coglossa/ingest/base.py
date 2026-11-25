"""Shared ingestion helpers and the Ingestor protocol."""

import hashlib
import re
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from coglossa.schema import Block, BlockKind, InlineMark, KeepReason, Provenance, SourceDocument

# How a document notice about content left out begins (CGL-87). The run and its report show it
# as a document notice (FlagCode.UNREAD_CONTENT), not an error: the translation itself is fine.
UNREAD_PREFIX = "Some parts of this file couldn't be read, so they aren't translated: "


class IngestError(ValueError):
    """The file cannot be turned into translatable text; message is user-facing."""


class UnsupportedFormatError(IngestError):
    pass


class Ingestor(Protocol):
    format: str
    extensions: tuple[str, ...]

    def ingest(self, path: Path) -> SourceDocument: ...


_SCENE_BREAK_RE = re.compile(r"^\s*(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,}|~{3,}|#|§)\s*$")
_BLANK_LINES_RE = re.compile(r"\n\s*\n")


def is_scene_break(text: str) -> bool:
    return bool(_SCENE_BREAK_RE.match(text))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_utf8(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise IngestError(
            f"{path.name} is not valid UTF-8 (byte {e.start}); re-save it as UTF-8 and retry"
        ) from e


# An OLE compound file: what Office saves a password-protected file as, and its pre-2007 formats
_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


def check_office_zip(name: str, head: bytes, is_zip: bool, app: str, ext: str) -> None:
    """Refuse, in plain words, an Office file that is not the zip package its extension says
    (CGL-110): a password-protected file (an encrypted OLE container), one saved in the old
    format and renamed, or anything else. `head` is the file's first bytes."""
    if head.startswith(_OLE_MAGIC):
        raise IngestError(
            f"{name} is password-protected or saved in an older {app} format, so it can't be "
            f"read; open it in {app}, remove the password, save it as a {ext} file and try again"
        )
    if not is_zip:
        raise IngestError(f"{name} is not a readable {app} file")


# What a PowerPoint or Excel file may expand to: their libraries read every part into memory,
# so a small file that unpacks to gigabytes (a "zip bomb") is refused before they open it
MAX_OFFICE_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024


def check_office_file(path: Path, app: str, ext: str) -> None:
    with path.open("rb") as f:
        head = f.read(len(_OLE_MAGIC))
    is_zip = zipfile.is_zipfile(path)
    check_office_zip(path.name, head, is_zip, app, ext)
    try:
        with zipfile.ZipFile(path) as z:
            size = sum(info.file_size for info in z.infolist())
    except (zipfile.BadZipFile, ValueError) as e:
        raise IngestError(f"{path.name} is not a readable {app} file ({error_reason(e)})") from e
    if size > MAX_OFFICE_UNCOMPRESSED_BYTES:
        raise IngestError(
            f"{path.name} is too large to translate once unpacked (more than "
            f"{MAX_OFFICE_UNCOMPRESSED_BYTES // 2**30} GB); split it into smaller files and try "
            "again"
        )


def unread_notices(counts: Iterable[tuple[int, str, str]]) -> list[str]:
    """The notice naming what a file holds that cannot be read, from (count, singular, plural);
    none when every count is 0."""
    names = [f"{n} {one if n == 1 else many}" for n, one, many in counts if n]
    if not names:
        return []
    listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    return [f"{UNREAD_PREFIX}{listed}."]


def error_reason(e: BaseException) -> str:
    """A library exception's message on one line, for "not a readable … file (…)". A KeyError's
    str() quotes its message; its first argument does not."""
    reason = e.args[0] if isinstance(e, KeyError) and e.args else e
    return " ".join(str(reason).split())


def strip_nul(text: str) -> str:
    """NUL characters carry no text and Postgres cannot store them."""
    return text.replace("\x00", "")


def split_paragraphs(text: str) -> list[str]:
    normalized = strip_nul(text).replace("\r\n", "\n").replace("\r", "\n")
    return [p.strip() for p in _BLANK_LINES_RE.split(normalized) if p.strip()]


def trim_blank_lines(lines: Sequence[str]) -> str:
    """`lines` joined, without the blank lines at either end (by index: linear time)."""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


# Text a file marks to leave as written (CGL-111): the whole block, or pieces inside it
Protection = tuple[KeepReason | None, list[str]]
NO_PROTECTION: Protection = (None, [])


def make_blocks(
    source_path: str,
    items: list[tuple[BlockKind, str, str | None]],
    marks: list[list[InlineMark]] | None = None,  # each item's inline formatting (CGL-108)
    protect: list[Protection] | None = None,  # each item's keep and literals (CGL-111)
) -> list[Block]:
    protect = protect or [NO_PROTECTION] * len(items)
    return [
        Block(
            id=f"b{i + 1}",
            kind=kind,
            text=text,
            provenance=Provenance(
                source_path=source_path,
                index=i,
                locator=locator,
                marks=marks[i] if marks is not None else [],
                keep=protect[i][0],
                literals=protect[i][1],
            ),
        )
        for i, (kind, text, locator) in enumerate(items)
    ]
