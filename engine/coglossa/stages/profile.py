"""Profile stage: language, genre, register and pipeline profile (spec §4.1, §4.2)."""

from coglossa.config import ProfilesConfig
from coglossa.llm.agent import AGENT_FAILURES
from coglossa.schema import DocumentProfile, PipelineProfile, ProfileAssessment, Segment
from coglossa.segment.text_metrics import count_words
from coglossa.stages.context import StageContext, StageError

SAMPLE_HEAD_WORDS = 1000
SAMPLE_TAIL_WORDS = 500


def classify_length(word_count: int, profiles: ProfilesConfig) -> PipelineProfile:
    if word_count < profiles.short_max_words:
        return PipelineProfile.SHORT
    if word_count <= profiles.standard_max_words:
        return PipelineProfile.STANDARD
    return PipelineProfile.LONG_FORM


def _take(segments: list[Segment], max_words: int) -> list[Segment]:
    taken, words = [], 0
    for seg in segments:
        if words >= max_words:
            break
        taken.append(seg)
        words += count_words(seg.text)
    return taken


def build_sample(segments: list[Segment]) -> str:
    total = sum(count_words(s.text) for s in segments)
    if total <= SAMPLE_HEAD_WORDS + SAMPLE_TAIL_WORDS:
        return "\n".join(s.text for s in segments)
    head = _take(segments, SAMPLE_HEAD_WORDS)
    tail = list(reversed(_take(list(reversed(segments)), SAMPLE_TAIL_WORDS)))
    return "\n".join(s.text for s in head) + "\n[…]\n" + "\n".join(s.text for s in tail)


async def run_profile(
    ctx: StageContext,
    segments: list[Segment],
    *,
    source_lang: str | None = None,
    pipeline_profile: PipelineProfile | None = None,
) -> DocumentProfile:
    model_key = ctx.cfg.roles.profiler_guide.model
    try:
        assessment = await ctx.runner.run(
            role="profiler",
            model_key=model_key,
            max_output_tokens=ctx.cfg.output_limit(ctx.cfg.roles.profiler_guide),
            prompt_id="profile",
            prompt_vars={"target_lang": ctx.target_lang},
            payload={
                "task": "profile",
                "sample": build_sample(segments),
                "source_lang_hint": source_lang,
                "target_lang": ctx.target_lang,
            },
            output_type=ProfileAssessment,
        )
    except AGENT_FAILURES as e:
        raise StageError(f"profiling failed: {e}") from e
    words = sum(count_words(s.text) for s in segments)
    return DocumentProfile(
        source_lang=source_lang or assessment.source_lang,
        target_lang=ctx.target_lang,
        mixed_language=assessment.mixed_language,
        genre=assessment.genre,
        tone_register=assessment.tone_register,
        word_count=words,
        pipeline_profile=pipeline_profile or classify_length(words, ctx.cfg.profiles),
    )
