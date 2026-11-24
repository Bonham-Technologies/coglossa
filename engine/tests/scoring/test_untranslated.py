import time

import pytest

from coglossa.schema import Entity, GlossaryEntry, Guide
from coglossa.scoring.untranslated import (
    MIN_LETTERS,
    Untranslated,
    kept_names,
    target_scripts,
    untranslated_check,
)

# --- rule (a): the translation is the source, copied through --------------------------------


def test_a_copied_passage_is_flagged_with_both_texts():
    source = "The committee approved the budget, but the vote was close."
    found = untranslated_check(source, source, "es")
    assert found == Untranslated(rule="copied", source=source, translation=source)
    assert found.as_evidence() == {"rule": "copied", "source": source, "translation": source}
    assert "may not have been translated" in found.explanation()


@pytest.mark.parametrize(
    "translation",
    [
        "the committee approved the budget but the vote was close",  # case and punctuation
        "  The committee approved   the budget,\nbut the vote was close!  ",  # whitespace
        "« The committee approved the budget — but the vote was close »",  # other punctuation
        "The cómmittee approved the budget, but the vote was close.",  # an accent changed
    ],
)
def test_case_whitespace_punctuation_and_accents_are_ignored(translation):
    source = "The committee approved the budget, but the vote was close."
    found = untranslated_check(source, translation, "es")
    assert found is not None and found.rule == "copied"
    assert found.as_evidence()["translation"] == translation


def test_a_real_translation_is_not_flagged():
    assert (
        untranslated_check(
            "The committee approved the budget, but the vote was close.",
            "El comité aprobó el presupuesto, pero la votación fue reñida.",
            "es",
        )
        is None
    )


def test_a_translation_that_only_adds_a_word_is_not_a_copy():
    source = "The committee approved the budget, but the vote was close."
    assert untranslated_check(source, "[es] " + source, "es") is None


def test_a_copy_needs_enough_letters_in_more_than_one_word():
    short = "Please speak."  # "speak": too few letters outside names to tell
    assert len("speak") < MIN_LETTERS
    assert untranslated_check(short, short, "fr") is None
    assert untranslated_check("Please speak slowly.", "Please speak slowly.", "fr") is not None
    # one word is often the same in both languages, however long
    assert untranslated_check("internationalization", "internationalization", "fr") is None
    # a script written without spaces has no words to count
    assert untranslated_check("委员会今天批准了新的预算", "委员会今天批准了新的预算", "ja")


def test_a_spaceless_script_copy_is_flagged():
    source = "委员会批准了预算，但投票结果非常接近。"
    found = untranslated_check(source, source, "ja")
    assert found is not None and found.rule == "copied"


# --- what is never flagged as a copy -----------------------------------------------------------


@pytest.mark.parametrize(
    "passage",
    [
        "2026-11-17 14:30 1,200.50 € 15 %",  # numbers only
        "Barack Obama, Angela Merkel and Emmanuel Macron",  # names only
        "New York Stock Exchange",
        "https://example.com/docs/getting-started?lang=en#install",  # a URL
        "See www.example.org or write to help@example.org.",
        "`pip install coglossa`",  # code
        "user_id = get_user(request.session)",
        "if (count > limit) { return fetchNextPage(cursor); }",
        "config/settings.yaml and src/main.tsx",
    ],
)
def test_names_numbers_urls_and_code_copied_as_they_are_are_not_flagged(passage):
    assert untranslated_check(passage, passage, "de") is None


def test_names_the_guide_keeps_are_not_counted():
    passage = "the voyage of the starship enterprise"
    assert untranslated_check(passage, passage, "fr") is not None
    guide = Guide(
        summary="",
        style_guide="",
        entities=[Entity(name="the starship enterprise", kind="other", rendering="")],
        glossary=[GlossaryEntry(term="voyage", rendering="voyage")],
    )
    keep = kept_names(guide)
    assert "the starship enterprise" in keep and "voyage" in keep
    assert untranslated_check(passage, passage, "fr", keep=keep) is None


def test_kept_names_skip_glossary_entries_the_guide_translates():
    guide = Guide(
        summary="",
        style_guide="",
        glossary=[GlossaryEntry(term="budget", rendering="presupuesto")],
        entities=[Entity(name="Lagos", kind="place", rendering="Lagos")],
    )
    assert kept_names(guide) == ["Lagos"]


# --- rule (b): the translation is written in a script the target language does not use --------


def test_latin_text_for_an_amharic_target_is_flagged():
    found = untranslated_check(
        "The museum opened in 1901 and has twelve rooms.",
        "The museum was opened in 1901 and it has twelve rooms.",
        "am",
    )
    assert found is not None and found.rule == "script"
    assert found.script == "Latn" and found.expected == ("Ethi",)
    evidence = found.as_evidence()
    assert evidence["rule"] == "script"
    assert evidence["source"] == "The museum opened in 1901 and has twelve rooms."
    assert evidence["translation"] == "The museum was opened in 1901 and it has twelve rooms."
    assert evidence["script"] == "Latn" and evidence["expected"] == ["Ethi"]
    text = found.explanation()
    assert "Latin script" in text and "Amharic" in text and "Ethiopic script" in text
    assert "may not have been translated" in text


@pytest.mark.parametrize(
    ("lang", "words"),
    [
        ("ja", "Japanese is written in Chinese characters or Japanese kana."),
        ("pa", "Punjabi is written in Arabic script or Gurmukhi script."),
        ("xx-Guru", "the language you are translating into is written in Gurmukhi script."),
    ],
)
def test_the_explanation_names_the_scripts_in_plain_words(lang, words):
    found = untranslated_check("It rains.", "it rains all day long here", lang)
    assert found is not None and found.rule == "script"
    assert "the translation is mostly in Latin script" in found.explanation()
    assert words in found.explanation()


