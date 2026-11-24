"""Bring an existing glossary into a set guide (CGL-88): a CSV or TSV spreadsheet, or a TBX
termbase, becomes glossary entries of one language's guide. Pure functions: reading the file,
comparing its terms with the guide, and merging them in. `catalog` does the database part.

Spreadsheets: the term in the first column and its translation in the second; further columns
are ignored. A first row that names the columns ("term, translation", "source, target", or the
languages, as codes or English names) is a header and is skipped; the columns it names (the
guide's languages, else "term" and "translation" or their like) are used, in whatever order,
and a column of ids or notes never is. A header may also name a column of other accepted forms
of the translation ("forms", "other forms", "accepted forms"…) and one of other forms of the
term ("source forms"), several forms to a cell separated by commas, semicolons or "|"
(CGL-109). At most 20,000 terms, read until the limit is passed,
and each term, translation and note at most 2,000 characters, as the guide editor saves them;
the delimiter is guessed from the first lines. UTF-8 with or without a byte-order mark,
or UTF-16 with one (Excel's "Unicode text"). A .tsv file is split on tabs; any other file on
commas, semicolons or tabs, whichever the file uses.

TBX: each entry (termEntry, or conceptEntry in TBX v3) gives its source-language terms the
target-language term (the first one not marked deprecated or superseded); its other target
terms not so marked become the entry's accepted forms (CGL-109). Language codes
match case-insensitively with - and _ alike; failing an exact match, a file's bare language
("pt") serves a regional guide ("pt-BR"), and a regional one serves a bare guide, but sibling
regions ("pt-PT" for "pt-BR") never do. Without a known source language, the file's one other
language is taken; with several, the language of the set's documents (`source_hint`), else the
file's main language (the root's xml:lang). The XML is read without a DTD, entities or network
access, and a file that declares its own entities is refused.

Every problem is a GlossaryFileError naming the line, raised before anything is written."""

import codecs
import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from lxml import etree

from coglossa.ingest.base import IngestError, error_reason
from coglossa.languages import LANGUAGE_NAMES, label
from coglossa.schema import Entity, GlossaryEntry, Guide
from coglossa.schema.guide import tidy_forms
from coglossa.text import clean_text, entry_key

MAX_GLOSSARY_MB = 20  # a termbase of a hundred thousand entries is a few MB
MAX_TERMS = 20_000
# Each term, translation and note: what a guide edit may hold (coglossa_api.models.SHORT_MAX),
# so an imported guide can always be saved again (CGL-115).
MAX_FIELD_CHARS = 2_000

GlossaryFormat = Literal["csv", "tsv", "tbx"]
_FORMATS: dict[str, GlossaryFormat] = {
    ".csv": "csv",
    ".txt": "csv",
    ".tsv": "tsv",
    ".tab": "tsv",
    ".tbx": "tbx",
    ".xml": "tbx",
}
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_RETIRED = ("deprecated", "superseded", "obsolete")
# Header cells that name the term column, the translation column, or neither.
_TERM_WORDS = frozenset({"term", "terms", "source", "source term", "source text", "word", "words"})
_TRANSLATION_WORDS = frozenset(
    {"target", "target term", "target text", "translation", "translations", "rendering"}
)
_HEADER_WORDS = _TERM_WORDS | _TRANSLATION_WORDS | {"id"}
_UNUSED_WORDS = frozenset({"id", "notes", "note", "comment", "comments"})
# CGL-109: columns of other accepted forms of the translation, and of other forms of the term.
_FORM_WORDS = frozenset(
    {
        "forms",
        "other forms",
        "accepted forms",
        "inflected forms",
        "target forms",
        "translation forms",
        "variants",
    }
)
_SOURCE_FORM_WORDS = frozenset({"source forms", "term forms", "other source forms"})
_FORM_SEPARATORS = re.compile(r"[,;|]")
_SNIFF_LINES = 20  # csv.Sniffer takes seconds on some text: it sees a few lines only
_SNIFF_CHARS = 4096


class GlossaryFileError(IngestError):
    """The glossary file cannot be read; the message is plain and names the line."""


@dataclass(frozen=True)
class ImportedTerm:
    term: str
    rendering: str
    line: int  # where the term is in the file
    notes: str = ""
    forms: tuple[str, ...] = ()  # other accepted forms of the rendering (CGL-109)
    source_forms: tuple[str, ...] = ()  # other forms of the term


