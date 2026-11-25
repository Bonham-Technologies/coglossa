"""The Excel download (CGL-110): the translation written back into a copy of the original
workbook. Only the text cells that were read change; every other part of the file is copied
as it is, so formulas, numbers, dates, sheet names and sizes, merged cells, styles, charts and
pictures stay exactly as they were.

The workbook is edited as XML rather than loaded and saved again by a spreadsheet library,
which would drop what it cannot read (charts, shapes). A translated cell holds its text in
place ("inline"), with its words' bold, italic, underlined, superscript and subscript where the
passage kept them (CGL-108) and the look of its first original run; shared texts no cell uses
any more are emptied, so the original text does not stay hidden in the file. A cell whose
sentences were translated together with an earlier cell's is left empty."""

import io
import posixpath
import zipfile
from copy import deepcopy
from typing import Any

from lxml import etree

from coglossa.export.office import (
    ExportError,
    a_file,
    blocks_in_order,
    check_original,
    mismatch,
    translations,
)
from coglossa.export.rows import source_name
from coglossa.ingest.xlsx import APP, EXTENSION, parse_locator, xml_parser
from coglossa.schema import InlineMark, MarkKind, Report
from coglossa.segment.markup import char_keys
from coglossa.text import clean_text

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_RT_DOCUMENT = f"{_REL}/officeDocument"
_RT_SHEET = f"{_REL}/worksheet"
_RT_STRINGS = f"{_REL}/sharedStrings"
_TEXT_TYPES = ("s", "inlineStr", "str")
# Each kind of formatting: the run property that turns it on
_PROPS = (
    (MarkKind.BOLD, "b", None),
    (MarkKind.ITALIC, "i", None),
    (MarkKind.UNDERLINE, "u", None),
    (MarkKind.SUPERSCRIPT, "vertAlign", "superscript"),
    (MarkKind.SUBSCRIPT, "vertAlign", "subscript"),
)


def _q(tag: str) -> str:
    return f"{{{_MAIN}}}{tag}"


