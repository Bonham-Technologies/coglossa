import pytest

from coglossa.bench.codes import FLORES_CODES
from coglossa.languages import LANGUAGE_NAMES, label


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("ha", "Hausa (ha)"),
        ("yo", "Yoruba (yo)"),
        ("sw", "Swahili (sw)"),
        ("pt-BR", "Portuguese (pt-BR)"),
        ("zh-Hans", "Chinese, in Simplified Chinese characters (zh-Hans)"),
        ("zh-hant", "Chinese, in Traditional Chinese characters (zh-hant)"),
        ("ar-Latn", "Arabic, in Latin script (ar-Latn)"),
        ("sr-Zzzz", "Serbian (sr-Zzzz)"),
        ("EN", "English (EN)"),
        ("to", "Tongan (to)"),
    ],
)
def test_label_names_the_language_and_keeps_the_tag(tag, expected):
    assert label(tag) == expected


@pytest.mark.parametrize("tag", ["xx", "qaa", "", "x-private"])
def test_unknown_language_is_left_as_the_tag(tag):
    assert label(tag) == tag


def test_every_flores_language_has_a_name():
    missing = {t for t in FLORES_CODES.values() if t.split("-")[0] not in LANGUAGE_NAMES}
    assert missing == set()