@dataclass(frozen=True)
class ParsedGlossary:
    format: GlossaryFormat
    source_lang: str | None  # TBX: the language the terms were read from
    terms: list[ImportedTerm]
    skipped_entries: int = 0  # TBX entries without a term in one of the two languages


def glossary_format(filename: str) -> GlossaryFormat:
    dot = filename.rfind(".")
    fmt = _FORMATS.get(filename[dot:].casefold()) if dot >= 0 else None
    if fmt is None:
        raise GlossaryFileError(
            f"{filename or 'the file'} is not a glossary Coglossa can read: "
            "choose a .csv, .tsv or .tbx file"
        )
    return fmt


def parse_glossary(
    data: bytes,
    filename: str,
    *,
    target_lang: str,
    source_lang: str | None,
    source_hint: str | None = None,
) -> ParsedGlossary:
    """`source_hint`: the language the set's documents are in, which picks a TBX file's source
    language among several when `source_lang` is not given."""
    fmt = glossary_format(filename)
    if len(data) > MAX_GLOSSARY_MB * 1024 * 1024:
        raise GlossaryFileError(
            f"the file is larger than {MAX_GLOSSARY_MB} MB; split it into smaller files"
        )
    if fmt == "tbx":
        parsed = _parse_tbx(
            data, target_lang=target_lang, source_lang=source_lang, source_hint=source_hint
        )
    else:
        terms = _parse_table(
            _decode(data), "\t" if fmt == "tsv" else None, target_lang, source_lang
        )
        parsed = ParsedGlossary(format=fmt, source_lang=source_lang, terms=terms)
    if not parsed.terms:
        raise GlossaryFileError("the file has no terms in it")
    _check_lengths(parsed.terms)
    return parsed


def _check_lengths(terms: list[ImportedTerm]) -> None:
    for t in terms:
        fields = [(t.term, "term"), (t.rendering, "translation"), (t.notes, "note")]
        fields += [(f, "form") for f in (*t.forms, *t.source_forms)]
        for value, what in fields:
            if len(value) > MAX_FIELD_CHARS:
                raise GlossaryFileError(
                    f"line {t.line} has a {what} of {len(value):,} characters: keep each term, "
                    f"translation and note to {MAX_FIELD_CHARS:,} characters or fewer"
                )


def _check_count(terms: list[ImportedTerm]) -> None:
    """Called as terms are read, so a file over the limit stops being read at once."""
    if len(terms) > MAX_TERMS:
        raise GlossaryFileError(
            f"the file has more than {MAX_TERMS:,} terms; split it into smaller files"
        )


# Languages ---------------------------------------------------------------------------------


def _key(code: str) -> str:
    return code.strip().casefold().replace("_", "-")


def language_rank(code: str, wanted: str) -> int | None:
    """How well a file's language code serves `wanted`: 0 the same, 1 the file has the bare
    language of a regional `wanted`, 2 `wanted` is bare and the file regional; None: another
    language, or a sibling region."""
    have, want = _key(code), _key(wanted)
    if have == want:
        return 0
    base = want.split("-")[0]
    if have.split("-")[0] != base:
        return None
    if have == base:
        return 1
    return 2 if want == base else None


def _names_language(cell: str, wanted: str | None) -> bool:
    if wanted is None:
        return False
    name = LANGUAGE_NAMES.get(_key(wanted).split("-")[0], "")
    return language_rank(cell, wanted) is not None or (bool(name) and cell == name.casefold())


_NAMES = frozenset(n.casefold() for n in LANGUAGE_NAMES.values())


def _is_language(cell: str) -> bool:
    """A language code or English language name, any language."""
    return _key(cell).split("-")[0] in LANGUAGE_NAMES or cell in _NAMES


# Spreadsheets ------------------------------------------------------------------------------


def _decode(data: bytes) -> str:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError as e:
            line = data[: e.start].decode("utf-16", errors="replace").count("\n") + 1
            raise _not_text(line) from e
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise _not_text(data.count(b"\n", 0, e.start) + 1) from e


def _not_text(line: int) -> GlossaryFileError:
    return GlossaryFileError(
        f"line {line} is not readable text: save the file as UTF-8 "
        "(in Excel, “CSV UTF-8”) and try again"
    )


def _delimiter(text: str) -> str:
    lines = text[:_SNIFF_CHARS].splitlines(keepends=True)
    sample = "".join(lines[:_SNIFF_LINES])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        first = next((line for line in sample.splitlines() if line.strip()), "")
        return max("\t;,", key=first.count)  # ties: tab, then semicolon, then comma


