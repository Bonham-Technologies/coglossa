"""Numbers check (spec §6.3 hard trigger `number_mismatch`): the numbers written in digits in
the source and in the translation must be the same multiset. Pure functions; no I/O.

What counts as the same number:

- **Any digit script.** Every Unicode decimal digit is read by its value
  (`unicodedata.decimal`), so ``١٠٠٠`` (Arabic-Indic), ``۱۰۰۰`` (Extended Arabic-Indic),
  ``१०००`` (Devanagari) and fullwidth ``１０００`` are all 1000.
- **Any grouping and decimal separator.** ``1,000.5`` (English), ``1.000,5`` (German),
  ``1 000,5`` (French, also with a no-break or thin space), ``1'000.5`` (Swiss), ``١٬٠٠٠٫٥``
  (Arabic) and ``10,00,000`` (Indian grouping) are read as grouped numbers. A separator is
  grouping only when the groups after it have three digits (or two then three, Indian style).
  A single ``,`` or ``.`` before exactly three digits is ambiguous (``1,000`` is a thousand in
  English and one in German), so both readings are tried and whichever matches the other side
  wins (but ``1,000`` is never read as 1.000, one to three places). Text that is not a
  well-formed grouped or decimal number (``17.11.2026``, ``3:16``, ``2026-11-17``) is read as
  separate numbers, so a date written in another order still matches: ``2026-11-17`` and
  ``17.11.2026`` are both {2026, 11, 17}; ``12.30`` may also be a time, 12 and 30. A less likely
  reading is used only when it explains more than one number, so it cannot excuse a real
  change.
- **Signs, units and suffixes are ignored.** ``50%``, ``%50`` and ``50 %`` are all 50; ``1st``,
  ``21.`` and ``1er`` are 1 and 21; a minus sign or a dash in a range is not part of a number.
- **Large-number words.** Languages group large numbers differently (1.5 million is 150万 in
  Chinese and 15 लाख in Hindi), so a number written right next to a scale word (million, 万,
  लाख, тысяч, mil millones, ...; `_SCALES`) is also read with the scale applied: 1.5 million
  and 150万 are both 1,500,000 and match each other and 1,500,000 written out. Only a scale
  word next to the number does this, so 5 days against 50 days, or 5 million against 50
  million, is flagged. A scale word the table lacks leaves its number as written, which can
  flag a correct translation, never excuse a wrong one.

Limits, by design:

- **Spelled-out numbers are not read.** "three" and 3 are not compared. So that a number spelled
  out on one side only is not flagged, a number found on one side only is excused when it is
  one people commonly write as a word: 0 to 20, the tens to 100, and 1000. A dropped or added
  number outside that set is flagged; a changed number (one side has a number the other lacks,
  and the other side has one the first lacks) is always flagged, whatever its size. So a small
  number dropped outright ("3 items" left out) is not flagged; only the judge can catch
  that.
- Only digits are compared: the same digits with another scale word (2 million for 2 billion)
  are not flagged.
- A plain space between digit groups joins them, as in French: "chapter 5 100 people" reads
  5100.
- Numbers in other numeral systems (三, Roman numerals, ½) are not read either. A conversion
  between the two sides (a Buddhist-era year, a 12- to 24-hour time, pounds to kilograms) is
  flagged: a person should check it anyway.
"""

import itertools
import re
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass

_ARABIC_GROUP = "\u066c"  # ٬
_ARABIC_DECIMAL = "\u066b"  # ٫
_WIDE_SPACES = "\u00a0\u202f\u2009"  # no-break, narrow no-break and thin spaces
_SEPARATORS = ".," + _ARABIC_GROUP + _ARABIC_DECIMAL + " " + _WIDE_SPACES + "'\u2019"
_SEP_CLASS = "[" + re.escape(_SEPARATORS) + "]"
_TOKEN = re.compile(rf"\d+(?:{_SEP_CLASS}\d+)*")
_SPLIT = re.compile(f"({_SEP_CLASS})")
_DECIMAL_MARKS = frozenset({".", ",", _ARABIC_DECIMAL})
# Readings tried per span before falling back to each number's first reading.
_MAX_COMBINATIONS = 256
# Numbers people commonly write as words; one found on one side only is not flagged.
SPELLED_OUT = frozenset(
    {str(n) for n in range(21)} | {str(n) for n in range(30, 101, 10)} | {"1000"}
)


