"""Roundtrip stage (spec §6.1): back-translate final spans into the source language."""

from dataclasses import dataclass

from coglossa.llm.agent import AGENT_FAILURES
from coglossa.schema import DocumentProfile, ReconciledSpan, RunError, TranslationOutput
from coglossa.stages.context import StageContext
from coglossa.stages.translate import ChunkInput, check_cut_off


def check_backtranslation(out: TranslationOutput, spans: list[ReconciledSpan]) -> list[str]:
    expected = [(s.start, s.end) for s in spans]
    got = [(t.start, t.end) for t in out.spans]
    if got != expected:
        want = ", ".join(f"{a}..{b}" for a, b in expected)
        return [f"spans must be exactly [{want}] in order; got {len(got)} spans: {got}"]
    return check_cut_off(out.spans)


@dataclass
class RoundtripResult:
    texts: list[str | None] | None
    run_errors: list[RunError]


async def back_translate_chunk(
    ctx: StageContext, profile: DocumentProfile, ci: ChunkInput, spans: list[ReconciledSpan]
) -> RoundtripResult:
    role = ctx.cfg.roles.roundtrip
    if role is None:
        return RoundtripResult(None, [])
    sent = [s for s in spans if s.text.strip()]
    if not sent:
        return RoundtripResult([None for _ in spans], [])
    try:
        out = await ctx.runner.run(
            role="roundtrip",
            model_key=role.model,
            max_output_tokens=ctx.cfg.output_limit(role),
            prompt_id="back_translate",
            prompt_vars={"from_lang": profile.target_lang, "to_lang": profile.source_lang},
            payload={
                "task": "back_translate",
                "from_lang": profile.target_lang,
                "to_lang": profile.source_lang,
                "spans": [{"start": s.start, "end": s.end, "text": s.text} for s in sent],
            },
            output_type=TranslationOutput,
            validate=lambda o: check_backtranslation(o, sent),
        )
    except AGENT_FAILURES as e:
        return RoundtripResult(
            None,
            [RunError(stage="roundtrip", chunk_id=ci.chunk.id, role="roundtrip", message=str(e))],
        )
    by_range = {(t.start, t.end): t.text for t in out.spans}
    return RoundtripResult([by_range.get((s.start, s.end)) for s in spans], [])
