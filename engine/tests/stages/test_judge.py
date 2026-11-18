import json

import pytest

from coglossa.llm.types import OutputTruncatedError, PermanentBackendError
from coglossa.schema import (
    Agreement,
    Chunk,
    DocumentProfile,
    ErrorCategory,
    Genre,
    Guide,
    JudgeOutput,
    PipelineProfile,
    ReconciledSpan,
)
from coglossa.stages.context import StageContext
from coglossa.stages.judge import (
    JUDGE_BATCH_SPANS,
    check_judgements,
    judge_chunk,
    locate_excerpt,
)
from coglossa.stages.translate import build_chunk_inputs
from tests.conftest import BASE_CONFIG, make_segments

PROFILE = DocumentProfile(
    source_lang="fr",
    target_lang="en",
    mixed_language=False,
    genre=Genre.NEWS,
    tone_register="plain",
    word_count=4,
    pipeline_profile=PipelineProfile.SHORT,
)
GUIDE = Guide(summary="s", style_guide="g")


def _spans(*texts):
    return [
        ReconciledSpan(
            start=f"s{i + 1}",
            end=f"s{i + 1}",
            text=t,
            agreement=Agreement.AGREE,
            chosen_from="literal",
        )
        for i, t in enumerate(texts)
    ]


def _ci(n):
    segs = make_segments(*[f"Phrase {i}." for i in range(n)])
    return build_chunk_inputs([Chunk(id="c1", segment_ids=[s.id for s in segs])], segs)[0]


def _ctx(runner):
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


def _judge_cfg(make_config):
    return make_config(roles={**BASE_CONFIG["roles"], "judge": {"model": "model-b"}})


def _err(**kw):
    base = {
        "category": "accuracy/mistranslation",
        "severity": "major",
        "excerpt": "cat",
        "explanation": "wrong animal",
    }
    return {**base, **kw}


def test_check_judgements():
    spans = _spans("The cat sleeps.", "Rain.")
    ok = JudgeOutput.model_validate(
        {
            "spans": [
                {"start": "s1", "end": "s1", "errors": [_err()]},
                {
                    "start": "s2",
                    "end": "s2",
                    "errors": [_err(category="accuracy/omission", excerpt="")],
                },
            ]
        }
    )
    assert check_judgements(ok, spans) == []
    wrong_ranges = JudgeOutput.model_validate({"spans": [{"start": "s1", "end": "s2"}]})
    assert "must be exactly" in check_judgements(wrong_ranges, spans)[0]
    missing = JudgeOutput.model_validate(
        {
            "spans": [
                {"start": "s1", "end": "s1", "errors": [_err(excerpt="dog")]},
                {"start": "s2", "end": "s2", "errors": [_err(category="fluency", excerpt=" ")]},
            ]
        }
    )
    problems = check_judgements(missing, spans)
    assert "does not appear" in problems[0] and "excerpt is required" in problems[1]


async def test_no_judge_role_means_no_call(make_runner, fake_backend):
    result = await judge_chunk(_ctx(make_runner()), PROFILE, GUIDE, _ci(1), _spans("x"))
    assert result.errors_by_span is None and result.run_errors == []
    assert fake_backend.requests == []


async def test_judge_errors_are_aligned_and_blank_spans_skipped(
    make_config, make_runner, fake_backend
):
    reply = {
        "spans": [
            {"start": "s1", "end": "s1", "errors": [_err()]},
            {"start": "s3", "end": "s3", "errors": []},
        ]
    }
    fake_backend.script("judge", json.dumps(reply))
    runner = make_runner(_judge_cfg(make_config))
    result = await judge_chunk(
        _ctx(runner), PROFILE, GUIDE, _ci(3), _spans("The cat sleeps.", "  ", "Fine.")
    )
    assert [len(e) for e in result.errors_by_span] == [1, 0, 0]
    assert result.errors_by_span[0][0].category is ErrorCategory.MISTRANSLATION
    payload = json.loads(fake_backend.requests[0].messages[1].content.split("INPUT_JSON:", 1)[1])
    assert [s["start"] for s in payload["spans"]] == ["s1", "s3"]
    assert payload["task"] == "judge" and runner.log.records[0].role == "judge"


async def test_all_blank_spans_skip_call(make_config, make_runner, fake_backend):
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))), PROFILE, GUIDE, _ci(1), _spans("")
    )
    assert result.errors_by_span == [[]] and fake_backend.requests == []


def _payload_spans(fake_backend):
    return [
        [s["start"] for s in json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["spans"]]
        for r in fake_backend.requests
    ]


