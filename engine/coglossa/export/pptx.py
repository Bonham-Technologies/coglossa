"""The PowerPoint download (CGL-110): the translation written back into a copy of the original
deck, so its slides, layouts, pictures, charts and everything else stay as they were.

Each paragraph that was read (`coglossa.ingest.pptx`) gets the translation of its block in
place of its text. The paragraph keeps its own settings (bullets, indents, alignment) and its
first run's look (font, size, colour); bold, italic, underlined, superscript and subscript words
and links come back on the translated words where the passage kept them (CGL-108). A paragraph
whose sentences were translated together with an earlier paragraph's is left empty. Charts,
diagrams and embedded objects keep their original text."""

import io
from copy import deepcopy
from typing import Any

from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.xmlchemy import OxmlElement

from coglossa.export.office import (
    ExportError,
    blocks_in_order,
    check_original,
    mismatch,
    translations,
)
from coglossa.export.rows import is_rtl, source_name
from coglossa.ingest.base import IngestError
from coglossa.ingest.pptx import _BR, _FLD, _HLINK, _R, _RPR, APP, EXTENSION, open_deck, walk
from coglossa.schema import InlineMark, MarkKind, Report
from coglossa.segment.markup import MarkKey, char_keys, link_target
from coglossa.text import clean_text

# Each kind of formatting: its attribute on a:rPr, its value when on, its value when off
_ATTRS = (
    ("b", {MarkKind.BOLD: "1"}, "0"),
    ("i", {MarkKind.ITALIC: "1"}, "0"),
    ("u", {MarkKind.UNDERLINE: "sng", MarkKind.LINK: "sng"}, "none"),
    ("baseline", {MarkKind.SUPERSCRIPT: "30000", MarkKind.SUBSCRIPT: "-25000"}, "0"),
)


def _turned_off(runs: list[Any]) -> set[str]:
    """The attributes a source run sets to off: a run of that paragraph that is not bold must
    say so, or it would take the bold of the placeholder it is in."""
    off: set[str] = set()
    for r in runs:
        rpr = r.find(_RPR)
        if rpr is None:
            continue
        for attr, _, value in _ATTRS:
            if rpr.get(attr) in (value, "false"):
                off.add(attr)
    return off


class _Writer:
    def __init__(self, p: Any, part: Any, lang: str) -> None:
        self.p, self.part = p, part
        runs = [c for c in p if c.tag in (_R, _FLD)]
        self.off = _turned_off(runs)
        first = runs[0].find(_RPR) if runs else None
        self.base = None if first is None else deepcopy(first)
        if self.base is not None:
            for attr, _, _ in _ATTRS:
                self.base.attrib.pop(attr, None)
            for link in self.base.findall(_HLINK):
                self.base.remove(link)
            if self.base.get("lang") is not None:
                self.base.set("lang", lang)
        for child in list(p):
            if child.tag in (_R, _BR, _FLD):
                p.remove(child)

    def _props(self, key: frozenset[MarkKey]) -> Any:
        rpr = deepcopy(self.base) if self.base is not None else None
        kinds = {kind for kind, _ in key}
        for attr, on, off in _ATTRS:
            value = next((v for kind, v in on.items() if kind in kinds), None)
            if value is None and attr in self.off:
                value = off
            if value is not None:
                if rpr is None:
                    rpr = OxmlElement("a:rPr")
                rpr.set(attr, value)
        links = (link_target(h) for kind, h in sorted(key, key=str) if kind is MarkKind.LINK)
        href = next((h for h in links if h is not None), None)  # stored marks are checked again
        if href is not None:
            # rpr is set: a link is underlined
            rpr.add_hlinkClick(self.part.relate_to(href, RT.HYPERLINK, is_external=True))
        return rpr

    def _line_break(self) -> None:
        br = self.p.add_br()
        if self.base is not None:
            br.insert(0, deepcopy(self.base))

    def write(self, text: str, marks: list[InlineMark]) -> None:
        text = clean_text(text)
        keys = char_keys(len(text), marks)
        start = 0
        while start < len(text):
            end = start + 1
            while end < len(text) and keys[end] == keys[start]:
                end += 1
            rpr = self._props(keys[start])
            for n, line in enumerate(text[start:end].split("\n")):
                if n:
                    self._line_break()
                if line:
                    r = self.p.add_r(line)
                    if rpr is not None:
                        r.insert(0, deepcopy(rpr))
            start = end


def translated_pptx(report: Report, original: bytes) -> bytes:
    check_original(report, original, "pptx", APP, EXTENSION)
    name = source_name(report).name
    try:
        presentation = open_deck(io.BytesIO(original), name)
        paragraphs, _ = walk(presentation, name)
    except IngestError as e:
        raise ExportError(str(e)) from e
    blocks = blocks_in_order(report)
    if [p.locator for p in paragraphs] != [b.provenance.locator for b in blocks]:
        raise mismatch(report, APP)
    texts = translations(report)
    lang = report.profile.target_lang
    rtl = is_rtl(lang)
    for paragraph, block in zip(paragraphs, blocks, strict=True):
        item = texts.get(block.id)
        writer = _Writer(paragraph.p, paragraph.part, lang)
        if item is not None:
            writer.write(item.text, item.marks)
        if rtl:
            paragraph.p.get_or_add_pPr().set("rtl", "1")
    out = io.BytesIO()
    presentation.save(out)
    return out.getvalue()