@dataclass(frozen=True)
class _Columns:
    term: int
    translation: int
    forms: int | None = None  # CGL-109: only a header names these
    source_forms: int | None = None


def _forms(cells: list[str], column: int | None) -> tuple[str, ...]:
    if column is None or column >= len(cells):
        return ()
    return tuple(tidy_forms(_FORM_SEPARATORS.split(cells[column])))


def _header_columns(cells: list[str], target_lang: str, source_lang: str | None) -> _Columns | None:
    """The term, translation and form columns when `cells` is a header row; None when it is
    not."""
    folded = [c.strip().casefold() for c in cells]
    first = folded[:2]
    # Every cell a label, and one a header word or the guide's language: a pair of terms that
    # happen to be language codes ("de", "es") stays a pair of terms.
    labels = all(c in _HEADER_WORDS or _is_language(c) for c in first)
    named = any(
        c in _HEADER_WORDS or _names_language(c, target_lang) or _names_language(c, source_lang)
        for c in first
    )
    if len(first) < 2 or not labels or not named:
        return None

    def column(found: Callable[[str], bool], other: int | None = None) -> int | None:
        return next((i for i, c in enumerate(folded) if i != other and found(c)), None)

    forms = column(lambda c: c in _FORM_WORDS)
    source_forms = column(lambda c: c in _SOURCE_FORM_WORDS)
    unused = _UNUSED_WORDS | _FORM_WORDS | _SOURCE_FORM_WORDS

    # The translation: the guide's language, else a word for it; the term: the source
    # language, else a word for it. A column of ids or notes is neither.
    target = column(lambda c: _names_language(c, target_lang))
    if target is None:
        target = column(lambda c: c in _TRANSLATION_WORDS)
    source = column(lambda c: _names_language(c, source_lang), target)
    if source is None:
        source = column(lambda c: c in _TERM_WORDS, target)
    free = [i for i, c in enumerate(folded) if c not in unused and i not in (source, target)]
    if source is None:
        source = free.pop(0) if free else (1 if target == 0 else 0)
    if target is None:
        after = [i for i in free if i > source] or free
        target = after[0] if after else source + 1  # an unnamed column after the term
    return _Columns(source, target, forms, source_forms)


def _parse_table(
    text: str, delimiter: str | None, target_lang: str, source_lang: str | None
) -> list[ImportedTerm]:
    reader = csv.reader(
        io.StringIO(text, newline=""), delimiter=delimiter or _delimiter(text), strict=True
    )
    terms: list[ImportedTerm] = []
    columns: _Columns | None = None
    line = 1
    rows = 0
    while True:
        rows += 1
        if rows > 2 * MAX_TERMS:  # blank lines too: reading 20 MB of them takes seconds
            raise GlossaryFileError(
                f"the file has more than {2 * MAX_TERMS:,} lines; split it into smaller files"
            )
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as e:
            raise GlossaryFileError(
                f"line {line} cannot be read ({error_reason(e)}): check its quotation marks"
            ) from e
        start, line = line, reader.line_num + 1
        cells = [clean_text(c).strip() for c in row]
        if not any(cells):
            continue
        if columns is None:
            columns = _header_columns(cells, target_lang, source_lang)
            if columns is not None:
                continue
            columns = _Columns(0, 1)
        term_col, rendering_col = columns.term, columns.translation
        term = cells[term_col] if term_col < len(cells) else ""
        rendering = cells[rendering_col] if rendering_col < len(cells) else ""
        if len(cells) < 2:
            raise GlossaryFileError(
                f"line {start} has only one column: each line needs a term, then its "
                "translation, separated by a comma, semicolon or tab"
            )
        if not term:
            raise GlossaryFileError(f"line {start} has a translation but no term")
        if not rendering:
            raise GlossaryFileError(f"line {start} has the term “{term}” but no translation")
        terms.append(
            ImportedTerm(
                term=term,
                rendering=rendering,
                line=start,
                forms=_forms(cells, columns.forms),
                source_forms=_forms(cells, columns.source_forms),
            )
        )
        _check_count(terms)
    return terms


# TBX ---------------------------------------------------------------------------------------


def _text(el: etree._Element) -> str:
    return " ".join(clean_text("".join(el.itertext())).split())


@dataclass(frozen=True)
class _Term:
    text: str
    line: int
    retired: bool


