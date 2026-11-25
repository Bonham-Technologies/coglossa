import pytest

from coglossa.scoring.literals import (
    Changed,
    LiteralMismatch,
    Missing,
    find_literals,
    literal_check,
)

# --- web addresses ----------------------------------------------------------------------------


def test_a_changed_web_address_is_flagged_with_both_values():
    found = literal_check(
        "See https://example.com/docs/setup for details.",
        "Voir https://example.com/fr/docs/setup pour plus de détails.",
    )
    assert found == LiteralMismatch(
        changed=(
            Changed("link", "https://example.com/docs/setup", "https://example.com/fr/docs/setup"),
        )
    )
    assert found.as_evidence() == {
        "changed": [
            {
                "kind": "link",
                "source": "https://example.com/docs/setup",
                "translation": "https://example.com/fr/docs/setup",
            }
        ],
        "missing": [],
    }
    assert "https://example.com/docs/setup" in found.explanation()
    assert "https://example.com/fr/docs/setup" in found.explanation()


def test_a_missing_web_address_is_flagged():
    found = literal_check("Read www.example.org/guide first.", "Lisez d'abord le guide.")
    assert found == LiteralMismatch(missing=(Missing("link", "www.example.org/guide"),))
    assert "www.example.org/guide" in found.explanation()


def test_an_identical_web_address_is_not_flagged():
    assert (
        literal_check(
            "Go to https://example.com/a?b=1&c=2#top now.",
            "Allez sur https://example.com/a?b=1&c=2#top maintenant.",
        )
        is None
    )


@pytest.mark.parametrize(
    ("source", "translation"),
    [
        # trailing sentence punctuation is not part of the address
        ("Visit https://example.com/page.", "Besuchen Sie https://example.com/page!"),
        ("Visit https://example.com/page, then log in.", "Visitez https://example.com/page puis"),
        ("(see https://example.com/a_(b))", "(voir https://example.com/a_(b))"),
        ("(see https://example.com/page)", "voir https://example.com/page"),
        ("Visit https://example.com/page.", "请访问https://example.com/page。"),
        ("Visit www.example.com today.", "今天访问www.example.com。"),
        ('He said "https://example.com/x".', "Er sagte „https://example.com/x“."),
        ("[the docs](https://example.com/d)", "[la doc](https://example.com/d)"),
        ("<https://example.com/x>", "«https://example.com/x»"),
    ],
)
def test_addresses_next_to_punctuation_or_other_scripts_are_read_the_same(source, translation):
    assert literal_check(source, translation) is None


# --- email addresses --------------------------------------------------------------------------


def test_a_changed_email_address_is_flagged():
    found = literal_check(
        "Write to support@example.com.", "Écrivez à support@exemple.com pour toute question."
    )
    assert found == LiteralMismatch(
        changed=(Changed("email", "support@example.com", "support@exemple.com"),)
    )


def test_text_around_an_email_address_may_be_localised():
    assert (
        literal_check(
            "Questions? Email jane.doe+news@mail.example.co.uk.",
            "¿Preguntas? Escribe a jane.doe+news@mail.example.co.uk, te responderemos.",
        )
        is None
    )
    assert literal_check("Mail: a@example.com", "メール：a@example.comまで") is None


def test_an_email_address_inside_a_web_address_is_part_of_the_address():
    assert [(x.kind, x.text) for x in find_literals("ftp://user@host.example.com/f")] == [
        ("link", "ftp://user@host.example.com/f")
    ]


