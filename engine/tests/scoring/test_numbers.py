import time
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from coglossa.scoring.numbers import SPELLED_OUT, NumberMismatch, number_check

# --- changed, dropped and added numbers are flagged -----------------------------------------


def test_changed_number_is_flagged_with_both_values():
    found = number_check("The fire burned 1,200 hectares.", "Le feu a brûlé 1 300 hectares.")
    assert found == NumberMismatch(source=("1,200",), target=("1 300",))
    assert found.as_evidence() == {"source": ["1,200"], "target": ["1 300"]}


def test_changed_small_number_is_flagged():
    # small numbers are excused only when one side has no leftover number at all
    found = number_check("Three of the 4 boys left.", "Drei der 5 Jungen gingen.")
    assert found == NumberMismatch(source=("4",), target=("5",))


def test_dropped_number_is_flagged():
    found = number_check("It opened in 1989 and closed in 2004.", "Es öffnete 1989 und schloss.")
    assert found == NumberMismatch(source=("2004",), target=())


def test_added_number_is_flagged():
    found = number_check("Il coûte 250 euros.", "It costs 250 euros, or 270 dollars.")
    assert found == NumberMismatch(source=(), target=("270",))


def test_numbers_are_compared_as_multisets():
    assert number_check("In 2010 and again in 2010.", "In 2010 and again.") == NumberMismatch(
        source=("2010",), target=()
    )
    assert number_check("2010, 2011", "2011, 2010") is None


def test_explanations_are_plain_and_name_the_values():
    changed = NumberMismatch(source=("25",), target=("26",))
    assert changed.explanation() == (
        "The numbers don't match the original: the original has 25; the translation has 26. "
        "Check the numbers, dates and amounts in this passage."
    )
    # values are never joined by a comma, which would read as part of a number
    dropped = NumberMismatch(source=("2004", "1,500"), target=())
    assert dropped.explanation().startswith(
        "A number may be missing: the original has 2004 and 1,500; the translation doesn't."
    )
    added = NumberMismatch(source=(), target=("270", "3,5", "12"))
    assert added.explanation().startswith(
        "The translation has a number the original doesn't: 270; 3,5 and 12."
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("It took 5 days.", "Tardó 50 días."),
        ("100 people came.", "1000 Leute kamen."),
        ("It cost $1,500.", "Costó 150 dólares."),
        ("Turnout was 0.5.", "Katılım %50 oldu."),
        ("It costs 5 million dollars.", "Cuesta 50 millones de dólares."),
        ("It cost ¥130,000.", "14万日元"),
    ],
)
def test_the_same_digits_at_another_size_are_flagged(source, target):
    # only a scale word next to a number lets its size change (1.5 million and 150万)
    assert number_check(source, target) is not None
    assert number_check(target, source) is not None


def test_no_numbers_at_all():
    assert number_check("Il pleut.", "It rains.") is None
    assert number_check("", "") is None


