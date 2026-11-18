import itertools
import json
import time

import pytest

from coglossa.llm.loops import MAX_PERIOD, MIN_CHARS, Repetition, RepetitionGuard


def _stream(text: str, *, context: str = "", step: int = 5) -> Repetition | None:
    """Feed `text` as a server streams it, a few characters at a time; the first loop seen."""
    guard = RepetitionGuard(context=context)
    for i in range(0, len(text), step):
        found = guard.feed(text[i : i + step])
        if found is not None:
            return found
    return None


# --- loops -------------------------------------------------------------------------------------

PREFIX = '{"spans": [{"start": "s1", "end": "s1", "text": "Omi ti a fi sinu igo", "notes": "'


@pytest.mark.parametrize(
    "phrase",
    [
        # the October 2026 benchmark's saved Qwen loop
        "'Bottled water' is 'omi ti a paapaa' is not right. ",
        # Amharic (Ge'ez script)
        "ይህ ውሃ ንጹህ ነው። ",
        # Chinese, no spaces at all
        "这个翻译不对，应该是瓶装水。",
        # Japanese, a short phrase
        "ボトル入りの水です。",
        # a model that keeps writing the same JSON object
        '{"start": "s7", "end": "s7", "text": "ውሃ", "notes": ""}, ',
    ],
)
def test_a_repeated_phrase_is_stopped_soon_after_it_starts(phrase):
    text = PREFIX + phrase * 2000
    found = _stream(text)
    assert found is not None
    assert found.period == len(phrase)
    # spotted within the thresholds plus one check interval of the loop's start
    loop_chars = found.at_chars - len(PREFIX)
    assert loop_chars <= max(MIN_CHARS, 4 * len(phrase)) + 256 + len(phrase)
    assert found.repeats >= 4


@pytest.mark.parametrize("unit", ["\n", " ", "\\n", "ha", "-"])
def test_endless_whitespace_or_one_character_is_a_loop(unit):
    # constrained decoding sometimes ends in a stream of newlines or spaces
    found = _stream('{"spans": [' + unit * 20_000)
    assert found is not None and found.period == len(unit)


def test_a_long_repeated_paragraph_is_a_loop():
    paragraph = " ".join(
        f"Sentence {i} of a long paragraph that the model repeats." for i in range(15)
    )
    assert 600 < len(paragraph) <= MAX_PERIOD
    found = _stream(PREFIX + (paragraph + " ") * 8)
    assert found is not None and found.period == len(paragraph) + 1


def test_tokens_one_character_at_a_time():
    found = _stream(PREFIX + "ውሃ ንጹህ ነው። " * 500, step=1)
    assert found is not None and found.period == len("ውሃ ንጹህ ነው። ")


# --- ordinary repetition -----------------------------------------------------------------------

AMHARIC = [
    "የከተማው ምክር ቤት አዲሱን የውሃ ፕሮጀክት ትናንት አጸደቀ።",
    "ነዋሪዎቹ ንጹህ የመጠጥ ውሃ በቅርቡ እንደሚያገኙ ተነግሯቸዋል።",
    "ፕሮጀክቱ በሁለት ዓመት ውስጥ ይጠናቀቃል ተብሎ ይጠበቃል።",
    "የገንዘብ ድጋፉ ከክልሉ መንግሥት እና ከአጋር ድርጅቶች ይመጣል።",
    "ከንቲባዋ ሥራው ነገ እንደሚጀመር ገለጹ።",
    "ብዙ ነዋሪዎች በውሳኔው ደስታቸውን ገልጸዋል።",
]
CHINESE = [
    "市议会昨天批准了新的供水项目。",
    "居民们被告知很快就能喝上干净的水。",
    "该项目预计将在两年内完成。",
    "资金来自省政府和合作伙伴组织。",
    "市长表示工程将于明天开工。",
    "许多居民对这一决定表示欢迎。",
]


def _judge_answer(spans: int) -> str:
    """The judge's answer: one entry per span, most with no errors, the rest much alike."""
    out = []
    for i in range(1, spans + 1):
        errors = []
        if i % 7 == 0:
            errors = [
                {
                    "category": "fluency/grammar",
                    "severity": "minor",
                    "excerpt": AMHARIC[i % 4][:12],
                    "explanation": "The verb does not agree with its subject.",
                }
            ]
        out.append({"start": f"s{i}", "end": f"s{i}", "errors": errors})
    return json.dumps({"spans": out}, ensure_ascii=False, indent=2)


