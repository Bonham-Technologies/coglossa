"""CGL-88: reading CSV, TSV and TBX glossaries, and comparing and merging them with a guide."""

import codecs
import csv

import pytest

from coglossa import glossary_import
from coglossa.glossary_import import (
    MAX_FIELD_CHARS,
    MAX_TERMS,
    GlossaryFileError,
    ImportedTerm,
    ImportStatus,
    glossary_format,
    language_rank,
    merge_import,
    parse_glossary,
    plan_import,
)
from coglossa.ingest.base import IngestError
from coglossa.schema import Entity, GlossaryEntry, Guide


def _parse(data: bytes, name: str = "terms.csv", *, target: str = "en", source: str | None = "fr"):
    return parse_glossary(data, name, target_lang=target, source_lang=source)


def _pairs(parsed) -> list[tuple[str, str, int]]:
    return [(t.term, t.rendering, t.line) for t in parsed.terms]


# Spreadsheets ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b"chat,cat\nchien,dog\n", "terms.csv"),
        (codecs.BOM_UTF8 + b"chat,cat\nchien,dog\n", "terms.csv"),
        (b"chat;cat\r\nchien;dog\r\n", "terms.csv"),
        (b"chat\tcat\nchien\tdog", "terms.tsv"),
        (b"chat\tcat\nchien\tdog\n", "terms.txt"),
        (b"chat,cat,animal,x\nchien,dog,animal,y\n", "terms.csv"),  # extra columns ignored
        ("chat\tcat\nchien\tdog\n".encode("utf-16"), "terms.txt"),  # Excel "Unicode text"
    ],
)
def test_two_columns_with_any_delimiter_and_encoding(data, name):
    parsed = _parse(data, name)
    assert _pairs(parsed) == [("chat", "cat", 1), ("chien", "dog", 2)]
    assert parsed.format == ("tsv" if name.endswith(".tsv") else "csv")


@pytest.mark.parametrize(
    "header",
    [b"term,translation", b"Source,Target", b"fr,en", b"French,English", b"FR_fr,EN"],
)
def test_a_header_row_is_skipped(header):
    parsed = _parse(header + b"\nchat,cat\n")
    assert _pairs(parsed) == [("chat", "cat", 2)]


def test_a_header_naming_the_languages_picks_the_columns():
    parsed = _parse(b"id,en,fr\n1,cat,chat\n2,dog,chien\n")
    assert _pairs(parsed) == [("chat", "cat", 2), ("chien", "dog", 3)]
    reversed_ = _parse(b"English,French\ncat,chat\n", source=None)
    assert _pairs(reversed_) == [("chat", "cat", 2)]


@pytest.mark.parametrize(
    ("header", "row"),
    [
        (b"id,term,translation", b"1,chat,cat"),
        (b"ID;Source;Target;Notes", b"7;chat;cat;x"),
        (b"translation,term", b"cat,chat"),
        (b"id,translation,source term", b"1,cat,chat"),
        (b"Term,ID,Translation", b"chat,1,cat"),
        (b"id,en,term", b"1,cat,chat"),  # the target named by its language
        (b"id,term,meaning", b"1,chat,cat"),  # the translation: the next unnamed column
    ],
)
def test_a_header_naming_its_columns_picks_them(header, row):
    """An id column, or the translation first, is not the term (QC on CGL-88)."""
    assert _pairs(_parse(header + b"\n" + row + b"\n")) == [("chat", "cat", 2)]


def test_a_first_row_of_terms_is_not_a_header():
    assert _pairs(_parse(b"chat,cat\n")) == [("chat", "cat", 1)]
    # language codes, but neither is the guide's: a term pair
    assert _pairs(_parse(b"de,es\n", target="en")) == [("de", "es", 1)]