def test_latin_text_for_a_hindi_target_is_flagged():
    found = untranslated_check(
        "Please speak slowly.", "please speak slowly so that everyone understands", "hi"
    )
    assert found is not None and found.rule == "script" and found.expected == ("Deva",)


def test_a_few_latin_names_inside_an_ethiopic_passage_are_not_flagged():
    translation = "ዶክተር Okafor ክትባቱ ለ Lagos ልጆች ደህንነቱ የተጠበቀ ነው ብለዋል።"
    assert untranslated_check("Dr. Okafor said the vaccine is safe.", translation, "am") is None


@pytest.mark.parametrize(
    ("translation", "lang"),
    [
        ("该函数返回一个 boolean 值，表示 cache 是否命中。", "zh"),
        ("請打開 settings 並啟用 dark mode 功能", "zh-Hant"),
        ("設定を開いて、editor window の dark mode を有効にします。", "ja"),
        ("deployment file 과 함께 kubectl apply 명령을 사용합니다.", "ko"),
        ("कृपया settings खोलें और dark mode चालू करें", "hi"),
        ("يرجى فتح settings وتفعيل dark mode", "ar"),
    ],
)
def test_a_few_english_terms_in_a_dense_script_are_not_flagged(translation, lang):
    # a Chinese character or a Hangul syllable holds more than a Latin letter: counted one for
    # one, these correct translations read as mostly Latin
    assert untranslated_check("Open the settings.", translation, lang) is None


def test_a_passage_mostly_in_english_is_still_flagged_for_a_dense_script():
    found = untranslated_check(
        "x", "ቤተ መዘክሩ በ1901 ተከፈተ። the museum has twelve rooms and a garden", "am"
    )
    assert found is not None and found.rule == "script" and found.script == "Latn"
    found = untranslated_check("x", "会议将于明天中午在办公室举行，请准时参加", "en")
    assert found is not None and found.script == "Hani"


def test_a_copy_within_one_language_and_script_is_not_flagged():
    # en-US to en-GB is allowed: most passages rightly stay as they are
    passage = "The committee approved the budget, but the vote was close."
    assert untranslated_check(passage, passage, "en-GB", source_lang="en-US") is None
    assert untranslated_check(passage, passage, "en", source_lang="en_GB") is None
    assert untranslated_check(passage, passage, "en-GB", source_lang="fr").rule == "copied"
    # the same language in another script still has to be converted
    chinese = "委员会批准了预算，但投票结果非常接近。"
    assert untranslated_check(chinese, chinese, "zh-Hant", source_lang="zh-Hans").rule == "copied"


def test_a_correct_hindi_translation_with_a_url_is_not_flagged():
    translation = "कृपया धीरे बोलें और https://example.com/help/getting-started देखें।"
    assert untranslated_check("Please speak slowly.", translation, "hi") is None


def test_the_target_scripts_come_from_the_language():
    assert target_scripts("am") == frozenset({"Ethi"})
    assert target_scripts("hi-IN") == frozenset({"Deva"})
    assert target_scripts("zh-TW") == frozenset({"Hani"})
    assert target_scripts("ja") == frozenset({"Hani", "Hira", "Kana"})
    assert target_scripts("ar") == frozenset({"Arab"})  # not ar-Latn
    assert target_scripts("ar-Latn") == frozenset({"Latn"})
    assert "Latn" in target_scripts("sr")  # Serbian is written in both
    assert target_scripts("xx") is None and target_scripts(None) is None


def test_a_language_whose_script_is_unknown_is_never_flagged_by_script():
    assert untranslated_check("Please speak slowly.", "speak slowly please everyone", "xx") is None


def test_chinese_for_a_japanese_target_is_not_seen():
    # Japanese is written with Chinese characters too: the script rule cannot tell them apart
    assert untranslated_check("It rains.", "今天下雨了，我们明天再见面吧。", "ja") is None


# --- rule (b) leaves same-script pairs to rule (a) -------------------------------------------


def test_spanish_left_in_a_portuguese_translation_is_only_caught_as_a_copy():
    source = "El presidente visitó la ciudad el martes por la tarde."
    assert untranslated_check(source, source, "pt").rule == "copied"
    # wrong language, same script: not seen
    assert untranslated_check("It rains.", source, "pt") is None


def test_a_copy_into_another_script_is_reported_as_a_copy_once():
    source = "The museum opened in 1901 and has twelve rooms."
    found = untranslated_check(source, source, "am")
    assert found is not None and found.rule == "copied"


def test_a_blank_translation_is_not_flagged():
    assert untranslated_check("The museum opened in 1901 and has twelve rooms.", "  ", "am") is None


def test_a_long_passage_is_checked_quickly():
    passage = "the quick brown fox jumps over the lazy dog " * 2000
    started = time.perf_counter()
    assert untranslated_check(passage, passage, "am") is not None
    assert time.perf_counter() - started < 1.0


def test_a_large_guide_does_not_slow_the_check_down():
    keep = [f"term number {n}" for n in range(20_000)] + ["the starship enterprise"]
    passage = "the voyage of the starship enterprise " * 20
    started = time.perf_counter()
    for _ in range(50):
        untranslated_check(passage, passage, "fr", keep=keep)
    assert time.perf_counter() - started < 2.0
    # a name that occurs only in another case is still left out
    assert (
        untranslated_check("the starship ENTERPRISE", "the starship ENTERPRISE", "fr", keep=keep)
        is None
    )