async def test_an_error_filed_under_the_wrong_span_moves_to_the_one_quoted(
    make_config, make_runner, fake_backend
):
    # CGL-15: the judge quoted s2's text but filed the error under s1
    reply = {
        "spans": [
            {"start": "s1", "end": "s1", "errors": [_err(excerpt="dog barks")]},
            {"start": "s2", "end": "s2", "errors": []},
        ]
    }
    fake_backend.script("judge", json.dumps(reply), json.dumps(reply))
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))),
        PROFILE,
        GUIDE,
        _ci(2),
        _spans("The cat sleeps.", "The dog barks."),
    )
    assert result.errors_by_span[0] == [] and result.run_errors == []
    assert [e.excerpt for e in result.errors_by_span[1]] == ["dog barks"]
    assert len(fake_backend.requests) == 2  # the judge was asked to fix its quote first


@pytest.mark.parametrize(
    ("texts", "excerpt"),
    [
        (("日本の形式です。", "型を使う。"), "型"),  # too short to say which span it means
        (("The cat.", "the dog runs", "the dog runs too"), "the dog runs"),  # in two spans
    ],
)
async def test_an_excerpt_that_could_mean_another_span_stays_put_without_a_highlight(
    make_config, make_runner, fake_backend, texts, excerpt
):
    reply = {
        "spans": [
            {
                "start": f"s{k + 1}",
                "end": f"s{k + 1}",
                "errors": [_err(excerpt=excerpt)] if k == 0 else [],
            }
            for k in range(len(texts))
        ]
    }
    fake_backend.script("judge", json.dumps(reply), json.dumps(reply))
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))), PROFILE, GUIDE, _ci(len(texts)), _spans(*texts)
    )
    assert [e.excerpt for e in result.errors_by_span[0]] == [""]
    assert all(errors == [] for errors in result.errors_by_span[1:])


async def test_answers_that_are_not_json_do_not_split_the_batch(
    make_config, make_runner, fake_backend
):
    fake_backend.script("judge", "garbage", "garbage")
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))), PROFILE, GUIDE, _ci(2), _spans("A.", "B.")
    )
    assert result.errors_by_span is None and len(result.run_errors) == 1
    assert len(fake_backend.requests) == 2


async def test_a_batch_is_halved_at_most_twice(make_config, make_runner, fake_backend):
    cut = OutputTruncatedError("output truncated (finish_reason=length)")
    fake_backend.script("judge", *[cut] * 7)
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))),
        PROFILE,
        GUIDE,
        _ci(JUDGE_BATCH_SPANS),
        _spans(*[f"Sentence {i}." for i in range(JUDGE_BATCH_SPANS)]),
    )
    assert _payload_spans(fake_backend)[-1] == [f"s{i}" for i in range(16, 21)]
    assert len(fake_backend.requests) == 7  # 20, then 10 and 10, then four batches of 5
    assert result.errors_by_span is None
    assert [e.message.split(":")[0] for e in result.run_errors] == [
        "spans s1..s5",
        "spans s6..s10",
        "spans s11..s15",
        "spans s16..s20",
    ]


async def test_an_excerpt_found_nowhere_keeps_its_error_without_a_highlight(
    make_config, make_runner, fake_backend
):
    # the judge "corrected" a typo as it quoted; one bad excerpt no longer costs the batch
    reply = {
        "spans": [
            {"start": "s1", "end": "s1", "errors": [_err(excerpt="kasu 29")]},
            {"start": "s2", "end": "s2", "errors": [_err(excerpt="dog")]},
        ]
    }
    fake_backend.script("judge", json.dumps(reply), json.dumps(reply))
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))),
        PROFILE,
        GUIDE,
        _ci(2),
        _spans("kaso 29 cats.", "The dog."),
    )
    assert result.run_errors == []
    (kept,) = result.errors_by_span[0]
    assert kept.excerpt == "" and kept.explanation == "wrong animal"
    assert [e.excerpt for e in result.errors_by_span[1]] == ["dog"]
    assert len(fake_backend.requests) == 2  # the repair turn was tried first


async def test_merged_ranges_split_the_batch(make_config, make_runner, fake_backend):
    merged = json.dumps({"spans": [{"start": "s1", "end": "s2", "errors": []}]})
    fake_backend.script("judge", merged, merged)
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))), PROFILE, GUIDE, _ci(2), _spans("A.", "B.")
    )
    assert result.errors_by_span == [[], []] and result.run_errors == []
    assert _payload_spans(fake_backend) == [["s1", "s2"], ["s1", "s2"], ["s1"], ["s2"]]


async def test_a_batch_cut_off_is_split_and_a_span_that_keeps_failing_costs_only_itself(
    make_config, make_runner, fake_backend
):
    cut = OutputTruncatedError("output truncated (finish_reason=length)")
    fake_backend.script("judge", cut, cut)  # the batch, then s1 alone
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))), PROFILE, GUIDE, _ci(2), _spans("A.", "B.")
    )
    assert result.errors_by_span == [None, []]
    (error,) = result.run_errors
    assert error.message.startswith("spans s1..s1: ") and "truncated" in error.message


