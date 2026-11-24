import random

import pytest

from coglossa.bench.inject import (
    MEANING_KINDS,
    STYLE_KINDS,
    InjectionKind,
    inject_with_model,
    leave_untranslated,
    omit,
    swap_name,
    swap_number,
    violate_locked_term,
)


def test_leave_untranslated_copies_the_source_through():
    inj = leave_untranslated("Please speak slowly.")
    assert inj.kind is InjectionKind.UNTRANSLATED and inj.kind in MEANING_KINDS
    assert inj.text == "Please speak slowly." and inj.detail == "left untranslated"
    assert leave_untranslated("  ") is None


def test_omit_drops_the_longest_clause():
    inj = omit("The committee approved the budget, but the vote was very close indeed.")
    assert inj.kind is InjectionKind.OMISSION
    assert inj.text == "The committee approved the budget"
    assert "dropped" in inj.detail


def test_omit_without_clauses_drops_the_tail():
    inj = omit("Please speak much more slowly to the children today")
    assert (
        inj.text == "Please speak much more slowly"
    )  # 9 words: the first 5 (60%, rounded down) stay


def test_omit_counts_characters_without_spaces():
    inj = omit("我们明天在图书馆见面然后一起吃饭")
    assert inj is not None and len(inj.text) <= 0.6 * len("我们明天在图书馆见面然后一起吃饭") + 1


def test_omit_does_not_apply_to_tiny_text():
    assert omit("Hi.") is None


def test_swap_number_changes_one_number():
    inj = swap_number("The river flows north for 300 kilometres.", random.Random(1))
    assert inj.kind is InjectionKind.NUMBER
    assert inj.text != "The river flows north for 300 kilometres."
    assert "300" not in inj.text and "kilometres" in inj.text
    assert swap_number("No digits here.", random.Random(1)) is None


def test_swap_name_uses_a_name_shared_with_the_source():
    src = "Yesterday Okafor said the vaccine is safe."
    ref = "Ayer Okafor dijo que la vacuna es segura."
    inj = swap_name(src, ref, ["Marie llegó a Paris.", "Lagos es grande."], random.Random(1))
    assert inj.kind is InjectionKind.NAME
    assert "Okafor" not in inj.text and inj.text != ref
    assert swap_name("no names", "sin nombres", [], random.Random(1)) is None


def test_locked_term_carries_the_guide_entry():
    src = "Yesterday Okafor said the vaccine is safe."
    ref = "Ayer Okafor dijo que la vacuna es segura."
    inj = violate_locked_term(src, ref, ["Ayer llegó Marie."], random.Random(1))
    assert inj.kind is InjectionKind.LOCKED_TERM
    assert inj.locked_term.term == "Okafor" and inj.locked_term.rendering == "Okafor"
    assert inj.locked_term.locked is True
    assert "Okafor" not in inj.text


async def test_model_injector_negates(fake_runner):
    inj = await inject_with_model(fake_runner, "m", InjectionKind.NEGATION, "es", "Llueve hoy.")
    assert inj.kind is InjectionKind.NEGATION and inj.text != "Llueve hoy."


async def test_model_injector_returning_the_input_does_not_apply(fake_runner_echo):
    assert (
        await inject_with_model(fake_runner_echo, "m", InjectionKind.REGISTER, "es", "Llueve hoy.")
        is None
    )


async def test_rule_kinds_are_not_sent_to_the_model(fake_runner):
    with pytest.raises(ValueError, match="only negation and register"):
        await inject_with_model(fake_runner, "m", InjectionKind.NUMBER, "es", "x")


def test_kind_sets_partition_the_kinds():
    assert set(InjectionKind) == MEANING_KINDS | STYLE_KINDS
    assert not MEANING_KINDS & STYLE_KINDS


def test_omit_does_not_apply_below_the_minimum_size():
    assert omit("Hi, yo") is None


def test_swap_number_is_deterministic_and_keeps_leading_digits():
    a = swap_number("In 1998 it rained.", random.Random(3))
    b = swap_number("In 1998 it rained.", random.Random(3))
    assert a == b and a.text.startswith("In 199") and a.text != "In 1998 it rained."


def test_swap_name_needs_a_replacement_name():
    src = "Yesterday Okafor said so."
    ref = "Ayer Okafor lo dijo."
    assert swap_name(src, ref, ["sin nombres"], random.Random(1)) is None
    assert violate_locked_term(src, ref, [], random.Random(1)) is None
    assert (
        violate_locked_term("no names", "sin nombres", ["Marie llegó."], random.Random(1)) is None
    )


def test_sentence_initial_words_are_not_names():
    # "Cuando" and "Según" open their sentences, so they are not replacement names
    src, ref = "We met Okafor today", "Vimos a Okafor hoy"
    assert swap_name(src, ref, ["Cuando llueve", "Según él."], random.Random(1)) is None
    # a capitalized cognate opening both sentences is not a present name either
    assert (
        swap_name("Okafor spoke", "Okafor a parlé", ["Ayer vio a Marie"], random.Random(1)) is None
    )
    inj = swap_name(src, ref, ["Cuando llueve. Vi a Marie."], random.Random(1))
    assert inj.text == "Vimos a Marie hoy"
    # a name right after sentence-ending punctuation of any script is initial
    assert swap_name(src, ref, ["Hola。Marie", "Qué! Lagos"], random.Random(1)) is None


@pytest.mark.parametrize(
    ("text", "digits"), [("طوله ٣٠٠ كيلومتر", "٠١٢٣٤٥٦٧٨٩"), ("लंबाई ३०० किमी", "०१२३४५६७८९")]
)
def test_swap_number_keeps_the_script_of_the_digits(text, digits):
    inj = swap_number(text, random.Random(2))
    new = [c for c in inj.text if c in digits]
    assert len(new) == 3 and new[:2] == [digits[3], digits[0]] and new[2] != digits[0]
    assert not any(c.isascii() and c.isdigit() for c in inj.text)


def test_omit_splits_arabic_clauses():
    inj = omit("وافقت اللجنة على الميزانية، لكن التصويت كان متقاربا جدا بالفعل.")
    assert inj.text == "وافقت اللجنة على الميزانية"
    assert "،" not in inj.text


def test_omit_falls_back_to_the_tail_when_the_clause_is_too_small():
    ref = "alpha one, bravo two, gamma six, delta four, echo five"
    inj = omit(ref)  # dropping one of five clauses removes under 25%
    assert inj.text == "alpha one, bravo two, gamma six,"
    assert inj.detail == "dropped 'delta four, echo five'"
