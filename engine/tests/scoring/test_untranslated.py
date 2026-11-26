import time

import pytest

from coglossa.schema import Entity, GlossaryEntry, Guide
from coglossa.scoring.untranslated import (
    _URL,
    MIN_LETTERS,
    Untranslated,
    _citation,
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


@pytest.mark.parametrize(
    "text",
    [
        "a." * 100_000,  # dots and letters with no @ or :// (a hash list, a flattened table)
        "a" * 200_000,  # one long word
        "a." * 100_000 + "@",  # an @ with nothing after it
        "a.b-c+" * 35_000,  # every character a link or address may contain
    ],
)
def test_the_link_and_address_pattern_runs_in_linear_time(text):
    started = time.perf_counter()
    _URL.sub(" ", text)
    assert time.perf_counter() - started < 1.0
    started = time.perf_counter()
    untranslated_check(text, text, "am")
    assert time.perf_counter() - started < 2.0


@pytest.mark.parametrize(
    ("text", "removed"),
    [
        ("see https://example.org/a?b=c now", "https://example.org/a?b=c"),
        ("open git+ssh://host/repo.git", "git+ssh://host/repo.git"),
        ("go to www.example.com/path", "www.example.com/path"),
        ("write to first.last+tag@mail.example.co.uk today", "first.last+tag@mail.example.co.uk"),
        ("a@b.com+x@y.org", "a@b.com"),
    ],
)
def test_the_same_links_and_addresses_are_removed(text, removed):
    assert _URL.search(text).group() == removed


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


# --- fewer false positives (CGL-128) -----------------------------------------------------------


@pytest.mark.parametrize(
    ("translation", "lang"),
    [
        ("Το open source software είναι δωρεάν", "el"),
        ("Откройте settings then privacy options", "ru"),
        ("Используйте machine learning и deep learning для big data analytics pipeline", "ru"),
        ("이 앱은 dark mode and offline sync 지원", "ko"),
    ],
)
def test_correct_loanword_heavy_translations_are_not_flagged(translation, lang):
    # the first word is in the target script, so it is counted even though it is capitalised,
    # and a sentence is only read as written in another script when the target script holds
    # under a fifth of it
    assert untranslated_check("Open the settings.", translation, lang) is None


@pytest.mark.parametrize(("lang", "script"), [("am", "Ethi"), ("hi", "Deva"), ("ru", "Cyrl")])
@pytest.mark.parametrize(
    "translation",
    [
        "The museum was opened in 1901 and it has twelve rooms.",
        "the museum was opened in 1901 and it has twelve rooms",
    ],
)
def test_latin_text_is_still_flagged_for_other_scripts(translation, lang, script):
    found = untranslated_check("x", translation, lang)
    assert found is not None and found.rule == "script"
    assert found.script == "Latn" and found.expected == (script,)


def test_a_sentence_in_another_script_after_one_in_the_target_script_is_flagged():
    found = untranslated_check(
        "x", "Музей открылся в 1901 году. the museum has twelve rooms and a large garden", "ru"
    )
    assert found is not None and found.rule == "script" and found.script == "Latn"
    # the same words, mostly translated, are not
    assert (
        untranslated_check("x", "Музей открылся в 1901 году. В музее двенадцать залов.", "ru")
        is None
    )


@pytest.mark.parametrize(
    "entry",
    [
        "Smith, J., & Jones, K. (2020). Machine translation quality estimation. "
        "Computational Linguistics, 46(2), 123–145.",
        "Vaswani, A., Shazeer, N., Parmar, N., et al. (2017). Attention is all you need. "
        "In Advances in Neural Information Processing Systems 30, pp. 5998–6008.",
        "Koehn, Philipp. Statistical machine translation. Cambridge University Press, 2010.",
        "Bahdanau, D., Cho, K. and Bengio, Y. 2015. Neural machine translation by jointly "
        "learning to align and translate. https://arxiv.org/abs/1409.0473",
        "[4] Papineni, K., Roukos, S., Ward, T. and Zhu, W.-J. (2002). BLEU: a method for "
        "automatic evaluation of machine translation. Proceedings of ACL, 311–318.",
    ],
)
@pytest.mark.parametrize(("source_lang", "lang"), [("en", "de"), ("fr", "en"), ("en", "ru")])
def test_bibliography_entries_are_not_flagged(entry, source_lang, lang):
    assert untranslated_check(entry, entry, lang, source_lang=source_lang) is None


def test_a_sentence_with_a_year_is_not_taken_for_a_citation():
    for passage in (
        "Marie arrived in Paris on 14 July 1998, and she stayed for a year.",
        "The museum (built in 1901) has twelve rooms and a garden.",
        "In 2020, Dr. Okafor said the vaccine is safe for children.",
    ):
        assert untranslated_check(passage, passage, "fr").rule == "copied"


@pytest.mark.parametrize(
    ("passage", "source_lang", "lang"),
    [
        ("total (in millions)", "en", "fr"),
        ("kg per m2 per year", "en", "de"),
        ("see https://example.com/docs for details", "en", "es"),
        ("information et communication", "fr", "en"),
        ("Ad astra per aspera", "la", "en"),
        ("E pluribus unum", "la", "fr"),
    ],
)
def test_short_labels_and_mottos_that_stay_the_same_are_not_flagged_as_copies(
    passage, source_lang, lang
):
    assert untranslated_check(passage, passage, lang, source_lang=source_lang) is None


def test_a_copied_sentence_or_a_longer_label_is_still_flagged():
    # a short passage that ends like a sentence, or one of five words or more
    assert untranslated_check("Please speak slowly.", "Please speak slowly.", "fr").rule == "copied"
    label = "annual report on climate change"
    assert untranslated_check(label, label, "fr").rule == "copied"


def test_chinese_regions_stand_for_their_script():
    chinese = "委员会批准了预算，但投票结果非常接近。"
    assert untranslated_check(chinese, chinese, "zh-TW", source_lang="zh-CN").rule == "copied"
    assert untranslated_check(chinese, chinese, "zh-HK", source_lang="zh-SG").rule == "copied"
    assert untranslated_check(chinese, chinese, "zh_MO", source_lang="zh-Hans").rule == "copied"
    # the same script under another name is the same language
    assert untranslated_check(chinese, chinese, "zh-Hans", source_lang="zh-CN") is None
    assert untranslated_check(chinese, chinese, "zh-Hant-TW", source_lang="zh-TW") is None
    assert target_scripts("zh-HK") == frozenset({"Hani"})


def test_an_all_caps_copy_is_flagged():
    source = "THE COMMITTEE APPROVED THE BUDGET, BUT THE VOTE WAS CLOSE."
    found = untranslated_check(source, source, "es")
    assert found is not None and found.rule == "copied"
    found = untranslated_check("x", "PLEASE SPEAK SLOWLY SO THAT EVERYONE UNDERSTANDS", "am")
    assert found is not None and found.rule == "script"
    # a few acronyms in a passage are still names
    assert untranslated_check("NASA and ESA", "NASA and ESA", "fr") is None
    assert untranslated_check("x", "ናሳ NASA እና ESA በጋራ ይሰራሉ", "am") is None


@pytest.mark.parametrize(
    ("lang", "scripts", "name"),
    [
        ("no", {"Latn"}, "Norwegian"),
        ("iw", {"Hebr"}, "Hebrew"),
        ("fil", {"Latn"}, "Filipino"),
        ("dv", {"Thaa"}, "Dhivehi"),
        ("mo", {"Latn"}, "Romanian"),
    ],
)
def test_older_and_other_language_codes_are_checked_by_script(lang, scripts, name):
    assert target_scripts(lang) == frozenset(scripts)
    if scripts == {"Latn"}:
        return
    found = untranslated_check("x", "please speak slowly so that everyone understands", lang)
    assert found is not None and found.rule == "script"
    assert name in found.explanation()


def test_an_old_code_and_its_new_one_are_the_same_language():
    passage = "הוועדה אישרה את התקציב, אך ההצבעה הייתה צמודה."
    assert untranslated_check(passage, passage, "he", source_lang="iw") is None
    assert untranslated_check(passage, passage, "en", source_lang="iw").rule == "copied"


@pytest.mark.parametrize(
    "passage",
    [
        # prose that cites works, or a legal provision with a date, is not a reference entry
        "Recent work (Smith et al., 2020; Jones, 2019) shows that machine translation has "
        "improved a lot.",
        "Smith et al. (2020) found that the model works well on long documents.",
        "Since May 25, 2018, Article 5(2) requires controllers to demonstrate compliance.",
        "1. Since May 25, 2018, Article 5(2) requires controllers to demonstrate compliance.",
    ],
)
def test_prose_that_cites_works_is_still_checked(passage):
    assert untranslated_check(passage, passage, "de", source_lang="en").rule == "copied"
    found = untranslated_check("x", passage, "am")
    assert found is not None and found.rule == "script"


@pytest.mark.parametrize(
    "passage",
    [
        # a numbered paragraph of a judgment, full of dates and case names
        "[12] On May 25, 2018, the appellant filed a notice of appeal against the decision of "
        "the tribunal.",
        "[12] In R v Smith (2018), the court held that the evidence was inadmissible and "
        "ordered a new trial.",
        # a name and an initial, then a sentence
        "Miller, J. delivered the judgment of the court on March 3, 2020, and dismissed the "
        "appeal with costs.",
        "Smith, J. (2020) argues that machine translation has improved, and the rest of this "
        "section explains why.",
        # a sentence, then a year in brackets
        "The report was published. (2020) It found that the factory had polluted the river for "
        "years; see pp. 4.",
    ],
)
def test_prose_that_starts_like_a_reference_is_still_checked(passage):
    found = untranslated_check(passage, passage, "fr", source_lang="en")
    assert found is not None and found.rule == "copied"
    found = untranslated_check("x", passage, "ru")
    assert found is not None and found.rule == "script"


@pytest.mark.parametrize(
    "entry",
    [
        "World Health Organization. (2020). Global tuberculosis report 2020. Geneva: WHO, "
        "pp. 12-20.",
        "Smith J, Jones K. Machine translation quality. Comput Linguist. 2020;46(2):123-45.",
        'J. Smith and K. Jones, "Machine translation quality," IEEE Trans., vol. 4, '
        "pp. 1-10, 2020.",
        "van der Berg, A. (2019). Neural models for translation. Journal of MT, 12(3), 45-67.",
        "Smith, J. A., & Doe, R. (2020, May 3). A post about translation. Retrieved from "
        "https://example.com",
        "Koehn, Philipp. 2010. Statistical Machine Translation. Cambridge: Cambridge "
        "University Press.",
        "12. Vaswani, A., et al. (2017). Attention is all you need. NeurIPS, 30, 5998-6008.",
        "Smith J, Jones K, et al. Machine translation quality. Comput Linguist. "
        "2020 Mar;46(2):123-45.",
    ],
)
def test_other_reference_styles_are_not_flagged(entry):
    assert untranslated_check(entry, entry, "de", source_lang="en") is None


def test_a_copied_headline_in_sentence_case_is_flagged():
    # its capitalised first word is not counted as a letter, but it is one of the five words
    headline = "Government announces new tax plan"
    assert untranslated_check(headline, headline, "de", source_lang="en").rule == "copied"
    assert untranslated_check("Click Save to keep changes", "Click Save to keep changes", "de")


def test_acronyms_in_a_script_without_capitals_are_not_a_passage_in_capitals():
    assert untranslated_check("x", "ใช้ API SDK CLI GPU TPU NASA", "th") is None


def test_a_long_reference_like_passage_is_checked_quickly():
    start = time.perf_counter()
    # each would take exponential time if a pattern could match the same text in two ways
    for passage in ("Ab" + "-Ab" * 200 + " 1", ("A.  -  " * 200) + "1", "ab " * 200):
        untranslated_check(passage, passage, "fr")
    assert time.perf_counter() - start < 2


@pytest.mark.parametrize(
    "passage",
    [
        "Ab, A., and " * 9000 + "1",
        "Ab, A. " * 15000 + "1",
        "A. Ab and " * 10000 + "1",
        "Ab A, " * 17000 + "1",
        "Ab, Abc Ab A. " * 7000 + "1",
        "Ab, Abc. " + "ab. " * 25000 + "1",
        "World of " * 11000 + "1",
        "[1] " + "Ab, A.-B. " * 10000 + "(2020)",
        "Ab, A. " + "ab, 2020; " * 10000,
    ],
)
def test_a_100_kb_reference_like_passage_is_checked_quickly(passage):
    # each is a long author list, or an entry's start, that turns out not to be an entry
    assert len(passage) >= 90_000
    start = time.perf_counter()
    assert not _citation(passage)
    assert time.perf_counter() - start < 0.5
    start = time.perf_counter()
    untranslated_check(passage, passage, "fr")
    assert time.perf_counter() - start < 3
