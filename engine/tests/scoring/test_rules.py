import pytest

from coglossa.schema import Agreement, Entity, ErrorSeverity, GlossaryEntry, Guide, JudgeError
from coglossa.scoring.rules import (
    GuideViolation,
    agreement_signal,
    guide_check,
    judge_penalties,
    length_ratio,
    term_in,
)

PENALTIES = {ErrorSeverity.MINOR: 1.0, ErrorSeverity.MAJOR: 5.0, ErrorSeverity.CRITICAL: 10.0}


@pytest.mark.parametrize(
    ("agreement", "sim", "expected"),
    [
        (Agreement.AGREE, None, 1.0),
        (Agreement.MINOR, None, 0.7),
        (Agreement.MAJOR, 0.6, 0.4),
        (Agreement.UNKNOWN, 0.8, 0.8),
        (Agreement.UNKNOWN, None, None),
    ],
)
def test_agreement_signal(agreement, sim, expected):
    result = agreement_signal(agreement, sim)
    assert result == pytest.approx(expected) if expected is not None else result is None


GUIDE = Guide(
    summary="s",
    style_guide="g",
    glossary=[
        GlossaryEntry(term="Gestalt", rendering="form", locked=True),
        GlossaryEntry(term="Angst", rendering="dread"),
    ],
    entities=[Entity(name="Anna", rendering="Ana", kind="person")],
)


def test_guide_check_all_satisfied():
    signal, violations = guide_check("Anna fühlt Angst.", "Ana feels dread.", GUIDE)
    assert signal == 1.0 and violations == []


def test_guide_check_reports_violations():
    signal, violations = guide_check("Anna sah die GESTALT.", "Anna saw the shape.", GUIDE)
    assert signal == 0.0
    assert violations == [
        GuideViolation(term="Gestalt", expected="form", kind="glossary", locked=True),
        GuideViolation(term="Anna", expected="Ana", kind="entity", locked=False),
    ]
    assert violations[0].as_evidence() == {
        "term": "Gestalt",
        "expected": "form",
        "kind": "glossary",
        "locked": True,
    }


def test_guide_check_not_applicable():
    assert guide_check("Nichts hier.", "Nothing here.", GUIDE) == (None, [])


@pytest.mark.parametrize(
    ("term", "text", "found"),
    [
        ("UE", "Ce que tu veux.", False),  # short terms need both word boundaries
        ("UE", "Les pays de l'UE.", True),
        ("Art", "Il faut partir.", False),
        ("Art", "Modern art.", True),
        ("Ana", "A banana.", False),
        ("Ana", "Ana's book.", True),
        ("Gestalt", "Die Gestalten tanzen.", True),  # longer terms: left boundary only
        ("Gestalt", "Urgestalt.", False),
        ("東京", "私は東京に住んでいる。", True),  # spaceless scripts: plain substring
        ("ภาษา", "เรียนภาษาไทย", True),
        ("gestalt", "GESTALT", True),
        ("Coglossa", "新しいCoglossaを使う", True),  # Latin term touching spaceless script
        ("API", "このAPIは", True),
        ("EU", "EU는 결정했다", True),  # ... or Hangul
        ("Art", "新しいpartirを使う", False),  # still not inside a longer Latin word
        ("Art", "partir 東京", False),
        ("  ", "anything", False),
    ],
)
def test_term_in_respects_word_boundaries(term, text, found):
    assert term_in(term, text) is found


def test_guide_check_does_not_match_inside_words():
    guide = Guide(
        summary="s",
        style_guide="g",
        glossary=[
            GlossaryEntry(term="Art", rendering="Kunst", locked=True),
            GlossaryEntry(term="Union", rendering="UE", locked=True),
        ],
    )
    # "Art" is not in "partir"; the rendering "UE" is not in "que"
    signal, violations = guide_check("Partir avec l'Union.", "Partir: ce que l'union veut.", guide)
    assert signal == 0.0
    assert violations == [GuideViolation("Union", "UE", "glossary", True)]


def test_guide_check_finds_latin_term_next_to_cjk():
    guide = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="Coglossa", rendering="Coglossa", locked=True)],
    )
    assert guide_check("Coglossa traduit.", "新しいCoglossaは翻訳する。", guide) == (1.0, [])


def test_length_ratio():
    assert length_ratio("abcd", " ab ") == 0.5
    assert length_ratio("   ", "x") is None


def test_judge_penalties_split_accuracy_and_style():
    errors = [
        JudgeError(category="accuracy/omission", severity="major", explanation="x"),
        JudgeError(category="terminology", severity="minor", explanation="x"),
        JudgeError(category="fluency", severity="critical", explanation="x"),
        JudgeError(category="locale", severity="minor", explanation="x"),
    ]
    assert judge_penalties(errors, PENALTIES) == (6.0, 11.0)
    assert judge_penalties([], PENALTIES) == (0.0, 0.0)


def test_locked_entity_violations_are_locked():
    guide = Guide(
        summary="",
        style_guide="",
        entities=[
            Entity(name="Ana", rendering="Anna", kind="person", locked=True),
            Entity(name="Rio", rendering="Rio de Janeiro", kind="place"),
        ],
    )
    _, violations = guide_check("Ana fue a Rio.", "She went to the city.", guide)
    assert [(v.term, v.kind, v.locked) for v in violations] == [
        ("Ana", "entity", True),
        ("Rio", "entity", False),
    ]