def _terms(lang_set: etree._Element) -> list[_Term]:
    found = []
    for el in lang_set.iter("{*}term"):  # tag filters run in lxml: any namespace, or none
        if not (text := _text(el)):
            continue
        group = el.getparent()
        status = " ".join(
            _text(n).casefold()
            for n in (group.iterchildren("{*}termNote") if group is not None else [])
            if n.get("type") == "administrativeStatus"
        )
        found.append(_Term(text, el.sourceline or 0, any(w in status for w in _RETIRED)))
    return found


def _definition(entry: etree._Element) -> str:
    for el in entry.iter("{*}descrip"):
        if el.get("type") == "definition" and (text := _text(el)):
            return text
    return ""


def _pick(lang_sets: list[tuple[str, etree._Element]], wanted: str) -> etree._Element | None:
    ranked = [
        (rank, i, el)
        for i, (code, el) in enumerate(lang_sets)
        if (rank := language_rank(code, wanted)) is not None
    ]
    return min(ranked, key=lambda r: (r[0], r[1]))[2] if ranked else None


def _read_xml(data: bytes) -> etree._Element:
    try:
        # A parser per file: files are read in worker threads, and a parser is not thread-safe.
        parser = etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
        )
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as e:
        raise GlossaryFileError(
            f"line {e.lineno or 1} is not valid XML ({e.msg}); export the termbase as TBX again"
        ) from e
    dtd = root.getroottree().docinfo.internalDTD
    if dtd is not None and any(True for _ in dtd.iterentities()):
        raise GlossaryFileError(
            "the file declares its own XML entities, which Coglossa does not read; "
            "export the termbase as TBX again"
        )
    return root


_ENTRIES = ("{*}termEntry", "{*}conceptEntry")  # TBX v2, v3; any namespace, or none


def _lang_sets(entry: etree._Element) -> list[tuple[str, etree._Element]]:
    found = []
    for lang_set in entry.iterchildren("{*}langSet", "{*}langSec"):  # TBX v2, v3
        code = lang_set.get(_XML_LANG) or lang_set.get("lang")
        if not code:
            raise GlossaryFileError(
                f"line {lang_set.sourceline}: a language section has no "
                "xml:lang, so its language is unknown"
            )
        found.append((code, lang_set))
    return found


def _source_language(root: etree._Element, target_lang: str, hint: str | None) -> str:
    others: dict[str, str] = {}
    for el in root.iter(*_ENTRIES):
        for code, _ in _lang_sets(el):
            if language_rank(code, target_lang) is None:
                others.setdefault(_key(code), code)
    if len(others) == 1:
        return next(iter(others.values()))
    if hint is not None:
        ranked = sorted(
            (rank, c) for c in others.values() if (rank := language_rank(c, hint)) is not None
        )
        if ranked and (len(ranked) == 1 or ranked[0][0] < ranked[1][0]):
            return ranked[0][1]  # one best match: "pt-BR" and "pt-PT" for "pt" are not
    main = root.get(_XML_LANG)
    if main and _key(main) in others:
        return others[_key(main)]
    if not others:
        raise GlossaryFileError(f"the file has terms only in {label(target_lang)}")
    found = ", ".join(label(c) for c in sorted(others.values(), key=_key))
    raise GlossaryFileError(
        f"the file has terms in several languages ({found}) and does not say which one is the "
        "source language: name it, or export the termbase with only the two languages"
    )


def _parse_tbx(
    data: bytes, *, target_lang: str, source_lang: str | None, source_hint: str | None
) -> ParsedGlossary:
    root = _read_xml(data)
    if next(root.iter(*_ENTRIES), None) is None:
        raise GlossaryFileError(
            "the file has no term entries (termEntry or conceptEntry): is it a TBX file?"
        )
    source = source_lang or _source_language(root, target_lang, source_hint)
    terms: list[ImportedTerm] = []
    skipped = 0
    # One entry at a time, so a file over the term limit stops being read at the limit.
    for entry in root.iter(*_ENTRIES):
        lang_sets = _lang_sets(entry)
        source_set, target_set = _pick(lang_sets, source), _pick(lang_sets, target_lang)
        renderings = _terms(target_set) if target_set is not None else []
        accepted = [t for t in renderings if not t.retired]
        rendering = accepted[0] if accepted else None
        sources = _terms(source_set) if source_set is not None else []
        if rendering is None or not sources or source_set is target_set:
            skipped += 1
            continue
        notes = _definition(entry)
        forms = tuple(tidy_forms([t.text for t in accepted[1:] if t.text != rendering.text]))
        terms.extend(ImportedTerm(t.text, rendering.text, t.line, notes, forms) for t in sources)
        _check_count(terms)
    if not terms:
        raise GlossaryFileError(
            f"no entry of the file has terms in both {label(source)} and {label(target_lang)}"
        )
    return ParsedGlossary(format="tbx", source_lang=source, terms=terms, skipped_entries=skipped)


