"""Judge stage (spec §6.1): an LLM marks MQM-style error spans in each final translation."""

import asyncio
import re
import unicodedata
from dataclasses import dataclass

from pydantic import ValidationError

from coglossa.config import JUDGE_MAX_OUTPUT_TOKENS, RoleConfig
from coglossa.llm.agent import AGENT_FAILURES, UNUSABLE_OUTPUT, StructuredOutputError, extract_json
from coglossa.llm.types import OVERSIZE_ERRORS
from coglossa.schema import (
    DocumentProfile,
    ErrorCategory,
    Guide,
    JudgeError,
    JudgeOutput,
    ReconciledSpan,
    RunError,
    SpanJudgement,
)
from coglossa.stages.context import StageContext
from coglossa.stages.guide import guide_slice
from coglossa.stages.translate import ChunkInput

# Characters a model often types in place of the translation's own: curly quotes, guillemets,
# dashes. NFKC already turns "…" into "..." and odd spaces into plain ones.
_PLAIN = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2013": "-",
        "\u2014": "-",
    }
)
_ELISION = re.compile(r"\s*\.{3}\s*")


def _plain(text: str) -> tuple[str, list[tuple[int, int]]]:
    """The text made plain, and where in text each of its characters came from.

    Each letter is normalised (NFKC) together with the marks that follow it, so a decomposed
    "ọ́" equals a composed one and a mark typed twice equals one; quotes, dashes and
    runs of spaces become plain; and invisible format characters (the zero-width spaces between
    Khmer words, for one) are dropped.
    """
    out: list[str] = []
    where: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        end = i + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        # a mark typed twice (a typo some texts have) reads the same as once; drop it while the
        # marks are still apart, before NFKC composes the first with its letter
        nfd = unicodedata.normalize("NFD", text[i:end])
        nfd = "".join(
            c
            for k, c in enumerate(nfd)
            if k == 0 or c != nfd[k - 1] or not unicodedata.combining(c)
        )
        cluster = unicodedata.normalize("NFKC", nfd).translate(_PLAIN)
        for c in cluster:
            if unicodedata.category(c) == "Cf":
                continue
            if c.isspace():
                if out and out[-1] == " ":
                    continue
                c = " "
            out.append(c)
            where.append((i, end))
        i = end
    return "".join(out), where


def locate_excerpt(excerpt: str, text: str) -> str | None:
    """The part of text an excerpt quotes, exactly as text has it, or None.

    Judges copy imperfectly: they type straight quotes for curly ones and "..." for "…", compose
    accents the text has decomposed, leave out zero-width spaces, and shorten a long quote with
    an ellipsis. Such an excerpt still names one place in the text,
    from its first part to its last; the UI highlights it by exact match, so it gets that text.
    """
    if excerpt in text:
        return excerpt
    plain, where = _plain(text)
    quoted = _plain(excerpt)[0].strip()
    if not quoted:
        return None
    found = plain.find(quoted)
    if found != -1:  # as quoted, with any ellipsis in it
        return text[where[found][0] : where[found + len(quoted) - 1][1]]
    parts = [p for p in _ELISION.split(quoted) if p]  # else "..." marks a gap
    if not parts:
        return None
    first, at = -1, 0
    for part in parts:
        found = plain.find(part, at)
        if found == -1:
            return None
        first = found if first == -1 else first
        at = found + len(part)
    return text[where[first][0] : where[at - 1][1]]


def check_judgements(out: JudgeOutput, spans: list[ReconciledSpan]) -> list[str]:
    expected = [(s.start, s.end) for s in spans]
    got = [(j.start, j.end) for j in out.spans]
    if got != expected:
        want = ", ".join(f"{a}..{b}" for a, b in expected)
        return [f"spans must be exactly [{want}] in order; got {len(got)} spans: {got}"]
    texts = {(s.start, s.end): s.text for s in spans}
    problems: list[str] = []
    for k, judgement in enumerate(out.spans):
        text = texts[(judgement.start, judgement.end)]
        for m, err in enumerate(judgement.errors):
            where = f"spans[{k}].errors[{m}]"
            if err.category is not ErrorCategory.OMISSION and not err.excerpt.strip():
                problems.append(f"{where}: excerpt is required for {err.category.value}")
            elif err.excerpt and locate_excerpt(err.excerpt, text) is None:
                problems.append(
                    f"{where}: excerpt {err.excerpt!r} does not appear in the span's translation"
                )
    return problems


def _same_ranges(out: JudgeOutput, spans: list[ReconciledSpan]) -> bool:
    return [(j.start, j.end) for j in out.spans] == [(s.start, s.end) for s in spans]


# Shorter excerpts ("型", one word) may well appear in another span by chance.
MOVABLE_EXCERPT_CHARS = 4


def _move_misfiled_errors(out: JudgeOutput, spans: list[ReconciledSpan]) -> JudgeOutput:
    """An error whose excerpt is not in its own span but in exactly one other span of the batch,
    and long enough to mean that span, was filed under the wrong span (CGL-15): it moves."""
    texts = [s.text for s in spans]
    errors: list[list[JudgeError]] = [[] for _ in spans]
    for k, judgement in enumerate(out.spans):
        for err in judgement.errors:
            target = k
            movable = len(_plain(err.excerpt)[0].strip()) >= MOVABLE_EXCERPT_CHARS
            if movable and locate_excerpt(err.excerpt, texts[k]) is None:
                quoted = [
                    m for m, t in enumerate(texts) if locate_excerpt(err.excerpt, t) is not None
                ]
                target = quoted[0] if len(quoted) == 1 else k
            errors[target].append(err)
    return JudgeOutput(
        spans=[
            SpanJudgement(start=j.start, end=j.end, errors=e)
            for j, e in zip(out.spans, errors, strict=True)
        ]
    )


