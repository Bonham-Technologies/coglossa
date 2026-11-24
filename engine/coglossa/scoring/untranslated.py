"""Untranslated check (hard trigger `untranslated`, CGL-107): local models sometimes copy the
source through or answer in another language, and a copied passage can even score well on
similarity. Two rules, both plain text rules with no language model. Pure functions; no I/O.

- **Copied (rule a).** The translation is the source again: the same letters once case,
  whitespace, punctuation, digits and accents are ignored ("nearly the same" means exactly
  that, so a real translation between close languages is never caught by a similarity score).
- **Wrong script (rule b).** Most of the translation's letters are in a script the target
  language is not written in: Latin text for an Amharic (`am`) or Hindi (`hi`) target. The
  scripts each language is written in come from the FLORES-200 codes (`amh_Ethi`;
  `coglossa.bench.codes`), plus the other scripts some languages are commonly written in
  (`_ALSO_WRITTEN_IN`: Serbian in Latin as well as Cyrillic, ...). A language that is in neither
  is never checked by script.

What is not counted, on either rule: names (a word that starts with a capital letter, and the
names and kept terms of the translation guide), numbers, web and email addresses, and code
(anything between backticks; a word with code punctuation such as snake_case, file.ext, a/path,
f(x), --option or key=value, or in camelCase; a passage dense with brackets and operators). A
passage needs at least `MIN_LETTERS` counted letters, in at least `MIN_WORDS` words unless its
script is written without spaces, to be flagged, so passages that are only names, numbers,
addresses or code, single words and very short ones never are.

Limits, by design:

- Two languages written in the same script (Spanish and Portuguese) are told apart by rule (a)
  only: a passage copied through is flagged, one written in the wrong language is not.
- Languages that share a script with another (Japanese uses Chinese characters) are not told
  apart either: Chinese for a Japanese target is not flagged.
- A passage that should stay as it is (a quotation, a motto, a command written as plain words)
  is flagged; a person marks it checked.
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
# ISO 15924 codes that stand for several scripts of letters.
_MIXED = {
    "Hans": frozenset({"Hani"}),
    "Hant": frozenset({"Hani"}),
    "Jpan": frozenset({"Hani", "Hira", "Kana"}),
    "Kore": frozenset({"Hang", "Hani"}),
    "Hang": frozenset({"Hang", "Hani"}),  # Korean may use Hanja
}
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
    for base, extra in _ALSO_WRITTEN_IN.items():
        more = frozenset(s for code in extra.split() for s in _expand(code))
        by_base[base] = by_base.get(base, frozenset()) | more
        exact[base] = exact.get(base, frozenset()) | more
    return exact, by_base


_EXACT, _BY_BASE = _script_table()


def target_scripts(lang: str | None) -> frozenset[str] | None:
    """The scripts (ISO 15924, as letters are told apart) a BCP-47 language is written in, or
    None when it is not known: then the script rule does not fire."""
    if not lang:
        return None
    subtags = lang.replace("_", "-").casefold().split("-")
    script = next((t for t in subtags[1:] if len(t) == 4 and t.isalpha()), None)
    if script is not None:
        return _expand(script.title()) or None
    base = subtags[0]
    return _EXACT.get(base) or _BY_BASE.get(base) or None


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
_CODE_INSIDE = re.compile(r"[_=/\\{}\[\]<>$#@|&^~+()]|[a-z][A-Z]|\w\.\w|^-")
_CODE_SYMBOLS = frozenset("{}()[];=<>$\\|&")
_WORD = re.compile(r"[^\W\d_]+")


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


def _counted(text: str, names: re.Pattern[str] | None) -> Counter[str] | None:
    """Letters per script in the words that are not names, numbers, addresses or code; None
    when there are too few to tell (`MIN_LETTERS`, `MIN_WORDS`)."""
    text = unicodedata.normalize("NFC", text)
    if names is not None:
        text = names.sub(" ", text)
    text = _URL.sub(" ", _BACKTICKS.sub(" ", text))
    if _looks_like_code(text):
        return None
    text = _LATIN_RUN.sub(_code_run, text)
    scripts: Counter[str] = Counter()
    words = 0
    for m in _WORD.finditer(text):
        word = m.group()
        if word[0].isupper() or word[0].istitle():  # a name, or a sentence's first word
            continue
        words += 1
        scripts.update(s for c in word if (s := _script(c)) is not None)
    spaceless = not _SPACELESS_SCRIPTS.isdisjoint(scripts)
    if scripts.total() < MIN_LETTERS or (words < MIN_WORDS and not spaceless):
        return None
    return scripts


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
        base = (self.language or "").replace("_", "-").split("-")[0].casefold()
        language = LANGUAGE_NAMES.get(base, "the language you are translating into")
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
) -> Untranslated | None:
    """Flag a translation that is the source copied through (rule a) or is mostly written in a
    script the target language does not use (rule b); `keep` are names the guide keeps as they
    are, which are not counted. See the module docstring for what is not counted."""
    counted = _counted(final_text, _names_pattern(keep, final_text))
    if counted is None:
        return None
    if _letters(source_text) == _letters(final_text):
        return Untranslated("copied", source_text, final_text)
    expected = target_scripts(target_lang)
    if expected is None:
        return None
    inside = sum(n for s, n in counted.items() if s in expected)
    outside = Counter({s: n for s, n in counted.items() if s not in expected})
    if outside.total() < MIN_LETTERS or outside.total() <= inside:
        return None
    [(script, _)] = outside.most_common(1)
    return Untranslated(
        "script", source_text, final_text, script, tuple(sorted(expected)), target_lang
    )