# --- the same number written another way is not flagged -------------------------------------


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("It weighs 1,000.5 kg.", "Es wiegt 1.000,5 kg."),
        ("It weighs 1,000 kg.", "يزن ١٠٠٠ كغ."),
        ("It weighs 1,000.5 kg.", "يزن ١٬٠٠٠٫٥ كغ."),
        ("It weighs 1000 kg.", "وزنش ۱۰۰۰ کیلوگرم است."),  # Extended Arabic-Indic
        ("It weighs 1,000 kg.", "इसका वज़न १,००० किलो है।"),  # Devanagari
        ("It weighs 1,000 kg.", "重さは１０００キロです。"),  # fullwidth
        ("It weighs 1,000.5 kg.", "Il pèse 1\u202f000,5 kg."),  # narrow no-break space
        ("It weighs 1,000.5 kg.", "Il pèse 1 000,5 kg."),
        ("It costs 1,000,000 rupees.", "इसकी कीमत 10,00,000 रुपये है।"),  # Indian grouping
        ("It costs 1,000.50 CHF.", "Es kostet 1'000.50 CHF."),
        ("It costs 1,000.50 CHF.", "Es kostet 1\u2019000.50 CHF."),
        ("Pi is about 3.14.", "Pi ist etwa 3,14."),
        ("It weighs 1.000 kg.", "It weighs 1,000 kg."),  # an ambiguous reading on both sides
        ("The meeting is on 2026-11-17.", "Das Treffen ist am 17.11.2026."),
        ("The meeting is on 2026-11-17.", "La réunion a lieu le 17/11/2026."),
        ("The meeting is on November 17, 2026.", "Das Treffen ist am 17.11.2026."),
        ("We meet at 12:30.", "Kami bertemu pukul 12.30."),
        ("Turnout was 50%.", "Katılım %50 oldu."),
        ("Turnout was 50%.", "La participation était de 50 %."),
        ("Turnout was 50%.", "وكانت نسبة المشاركة ٥٠٪."),
        ("It was the 1st race of the 21st season.", "C'était la 1re course de la 21e saison."),
        ("It was the 21st of May.", "Es war der 21. Mai."),
        ("It costs 1.5 million dollars.", "它价值150万美元。"),
        ("It costs 1.5 million dollars.", "इसकी कीमत 15 लाख डॉलर है।"),
        ("It costs 1.5 million dollars.", "It costs 1,500,000 dollars."),
        ("It costs 1.5 million dollars.", "Inagharimu dola milioni 1.5."),  # scale word first
        ("Revenue was 2 billion.", "收入为20亿。"),
        ("Revenue was 5 billion.", "Los ingresos fueron 5 mil millones."),  # two scale words
        ("Revenue was 5 billion.", "Le chiffre d'affaires était de 5 milliards."),
        ("It costs 1.2 million.", "Es kostet 1,2 Mio."),
        ("An extra AUD$45 million.", "额外拨付 4500 万澳元。"),
        ("About 10,000 years ago.", "منذ نحو 10 آلاف عام."),
        ("About 400,000 known cases.", "Около 400 тысяч случаев."),
        ("Tickets cost up to ¥130,000.", "チケットは最高13万円です。"),
        ("A population of 1,200,000.", "인구는 120만 명이다."),
        ("It cost 15 crore.", "It cost 150 million."),
        ("Born in 05/2001.", "Né en mai 2001."),  # a month written as a word
        ("The 35mm film is -5 to 10 degrees.", "Le film 35 mm supporte de −5 à 10 degrés."),
    ],
)
def test_same_numbers_written_differently_are_not_flagged(source, target):
    assert number_check(source, target) is None
    assert number_check(target, source) is None


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("Three people died.", "3 Menschen starben."),
        ("3 people died.", "Trois personnes sont mortes."),
        ("It took twenty years.", "Es dauerte 20 Jahre."),
        ("A thousand people came.", "1000 Menschen kamen."),
        ("1. Mix the flour.", "Mélangez la farine."),  # list numbering on one side
    ],
)
def test_a_number_spelled_out_on_one_side_is_not_flagged(source, target):
    assert number_check(source, target) is None
    assert number_check(target, source) is None


def test_documented_limits_of_spelled_out_numbers():
    # "three" is not read: a changed spelled-out small number is not seen ...
    assert number_check("Three people died.", "4 Menschen starben.") is None
    # ... and a larger number spelled out on one side is flagged
    assert number_check("Twenty-five people died.", "25 Menschen starben.") == NumberMismatch(
        source=(), target=("25",)
    )
    assert {"0", "7", "20", "30", "100", "1000"} <= SPELLED_OUT and "25" not in SPELLED_OUT


def test_a_time_like_decimal_still_matches_a_decimal():
    assert number_check("It grew 12.30 percent.", "Es wuchs um 12,30 Prozent.") is None
    assert number_check("It grew 12.3 percent.", "Es wuchs um 12,30 Prozent.") is None


def test_many_ambiguous_numbers_fall_back_to_their_first_reading():
    # 2^10 combinations of readings: each number is read as a grouped integer
    numbers = [f"{n},{n:03d}" for n in range(101, 111)]
    source = " and ".join(numbers)
    assert number_check(source, source.replace(",", ".")) is None
    changed = source.replace("110,110", "110,111")
    assert number_check(source, changed) == NumberMismatch(source=("110,110",), target=("110,111",))


# --- properties ------------------------------------------------------------------------------

_SCRIPTS = {
    "ascii": "0123456789",
    "arabic_indic": "٠١٢٣٤٥٦٧٨٩",
    "extended_arabic_indic": "۰۱۲۳۴۵۶۷۸۹",
    "devanagari": "०१२३४५६७८९",
    "bengali": "০১২৩৪৫৬৭৮৯",
    "fullwidth": "０１２３４５６７８９",
}
# (group separator, decimal separator, Indian grouping)
_STYLES = {
    "plain": ("", ".", False),
    "en": (",", ".", False),
    "de": (".", ",", False),
    "fr": ("\u202f", ",", False),
    "fr_space": (" ", ",", False),
    "ch": ("'", ".", False),
    "ar": ("٬", "٫", False),
    "in": (",", ".", True),
}