async def test_backend_failure_degrades_without_splitting(make_config, make_runner, fake_backend):
    fake_backend.script("judge", PermanentBackendError("judge model gone"))
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))), PROFILE, GUIDE, _ci(2), _spans("x", "y")
    )
    assert result.errors_by_span is None and "judge model gone" in result.run_errors[0].message
    assert len(fake_backend.requests) == 1


@pytest.mark.parametrize(
    ("excerpt", "text", "found"),
    [
        ("cat", "The cat sleeps.", "cat"),
        # a model that types straight quotes or "..." for the translation's own characters
        ('"chat"', "Le « chat » dort.", None),
        ('"chat"', "Le «chat» dort.", "«chat»"),
        ("it's", "it’s late", "it’s"),
        ("La neta... no es tan chido.", "La neta… no es tan chido.", "La neta… no es tan chido."),
        ("two  words", "two words here", "two words"),
        # an excerpt shortened with an ellipsis covers the text from its first to its last part
        (
            "Oye, la estrategia... se fue de madre",
            "Oye, la estrategia, que metió mano, se fue de madre.",
            "Oye, la estrategia, que metió mano, se fue de madre",
        ),
        ("...no es tan chido", "La neta, no es tan chido.", "no es tan chido"),
        ("dog", "The cat sleeps.", None),
        ("sleeps... The", "The cat sleeps.", None),
        ("...", "Wait... what?", "..."),
        ("…", "Wait. What?", None),
        # Khmer separates words with zero-width spaces, which a judge leaves out
        (
            "ប្រេងអ",
            "x ប្រេង​អ y",
            "ប្រេង​អ",
        ),
        # Yoruba: the text has decomposed accents and the judge's copy composed ones, or the reverse
        ("Àwòrán", "Àwòrán tó", "Àwòrán"),
        ("ọ́ da", "wọ́ da", "ọ́ da"),
        # a doubled accent in the text (a typo in FLORES Yoruba) that the judge's copy corrects
        ("wọ́n ni", "ma wọ́́n ni", "wọ́́n ni"),
        # ... also where the first mark composes with its letter (á), in either direction
        ("wá ni", "ma wá́ ni", "wá́ ni"),
        ("wá́ ni", "ma wá ni", "wá ni"),
        # an ellipsis the judge quotes is part of the excerpt, not a gap
        ("dort...", "Il dort… encore", "dort…"),
        ("...", "Hakika… ni", "…"),
    ],
)
def test_locate_excerpt(excerpt, text, found):
    assert locate_excerpt(excerpt, text) == found


def test_check_judgements_accepts_an_excerpt_it_can_locate():
    spans = _spans("Le «chat» dort… longtemps.")
    out = JudgeOutput.model_validate(
        {"spans": [{"start": "s1", "end": "s1", "errors": [_err(excerpt='"chat" dort...')]}]}
    )
    assert check_judgements(out, spans) == []


async def test_located_excerpts_are_returned_as_the_translation_has_them(
    make_config, make_runner, fake_backend
):
    reply = {"spans": [{"start": "s1", "end": "s1", "errors": [_err(excerpt="The... sleeps")]}]}
    fake_backend.script("judge", json.dumps(reply))
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))),
        PROFILE,
        GUIDE,
        _ci(1),
        _spans("The cat sleeps."),
    )
    assert result.errors_by_span[0][0].excerpt == "The cat sleeps"


async def test_a_long_chunk_is_judged_in_batches_with_their_own_sources(
    make_config, make_runner, fake_backend
):
    n = JUDGE_BATCH_SPANS * 2 + 5
    texts = [f"Sentence {i}." for i in range(n)]
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))), PROFILE, GUIDE, _ci(n), _spans(*texts)
    )
    payloads = [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1]) for r in fake_backend.requests
    ]
    assert [len(p["spans"]) for p in payloads] == [JUDGE_BATCH_SPANS, JUDGE_BATCH_SPANS, 5]
    for p in payloads:
        assert [s["id"] for s in p["segments"]] == [s["start"] for s in p["spans"]]
    assert len(result.errors_by_span) == n and None not in result.errors_by_span
    assert result.run_errors == []


async def test_a_failed_batch_costs_only_its_own_spans(make_config, make_runner, fake_backend):
    n = JUDGE_BATCH_SPANS + 3
    fake_backend.script("judge", PermanentBackendError("judge model gone"))
    result = await judge_chunk(
        _ctx(make_runner(_judge_cfg(make_config))),
        PROFILE,
        GUIDE,
        _ci(n),
        _spans(*[f"Sentence {i}." for i in range(n)]),
    )
    assert result.errors_by_span[:JUDGE_BATCH_SPANS] == [None] * JUDGE_BATCH_SPANS
    assert all(e is not None for e in result.errors_by_span[JUDGE_BATCH_SPANS:])
    assert len(result.run_errors) == 1
    assert result.run_errors[0].message.startswith(f"spans s1..s{JUDGE_BATCH_SPANS}: ")
