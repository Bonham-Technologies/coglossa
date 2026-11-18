from coglossa.segment.text_metrics import SPACELESS, count_words, estimate_tokens


def test_count_words_spaced_scripts():
    assert count_words("Hello brave  new\nworld") == 4
    assert count_words("مرحبا بكم في البيت") == 4
    assert count_words("") == 0


def test_count_words_spaceless_scripts():
    assert count_words("今日は晴れです") == 4  # 7 chars -> ceil(7/2)
    assert count_words("Tokyo 東京") == 2  # 1 word + ceil(2/2)


def test_count_words_korean_is_a_spaced_script():
    assert count_words("한국어 문장 입니다") == 3


def test_cjk_compatibility_ideograph_is_spaceless():
    assert count_words("\uf900\uf901") == 1  # two spaceless chars -> ceil(2/2)
    assert SPACELESS.search("\ufaff") is not None
    assert SPACELESS.search("\uac00") is None  # Hangul syllable
    assert SPACELESS.search("\ue000") is None  # private use


def test_estimate_tokens_is_byte_based():
    assert estimate_tokens("abcde", 2.5) == 2
    assert estimate_tokens("日本", 2.5) == 3  # 6 bytes
    assert estimate_tokens("", 2.5) == 0