# Comparing and merging ---------------------------------------------------------------------


class ImportStatus(StrEnum):
    NEW = "new"  # not in the guide
    IDENTICAL = "identical"  # in the guide with the same translation
    CONFLICT = "conflict"  # in the guide with another translation
    DUPLICATE = "duplicate"  # an earlier line of the file has the same term; that one is used


@dataclass(frozen=True)
class PlannedTerm:
    term: ImportedTerm
    status: ImportStatus
    current: str | None = None  # the guide's translation, or the earlier line's (duplicate)
    current_locked: bool = False  # whether the guide's entry is locked
    first_line: int | None = None  # duplicate: the line whose term is used


def _by_key(
    glossary: list[GlossaryEntry], entities: list[Entity]
) -> dict[str, GlossaryEntry | Entity]:
    """Terms and names by key, as the guide merges them (CGL-28): one key, one rendering."""
    found: dict[str, GlossaryEntry | Entity] = {}
    for g in glossary:
        found.setdefault(entry_key(g.term), g)
    for e in entities:
        found.setdefault(entry_key(e.name), e)
    return found


def plan_import(guide: Guide | None, terms: list[ImportedTerm]) -> list[PlannedTerm]:
    existing = _by_key(guide.glossary, guide.entities) if guide else {}
    seen: dict[str, ImportedTerm] = {}
    plan: list[PlannedTerm] = []
    for t in terms:
        key = entry_key(t.term)
        if (first := seen.get(key)) is not None:
            plan.append(PlannedTerm(t, ImportStatus.DUPLICATE, first.rendering, False, first.line))
            continue
        seen[key] = t
        if (have := existing.get(key)) is None:
            plan.append(PlannedTerm(t, ImportStatus.NEW))
            continue
        same = have.rendering.strip() == t.rendering.strip()
        status = ImportStatus.IDENTICAL if same else ImportStatus.CONFLICT
        plan.append(PlannedTerm(t, status, have.rendering, have.locked))
    return plan


@dataclass(frozen=True)
class MergeCounts:
    added: int = 0
    updated: int = 0  # conflicts whose guide translation the file replaced
    locked: int = 0  # imported entries that were not locked and now are
    kept: int = 0  # conflicts left as the guide has them


def merge_import(
    guide: Guide | None, plan: list[PlannedTerm], *, lock_all: bool, overwrite_conflicts: bool
) -> tuple[Guide, MergeCounts]:
    """`guide` with the planned terms merged in. A conflict replaces the guide's translation
    only with `overwrite_conflicts`, a locked entry's too; nothing else in the guide changes."""
    base = guide or Guide(summary="", style_guide="")
    glossary = [g.model_copy() for g in base.glossary]
    entities = [e.model_copy() for e in base.entities]
    existing = _by_key(glossary, entities)
    added = updated = locked = kept = 0
    for p in plan:
        t = p.term
        if p.status is ImportStatus.DUPLICATE:
            continue
        if p.status is ImportStatus.NEW:
            glossary.append(
                GlossaryEntry(
                    term=t.term,
                    rendering=t.rendering,
                    notes=t.notes,
                    locked=lock_all,
                    forms=list(t.forms),
                    source_forms=list(t.source_forms),
                )
            )
            added += 1
            locked += lock_all
            continue
        entry = existing[entry_key(t.term)]
        if p.status is ImportStatus.CONFLICT:
            if not overwrite_conflicts:
                kept += 1
                continue
            entry.rendering = t.rendering
            entry.forms = []  # the old translation's forms; the file's are added below
            if t.notes and isinstance(entry, GlossaryEntry):
                entry.notes = t.notes
            updated += 1
        # CGL-109: the file's forms join the entry's (an identical one's, or a replaced one's)
        entry.forms = tidy_forms([*entry.forms, *t.forms])
        entry.source_forms = tidy_forms([*entry.source_forms, *t.source_forms])
        if lock_all and not entry.locked:
            entry.locked = True
            locked += 1
    merged = base.model_copy(update={"glossary": glossary, "entities": entities})
    return merged, MergeCounts(added=added, updated=updated, locked=locked, kept=kept)
