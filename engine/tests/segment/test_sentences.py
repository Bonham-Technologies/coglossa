import time

from hypothesis import given, settings
from hypothesis import strategies as st

from coglossa.segment.sentences import MAX_SEGMENT_CHARS, split_sentences


def _split(text: str, **kw) -> list[str]:
    return [text[s:e] for s, e in split_sentences(text, **kw)]


def test_english_abbreviations_initials_and_dialogue():
    text = (
        "Dr. Smith met J. K. Rowling. They talked, e.g. about books! "
        '"Stop!" she said. Did it work? Yes…'
    )
    assert _split(text) == [
        "Dr. Smith met J. K. Rowling.",
        "They talked, e.g. about books!",
        '"Stop!" she said.',
        "Did it work?",
        "Yes…",
    ]


def test_decimals_are_not_boundaries():
    assert _split("Pi is 3.14 today. Ok.") == ["Pi is 3.14 today.", "Ok."]


def test_japanese_without_spaces():
    assert _split("今日は晴れです。明日は雨でしょう！本当？") == [
        "今日は晴れです。",
        "明日は雨でしょう！",
        "本当？",
    ]


def test_arabic_right_to_left():
    assert _split("مرحبا بكم. كيف حالكم؟ أنا بخير.") == ["مرحبا بكم.", "كيف حالكم؟", "أنا بخير."]


def test_hindi_danda_and_amharic():
    assert _split("मैं घर जा रहा हूँ। तुम कहाँ हो?") == ["मैं घर जा रहा हूँ।", "तुम कहाँ हो?"]
    assert _split("ሰላም ነው። እንዴት ነህ፧") == ["ሰላም ነው።", "እንዴት ነህ፧"]


def test_poetry_line_breaks_stay_in_one_segment():
    assert _split("Roses are red,\nviolets are blue") == ["Roses are red,\nviolets are blue"]


def test_unpunctuated_thai_is_split_at_spaces():
    text = "สวัสดีครับ " * 120
    parts = _split(text)
    assert len(parts) > 1
    assert all(len(p) <= MAX_SEGMENT_CHARS for p in parts)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")


def test_long_sentence_prefers_comma_then_space_then_hard_cut():
    text = ("word " * 30).strip() + ", " + ("more " * 30).strip()
    parts = _split(text, max_chars=160)
    assert parts[0].endswith(",")
    assert _split("x" * 50, max_chars=20) == ["x" * 20, "x" * 20, "x" * 10]


def test_whitespace_only():
    assert split_sentences("  \n\t ") == []


def _assert_covers_non_space(text: str, spans: list[tuple[int, int]]) -> None:
    covered = [0] * len(text)
    for s, e in spans:
        for i in range(s, e):
            covered[i] += 1
    for i, ch in enumerate(text):
        assert covered[i] <= 1
        if not ch.isspace():
            assert covered[i] == 1


def test_long_terminator_runs_are_linear_time():
    for text in ("a" + "." * 30000 + " " + "x" * 10, "." * 30000 + " x"):
        start = time.perf_counter()
        spans = split_sentences(text)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"took {elapsed:.2f}s"
        _assert_covers_non_space(text, spans)


def test_spaceless_terminator_after_spaced_one_still_splits():
    assert _split("a.。b") == ["a.。", "b"]


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=2500), st.integers(min_value=20, max_value=700))
def test_spans_cover_every_non_space_character_exactly_once(text, max_chars):
    spans = split_sentences(text, max_chars=max_chars)
    covered = [0] * len(text)
    prev_end = 0
    for s, e in spans:
        assert prev_end <= s < e <= len(text)
        assert e - s <= max_chars
        assert not text[s].isspace() and not text[e - 1].isspace()
        for i in range(s, e):
            covered[i] += 1
        prev_end = e
    for i, ch in enumerate(text):
        assert covered[i] <= 1
        if not ch.isspace():
            assert covered[i] == 1


@settings(deadline=None)
@given(
    st.lists(
        st.sampled_from(["Hola.", "¿Qué?", "東京。", "नमस्ते।", "word", "Mr.", "\n", " "]),
        max_size=200,
    )
)
def test_sentence_like_inputs_never_crash(pieces):
    text = " ".join(pieces)
    assert all(e - s <= MAX_SEGMENT_CHARS for s, e in split_sentences(text))
