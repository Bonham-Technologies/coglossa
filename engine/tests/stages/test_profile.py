import pytest

from coglossa.config import ProfilesConfig
from coglossa.schema import PipelineProfile
from coglossa.stages.context import StageContext, StageError
from coglossa.stages.profile import build_sample, classify_length, run_profile
from tests.conftest import make_segments


@pytest.mark.parametrize(
    ("words", "expected"),
    [(1, "short"), (1499, "short"), (1500, "standard"), (20000, "standard"), (20001, "long-form")],
)
def test_classify_length(words, expected):
    assert classify_length(words, ProfilesConfig()).value == expected


def test_build_sample_short_document_is_whole_text():
    assert build_sample(make_segments("One.", "Two.")) == "One.\nTwo."


def test_build_sample_long_document_keeps_head_and_tail():
    segs = make_segments(*[f"Sentence number {i} here." for i in range(1000)])  # 4,000 words
    sample = build_sample(segs)
    assert sample.startswith("Sentence number 0 here.")
    assert sample.endswith("Sentence number 999 here.")
    assert "\n[…]\n" in sample
    assert "Sentence number 500 here." not in sample


def _ctx(make_runner, cfg=None):
    runner = make_runner(cfg)
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


async def test_run_profile_uses_hint_and_counts_words(make_runner, fake_backend):
    ctx = _ctx(make_runner)
    profile = await run_profile(ctx, make_segments("Bonjour tout le monde."), source_lang="fr")
    assert (profile.source_lang, profile.target_lang, profile.word_count) == ("fr", "en", 4)
    assert profile.pipeline_profile is PipelineProfile.SHORT
    assert ctx.runner.log.records[0].role == "profiler"


async def test_run_profile_detects_language_and_honours_override(make_runner):
    profile = await run_profile(
        _ctx(make_runner), make_segments("x"), pipeline_profile=PipelineProfile.LONG_FORM
    )
    assert profile.source_lang == "und"  # synthetic model's "detected" language
    assert profile.pipeline_profile is PipelineProfile.LONG_FORM


async def test_run_profile_failure_is_stage_error(make_runner, fake_backend):
    fake_backend.script("profile", "garbage", "garbage")
    with pytest.raises(StageError, match="profiling failed"):
        await run_profile(_ctx(make_runner), make_segments("x"))
