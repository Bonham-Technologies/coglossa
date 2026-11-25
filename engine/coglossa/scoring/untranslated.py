"""Untranslated check (hard trigger `untranslated`, CGL-107): local models sometimes copy the
source through or answer in another language, and a copied passage can even score well on
similarity. Two rules, both plain text rules with no language model. Pure functions; no I/O.

- **Copied (rule a).** The translation is the source again: the same letters once case,
  whitespace, punctuation, digits and accents are ignored ("nearly the same" means exactly
  that, so a real translation between close languages is never caught by a similarity score).
- **Wrong script (rule b).** Most of the translation's letters are in a script the target
  language is not written in: Latin text for an Amharic (`am`) or Hindi (`hi`) target. "Most"
  weighs each letter by how much it holds (`_WEIGHT`: a Chinese character or a Hangul syllable
  counts for three Latin letters), so a Chinese, Korean or Hindi passage with a few English
  terms in it ("dark mode", "npm install") is not read as mostly Latin. It is read sentence by
  sentence: a sentence counts as written in another script only when that script outweighs the
  target script more than `MARGIN` times (less than a fifth of it in the target script), so a
  correct translation full of English terms is not flagged ("Используйте machine learning и
  deep learning ..."), and the passage is flagged when such sentences outweigh the rest. A
  capitalised word in the target script counts for the target script here ("Откройте settings
  ..."). The scripts each language is written in come from the FLORES-200 codes (`amh_Ethi`;
  `coglossa.bench.codes`), plus the other scripts some languages are commonly written in
  (`_ALSO_WRITTEN_IN`: Serbian in Latin as well as Cyrillic, ...), and those of a few languages
  FLORES-200 lacks (`_NOT_IN_FLORES`: Dhivehi). Older codes are read as their current ones
  (`_ALIASES`: `iw` is `he`, `no` is `nb`, `fil` is `tl`, `mo` is `ro`), and Chinese with a
  region and no script as the script that region writes (zh-CN and zh-SG simplified; zh-TW,
  zh-HK and zh-MO traditional), so zh-CN to zh-TW must be converted. A language that is in none
  of these is never checked by script.

What is not counted, on either rule: names (a word that starts with a capital letter, unless
the passage is written in capitals, and the names and kept terms of the translation guide),
numbers and words joined to digits (m2, CO2), web and email addresses, and code (anything
between backticks; a word with code punctuation such as snake_case, file.ext, a/path, f(x),
--option or key=value, or in camelCase; a passage dense with brackets and operators). A
passage needs at least `MIN_LETTERS` counted letters, in at least `MIN_WORDS` words unless its
script is written without spaces, to be flagged, so passages that are only names, numbers,
addresses or code, single words and very short ones never are. A bibliography entry is never
flagged: a reference list stays as it is. It must start like one: its number "[4]" if any, then
its authors in one of the usual shapes with the year where that style puts it ("Smith, J., &
Jones, K. (2020).", "J. Smith and K. Jones, ...", "Smith J, Jones K. ... 2020;46(2)", "Koehn,
Philipp. Title. Publisher, 2010.", "World Health Organization. (2020)."), and it must have two
marks of one, such as "(2020).", "46(2)", "pp. 12", "et al." or a DOI. Prose that cites works,
or has a number, a name and a date, does not start like that and is checked ("Recent work
(Smith et al., 2020) shows ...", "[12] On May 25, 2018, the appellant ...", "Smith, J. (2020)
argues ..."). A copy that does not end like a sentence needs `MIN_FRAGMENT_WORDS` words
(capitalised ones included), so labels, units and mottos that rightly stay the same ("total (in
millions)", "kg per m2 per year") are not flagged as copies.

Rule (a) is left out when the source is already in the target language and script (en-US to
en-GB, which the web app allows): there most passages rightly stay as they are.

Limits, by design:

- Two languages written in the same script (Spanish and Portuguese) are told apart by rule (a)
  only: a passage copied through is flagged, one written in the wrong language is not.
- Languages that share a script with another (Japanese uses Chinese characters) are not told
  apart either: Chinese for a Japanese target is not flagged.
- A passage that should stay as it is (a quotation, a motto that ends like a sentence, a
  command written as plain words) is flagged; a person marks it checked. So is a passage of
  the source already in the target language (a French quotation in an English source to be
  translated into French), which rightly stays as it is.
- A short label, heading or table cell copied through ("Terms and conditions", four words or
  fewer that do not end like a sentence) is not flagged when the target is written in the same
  script; in another script, rule (b) still flags it.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache, lru_cache
from typing import Literal

from coglossa.bench.codes import FLORES_CODES
from coglossa.languages import LANGUAGE_NAMES, SCRIPT_NAMES
from coglossa.schema import Guide
from coglossa.segment.text_metrics import SPACELESS

# Counted letters, and words in a script written with spaces, a passage needs before either rule
# can flag it: a single word ("software", "international") is often the same in both languages.
MIN_LETTERS = 10
MIN_WORDS = 2
_SPACELESS_SCRIPTS = frozenset({"Hani", "Hira", "Kana", "Thai", "Laoo", "Khmr", "Mymr", "Tibt"})
# A copy that does not end like a sentence (a label, a unit, a motto) needs this many words,
# capitalised ones included, to be flagged: "total (in millions)" and "kg per m2 per year"
# often rightly stay as they are, while a copied headline in sentence case ("Government
# announces new tax plan") is still caught. A passage in a script written without spaces has
# no words to count and is exempt.
MIN_FRAGMENT_WORDS = 5
# Rule (b) reads a sentence as written in another script when its letters in that script weigh
# more than this many times its letters in the target script's: a correct translation can hold
# several English terms ("Используйте machine learning и deep learning ...").
MARGIN = 4

# The first word(s) of a letter's Unicode name -> its ISO 15924 script.
_SCRIPT_BY_NAME = {
    "LATIN": "Latn",
    "CYRILLIC": "Cyrl",
    "GREEK": "Grek",
    "ARMENIAN": "Armn",
    "HEBREW": "Hebr",
    "ARABIC": "Arab",
    "SYRIAC": "Syrc",
    "THAANA": "Thaa",
    "NKO": "Nkoo",
    "DEVANAGARI": "Deva",
    "BENGALI": "Beng",
    "GURMUKHI": "Guru",
    "GUJARATI": "Gujr",
    "ORIYA": "Orya",
    "TAMIL": "Taml",
    "TELUGU": "Telu",
    "KANNADA": "Knda",
    "MALAYALAM": "Mlym",
    "SINHALA": "Sinh",
    "THAI": "Thai",
    "LAO": "Laoo",
    "TIBETAN": "Tibt",
    "MYANMAR": "Mymr",
    "GEORGIAN": "Geor",
    "HANGUL": "Hang",
    "ETHIOPIC": "Ethi",
    "CHEROKEE": "Cher",
    "KHMER": "Khmr",
    "MONGOLIAN": "Mong",
    "HIRAGANA": "Hira",
    "KATAKANA": "Kana",
    "KATAKANA-HIRAGANA": "Kana",
    "CJK": "Hani",
    "IDEOGRAPHIC": "Hani",
    "TIFINAGH": "Tfng",
    "ADLAM": "Adlm",
    "VAI": "Vaii",
    "OL CHIKI": "Olck",
    "MEETEI MAYEK": "Mtei",
}
_KNOWN = frozenset(_SCRIPT_BY_NAME.values())
# Roughly how many Latin letters one counted letter of a script stands for, when telling which
# script a passage is mostly in: a Chinese character or a Hangul syllable is a syllable or a word,
# kana and the other syllabaries a syllable; the Brahmic scripts' vowel signs are not letters, so
# each counted letter carries a vowel too; Arabic and Hebrew leave short vowels out. Others: 1.
_WEIGHT = (
    dict.fromkeys(("Hani", "Hang"), 3)
    | dict.fromkeys(("Hira", "Kana", "Ethi", "Cher", "Vaii", "Thaa", "Mtei"), 2)
    | dict.fromkeys(
        (
            "Deva",
            "Beng",
            "Guru",
            "Gujr",
            "Orya",
            "Taml",
            "Telu",
            "Knda",
            "Mlym",
            "Sinh",
            "Thai",
            "Laoo",
            "Tibt",
            "Mymr",
            "Khmr",
        ),
        2,
    )
    | dict.fromkeys(("Arab", "Hebr", "Syrc"), 1.5)
)
# ISO 15924 codes that stand for several scripts of letters.
_MIXED = {
    "Hans": frozenset({"Hani"}),
    "Hant": frozenset({"Hani"}),
    "Jpan": frozenset({"Hani", "Hira", "Kana"}),
    "Kore": frozenset({"Hang", "Hani"}),
    "Hang": frozenset({"Hang", "Hani"}),  # Korean may use Hanja
}
# Older or other codes for a language FLORES-200 has, by the code it has it under.
_ALIASES = {"iw": "he", "no": "nb", "fil": "tl", "mo": "ro", "in": "id", "ji": "yi"}
# The regions whose Chinese is written in simplified or in traditional characters.
_REGION_SCRIPT = {
    "zh": {"cn": "hans", "sg": "hans", "my": "hans", "tw": "hant", "hk": "hant", "mo": "hant"}
}
# Scripts of languages FLORES-200 does not have.
_NOT_IN_FLORES = {"dv": "Thaa"}
# Scripts a language is commonly written in besides its FLORES-200 one.
_ALSO_WRITTEN_IN = {
    "az": "Cyrl Arab",
    "be": "Latn",
    "bs": "Cyrl",
    "ckb": "Latn",
    "crh": "Cyrl",
    "ff": "Adlm",
    "ha": "Arab",
    "kk": "Latn",
    "ku": "Arab Cyrl",
    "ky": "Arab",
    "mn": "Mong",
    "mni": "Mtei",
    "ms": "Arab",
    "pa": "Arab",
    "sat": "Latn Beng Deva",
    "sd": "Deva",
    "sr": "Latn",
    "tg": "Latn",
    "tk": "Cyrl",
    "tt": "Latn",
    "tzm": "Latn",
    "ug": "Cyrl Latn",
    "uz": "Cyrl",
}


def _expand(code: str) -> frozenset[str]:
    return _MIXED.get(code, frozenset({code}) if code in _KNOWN else frozenset())


def _script_table() -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    exact: dict[str, frozenset[str]] = {}
    by_base: dict[str, frozenset[str]] = {}
    for code, tag in FLORES_CODES.items():
        scripts = _expand(code.split("_")[1])
        tag = tag.casefold()
        exact[tag] = exact.get(tag, frozenset()) | scripts
        base = tag.split("-")[0]
        by_base[base] = by_base.get(base, frozenset()) | scripts
    for base, extra in (_ALSO_WRITTEN_IN | _NOT_IN_FLORES).items():
        more = frozenset(s for code in extra.split() for s in _expand(code))
        by_base[base] = by_base.get(base, frozenset()) | more
        exact[base] = exact.get(base, frozenset()) | more
    return exact, by_base


_EXACT, _BY_BASE = _script_table()


def _language_and_script(lang: str) -> tuple[str, str | None]:
    """A BCP-47 tag's language subtag (an older code read as its current one) and its script
    subtag, or the script its region writes Chinese in (zh-TW: hant), or None; folded."""
    subtags = lang.replace("_", "-").casefold().split("-")
    base = _ALIASES.get(subtags[0], subtags[0])
    script = next((t for t in subtags[1:] if len(t) == 4 and t.isalpha()), None)
    if script is None:
        regions = _REGION_SCRIPT.get(base, {})
        script = next((regions[t] for t in subtags[1:] if t in regions), None)
    return base, script


def target_scripts(lang: str | None) -> frozenset[str] | None:
    """The scripts (ISO 15924, as letters are told apart) a BCP-47 language is written in, or
    None when it is not known: then the script rule does not fire."""
    if not lang:
        return None
    base, script = _language_and_script(lang)
    if script is not None:
        return _expand(script.title()) or None
    return _EXACT.get(base) or _BY_BASE.get(base) or None


def _language_name(lang: str | None) -> str:
    first = (lang or "").replace("_", "-").split("-")[0].casefold()
    name = LANGUAGE_NAMES.get(first) or LANGUAGE_NAMES.get(_ALIASES.get(first, first))
    return name or "the language you are translating into"


def _same_language(source_lang: str | None, target_lang: str | None) -> bool:
    """Whether the source is already in the target language and script (en-US and en-GB; not
    zh-Hans and zh-Hant)."""
    if not source_lang or not target_lang:
        return False
    return _language_and_script(source_lang) == _language_and_script(target_lang)


def _weighed(letters: Counter[str]) -> float:
    return sum(n * _WEIGHT.get(script, 1) for script, n in letters.items())


@cache
def _script(char: str) -> str | None:
    """A letter's script, or None for anything else (and for scripts the table lacks)."""
    if not unicodedata.category(char).startswith("L"):
        return None
    name = unicodedata.name(char, "").removeprefix("FULLWIDTH ").removeprefix("HALFWIDTH ")
    words = name.split(" ")
    return _SCRIPT_BY_NAME.get(" ".join(words[:2])) or _SCRIPT_BY_NAME.get(words[0])