def _reviewer_answer(spans: int) -> str:
    """The reviewer's segment list: the same fields and the same values, span after span."""
    out = [
        {
            "start": f"s{i}",
            "end": f"s{i}",
            "text": CHINESE[i % 4],
            "agreement": "agree",
            "chosen_from": "literal",
            "rationale": "",
            "issues": [],
            "cultural_or_wordplay": False,
            "proposed_terms": [],
        }
        for i in range(1, spans + 1)
    ]
    return json.dumps({"spans": out}, ensure_ascii=False)


def _table() -> str:
    """A Markdown table whose cells repeat row after row."""
    rows = ["| Week | Mon | Tue | Wed | Thu | Fri |", "|---|---|---|---|---|---|"]
    rows += [f"| {w} | ✓ | ✓ | ✓ | ✓ | — |" for w in range(1, 120)]
    return "\n".join(rows)


def _numbered_list() -> str:
    """A form: the same line with only its number changing."""
    lines = [f"{i}. ስም፦ ____________ ፊርማ፦ ____________ ቀን፦ ________" for i in range(1, 150)]
    return "\n".join(lines)


def _song() -> str:
    chorus = "Glory, glory, hallelujah! " * 3 + "His truth is marching on.\n"
    verses = [
        "Mine eyes have seen the glory of the coming of the Lord;\n",
        "I have seen Him in the watch-fires of a hundred circling camps;\n",
        "I have read a fiery gospel writ in burnished rows of steel;\n",
        "He has sounded forth the trumpet that shall never call retreat;\n",
        "In the beauty of the lilies Christ was born across the sea;\n",
        "He is coming like the glory of the morning on the wave;\n",
    ]
    return "".join(v + chorus for v in verses) * 2  # sung through twice


def _prose(sentences: list[str], joiner: str) -> str:
    # the same few sentences over and over in every order: plenty of shared phrases, and each
    # sentence often recurs a sentence or two later, but never one phrase back to back
    return joiner.join(joiner.join(p) for p in itertools.permutations(sentences, 3))


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_judge_answer(300), id="judge-error-spans"),
        pytest.param(_reviewer_answer(200), id="reviewer-segment-list"),
        pytest.param(_table(), id="markdown-table"),
        pytest.param(_numbered_list(), id="numbered-form-lines"),
        pytest.param(_song(), id="song-with-refrain"),
        pytest.param(_prose(AMHARIC, " "), id="amharic-prose"),
        pytest.param(_prose(CHINESE, ""), id="chinese-prose-no-spaces"),
        pytest.param(
            json.dumps({"text": "哈哈哈哈哈哈，" + "".join(CHINESE) * 3}), id="cjk-laughter"
        ),
        pytest.param(
            "\n".join(f"Step {i}. " + "Check the valve. " * 3 for i in range(1, 40)),
            id="steps-repeating-a-sentence",
        ),
    ],
)
def test_ordinary_repetition_is_not_a_loop(text):
    assert len(text) > MIN_CHARS
    assert _stream(text) is None
    assert _stream(text, step=1) is None


def test_repetition_the_source_has_too_is_not_a_loop_until_the_answer_outgrows_it():
    ruler = "=" * 3000
    prompt = f'INPUT_JSON:\n{{"segments": [{{"id": "s1", "text": "Title\\n{ruler}\\nBody"}}]}}'
    answer = f'{{"spans": [{{"start": "s1", "end": "s1", "text": "Titel\\n{ruler}\\nInhalt"}}]}}'
    assert _stream(answer, context=prompt) is None
    assert _stream(answer) is not None  # without the prompt the same rule is a loop
    looping = _stream(answer[:40] + "=" * 20_000, context=prompt)
    assert looping is not None and 3000 < looping.at_chars < 3000 + MIN_CHARS + 300


def test_checking_is_cheap_on_a_long_answer():
    text = _judge_answer(1500)  # about 140,000 characters, many times the output limit
    assert len(text) > 100_000
    started = time.perf_counter()
    assert _stream(text, step=3) is None
    # incremental: a tail window every 256 characters, not the whole answer every chunk
    assert time.perf_counter() - started < 1.0


# --- repetition the source has, translated -----------------------------------------------------