def _parsed(failure: StructuredOutputError) -> JudgeOutput | None:
    try:
        return JudgeOutput.model_validate_json(extract_json(failure.raw or ""))
    except ValidationError:
        return None


def _salvaged(out: JudgeOutput, spans: list[ReconciledSpan]) -> JudgeOutput:
    """An answer whose ranges are right but whose excerpts were still wrong after repair. A
    misfiled error moves to the span it quotes; one whose excerpt cannot be found keeps its
    category, severity and explanation without a highlight (judges correct a typo as they copy
    it). Losing the batch's judgements cost more."""
    out = _move_misfiled_errors(out, spans)
    return JudgeOutput(
        spans=[
            SpanJudgement(
                start=j.start,
                end=j.end,
                errors=[
                    e.model_copy(update={"excerpt": ""})
                    if e.excerpt and locate_excerpt(e.excerpt, s.text) is None
                    else e
                    for e in j.errors
                ],
            )
            for j, s in zip(out.spans, spans, strict=True)
        ]
    )


# Spans per judge call. A mid-size model asked to judge a whole chunk (a hundred sentences) in
# one call drops or merges spans, misquotes excerpts and runs out of output, and one bad span
# cost the chunk; batches run concurrently, and a batch whose answer has the wrong ranges or is
# cut off is halved and judged again (twice at most: a model that loops loops on less too).
JUDGE_BATCH_SPANS = 20
JUDGE_HALVINGS = 2


@dataclass
class JudgeResult:
    # None: no judge. Per span, None: its batch failed; []: judged, no errors.
    errors_by_span: list[list[JudgeError] | None] | None
    run_errors: list[RunError]


async def judge_chunk(
    ctx: StageContext,
    profile: DocumentProfile,
    guide: Guide,
    ci: ChunkInput,
    spans: list[ReconciledSpan],
) -> JudgeResult:
    role = ctx.cfg.roles.judge
    if role is None:
        return JudgeResult(None, [])
    judged = [s for s in spans if s.text.strip()]
    if not judged:
        return JudgeResult([[] for _ in spans], [])
    batches = [judged[k : k + JUDGE_BATCH_SPANS] for k in range(0, len(judged), JUDGE_BATCH_SPANS)]
    results = await asyncio.gather(
        *(_judge_batch(ctx, profile, guide, ci, batch, role) for batch in batches)
    )
    by_range: dict[tuple[str, str], list[JudgeError]] = {}
    run_errors: list[RunError] = []
    for batch_errors, failures in results:
        by_range.update(batch_errors)
        run_errors.extend(failures)
    if not by_range:
        return JudgeResult(None, run_errors)
    return JudgeResult(
        [by_range.get((s.start, s.end)) if s.text.strip() else [] for s in spans], run_errors
    )


async def _judge_batch(
    ctx: StageContext,
    profile: DocumentProfile,
    guide: Guide,
    ci: ChunkInput,
    batch: list[ReconciledSpan],
    role: RoleConfig,
    halvings: int = JUDGE_HALVINGS,
) -> tuple[dict[tuple[str, str], list[JudgeError]], list[RunError]]:
    """One judge call over consecutive spans, given the source segments they cover; halved and
    judged again if the answer is unusable."""
    position = {seg.id: k for k, seg in enumerate(ci.segments)}
    segments = ci.segments[position[batch[0].start] : position[batch[-1].end] + 1]
    sliced = guide_slice(guide, "\n".join(seg.text for seg in segments))
    try:
        out = await ctx.runner.run(
            role="judge",
            model_key=role.model,
            max_output_tokens=ctx.cfg.output_limit(role, JUDGE_MAX_OUTPUT_TOKENS),
            prompt_id="judge",
            prompt_vars={"source_lang": profile.source_lang, "target_lang": profile.target_lang},
            payload={
                "task": "judge",
                "source_lang": profile.source_lang,
                "target_lang": profile.target_lang,
                "guide": sliced.prompt_dump(),
                "segments": [{"id": seg.id, "text": seg.text} for seg in segments],
                "spans": [{"start": s.start, "end": s.end, "text": s.text} for s in batch],
            },
            output_type=JudgeOutput,
            validate=lambda o: check_judgements(o, batch),
        )
    except UNUSABLE_OUTPUT as e:
        answer = _parsed(e) if isinstance(e, StructuredOutputError) else None
        if answer is not None and _same_ranges(answer, batch):
            out = _salvaged(answer, batch)
        elif (answer is not None or isinstance(e, OVERSIZE_ERRORS)) and halvings and len(batch) > 1:
            mid = len(batch) // 2
            halves = await asyncio.gather(
                _judge_batch(ctx, profile, guide, ci, batch[:mid], role, halvings - 1),
                _judge_batch(ctx, profile, guide, ci, batch[mid:], role, halvings - 1),
            )
            return {**halves[0][0], **halves[1][0]}, halves[0][1] + halves[1][1]
        else:
            return {}, [_run_error(ci, batch, e)]
    except AGENT_FAILURES as e:
        return {}, [_run_error(ci, batch, e)]
    texts = {(s.start, s.end): s.text for s in batch}
    judged = {
        (j.start, j.end): [
            e.model_copy(update={"excerpt": locate_excerpt(e.excerpt, texts[(j.start, j.end)])})
            if e.excerpt
            else e
            for e in j.errors
        ]
        for j in out.spans
    }
    return judged, []


def _run_error(ci: ChunkInput, batch: list[ReconciledSpan], e: Exception) -> RunError:
    where = f"spans {batch[0].start}..{batch[-1].end}"
    return RunError(stage="judge", chunk_id=ci.chunk.id, role="judge", message=f"{where}: {e}")
