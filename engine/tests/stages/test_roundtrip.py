import json

from coglossa.llm.types import PermanentBackendError
from coglossa.schema import (
    Agreement,
    Chunk,
    DocumentProfile,
    Genre,
    PipelineProfile,
    ReconciledSpan,
    TranslationOutput,
)
from coglossa.stages.context import StageContext
from coglossa.stages.roundtrip import back_translate_chunk, check_backtranslation
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


def _rt_cfg(make_config):
    models = {**BASE_CONFIG["models"], "model-c": {"backend": "fake", "context_window": 32768}}
    return make_config(
        models=models, roles={**BASE_CONFIG["roles"], "roundtrip": {"model": "model-c"}}
    )


def test_check_backtranslation_refuses_a_text_cut_off():
    spans = _spans("Hola.")
    out = TranslationOutput.model_validate({"spans": [{"start": "s1", "end": "s1", "text": "Hi "}]})
    assert "cut off" in check_backtranslation(out, spans)[0]


def test_check_backtranslation():
    spans = _spans("a", "b")
    good = TranslationOutput.model_validate(
        {
            "spans": [
                {"start": "s1", "end": "s1", "text": "x"},
                {"start": "s2", "end": "s2", "text": "y"},
            ]
        }
    )
    assert check_backtranslation(good, spans) == []
    bad = TranslationOutput.model_validate({"spans": [{"start": "s1", "end": "s2", "text": "x"}]})
    assert "must be exactly" in check_backtranslation(bad, spans)[0]


async def test_no_roundtrip_role_means_no_call(make_runner, fake_backend):
    result = await back_translate_chunk(_ctx(make_runner()), PROFILE, _ci(1), _spans("x"))
    assert result.texts is None and fake_backend.requests == []


async def test_back_translation_aligned_with_blank_spans(make_config, make_runner, fake_backend):
    runner = make_runner(_rt_cfg(make_config))
    result = await back_translate_chunk(
        _ctx(runner), PROFILE, _ci(3), _spans("It rains.", "", "Sun.")
    )
    assert result.texts == ["[fr] It rains.", None, "[fr] Sun."]
    payload = json.loads(fake_backend.requests[0].messages[1].content.split("INPUT_JSON:", 1)[1])
    assert (payload["task"], payload["from_lang"], payload["to_lang"]) == (
        "back_translate",
        "en",
        "fr",
    )
    assert runner.log.records[0].role == "roundtrip"
    assert runner.log.records[0].model == "model-c"


async def test_all_blank_makes_no_call(make_config, make_runner, fake_backend):
    result = await back_translate_chunk(
        _ctx(make_runner(_rt_cfg(make_config))), PROFILE, _ci(1), _spans(" ")
    )
    assert result.texts == [None] and fake_backend.requests == []


async def test_failure_degrades(make_config, make_runner, fake_backend):
    fake_backend.script("back_translate", PermanentBackendError("down"))
    result = await back_translate_chunk(
        _ctx(make_runner(_rt_cfg(make_config))), PROFILE, _ci(1), _spans("x")
    )
    assert result.texts is None
    assert [(e.stage, e.role) for e in result.run_errors] == [("roundtrip", "roundtrip")]