FORM_LINE = "Signature: ________________  Date: ________"
FORM_LINES = 36  # about 1,400 to 2,400 characters: a loop, but for the source
TRANSLATED_LINES = {
    "fr": "Signature : ________________  Date : ________",
    "am": "ፊርማ፦ ________________  ቀን፦ ________",
    "de": "Unterschrift des Antragstellers: ________________  Datum: ________",
}


def _form_prompt(lines: int = FORM_LINES) -> str:
    from coglossa.llm.payload import render_input

    text = "\n".join([FORM_LINE] * lines)
    return render_input({"task": "translate", "segments": [{"id": "s1", "text": text}]})


def _form_answer(line: str, lines: int = FORM_LINES) -> str:
    text = "\n".join([line] * lines)
    return json.dumps({"spans": [{"start": "s1", "end": "s1", "text": text}]}, ensure_ascii=False)


@pytest.mark.parametrize("lang", sorted(TRANSLATED_LINES))
@pytest.mark.parametrize("lines", [FORM_LINES, 120])
def test_a_form_of_identical_lines_translated_is_not_a_loop(lang, lines):
    # the translation repeats as often as the source, with other words: no copy of the prompt
    answer = _form_answer(TRANSLATED_LINES[lang], lines)
    prompt = _form_prompt(lines)
    assert len(answer) > MIN_CHARS
    assert TRANSLATED_LINES[lang] not in prompt
    assert _stream(answer) is not None  # without the source, the same answer is a loop
    assert _stream(answer, context=prompt) is None
    assert _stream(answer, context=prompt, step=1) is None


@pytest.mark.parametrize("lang", sorted(TRANSLATED_LINES))
def test_a_translated_form_that_runs_on_far_past_the_source_is_a_loop(lang):
    line = TRANSLATED_LINES[lang]
    answer = _form_answer(line)
    looping = answer[: -len('"}]}')] + ("\\n" + line) * 2000
    found = _stream(looping, context=_form_prompt())
    assert found is not None and found.period == len(line) + 2
    # stopped at a little over twice the source's count, not at the output limit
    assert 2 * FORM_LINES < found.repeats <= 2 * FORM_LINES + 4 + 256 // len(line) + 2
    assert found.at_chars < len(answer) * 3


@pytest.mark.parametrize(
    "phrase",
    [
        "ha ",  # much shorter than the form's line
        " ".join(f"Sentence {i} of a paragraph the model repeats." for i in range(6)),  # longer
    ],
)
def test_a_source_run_does_not_excuse_a_loop_of_a_different_phrase_length(phrase):
    found = _stream(PREFIX + phrase * 2000, context=_form_prompt())
    assert found is not None and found.period == len(phrase)
    assert found.at_chars - len(PREFIX) <= max(MIN_CHARS, 4 * len(phrase)) + 256 + len(phrase)


def test_a_loop_of_a_similar_phrase_length_is_stopped_past_the_margin():
    # the guard cannot tell the form's lines from a loop of the same length: it allows the
    # margin, then stops it, long before the output limit
    phrase = "'Bottled water' is 'omi ti a paapaa' is not right. "
    found = _stream(PREFIX + phrase * 2000, context=_form_prompt())
    assert found is not None
    assert found.repeats <= 2 * FORM_LINES + 4 + 256 // len(phrase) + 2
    # a source of a few identical lines excuses only a few more
    found = _stream(_form_answer(TRANSLATED_LINES["fr"], 400), context=_form_prompt(8))
    assert found is not None and found.at_chars < 2000


def test_a_copied_run_longer_than_the_window_is_measured_whole():
    ruler = "=" * 6000  # longer than the guard's window
    prompt = f'INPUT_JSON:\n{{"segments": [{{"id": "s1", "text": "{ruler}"}}]}}'
    answer = f'{{"spans": [{{"start": "s1", "end": "s1", "text": "{ruler}"}}]}}'
    assert _stream(answer, context=prompt) is None
    looping = _stream(answer[:40] + "=" * 30_000, context=prompt)
    assert looping is not None and 6000 < looping.at_chars < 6000 + 300 + 40
    assert looping.repeats > 6000


def test_reading_the_prompt_is_cheap():
    from coglossa.llm.loops import LoopContext

    prompts = [_judge_answer(1500), " " * 100_000, _form_prompt(2000)]
    started = time.perf_counter()
    for prompt in prompts:
        LoopContext(prompt)
    assert time.perf_counter() - started < 1.0