def test_quoted_cells_blank_lines_and_spaces():
    data = b'"chat, noir","black cat"\n\n  chien  , dog \n"line\nbreak",x\nfin,end\n'
    parsed = _parse(data)
    assert _pairs(parsed) == [
        ("chat, noir", "black cat", 1),
        ("chien", "dog", 3),
        ("line\nbreak", "x", 4),
        ("fin", "end", 6),
    ]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"chat,cat\nchien\n", "line 2 has only one column"),
        (b"chat,cat\nchien,\n", "line 2 has the term “chien” but no translation"),
        (b"chat,cat\n,dog\n", "line 2 has a translation but no term"),
        (b'chat,cat\n"x"y,z\n', "line 2 cannot be read"),
        (b'chat,cat\nchien,"dog\n', "line 2 cannot be read"),
        (b"chat,cat\n\xff\xfe,x\n", "line 2 is not readable text"),
        (b"\n\n", "the file has no terms"),
    ],
)
def test_a_malformed_spreadsheet_names_the_line(data, message):
    with pytest.raises(GlossaryFileError, match=message) as e:
        _parse(data)
    assert isinstance(e.value, IngestError)  # shown to the person as it is


def test_a_broken_utf16_file_names_the_line():
    data = "chat\tcat\n".encode("utf-16") + b"\x00\xd8"  # a lone surrogate
    with pytest.raises(GlossaryFileError, match="line 2 is not readable text"):
        _parse(data, "terms.txt")


def test_the_file_type_comes_from_its_name():
    assert glossary_format("Termbase.TBX") == "tbx"
    assert glossary_format("a.tab") == "tsv"
    for name in ("terms.xlsx", "terms", ""):
        with pytest.raises(GlossaryFileError, match="choose a .csv, .tsv or .tbx file"):
            glossary_format(name)


def test_size_and_term_limits(monkeypatch):
    monkeypatch.setattr("coglossa.glossary_import.MAX_GLOSSARY_MB", 0)
    with pytest.raises(GlossaryFileError, match="larger than 0 MB"):
        _parse(b"chat,cat\n")
    monkeypatch.undo()
    many = "".join(f"t{i},r{i}\n" for i in range(MAX_TERMS + 1)).encode()
    with pytest.raises(GlossaryFileError, match="more than 20,000 terms"):
        _parse(many)


def test_reading_stops_at_the_term_limit(monkeypatch):
    """A file over the limit is refused as soon as the limit is passed, not after reading
    the rest: the broken last line is never reached (QC on CGL-88)."""
    monkeypatch.setattr("coglossa.glossary_import.MAX_TERMS", 2)
    with pytest.raises(GlossaryFileError, match="more than 2 terms"):
        _parse(b"a,b\nc,d\ne,f\ng\n")
    # Blank lines count too: 20 MB of them is a lot of reading for no terms.
    with pytest.raises(GlossaryFileError, match="more than 4 lines"):
        _parse(b"a,b\n" + b"\n" * 10 + b"g\n")


def test_a_termbase_stops_at_the_term_limit(monkeypatch):
    monkeypatch.setattr("coglossa.glossary_import.MAX_TERMS", 2)
    entry = (
        b'<termEntry><langSet xml:lang="fr"><term>chat%d</term></langSet>'
        b'<langSet xml:lang="en"><term>cat%d</term></langSet></termEntry>'
    )
    data = b"<martif>" + b"".join(entry % (i, i) for i in range(6)) + b"</martif>"
    read: list[object] = []
    real = glossary_import._definition
    monkeypatch.setattr("coglossa.glossary_import._definition", lambda e: read.append(e) or real(e))
    with pytest.raises(GlossaryFileError, match="more than 2 terms"):
        _parse(data, "a.tbx")
    assert len(read) == 3  # the entry that passed the limit, and no further


def test_the_delimiter_is_guessed_from_the_first_lines_only(monkeypatch):
    """csv.Sniffer is slow on some text (seconds for 64 KB): it sees a few lines, at most
    a few KB (QC on CGL-88)."""
    samples: list[str] = []
    real = csv.Sniffer.sniff
    monkeypatch.setattr(
        csv.Sniffer,
        "sniff",
        lambda self, s, delimiters=None: samples.append(s) or real(self, s, delimiters),
    )
    many = "".join(f"t{i},r{i}\n" for i in range(5000)).encode()
    assert len(_parse(many).terms) == 5000
    long_line = b"a,b" + b",x" * 50_000 + b"\nc,d\n"  # extra columns are ignored
    assert len(_parse(long_line).terms) == 2
    assert len(samples) == 2
    assert samples[0].count("\n") <= 20
    assert all(len(s) <= 4096 for s in samples)


