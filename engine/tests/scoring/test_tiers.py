import pytest

from coglossa.config import ConfigError
from coglossa.schema import Reliability
from coglossa.scoring.tiers import DEFAULT_TIERS_FILE, LanguageTiers, base_language, downgrade

A, B, C = Reliability.A, Reliability.B, Reliability.C


@pytest.mark.parametrize(
    ("code", "base"), [("pt-BR", "pt"), ("ZH_Hant", "zh"), (" EN ", "en"), ("und", "und")]
)
def test_base_language(code, base):
    assert base_language(code) == base


def test_downgrade():
    assert [downgrade(t) for t in (A, B, C, Reliability.UNSCORED)] == [
        B,
        C,
        C,
        Reliability.UNSCORED,
    ]


@pytest.mark.parametrize(
    ("src", "tgt", "tier"),
    [
        ("en", "de", A),
        ("fr", "EN", A),
        ("en", "ko", B),
        ("ko", "en", B),
        ("en", "yo", C),
        ("pt-BR", "en", A),
        ("zh-Hant", "ja", A),
        ("und", "en", C),
        ("ka", "hy", C),
    ],
)
def test_default_tiers(src, tgt, tier):
    assert LanguageTiers.load().tier(src, tgt) is tier


def test_default_file_points_to_the_calibration_and_bench():
    text = DEFAULT_TIERS_FILE.read_text(encoding="utf-8")
    assert "bench-results-2026-10.md" in text and "coglossa bench" in text


def test_pair_override_and_custom_default(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text("default: B\nlanguages:\n  A: [en]\npairs:\n  'en>yo': A\n", encoding="utf-8")
    tiers = LanguageTiers.load(path)
    assert tiers.tier("en", "yo") is A
    assert tiers.tier("yo", "en") is B
    assert tiers.tier("en", "en") is A


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("- not a mapping\n", "expected a mapping"),
        ("default: D\n", "tier must be A, B or C"),
        ("languages:\n  Q: [en]\n", "tier must be A, B or C"),
        ("languages:\n  A: en\n", "must be a list"),
        ("pairs:\n  'en-de': A\n", "src>tgt"),
        ("pairs:\n  'en>de': Z\n", "tier must be A, B or C"),
        ("default: [unclosed\n", "cannot read"),
        ("default: [A]\n", "tier must be A, B or C"),
        ("default: {}\n", "tier must be A, B or C"),
        ("pairs:\n  'en>de': [A]\n", "tier must be A, B or C"),
        ("languages: [en]\n", "languages must be a mapping of tier -> list"),
        ("pairs: [x]\n", "pairs must be a mapping of 'src>tgt' -> tier"),
        ("languages:\n  B: [nb, no]\n", r"languages\.B: language code False is not a string"),
        ("languages:\n  B: [12]\n", r"language code 12 is not a string"),
    ],
)
def test_bad_tier_files(tmp_path, content, message):
    path = tmp_path / "t.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        LanguageTiers.load(path)


def test_missing_tier_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        LanguageTiers.load(tmp_path / "absent.yaml")


def test_empty_file_uses_defaults(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text("", encoding="utf-8")
    assert LanguageTiers.load(path).tier("en", "de") is C
