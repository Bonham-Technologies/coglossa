"""A FLORES sentence is one paragraph of the bench document. Translators may merge sentences
across paragraphs, so outputs are compared per unit: a maximal run of paragraphs joined by
spans. Most units are one sentence."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from coglossa.schema import Block, Segment

# Languages written without spaces between words or sentences: Chinese (and Cantonese),
# Japanese, Thai, Lao, Khmer and Burmese.
_SPACELESS = frozenset({"zh", "yue", "ja", "th", "lo", "km", "my"})


def joiner(lang: str) -> str:
    """What joins two sentences of `lang` (a BCP-47 tag): nothing for a language written
    without spaces, else a space."""
    return "" if lang.replace("_", "-").split("-")[0].lower() in _SPACELESS else " "


@dataclass(frozen=True)
class Unit:
    first: int  # 0-based sentence index, inclusive
    last: int
    text: str


def write_source_document(sentences: list[str], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "source.txt"
    paragraphs = [" ".join(s.split()) for s in sentences]
    path.write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
    return path


def group_units(
    spans: Sequence[tuple[str, str, str]],
    segments: list[Segment],
    blocks: list[Block],
    n: int,
    sep: str = " ",
) -> list[Unit]:
    """`sep` joins the texts of a unit's spans: `joiner(target_lang)`."""
    if len(blocks) != n:
        raise ValueError(f"the bench document has {len(blocks)} paragraphs for {n} sentences")
    block_index = {b.id: k for k, b in enumerate(blocks)}
    seg_block = {s.id: block_index[s.block_id] for s in segments}
    groups: list[list] = []  # [first, last, texts]
    for start, end, text in spans:
        a, b = seg_block[start], seg_block[end]
        if groups and a <= groups[-1][1]:
            groups[-1][1] = max(groups[-1][1], b)
            groups[-1][2].append(text)
        else:
            groups.append([a, b, [text]])
    return [
        Unit(first, last, sep.join(t.strip() for t in texts if t.strip()))
        for first, last, texts in groups
    ]


def fill_missing(units: list[Unit], n: int) -> list[Unit]:
    """Sentences no span covers (a failed chunk) get empty units, so they are scored against an
    empty output rather than silently dropped."""
    filled: list[Unit] = []
    nxt = 0
    for unit in units:
        filled.extend(Unit(i, i, "") for i in range(nxt, unit.first))
        filled.append(unit)
        nxt = unit.last + 1
    filled.extend(Unit(i, i, "") for i in range(nxt, n))
    return filled