def test_the_delimiter_falls_back_to_the_first_line():
    # One row: the sniffer cannot tell; the first line has one semicolon
    assert _pairs(_parse(b"chat;cat")) == [("chat", "cat", 1)]


# TBX ---------------------------------------------------------------------------------------

TBX = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE martif SYSTEM "TBXcoreStructV02.dtd">
<martif type="TBX" xml:lang="fr">
<text><body>
<termEntry id="1">
  <descrip type="definition">A small pet.</descrip>
  <langSet xml:lang="FR">
    <tig><term>chat</term></tig>
    <tig><term>minou</term></tig>
  </langSet>
  <langSet xml:lang="en">
    <tig><term>moggy</term>
      <termNote type="administrativeStatus">deprecatedTerm-admn-sts</termNote></tig>
    <tig><term>cat</term></tig>
  </langSet>
  <langSet xml:lang="de"><tig><term>Katze</term></tig></langSet>
</termEntry>
<termEntry id="2">
  <langSet xml:lang="fr"><ntig><termGrp><term>chien</term></termGrp></ntig></langSet>
  <langSet xml:lang="en-GB"><ntig><termGrp><term>dog</term></termGrp></ntig></langSet>
</termEntry>
<termEntry id="3"><langSet xml:lang="fr"><tig><term>seul</term></tig></langSet></termEntry>
</body></text>
</martif>
"""


def test_tbx_terms_of_the_guide_languages():
    parsed = _parse(TBX, "base.tbx")
    assert parsed.format == "tbx" and parsed.source_lang == "fr"
    assert _pairs(parsed) == [("chat", "cat", 8), ("minou", "cat", 9), ("chien", "dog", 19)]
    assert parsed.terms[0].notes == "A small pet." and parsed.terms[2].notes == ""
    assert parsed.skipped_entries == 1  # entry 3 has no English


def test_tbx_empty_terms_and_other_descriptions_are_passed_over():
    data = b"""<martif><termEntry>
<descrip type="context">Le chat dort.</descrip><descrip type="definition">A pet.</descrip>
<langSet xml:lang="fr"><tig><term/></tig><tig><term>chat</term></tig></langSet>
<langSet xml:lang="en"><tig><term> </term></tig><tig><term>cat</term></tig></langSet>
</termEntry></martif>"""
    parsed = _parse(data, "a.tbx")
    assert _pairs(parsed) == [("chat", "cat", 3)] and parsed.terms[0].notes == "A pet."


def test_tbx_v3_concept_entries():
    data = b"""<tbx xmlns="urn:iso:std:iso:30042:ed-2" type="TBX-Core" xml:lang="de">
<text><body><conceptEntry id="c1">
<langSec xml:lang="de"><termSec><term>Katze</term></termSec></langSec>
<langSec xml:lang="en"><termSec><term>cat</term></termSec></langSec>
</conceptEntry></body></text></tbx>"""
    parsed = _parse(data, "v3.tbx", source=None)
    assert parsed.source_lang == "de" and _pairs(parsed) == [("Katze", "cat", 3)]


def test_tbx_source_language_from_the_file():
    two = TBX.replace(b'<langSet xml:lang="de"><tig><term>Katze</term></tig></langSet>', b"")
    assert _parse(two, "a.tbx", source=None).source_lang == "FR"  # the one other language
    assert _parse(TBX, "a.tbx", source=None).source_lang == "FR"  # the file's main language
    no_main = TBX.replace(b'type="TBX" xml:lang="fr"', b'type="TBX"')
    with pytest.raises(
        GlossaryFileError, match=r"several languages \(German \(de\), French \(FR\)\)"
    ):
        _parse(no_main, "a.tbx", source=None)
    only_en = (
        b'<martif><termEntry><langSet xml:lang="en"><term>cat</term></langSet></termEntry></martif>'
    )
    with pytest.raises(GlossaryFileError, match="terms only in English"):
        _parse(only_en, "a.tbx", source=None)


def test_tbx_source_language_from_the_sets_documents():
    """A termbase in several languages: the set's documents pick the source, ahead of the
    file's main language; a hint that matches none, or two regions alike, does not."""
    no_main = TBX.replace(b'type="TBX" xml:lang="fr"', b'type="TBX"')

    def source(data: bytes, hint: str) -> str:
        parsed = parse_glossary(data, "a.tbx", target_lang="en", source_lang=None, source_hint=hint)
        return parsed.source_lang or ""

    assert source(no_main, "de-AT") == "de"
    assert source(TBX, "de") == "de"  # ahead of the file's xml:lang="fr"
    assert source(TBX, "ja") == "FR"  # no match: the file's main language
    regions = no_main.replace(b'xml:lang="FR"', b'xml:lang="de-CH"').replace(
        b'xml:lang="de"', b'xml:lang="de-DE"'
    )
    assert source(regions, "de-CH") == "de-CH"
    with pytest.raises(GlossaryFileError, match="several languages"):
        source(regions, "de-AT")  # sibling regions of the hint do not count
    with pytest.raises(GlossaryFileError, match="several languages"):
        source(regions, "de")  # de-CH and de-DE serve "de" alike