class _Package:
    def __init__(self, data: bytes) -> None:
        self.zip = zipfile.ZipFile(io.BytesIO(data))
        self.changed: dict[str, bytes] = {}
        self.trees: dict[str, Any] = {}

    def xml(self, name: str) -> Any:
        if name not in self.trees:
            self.trees[name] = etree.fromstring(self.zip.read(name), xml_parser())
        return self.trees[name]

    def rels(self, name: str) -> list[tuple[str, str, str]]:
        """(id, type, part name) of the internal relationships of part `name`."""
        folder, base = posixpath.split(name)
        rels_name = posixpath.join(folder, "_rels", f"{base}.rels")
        if rels_name not in self.zip.namelist():
            return []
        out = []
        for rel in self.xml(rels_name).iter(f"{{{_PKG_REL}}}Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            path = target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
            out.append((rel.get("Id", ""), rel.get("Type", ""), posixpath.normpath(path)))
        return out

    def save(self) -> bytes:
        for name, tree in self.trees.items():
            if name in self.changed:
                self.changed[name] = etree.tostring(
                    tree, xml_declaration=True, encoding="UTF-8", standalone=True
                )
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for info in self.zip.infolist():
                z.writestr(info, self.changed.get(info.filename) or self.zip.read(info))
        return out.getvalue()


def _string_items(c: Any, shared: list[Any]) -> Any | None:
    """The element holding a text cell's text: its shared string, or its own inline string."""
    if c.get("t") == "s":
        v = c.find(_q("v"))
        try:
            return shared[int(v.text)]
        except (AttributeError, IndexError, TypeError, ValueError):
            return None
    return c.find(_q("is"))


def _base_props(item: Any | None) -> Any | None:
    """The look of the cell's first original run, without the formatting the marks set."""
    rpr = None if item is None else item.find(f"{_q('r')}/{_q('rPr')}")
    if rpr is None:
        return None
    rpr = deepcopy(rpr)
    for child in list(rpr):
        if child.tag in {_q(tag) for _, tag, _ in _PROPS}:
            rpr.remove(child)
    return rpr


def _text_element(parent: Any, text: str) -> None:
    t = etree.SubElement(parent, _q("t"))
    t.text = text
    if text != text.strip():
        t.set(_XML_SPACE, "preserve")


def _inline(text: str, marks: list[InlineMark], base: Any | None) -> Any:
    inline = etree.Element(_q("is"))
    if not marks and base is None:
        _text_element(inline, text)
        return inline
    keys = char_keys(len(text), marks)
    start = 0
    while start < len(text):
        end = start + 1
        while end < len(text) and keys[end] == keys[start]:
            end += 1
        r = etree.SubElement(inline, _q("r"))
        rpr = deepcopy(base) if base is not None else etree.Element(_q("rPr"))
        kinds = {kind for kind, _ in keys[start]}
        for kind, tag, value in _PROPS:
            if kind in kinds and rpr.find(_q(tag)) is None:
                prop = etree.SubElement(rpr, _q(tag))
                if value is not None:
                    prop.set("val", value)
        if len(rpr):
            r.append(rpr)
        _text_element(r, text[start:end])
        start = end
    return inline


def _write_cell(c: Any, inline: Any | None) -> None:
    """The cell holds `inline`; None leaves it empty (it keeps its look)."""
    for child in list(c):
        if child.tag in (_q("v"), _q("is")):
            c.remove(child)
    if inline is None:
        c.attrib.pop("t", None)
        return
    ext = c.find(_q("extLst"))
    if ext is not None:
        ext.addprevious(inline)
    else:
        c.append(inline)
    c.set("t", "inlineStr")


def translated_xlsx(report: Report, original: bytes) -> bytes:
    check_original(report, original, "xlsx", APP, EXTENSION)
    try:
        return _translated(report, original)
    except ExportError:
        raise
    except Exception as e:  # malformed markup the ingestor's library read past
        raise ExportError(
            f"{source_name(report).name} could not be written back as {a_file(APP)}: part of it "
            "is damaged"
        ) from e


def _translated(report: Report, original: bytes) -> bytes:
    package = _Package(original)
    workbook = next(p for _, t, p in package.rels("") if t == _RT_DOCUMENT)
    rels = package.rels(workbook)
    by_id = {rid: path for rid, t, path in rels if t == _RT_SHEET}
    sheets = {
        s.get("name"): by_id.get(s.get(f"{{{_REL}}}id", ""))
        for s in package.xml(workbook).iter(_q("sheet"))
    }
    strings = next((p for _, t, p in rels if t == _RT_STRINGS), None)
    shared = list(package.xml(strings).iter(_q("si"))) if strings else []

    wanted: dict[str, dict[str, Any]] = {}  # sheet part -> cell -> its block
    for block in blocks_in_order(report):
        where = parse_locator(block.provenance.locator)
        part = sheets.get(where[0]) if where is not None else None
        if where is None or part is None:
            raise mismatch(report, APP)
        wanted.setdefault(part, {})[where[1]] = block
    texts = translations(report)
    for part, cells in wanted.items():
        found = 0
        for c in package.xml(part).iter(_q("c")):
            block = cells.get(c.get("r", ""))
            if block is None:
                continue
            if c.get("t") not in _TEXT_TYPES or c.find(_q("f")) is not None:
                raise mismatch(report, APP)
            item = texts.get(block.id)
            text, marks = (clean_text(item.text), item.marks) if item else ("", [])
            base = _base_props(_string_items(c, shared))
            _write_cell(c, _inline(text, marks, base) if text else None)
            package.changed[part] = b""
            found += 1
        if found != len(cells):
            raise mismatch(report, APP)
    if strings:
        _empty_unused(package, strings, shared, sheets.values())
    return package.save()


def _empty_unused(package: _Package, strings: str, shared: list[Any], sheets: Any) -> None:
    """Shared texts no cell of any sheet uses any more are emptied (their indexes stay)."""
    used: set[int] = set()
    refs = 0
    for part in sheets:
        if part is None:
            continue
        for v in package.xml(part).iterfind(f".//{_q('c')}[@t='s']/{_q('v')}"):
            refs += 1
            if (v.text or "").strip().isdigit():
                used.add(int(v.text))
    emptied = False
    for n, si in enumerate(shared):
        if n not in used and (len(si) != 1 or si[0].tag != _q("t") or si[0].text):
            for child in list(si):
                si.remove(child)
            etree.SubElement(si, _q("t"))
            emptied = True
    if emptied:
        root = package.xml(strings)
        if root.get("count") is not None:
            root.set("count", str(refs))
        package.changed[strings] = b""