def _group(whole: str, sep: str, indian: bool) -> str:
    if not sep or len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    size = 2 if indian else 3
    groups = []
    while head:
        groups.insert(0, head[-size:])
        head = head[:-size]
    return sep.join([*groups, tail])


def render(whole: int, fraction: str, style: str, script: str) -> str:
    sep, mark, indian = _STYLES[style]
    text = _group(str(whole), sep, indian) + (mark + fraction if fraction else "")
    digits = _SCRIPTS[script]
    return "".join(digits[int(c)] if c.isdigit() else c for c in text)


numbers = st.tuples(
    st.integers(min_value=0, max_value=10**10),
    st.one_of(st.just(""), st.from_regex(r"\A[0-9]{0,2}[1-9]\Z")),
)
styles = st.sampled_from(sorted(_STYLES))
scripts = st.sampled_from(sorted(_SCRIPTS))


@settings(max_examples=300, deadline=None)
@given(
    st.lists(numbers, min_size=1, max_size=4),
    styles,
    styles,
    scripts,
    scripts,
    st.randoms(use_true_random=False),
)
def test_property_separators_and_digit_scripts_never_flag(values, s1, s2, d1, d2, rnd):
    source = "Les chiffres : " + " et ".join(render(w, f, s1, d1) for w, f in values) + "."
    shuffled = list(values)
    rnd.shuffle(shuffled)
    target = "The figures: " + " and ".join(render(w, f, s2, d2) for w, f in shuffled) + "."
    assert number_check(source, target) is None


def _changed_last_digit(whole: int, fraction: str, bump: int) -> tuple[int, str]:
    if fraction:
        return whole, fraction[:-1] + str((int(fraction[-1]) + bump) % 10)
    return whole - whole % 10 + (whole % 10 + bump) % 10, fraction


@settings(max_examples=300, deadline=None)
@given(numbers, st.integers(min_value=1, max_value=9), styles, styles, scripts, scripts)
def test_property_a_changed_digit_is_always_flagged(value, bump, s1, s2, d1, d2):
    whole, fraction = value
    new_whole, new_fraction = _changed_last_digit(whole, fraction, bump)
    source = f"Le total était de {render(whole, fraction, s1, d1)} cette année."
    target = f"The total was {render(new_whole, new_fraction, s2, d2)} this year."
    found = number_check(source, target)
    assert found is not None and found.source and found.target
    assert Decimal(f"{whole}.{fraction or 0}") != Decimal(f"{new_whole}.{new_fraction or 0}")


@settings(max_examples=300, deadline=None)
@given(
    st.integers(min_value=1, max_value=10**8),
    st.integers(min_value=1, max_value=3),
    styles,
    styles,
    scripts,
    scripts,
)
def test_property_the_same_digits_at_another_size_are_always_flagged(whole, power, s1, s2, d1, d2):
    # 5 against 50 days: with no scale word next to either number, a size change is flagged
    source = f"Le total était de {render(whole, '', s1, d1)} jours."
    target = f"The total was {render(whole * 10**power, '', s2, d2)} days."
    for a, b in ((source, target), (target, source)):
        found = number_check(a, b)
        assert found is not None and found.source and found.target


@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=1, max_value=999), st.integers(min_value=0, max_value=9), styles)
def test_property_a_scale_word_matches_the_number_written_out(units, tenth, style):
    value = f"{units}.{tenth}" if tenth else str(units)
    written_out = render(units * 10**6 + tenth * 10**5, "", style, "ascii")
    assert number_check(f"It costs {value} million.", f"Es kostet {written_out}.") is None
    assert (
        number_check(f"It costs {value} million.", f"它价值{units * 100 + tenth * 10}万。") is None
    )


def test_a_scaled_number_pairs_with_one_number_only():
    # "billion" may mean 10^9 or 10^12, but it stands for one number, not both
    assert number_check("2 billion", "2,000,000,000 or 2,000,000,000,000") == NumberMismatch(
        source=(), target=("2,000,000,000,000",)
    )
    assert number_check("2,000,000,000 or 2,000,000,000,000", "2 billion") == NumberMismatch(
        source=("2,000,000,000,000",), target=()
    )


def test_a_long_table_of_numbers_is_checked_quickly():
    # every number differs, and one scale word sends the pairing down its slower path
    source = " x ".join(str(n) for n in range(20_000)) + ", 1 million"
    target = " x ".join(str(n + 100_000) for n in range(20_000)) + ", 1,000,000"
    start = time.perf_counter()
    found = number_check(source, target)
    assert time.perf_counter() - start < 5
    assert found is not None and len(found.source) == len(found.target) == 20_000