def test_tbx_without_the_target_language():
    with pytest.raises(GlossaryFileError, match=r"both French \(fr\) and Japanese \(ja\)"):
        _parse(TBX, "a.tbx", target="ja")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"<martif><termEntry>\n<langSet>", "line 2 is not valid XML"),
        (b"<martif><body/></martif>", "no term entries"),
        (
            b"<martif><termEntry>\n<langSet><term>x</term></langSet></termEntry></martif>",
            "line 2: a language section has no xml:lang",
        ),
    ],
)
def test_a_malformed_tbx_names_the_line(data, message):
    with pytest.raises(GlossaryFileError, match=message):
        _parse(data, "a.tbx")


@pytest.mark.parametrize(
    "doctype",
    [
        b'<!DOCTYPE m [<!ENTITY a "aaaaaaaaaa"><!ENTITY e "&a;&a;&a;&a;&a;&a;&a;&a;">]>',
        b'<!DOCTYPE m [<!ENTITY e SYSTEM "file:///etc/passwd">]>',
    ],
)
def test_tbx_entities_are_never_expanded_or_fetched(doctype):
    data = (
        doctype + b'<m><termEntry><langSet xml:lang="fr"><term>&e;</term></langSet>'
        b'<langSet xml:lang="en"><term>x</term></langSet></termEntry></m>'
    )
    with pytest.raises(GlossaryFileError, match="declares its own XML entities"):
        _parse(data, "a.tbx")


@pytest.mark.parametrize(
    ("code", "wanted", "rank"),
    [
        ("EN_us", "en-US", 0),
        ("pt", "pt-BR", 1),
        ("pt-BR", "pt", 2),
        ("pt-PT", "pt-BR", None),
        ("es", "en", None),
    ],
)
def test_language_codes_match_case_insensitively_with_a_base_fallback(code, wanted, rank):
    assert language_rank(code, wanted) == rank


def test_an_exact_language_beats_the_base_one():
    data = b"""<martif><termEntry>
<langSet xml:lang="fr"><term>chat</term></langSet>
<langSet xml:lang="pt"><term>gato</term></langSet>
<langSet xml:lang="pt-br"><term>gatinho</term></langSet>
</termEntry></martif>"""
    assert _parse(data, "a.tbx", target="pt-BR").terms[0].rendering == "gatinho"
    assert _parse(data, "a.tbx", target="pt-PT").terms[0].rendering == "gato"


# Comparing and merging ---------------------------------------------------------------------

GUIDE = Guide(
    summary="s",
    style_guide="",
    glossary=[
        GlossaryEntry(term="chat", rendering="cat", notes="keep"),
        GlossaryEntry(term="chien", rendering="dog", locked=True),
        GlossaryEntry(term="pluie", rendering="rain"),
    ],
    entities=[Entity(name="Paris", rendering="Paris", kind="place")],
)
TERMS = [
    ImportedTerm("Chat ", "cat", 1),  # same key, same translation
    ImportedTerm("chien", "hound", 2),  # locked, differs
    ImportedTerm("pluie", "rainfall", 3),  # unlocked, differs
    ImportedTerm("soleil", "sun", 4, "a star"),
    ImportedTerm("SOLEIL", "sunshine", 5),  # repeats line 4
    ImportedTerm("paris", "Paris", 6),  # a name the guide has
]


