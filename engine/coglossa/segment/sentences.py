"""Language-aware sentence segmentation with no external models (spec §4.1).

Guarantee: spans are ordered, non-overlapping, non-empty, trimmed of whitespace, at most
max_chars long, and together cover every non-whitespace character of the input."""

from collections.abc import Iterator

MAX_SEGMENT_CHARS = 600

# End a sentence only when followed by whitespace (or end of text) and not by a lowercase word.
_SPACED_TERMINATORS = ".!?…‼⁇⁈⁉"
# End a sentence even without a following space: CJK, Indic danda, Arabic/Urdu, Ethiopic,
# Armenian, Myanmar, Georgian.
_SPACELESS_TERMINATORS = "。！？｡।॥؟۔።፧፨։။჻"
_TERMINATORS = _SPACED_TERMINATORS + _SPACELESS_TERMINATORS
_CLOSERS = "\"'”’»)]}」』）】〉》"
_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "no",
        "fig",
        "vol",
        "p",
        "pp",
        "cf",
        "al",
        "approx",
        "dept",
        "inc",
        "ltd",
        "co",
        "mt",
        "ft",
        "gen",
        "col",
        "capt",
        "lt",
        "sgt",
        "rev",
        "hon",
        "a.m",
        "p.m",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
    }
)
# Preferred cut points for over-long segments, strongest first.
_SOFT_BREAKS = (
    ("\n",),
    (",", ";", ":", "、", "，", "；", "،", "؛"),
    (" ", "\t", "　"),
)


def split_sentences(text: str, max_chars: int = MAX_SEGMENT_CHARS) -> list[tuple[int, int]]:
    raw: list[tuple[int, int]] = []
    start = 0
    for end in _boundaries(text):
        raw.append((start, end))
        start = end
    if start < len(text):
        raw.append((start, len(text)))
    spans: list[tuple[int, int]] = []
    for s, e in raw:
        for s2, e2 in _split_long(text, s, e, max_chars):
            s3, e3 = _trim(text, s2, e2)
            if s3 < e3:
                spans.append((s3, e3))
    return spans


def _boundaries(text: str) -> Iterator[int]:
    i, n = 0, len(text)
    while i < n:
        if text[i] in _TERMINATORS:
            j = i + 1
            while j < n and (text[j] in _TERMINATORS or text[j] in _CLOSERS):
                j += 1
            # Decide each terminator run once; never rescan inside it (linear time).
            if _is_boundary(text, i, j) or any(ch in _SPACELESS_TERMINATORS for ch in text[i:j]):
                yield j
            i = j
            continue
        i += 1


def _is_boundary(text: str, i: int, j: int) -> bool:
    if text[i] in _SPACELESS_TERMINATORS:
        return True
    if j < len(text) and not text[j].isspace():
        return False  # e.g. 3.14, example.com, "a.b"
    nxt = _next_non_space(text, j)
    if nxt is not None and nxt.islower():
        return False  # '"Stop!" she said', 'e.g. about'
    if text[i] == ".":
        word = _word_before(text, i)
        if word.lower() in _ABBREVIATIONS:
            return False
        if len(word) == 1 and word.isalpha() and word.isupper():
            return False  # initials: J. K. Rowling
    return True


def _word_before(text: str, i: int) -> str:
    k = i
    while k > 0 and (text[k - 1].isalpha() or text[k - 1] == "."):
        k -= 1
    return text[k:i].strip(".")


def _next_non_space(text: str, j: int) -> str | None:
    while j < len(text) and text[j].isspace():
        j += 1
    while j < len(text) and text[j] in _CLOSERS:  # skip opening quotes etc.
        j += 1
    return text[j] if j < len(text) else None


def _split_long(text: str, s: int, e: int, max_chars: int) -> Iterator[tuple[int, int]]:
    while e - s > max_chars:
        cut = _best_cut(text, s, s + max_chars)
        yield s, cut
        s = cut
    yield s, e


def _best_cut(text: str, start: int, limit: int) -> int:
    window = text[start:limit]
    floor = len(window) // 4  # avoid tiny fragments
    for group in _SOFT_BREAKS:
        k = max(window.rfind(ch) for ch in group)
        if k > floor:
            return start + k + 1
    return limit


def _trim(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e