# --- placeholders -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "placeholder",
    ["{name}", "{0}", "{}", "{user.name}", "{0:.2f}", "{{var}}", "{{ count }}", "${total}"]
    + ["%s", "%d", "%(name)s", "%1$s", "%.2f", "%@", "%-5d", "%02d"],
)
def test_placeholders_are_found(placeholder):
    assert [(x.kind, x.text) for x in find_literals(f"Hello {placeholder} there")] == [
        ("placeholder", placeholder)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Prices rose 50% of the time.",
        "It was 100%done.",
        "Use 50%s",  # a number before % is a percentage
        "{count, plural, one {# item} other {# items}}",
        "a%2Fb and %E2%80%99",
        "Mix 100% d'eau.",
    ],
)
def test_percent_signs_and_message_syntax_are_not_placeholders(text):
    assert [x for x in find_literals(text) if x.kind == "placeholder"] == []


def test_placeholders_are_compared_as_a_multiset():
    # a duplicated placeholder dropped once
    found = literal_check("{name} and {name} left.", "{name} est parti.")
    assert found == LiteralMismatch(missing=(Missing("placeholder", "{name}"),))
    # a dropped %s
    found = literal_check("Saved %s of %s files.", "%s Dateien gespeichert.")
    assert found == LiteralMismatch(missing=(Missing("placeholder", "%s"),))
    # a translated {{var}}
    found = literal_check("Hi {{user}}!", "Salut {{utilisateur}} !")
    assert found == LiteralMismatch(
        changed=(Changed("placeholder", "{{user}}", "{{utilisateur}}"),)
    )
    # moved, not changed
    assert literal_check("{a} sent {b} to {a}", "{a} a envoyé à {a} : {b}") is None


def test_only_what_the_source_has_is_required():
    # an added address or placeholder is not this check's business
    assert literal_check("Hello.", "Bonjour, voir https://example.com.") is None
    assert literal_check("No placeholders here.", "Aucun {x} ici.") is None


def test_changes_are_paired_by_kind_in_order():
    found = literal_check(
        "Mail a@example.com or see https://example.com/a and {name}.",
        "Écrire à b@example.com ou https://example.com/b.",
    )
    assert found == LiteralMismatch(
        changed=(
            Changed("email", "a@example.com", "b@example.com"),
            Changed("link", "https://example.com/a", "https://example.com/b"),
        ),
        missing=(Missing("placeholder", "{name}"),),
    )
    text = found.explanation()
    assert "a@example.com" in text and "{name}" in text and "https://example.com/b" in text


# --- protected text from the file (inline code, translate="no") -------------------------------


def test_protected_text_must_appear_as_written():
    assert literal_check("Run make test now.", "Lancez make test.", ("make test",)) is None
    found = literal_check("Run make test now.", "Lancez faire test.", ("make test",))
    assert found == LiteralMismatch(missing=(Missing("code", "make test"),))


def test_protected_text_counts_whole_words_only_next_to_latin_letters():
    # "if" inside "different" is not the protected word "if"
    found = literal_check("Use if differently.", "Benutze es anders, different.", ("if",))
    assert found == LiteralMismatch(missing=(Missing("code", "if"),))
    assert literal_check("Use if here.", "这里使用if语句。", ("if",)) is None


def test_protected_text_is_a_multiset_and_not_counted_twice_as_an_address():
    found = literal_check("x and x", "x", ("x", "x"))
    assert found == LiteralMismatch(missing=(Missing("code", "x"),))
    found = literal_check(
        "See https://example.com/a", "Voir https://example.com/b", ("https://example.com/a",)
    )
    assert found == LiteralMismatch(
        changed=(Changed("link", "https://example.com/a", "https://example.com/b"),)
    )


def test_nothing_to_check():
    assert literal_check("Il pleut.", "It rains.") is None
    assert literal_check("", "") is None


def test_the_explanation_is_plain_words():
    one = LiteralMismatch(missing=(Missing("placeholder", "{name}"),)).explanation()
    assert one.startswith("A placeholder is missing")
    assert "{name}" in one and "copied exactly" in one
    two = LiteralMismatch(
        changed=(Changed("link", "https://a.example/x", "https://a.example/y"),),
        missing=(Missing("email", "a@example.com"),),
    ).explanation()
    assert "https://a.example/x" in two and "a@example.com" in two and "kind" not in two