def test_plan_compares_with_terms_and_names():
    plan = plan_import(GUIDE, TERMS)
    assert [p.status for p in plan] == [
        ImportStatus.IDENTICAL,
        ImportStatus.CONFLICT,
        ImportStatus.CONFLICT,
        ImportStatus.NEW,
        ImportStatus.DUPLICATE,
        ImportStatus.IDENTICAL,
    ]
    assert (plan[1].current, plan[1].current_locked) == ("dog", True)
    assert (plan[4].current, plan[4].first_line) == ("sun", 4)
    assert all(p.status is ImportStatus.NEW for p in plan_import(None, TERMS[:1]))


def test_merge_keeps_conflicts_by_default():
    merged, counts = merge_import(
        GUIDE, plan_import(GUIDE, TERMS), lock_all=False, overwrite_conflicts=False
    )
    assert merged.glossary[:3] == GUIDE.glossary
    assert merged.glossary[3] == GlossaryEntry(term="soleil", rendering="sun", notes="a star")
    assert len(merged.glossary) == 4 and merged.entities == GUIDE.entities
    assert (counts.added, counts.updated, counts.locked, counts.kept) == (1, 0, 0, 2)


def test_merge_overwrites_conflicts_and_locks_everything_imported_when_asked():
    merged, counts = merge_import(
        GUIDE, plan_import(GUIDE, TERMS), lock_all=True, overwrite_conflicts=True
    )
    chat, chien, pluie, soleil = merged.glossary
    assert (chat.rendering, chat.notes, chat.locked) == ("cat", "keep", True)
    assert (chien.rendering, chien.locked) == ("hound", True)  # a locked one, when chosen
    assert (pluie.rendering, pluie.locked) == ("rainfall", True)
    assert (soleil.rendering, soleil.locked) == ("sun", True)
    assert merged.entities[0].locked  # the name the file repeats
    assert (counts.added, counts.updated, counts.locked, counts.kept) == (1, 2, 4, 0)
    assert GUIDE.glossary[0].locked is False  # the input guide is left alone


def test_merge_into_no_guide():
    merged, counts = merge_import(
        None, plan_import(None, TERMS[3:5]), lock_all=True, overwrite_conflicts=False
    )
    assert merged.summary == "" and merged.glossary == [
        GlossaryEntry(term="soleil", rendering="sun", notes="a star", locked=True)
    ]
    assert counts.added == 1 and counts.locked == 1


def test_an_overwritten_term_takes_the_file_notes():
    plan = plan_import(GUIDE, [ImportedTerm("pluie", "rainfall", 1, "weather")])
    merged, _ = merge_import(GUIDE, plan, lock_all=False, overwrite_conflicts=True)
    assert (merged.glossary[2].rendering, merged.glossary[2].notes) == ("rainfall", "weather")


def test_an_overwritten_name_keeps_its_notes_field():
    plan = plan_import(GUIDE, [ImportedTerm("Paris", "Paname", 1, "slang")])
    merged, _ = merge_import(GUIDE, plan, lock_all=False, overwrite_conflicts=True)
    assert merged.entities[0].rendering == "Paname" and merged.glossary == GUIDE.glossary


# CGL-115: a field the guide editor could not save again is refused at preview -----------------


@pytest.mark.parametrize(
    ("row", "what"),
    [
        ("x" * 2001 + ",cat", "term"),
        ("chat," + "x" * 2001, "translation"),
    ],
)
def test_a_field_over_the_editors_limit_is_refused_with_its_line(row, what):
    assert MAX_FIELD_CHARS == 2_000
    data = f"term,translation\nchien,dog\n{row}\n".encode()
    with pytest.raises(GlossaryFileError, match=f"line 3 has a {what} of 2,001 characters"):
        _parse(data)


def test_a_field_at_the_limit_is_read():
    parsed = _parse(f"chat,{'x' * 2000}\n".encode())
    assert len(parsed.terms[0].rendering) == 2000


