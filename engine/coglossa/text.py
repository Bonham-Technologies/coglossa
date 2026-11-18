"""Text that every layer can store and export: XML 1.0 (DOCX) and Postgres refuse some
characters that documents, model replies and pasted edits can carry."""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coglossa.schema import Guide

_LINE_BREAKS = str.maketrans({"\x0b": "\n", "\x0c": "\n"})  # vertical tab, form feed
# NUL and the other C0 controls except tab, line feed and carriage return; the two
# non-characters U+FFFE and U+FFFF; lone surrogates.
_ILLEGAL = re.compile("[\x00-\x08\x0e-\x1f￾￿\ud800-\udfff]")


def clean_text(text: str) -> str:
    """`text` with vertical tabs and form feeds as line breaks and every character XML 1.0
    forbids removed; tabs, line feeds and carriage returns stay."""
    return _ILLEGAL.sub("", text.translate(_LINE_BREAKS))


def entry_key(text: str) -> str:
    """The key that matches a guide term or name: trimmed and casefolded, so "Anna " and
    "anna" are one entry wherever entries are matched or merged."""
    return text.strip().casefold()


def clean_guide(guide: "Guide") -> "Guide":
    """`guide` with every text field cleaned: people paste guides from documents too."""
    return type(guide).model_validate(_clean(guide.model_dump()))


def _clean(value: object) -> object:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value