def _fold(text: str) -> str:
    """Lower case without accents or other combining marks, so that a table entry matches
    however the mark was typed (millón/millon, ألف/الف, हज़ार/हजार)."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return unicodedata.normalize(
        "NFC", "".join(c for c in decomposed if not unicodedata.combining(c))
    )


# Scale words: the powers of ten each one can mean. A word in a script written with spaces must
# end at a word boundary ("mil" is not "miles"); CJK, Korean, Thai and Khmer ones need not.
# Several next to each other add up (mil millones, 百万, 千万).
def _table(*rows: tuple[str, tuple[int, ...]]) -> dict[str, tuple[int, ...]]:
    return {_fold(w): exponents for words, exponents in rows for w in words.split()}


_SCALE_WORDS = _table(
    (
        "thousand thousands mil mille mila tausend тыс тысяча тысячи тысяч тис тисяча тисячі "
        "тисяч bin ribu nghìn ngàn elfu dubu ẹgbẹ̀rún ألف آلاف الف ألفا هزار हज़ार हजार হাজার "
        "ሺ ሺህ χιλιάδες χιλιάδων tys tysiąc tysiące tysięcy duizend אלף אלפים",
        (3,),
    ),
    ("lakh lakhs lac लाख লাখ লক্ষ", (5,)),
    (
        "million millions mn mln millón millones milhão milhões milione milioni millionen mio "
        "миллион миллиона миллионов млн мільйон мільйона мільйонів milyon juta triệu miliyan "
        "miliọnu ሚሊዮን مليون ملايين میلیون मिलियन মিলিয়ন εκατομμύριο εκατομμύρια miljoen "
        "milion miliony milionów מיליון",
        (6,),
    ),
    ("crore crores करोड़ কোটি", (7,)),
    (
        "bn milliard milliards milliarde milliarden mrd miliardo miliardi миллиард миллиарда "
        "миллиардов млрд мільярд мільярда мільярдів milyar miliar tỷ bilioni biliyan ቢሊዮን "
        "مليار مليارات میلیارد अरब bilhão bilhões miljard miliard miliardy mld מיליארד "
        "δισεκατομμύριο δισεκατομμύρια",
        (9,),
    ),
    ("billion billions", (9, 12)),  # short scale in English, long scale in French and German
    ("billón billones trillion trillions", (12,)),
)
_SCALE_CHARS = _table(
    ("十 십", (1,)),
    ("百 백", (2,)),
    ("千 천 พัน ពាន់", (3,)),
    ("万 萬 만 หมื่น ម៉ឺន", (4,)),
    ("แสน សែន", (5,)),
    ("ล้าน លាន", (6,)),
    ("億 亿 억", (8,)),
    ("兆 조", (6, 12)),  # 兆 is also a million in Chinese
)
# Swahili and Hausa put the scale word before the number (milioni 1.5, dubu 10).
_SCALE_BEFORE = _table(("elfu dubu", (3,)), ("milioni miliyan", (6,)), ("bilioni biliyan", (9,)))
_SCALES = _SCALE_WORDS | _SCALE_CHARS | _SCALE_BEFORE


def _alternation(words: dict[str, tuple[int, ...]]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_SCALE_NEXT = re.compile(
    rf"\s*(?:({_alternation(_SCALE_WORDS)})(?!\w)|({_alternation(_SCALE_CHARS)}))"
)
_SCALE_PREV = re.compile(rf"(?<!\w)({_alternation(_SCALE_BEFORE)})\s*\Z")
_SCALE_WINDOW = 40  # characters either side of a number looked at for scale words
_MAX_SCALE_WORDS = 2


def _exponents_after(text: str) -> set[int]:
    """The powers of ten the scale words at the start of ``text`` (already folded) can mean."""
    totals, pos = {0}, 0
    for _ in range(_MAX_SCALE_WORDS):
        m = _SCALE_NEXT.match(text, pos)
        if m is None:
            break
        totals = {t + e for t in totals for e in _SCALES[m.group(1) or m.group(2)]}
        pos = m.end()
    return totals - {0} if pos else set()


def _exponents_before(text: str) -> set[int]:
    m = _SCALE_PREV.search(text)
    return set(_SCALES[m.group(1)]) if m else set()


def _shift(value: str, exponent: int) -> str:
    """``value`` times 10**exponent, canonical."""
    whole, _, fraction = value.partition(".")
    digits, point = whole + fraction, len(whole) + exponent
    digits += "0" * max(0, point - len(digits))
    return _canonical(digits[:point], digits[point:])


@dataclass(frozen=True)
class Number:
    value: str  # canonical: ASCII digits, no leading zeros, "." before a non-empty fraction
    text: str  # as written
    scaled: frozenset[str] = frozenset()  # the value times a scale word next to it, if any

    def scale(self, exponents: set[int]) -> "Number":
        if not exponents:
            return self
        return Number(self.value, self.text, frozenset(_shift(self.value, e) for e in exponents))


Reading = tuple[Number, ...]


def _kind(sep: str) -> str:
    if sep in _WIDE_SPACES:
        return " "
    return "'" if sep == "\u2019" else sep


def _canonical(whole: str, fraction: str = "") -> str:
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


def _grouped(groups: list[str], kinds: list[str]) -> bool:
    """A well-formed grouped integer: one kind of separator, 1-3 leading digits, then groups of
    three (or, with commas, Indian groups of two ending in a group of three)."""
    if not kinds:
        return True
    kind = kinds[0]
    if any(k != kind for k in kinds) or kind == _ARABIC_DECIMAL or not 1 <= len(groups[0]) <= 3:
        return False
    rest = [len(g) for g in groups[1:]]
    if all(n == 3 for n in rest):
        return True
    return kind == "," and rest[-1] == 3 and all(n == 2 for n in rest[:-1])


def _readings(token: str) -> list[Reading]:
    """Every plausible reading of one run of digits and separators, most likely first."""
    digits = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in token)
    parts = _SPLIT.split(digits)
    groups, seps = parts[::2], parts[1::2]
    if not seps:
        return [(Number(_canonical(groups[0]), token),)]
    kinds = [_kind(s) for s in seps]
    readings: list[Reading] = []
    if _grouped(groups, kinds):
        readings.append((Number(_canonical("".join(groups)), token),))
    decimal = kinds[-1]
    own_mark = len(kinds) == 1 or kinds[0] != decimal  # a decimal mark is not the group mark
    # "1,000" or "1.000" is a thousand, never 1 written to three decimal places
    zeros = len(groups[-1]) == 3 and not groups[-1].strip("0") and bool(readings)
    if decimal in _DECIMAL_MARKS and own_mark and not zeros and _grouped(groups[:-1], kinds[:-1]):
        readings.append((Number(_canonical("".join(groups[:-1]), groups[-1]), token),))
    # 12.30 may be a time; anything that is no well-formed number is several numbers
    if not readings or (len(seps) == 1 and decimal in ".," and len(groups[-1]) == 2):
        texts = _SPLIT.split(token)[::2]
        readings.append(tuple(Number(_canonical(g), t) for g, t in zip(groups, texts, strict=True)))
    return readings


def _forms(n: Number) -> list[str]:
    return [n.value, *sorted(n.scaled)]


def _leftovers(a: Reading, b: Reading) -> tuple[list[Number], list[Number]]:
    """What each side has that the other lacks, after exact matches and then pairs that match
    once a scale word next to one of them is applied (1.5 million and 150万). Linear in the
    number of numbers: a long table of figures must not stall the check."""
    common = Counter(n.value for n in a) & Counter(n.value for n in b)
    left_a, left_b = _remove(list(a), common), _remove(list(b), common)
    if not any(n.scaled for n in left_a) and not any(n.scaled for n in left_b):
        return left_a, left_b
    # No value is left on both sides now, so two numbers whose forms meet differ as written
    # and meet only through a scale word.
    by_form: dict[str, deque[int]] = {}
    for j, m in enumerate(left_b):
        for form in _forms(m):
            by_form.setdefault(form, deque()).append(j)
    used: set[int] = set()
    kept_a = []
    for n in left_a:
        for form in _forms(n):
            queue = by_form.get(form)
            while queue and queue[0] in used:
                queue.popleft()
            if queue:
                used.add(queue.popleft())
                break
        else:
            kept_a.append(n)
    return kept_a, [m for j, m in enumerate(left_b) if j not in used]


def _remove(numbers: list[Number], common: Counter[str]) -> list[Number]:
    budget = Counter(common)
    kept = []
    for n in numbers:
        if budget[n.value]:
            budget[n.value] -= 1
        else:
            kept.append(n)
    return kept


def _choices(text: str) -> list[list[Reading]]:
    choices = []
    for m in _TOKEN.finditer(text):
        after = _exponents_after(_fold(text[m.end() : m.end() + _SCALE_WINDOW]))
        before = _exponents_before(_fold(text[max(0, m.start() - _SCALE_WINDOW) : m.start()]))
        readings = []
        for r in _readings(m.group()):
            if len(r) == 1:
                readings.append((r[0].scale(before | after),))
            else:
                readings.append((r[0].scale(before), *r[1:-1], r[-1].scale(after)))
        choices.append(readings)
    return choices


def _flatten(picked: list[Reading]) -> Reading:
    return tuple(n for reading in picked for n in reading)


@dataclass(frozen=True)
class NumberMismatch:
    source: tuple[str, ...]  # numbers in the source missing from the translation, as written
    target: tuple[str, ...]  # numbers in the translation missing from the source, as written

    def as_evidence(self) -> dict[str, object]:
        return {"source": list(self.source), "target": list(self.target)}

    def explanation(self) -> str:
        """Plain words for a reviewer (the web app shows them as they are)."""
        src, tgt = _listed(self.source), _listed(self.target)
        if self.source and self.target:
            head = (
                f"The numbers don't match the original: the original has {src}; "
                f"the translation has {tgt}."
            )
        elif self.source:
            head = f"A number may be missing: the original has {src}; the translation doesn't."
        else:
            head = f"The translation has a number the original doesn't: {tgt}."
        return head + " Check the numbers, dates and amounts in this passage."


def _listed(values: tuple[str, ...]) -> str:
    """2004 and 1,500; never a comma between values, which would read as part of a number."""
    return (
        " and ".join(values) if len(values) <= 2 else "; ".join(values[:-1]) + " and " + values[-1]
    )


def number_check(source_text: str, final_text: str) -> NumberMismatch | None:
    """Compare the numbers written in digits on both sides (see the module docstring for what
    counts as the same number and what the check cannot see)."""
    src_choices, tgt_choices = _choices(source_text), _choices(final_text)
    if not src_choices and not tgt_choices:
        return None
    choices = src_choices + tgt_choices
    combos = 1
    for c in choices:
        combos *= len(c)
    if combos > _MAX_COMBINATIONS:
        choices = [c[:1] for c in choices]
    # The readings that leave the fewest numbers unmatched win. Each less likely reading counts
    # as one unmatched number, so it is used only when it explains more than one: otherwise
    # reading 0.01 as "0 and 1" would excuse a change to 0.
    best: tuple[int, int, list[Number], list[Number]] | None = None
    for picks in itertools.product(*(range(len(c)) for c in choices)):
        picked = [c[k] for c, k in zip(choices, picks, strict=True)]
        left_src, left_tgt = _leftovers(
            _flatten(picked[: len(src_choices)]), _flatten(picked[len(src_choices) :])
        )
        alternates = sum(k > 0 for k in picks)
        cost = (len(left_src) + len(left_tgt) + alternates, alternates)
        if best is None or cost < best[:2]:
            best = (*cost, left_src, left_tgt)
    assert best is not None
    _, _, left_src, left_tgt = best
    one_sided = left_src or left_tgt
    if not (left_src and left_tgt) and all(n.value in SPELLED_OUT for n in one_sided):
        return None
    return NumberMismatch(tuple(n.text for n in left_src), tuple(n.text for n in left_tgt))