@pytest.mark.parametrize(
    ("note", "rendering", "what"),
    [("x" * 2001, "cat", "note"), ("", "x" * 2001, "translation")],
)
def test_a_tbx_field_over_the_limit_is_refused(note, rendering, what):
    tbx = (
        f'<martif><termEntry><descrip type="definition">{note}</descrip>'
        '<langSet xml:lang="fr"><term>chat</term></langSet>'
        f'<langSet xml:lang="en"><term>{rendering}</term></langSet></termEntry></martif>'
    ).encode()
    with pytest.raises(GlossaryFileError, match=f"has a {what} of 2,001 characters"):
        _parse(tbx, "base.tbx")


# Accepted forms (CGL-109) ------------------------------------------------------------------


def test_a_header_can_name_columns_of_forms():
    data = (
        "term,translation,other forms,source forms,notes\n"
        'contract,Vertrag,"Vertrags, Verträge",contracts,law\n'
        "deed,Urkunde,Urkunden | Urkunde,,\n"
        "seal,Siegel\n"
    ).encode()
    parsed = _parse(data, target="de", source="en")
    assert _pairs(parsed) == [
        ("contract", "Vertrag", 2),
        ("deed", "Urkunde", 3),
        ("seal", "Siegel", 4),
    ]
    assert [(t.forms, t.source_forms) for t in parsed.terms] == [
        (("Vertrags", "Verträge"), ("contracts",)),
        (("Urkunden", "Urkunde"), ()),
        ((), ()),
    ]


def test_without_a_header_extra_columns_are_still_not_forms():
    parsed = _parse(b"contract,Vertrag,Vertrags\n", target="de", source="en")
    assert parsed.terms[0].forms == ()


def test_tbx_other_accepted_target_terms_are_forms():
    data = """<martif><termEntry>
<langSet xml:lang="en"><tig><term>contract</term></tig></langSet>
<langSet xml:lang="de">
  <tig><term>Vertrag</term></tig>
  <tig><term>Kontrakt</term>
    <termNote type="administrativeStatus">deprecatedTerm-admn-sts</termNote></tig>
  <tig><term>Verträge</term></tig>
</langSet>
</termEntry></martif>""".encode()
    parsed = _parse(data, "a.tbx", target="de", source="en")
    assert _pairs(parsed) == [("contract", "Vertrag", 2)]
    assert parsed.terms[0].forms == ("Verträge",)


def test_tbx_with_one_target_term_has_no_forms():
    assert [t.forms for t in _parse(TBX, "base.tbx").terms] == [(), (), ()]


def test_forms_are_merged_into_the_guide():
    guide = Guide(
        summary="",
        style_guide="",
        glossary=[
            GlossaryEntry(term="contract", rendering="Vertrag", forms=["Vertrags"]),
            GlossaryEntry(term="deed", rendering="Akte", forms=["Akten"]),
        ],
    )
    terms = [
        ImportedTerm("contract", "Vertrag", 2, forms=("Verträge", "vertrags")),
        ImportedTerm("deed", "Urkunde", 3, forms=("Urkunden",)),
        ImportedTerm("seal", "Siegel", 4, forms=("Siegeln",), source_forms=("seals",)),
    ]
    merged, counts = merge_import(
        guide, plan_import(guide, terms), lock_all=False, overwrite_conflicts=True
    )
    assert [(g.term, g.rendering, g.forms, g.source_forms) for g in merged.glossary] == [
        ("contract", "Vertrag", ["Vertrags", "Verträge"], []),  # identical: the forms join
        ("deed", "Urkunde", ["Urkunden"], []),  # replaced: the old translation's forms go
        ("seal", "Siegel", ["Siegeln"], ["seals"]),
    ]
    assert (counts.added, counts.updated) == (1, 1)
    kept, _ = merge_import(
        guide, plan_import(guide, terms), lock_all=False, overwrite_conflicts=False
    )
    assert kept.glossary[1] == guide.glossary[1]


def test_a_form_over_the_limit_is_refused():
    long = "x" * (MAX_FIELD_CHARS + 1)
    with pytest.raises(GlossaryFileError, match="has a form of"):
        _parse(f"term,translation,forms\nchat,cat,{long}\n".encode())
