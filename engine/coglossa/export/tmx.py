"""A translation memory in TMX 1.4b (CGL-89), so checked work can go back into the tools
translators already use. Each span becomes one translation unit: its source text and its
translation, tagged with the run's languages. `approved` limits the memory to the passages a
person accepted or edited, with the text the person approved (`coglossa.review.approved_texts`).

The file is built with lxml, which escapes the text; characters XML 1.0 cannot hold (most
control characters, lone surrogates, U+FFFE and U+FFFF) are dropped first, since a valid file
matters more to a translation memory than an invisible character."""

import re
from collections.abc import Mapping
from datetime import UTC

from lxml import etree

import coglossa
from coglossa.export.rows import export_rows
from coglossa.schema import Report

DOCTYPE = '<!DOCTYPE tmx SYSTEM "tmx14.dtd">'
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
UNDETERMINED = "und"  # BCP-47's tag for a language that is not known
_NOT_XML = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
_TAG = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


def xml_text(text: str) -> str:
    return _NOT_XML.sub("", text)


def tmx_lang(code: str) -> str:
    """The run's language code as a BCP-47 tag, which TMX's xml:lang expects. Runs store what
    the person picked or the profiler detected: BCP-47-like codes that may use "_" and any
    case (pt_br). The separators become "-" and the subtags take BCP-47's conventional case:
    language lowercase, script title case, region uppercase (pt-BR, zh-Hant-TW), anything after
    a singleton such as x- lowercase. A code that is not a tag at all becomes "und"."""
    tag = code.strip().replace("_", "-")
    if not _TAG.match(tag):
        return UNDETERMINED
    first, *rest = tag.split("-")
    parts, extension = [first.lower()], False
    for part in rest:
        extension = extension or len(part) == 1
        if not extension and len(part) == 4 and part.isalpha():
            parts.append(part.title())
        elif not extension and ((len(part) == 2 and part.isalpha()) or part.isdigit()):
            parts.append(part.upper())
        else:
            parts.append(part.lower())
    return "-".join(parts)


def _tuv(tu: etree._Element, lang: str, text: str) -> None:
    tuv = etree.SubElement(tu, "tuv", {XML_LANG: lang})
    etree.SubElement(tuv, "seg").text = text


def tmx_bytes(report: Report, *, approved: Mapping[str, str] | None = None) -> bytes:
    """One unit per span in reading order, or, with `approved`, per approved span. A unit's
    segtype is "sentence" when its span covers one sentence and "block" when it covers more;
    the header says "block" if any unit does. A span whose source or translation is blank
    (nothing a memory could match or offer) is left out."""
    source_lang = tmx_lang(report.profile.source_lang)
    target_lang = tmx_lang(report.profile.target_lang)
    created = report.created_at
    created = created.replace(tzinfo=UTC) if created.tzinfo is None else created.astimezone(UTC)
    root = etree.Element("tmx", version="1.4")
    header = etree.SubElement(
        root,
        "header",
        {
            "creationtool": "Coglossa",
            "creationtoolversion": coglossa.__version__,
            "segtype": "sentence",
            "o-tmf": "Coglossa",
            "adminlang": "en",
            "srclang": source_lang,
            "datatype": "plaintext",
            "creationdate": created.strftime("%Y%m%dT%H%M%SZ"),
        },
    )
    body = etree.SubElement(root, "body")
    for row in export_rows(report):
        if approved is None:
            translation = row.span.text
        elif row.span.id in approved:
            translation = approved[row.span.id]
        else:
            continue
        source, target = xml_text(row.source), xml_text(translation)
        if not source.strip() or not target.strip():
            continue
        segtype = "sentence" if row.span.start == row.span.end else "block"
        if segtype == "block":
            header.set("segtype", "block")
        tu = etree.SubElement(body, "tu", tuid=row.span.id, segtype=segtype)
        _tuv(tu, source_lang, source)
        _tuv(tu, target_lang, target)
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True, doctype=DOCTYPE
    )