def _letters(text: str) -> str:
    """The text's letters only, ignoring case, accents, spaces, punctuation and digits. Marks
    that are part of a letter in other scripts (Devanagari vowel signs) are kept."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(
        c
        for c in decomposed
        if unicodedata.category(c)[0] in "LM" and not "\u0300" <= c <= "\u036f"
    )


_URL = re.compile(
    r"\b[a-z][a-z0-9+.-]*://\S+|\bwww\.\S+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE
)
_BACKTICKS = re.compile(r"`[^`\n]*`")
_LATIN_RUN = re.compile(r"[!-~\u00c0-\u024f]+")  # a run of Latin letters, digits and ASCII signs
_RUN_EDGES = ".,;:!?'\"()[]{}*"
# code punctuation, camelCase, file.ext, --option; or a letter next to a digit (m2, CO2, 14th)
_CODE_INSIDE = re.compile(r"[_=/\\{}\[\]<>$#@|&^~+()]|[a-z][A-Z]|\w\.\w|^-|[A-Za-z]\d|\d[A-Za-z]")
_CODE_SYMBOLS = frozenset("{}()[];=<>$\\|&")
_WORD = re.compile(r"[^\W\d_]+")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|(?<=[。！？።፧।॥؟\n])")
_ENDS_LIKE_A_SENTENCE = re.compile(r"[.!?…。！？።፧।॥؟][\"'”’»)\]]*\s*$")
# A bibliography entry: a year and at least two marks of one.
_YEAR = re.compile(r"(?<!\d)(?:1[5-9]|20)\d\d[a-z]?(?!\d)")
_CITATION_MARKS = (
    re.compile(r"[^\W\d_]{2},\s+(?:[A-Z]\.[\s-]*)+"),  # Smith, J. / Zhu, W.-J.
    re.compile(r"^\s*[A-Z][^\W\d_]+,\s+[A-Z][^\W\d_]+\.\s"),  # Koehn, Philipp.
    re.compile(r"(?:^|[.,;:(]\s*)(?:1[5-9]|20)\d\d[a-z]?\s*[).,;:]"),  # (2020). / , 2010.
    re.compile(r"\bet al\.|\bpp?\.\s*\d|\bvol\.\s*\d|\bISBN\b|\((?:[Ee]ds?)\.\)"),
    re.compile(r"\bdoi\b|doi\.org/|\b10\.\d{4,9}/|arxiv\.org/", re.IGNORECASE),
    re.compile(r"\b\d+\s?\(\d+(?:[–-]\d+)?\)"),  # volume(issue): 46(2)
    re.compile(r"^\s*\[\d+\]\s"),  # [4] a numbered reference
)
# How a bibliography entry starts: its number, then its authors in one of the usual shapes with
# the year where those styles put it, or an organisation as author. Prose does not start like
# that, even when it has a number, a name and a year ("[12] On May 25, 2018, the appellant ...",
# "Smith, J. (2020) argues that ..."). Author lists are atomic groups, so a long list that turns
# out not to be an entry is given up in linear time.
_UPPER = "A-ZÀ-ÖØ-Þ"
_U = f"[{_UPPER}]"
_L = r"[^\W\d_]"
_YEAR_RE = r"(?:1[5-9]|20)\d\d[a-z]?"
_SURNAME = rf"(?:(?!and\s)[a-z]{{1,3}}\s+){{0,2}}{_U}{_L}*(?:['’-]{_L}+)*"
_INITIALS = rf"{_U}\.(?:-?{_U}\.|\s?{_U}\.)*"  # J. / W.-J. / J. A.
_SEP = r"(?:,\s+(?:&\s+|and\s+)?|\s+(?:&|and)\s+)"  # , / , & / , and / and
_ET_AL = r"(?:,?\s+et\s+al\.)?"
# Smith, J. / Smith, John A. / van der Berg, A.
_SURNAME_FIRST = rf"{_SURNAME},\s+(?:{_U}{_L}+\s+)?{_INITIALS}"
_ENTRY_START = re.compile(
    rf"^\s*(?:\[\d+\]\s*|\d{{1,3}}[.)]\s+)?(?:"
    # APA and Harvard: Smith, J., & Jones, K. (2020). / ... and Bengio, Y. 2015.
    rf"(?>{_SURNAME_FIRST}(?:{_SEP}{_SURNAME_FIRST})*{_ET_AL})"
    rf"(?:\s*\({_YEAR_RE}(?:,[^()\n]{{1,30}})?\)|,?\s+{_YEAR_RE})\."
    # IEEE: J. Smith and K. Jones, "Title," ... / J. Smith (2020).
    rf"|(?>{_INITIALS}\s*{_SURNAME}(?:{_SEP}{_INITIALS}\s*{_SURNAME})*{_ET_AL})"
    rf"(?:,\s*[\"“]|\s*\({_YEAR_RE}\)\.|,\s+{_YEAR_RE}\.)"
    # Vancouver: Smith J, Jones K. Title. Journal. 2020;46(2):123-45.
    rf"|(?>{_SURNAME}\s+{_U}{{1,3}}(?:,\s+{_SURNAME}\s+{_U}{{1,3}})*(?:,?\s+et\s+al)?)\.\s"
    rf"[^\n]{{0,300}}?(?<!\d){_YEAR_RE}(?:\s{_U}[a-z]{{2}}(?:\s\d{{1,2}})?)?;\s*\d"
    # MLA and Chicago: Koehn, Philipp. Title. Publisher, 2010. / Koehn, Philipp. 2010.
    rf"|{_SURNAME},\s+{_U}{_L}+(?:\s+{_U}{_L}+)*(?:\s+{_U})?\.\s+"
    rf"(?:\(?{_YEAR_RE}\)?\.|(?:[^.\n]+\.[\"”]?\s+){{1,4}}[^.\n]*,\s*{_YEAR_RE}\.?\s*$)"
    # An organisation: World Health Organization. (2020).
    rf"|{_U}[\w'’&-]*(?:\s+(?:of|for|and|the|on|in|&|{_U}[\w'’&-]*)){{0,12}}"
    rf"\.\s*\({_YEAR_RE}(?:,[^()\n]{{1,30}})?\)\."
    r")"
)


def _code_run(m: re.Match[str]) -> str:
    return " " if _CODE_INSIDE.search(m.group().strip(_RUN_EDGES)) else m.group()


def _looks_like_code(text: str) -> bool:
    symbols = sum(c in _CODE_SYMBOLS for c in text)
    return symbols >= 4 and symbols >= 0.1 * len("".join(text.split()))


def _names_pattern(keep: Iterable[str], text: str) -> re.Pattern[str] | None:
    """A pattern for the kept names found in ``text``. A guide may hold thousands: only those
    that occur in the text, ignoring case, go into it."""
    folded = text.casefold()
    found = {n.strip() for n in keep if n.strip() and n.strip().casefold() in folded}
    return _compiled(tuple(sorted(found, key=lambda n: (-len(n), n)))) if found else None


@lru_cache(maxsize=256)
def _compiled(names: tuple[str, ...]) -> re.Pattern[str]:
    parts = [
        re.escape(n) if SPACELESS.search(n) else rf"(?<!\w){re.escape(n)}(?!\w)" for n in names
    ]
    return re.compile("|".join(parts), re.IGNORECASE)


def _citation(text: str) -> bool:
    """Whether the text looks like a bibliography entry, which stays as it is."""
    return (
        _ENTRY_START.search(text) is not None
        and _YEAR.search(text) is not None
        and sum(m.search(text) is not None for m in _CITATION_MARKS) >= 2
    )


@dataclass(frozen=True)
class _Counted:
    """Letters per script: `letters` in the words that are not names, numbers, addresses or
    code, `words` of them, and `length` the words in the passage, names included; `sentences`
    the same letters per sentence, each beside the letters of the capitalised words it left out
    (a word in the target script is the target language, name or not, so rule (b) counts them)."""

    letters: Counter[str]
    words: int
    length: int
    sentences: list[tuple[Counter[str], Counter[str]]]

    @property
    def spaceless(self) -> bool:
        return not _SPACELESS_SCRIPTS.isdisjoint(self.letters)


def _counted(text: str, names: re.Pattern[str] | None) -> _Counted | None:
    """The text's counted letters; None when there are too few to tell (`MIN_LETTERS`,
    `MIN_WORDS`)."""
    text = unicodedata.normalize("NFC", text)
    if names is not None:
        text = names.sub(" ", text)
    text = _URL.sub(" ", _BACKTICKS.sub(" ", text))
    if _looks_like_code(text):
        return None
    text = _LATIN_RUN.sub(_code_run, text)
    sentences = [_WORD.findall(s) for s in _SENTENCE_END.split(text)]
    every = [w for words in sentences for w in words]
    # a passage in capitals: its words are not all names. One with words in a script without
    # capitals (Hangul, Devanagari, Ethiopic) is not, and its capitals are acronyms and names.
    shouting = all(w.lower() != w.upper() for w in every) and sum(
        w.isupper() and len(w) > 1 for w in every
    ) >= max(MIN_WORDS, 0.8 * len(every))
    letters: Counter[str] = Counter()
    words = 0
    per_sentence: list[tuple[Counter[str], Counter[str]]] = []
    for sentence in sentences:
        counted: Counter[str] = Counter()
        named: Counter[str] = Counter()
        for word in sentence:
            name = word[0].isupper() or word[0].istitle()  # a name, or a sentence's first word
            if name and not (shouting and word.isupper()):
                named.update(s for c in word if (s := _script(c)) is not None)
                continue
            words += 1
            counted.update(s for c in word if (s := _script(c)) is not None)
        letters += counted
        per_sentence.append((counted, named))
    found = _Counted(letters, words, len(every), per_sentence)
    if letters.total() < MIN_LETTERS or (words < MIN_WORDS and not found.spaceless):
        return None
    return found


def kept_names(guide: Guide) -> list[str]:
    """What the guide keeps as it is: every name, and the terms it renders as themselves."""
    found = [n for e in guide.entities for n in (e.name, e.rendering)]
    found += [g.term for g in guide.glossary if g.term.casefold() == g.rendering.casefold()]
    return list(dict.fromkeys(n for n in found if n.strip()))


def _script_words(code: str) -> str:
    special = {
        "Hani": "Chinese characters",
        "Hira": "Japanese kana",
        "Kana": "Japanese kana",
        "Hang": "Korean Hangul",
    }
    if code in special:
        return special[code]
    if code in SCRIPT_NAMES:
        return SCRIPT_NAMES[code]
    name = next(n for n, c in _SCRIPT_BY_NAME.items() if c == code)
    return f"{name.title()} script"


@dataclass(frozen=True)
class Untranslated:
    rule: Literal["copied", "script"]
    source: str
    translation: str
    script: str | None = None  # rule "script": the translation's main script (ISO 15924)
    expected: tuple[str, ...] = ()  # rule "script": the scripts the target is written in
    language: str | None = None  # rule "script": the target language (BCP-47)

    def as_evidence(self) -> dict[str, object]:
        evidence: dict[str, object] = {
            "rule": self.rule,
            "source": self.source,
            "translation": self.translation,
        }
        if self.rule == "script":
            evidence |= {"script": self.script, "expected": list(self.expected)}
        return evidence

    def explanation(self) -> str:
        """Plain words for a reviewer (the web app shows them as they are)."""
        if self.rule == "copied":
            return (
                "This passage may not have been translated: the translation is the same as the "
                "original. Check whether it should stay as it is."
            )
        language = _language_name(self.language)
        written = " or ".join(dict.fromkeys(_script_words(s) for s in self.expected))
        return (
            "This passage may not have been translated: the translation is mostly in "
            f"{_script_words(self.script or '')}, but {language} is written in {written}. "
            "Check that it is in the right language."
        )


def untranslated_check(
    source_text: str,
    final_text: str,
    target_lang: str | None,
    keep: Iterable[str] = (),
    *,
    source_lang: str | None = None,
) -> Untranslated | None:
    """Flag a translation that is the source copied through (rule a) or is mostly written in a
    script the target language does not use (rule b); `keep` are names the guide keeps as they
    are, which are not counted. Rule (a) is left out when `source_lang` is the target language
    in the same script. See the module docstring for what is not counted."""
    if _citation(final_text):
        return None
    counted = _counted(final_text, _names_pattern(keep, final_text))
    if counted is None:
        return None
    copied = _letters(source_text) == _letters(final_text)
    if (
        copied
        and not _same_language(source_lang, target_lang)
        and _copy_counts(final_text, counted)
    ):
        return Untranslated("copied", source_text, final_text)
    expected = target_scripts(target_lang)
    if expected is None:
        return None
    # the letters outside the target script in the sentences mostly written outside it, against
    # all the others
    outside: Counter[str] = Counter()
    foreign = rest = 0.0
    for letters, named in counted.sentences:
        inside = _weighed(Counter({s: n for s, n in (letters + named).items() if s in expected}))
        out = Counter({s: n for s, n in letters.items() if s not in expected})
        if _weighed(out) > MARGIN * inside:
            outside += out
            foreign += _weighed(out)
            rest += inside
        else:
            rest += inside + _weighed(out)
    if outside.total() < MIN_LETTERS or foreign <= rest:
        return None
    script = max(outside, key=lambda s: outside[s] * _WEIGHT.get(s, 1))
    return Untranslated(
        "script", source_text, final_text, script, tuple(sorted(expected)), target_lang
    )


def _copy_counts(text: str, counted: _Counted) -> bool:
    """A copy is flagged when it ends like a sentence, has `MIN_FRAGMENT_WORDS` words, or is in
    a script written without spaces."""
    return (
        counted.spaceless
        or counted.length >= MIN_FRAGMENT_WORDS
        or _ENDS_LIKE_A_SENTENCE.search(text) is not None
    )
