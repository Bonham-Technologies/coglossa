"""Group segments into chunks that fit every model in the agent roster (spec §4.1, §7.2)."""

import math
from dataclasses import dataclass

from coglossa.config import CoglossaConfig, ConfigError, RoleConfig, TranslatorConfig
from coglossa.schema import BlockKind, Chunk, Segment, SourceDocument
from coglossa.segment.sentences import MAX_SEGMENT_CHARS
from coglossa.segment.text_metrics import count_words, estimate_tokens

OUTPUT_RATIO = 1.5  # an answer that grows with the chunk may be this much longer than its source


@dataclass(frozen=True)
class ChunkBudget:
    max_words: int
    target_words: int
    max_tokens: int
    bytes_per_token: float


def compute_budget(cfg: CoglossaConfig) -> ChunkBudget:
    translators = cfg.roles.translators()
    roster = [t.model for t in translators]
    roster += [cfg.roles.reviewer.model, cfg.roles.profiler_guide.model]
    models = {key: cfg.models[key] for key in roster}
    smallest = min(models, key=lambda key: models[key].context_window)
    window = models[smallest].context_window
    bytes_per_token = min(m.bytes_per_token for m in models.values())
    reserve = cfg.chunking.reserve_tokens
    # The reconcile call is the largest: source + one candidate per translator in, ~1.5x out.
    by_context = int((window - reserve) / (len(translators) + 1 + OUTPUT_RATIO))
    needed = math.ceil(MAX_SEGMENT_CHARS * 4 / bytes_per_token)  # worst-case single segment
    # (tokens per chunk a limit leaves, what to say when that is too few)
    caps = [
        (
            by_context,
            f"model '{smallest}' has a {window}-token context window, leaving "
            f"{max(by_context, 0)} tokens per chunk for {len(translators)} translators after "
            f"chunking.reserve_tokens={reserve}; at least {needed} are needed. Use a model "
            "with a larger context_window, lower chunking.reserve_tokens, or remove "
            "extra_translators.",
        )
    ]
    # Every answer that grows with the chunk must fit its role's output limit: the translations,
    # the reconciled one and the back-translation. The judge (batches of spans), the sweep (one
    # span) and the profile and guide (a sample, a term list) do not grow with it.
    writers: list[tuple[str, RoleConfig | TranslatorConfig]] = [
        *((f"translator:{t.name}", t) for t in translators),
        ("reviewer", cfg.roles.reviewer),
    ]
    if cfg.roles.roundtrip is not None:
        writers.append(("roundtrip", cfg.roles.roundtrip))
    # A role's own max_output_tokens counts where it is lower than its model's; the judge's
    # default limit is not passed here, since the judge is not a writer.
    limits = [(cfg.output_limit(role), label, role) for label, role in writers]
    known = [entry for entry in limits if entry[0] is not None]
    if known:
        limit, label, role = min(known, key=lambda entry: entry[0])
        by_output = int(limit / OUTPUT_RATIO)
        wanted = math.ceil(needed * OUTPUT_RATIO)
        leaves = (
            f"leaving {by_output} tokens per chunk for an answer up to {OUTPUT_RATIO}x as "
            f"long; at least {needed} are needed."
        )
        model_limit = cfg.models[role.model].max_output_tokens
        if role.max_output_tokens == limit:  # the role's own limit binds
            also = (
                f" (and model '{role.model}''s, which is {model_limit})"
                if model_limit is not None and model_limit < wanted
                else ""
            )
            why = (
                f"role '{label}' sets its own max_output_tokens={limit}, {leaves} Raise the "
                f"role's max_output_tokens to {wanted} or more{also}, or remove it."
            )
        else:
            why = (
                f"role '{label}' uses model '{role.model}' with max_output_tokens={limit}, "
                f"{leaves} Raise that model's max_output_tokens to {wanted} or more, or use "
                "another model for the role."
            )
        caps.append((by_output, why))
    max_tokens = min(cap for cap, _ in caps)
    # the tightest limit first, and the other too when it is also short
    short = sorted((cap, why) for cap, why in caps if cap < needed)
    if short:
        raise ConfigError(" Also: ".join(why for _, why in short))
    return ChunkBudget(
        max_words=cfg.chunking.max_words,
        target_words=cfg.chunking.target_words,
        max_tokens=max_tokens,
        bytes_per_token=bytes_per_token,
    )


def chunk_segments(
    doc: SourceDocument, segments: list[Segment], budget: ChunkBudget
) -> list[Chunk]:
    by_block: dict[str, list[Segment]] = {}
    for seg in segments:
        by_block.setdefault(seg.block_id, []).append(seg)

    chunks: list[Chunk] = []
    current: list[Segment] = []
    words = tokens = 0

    def flush() -> None:
        nonlocal current, words, tokens
        if current:
            chunks.append(Chunk(id=f"c{len(chunks) + 1}", segment_ids=[s.id for s in current]))
        current, words, tokens = [], 0, 0

    pending_break = False
    for block in doc.blocks:
        if block.kind is BlockKind.BREAK:
            pending_break = True
            continue
        block_segments = by_block.get(block.id, [])
        if not block_segments:
            continue
        section_start = pending_break or block.kind is BlockKind.HEADING
        pending_break = False
        if section_start and words >= budget.target_words // 2:
            flush()
        for seg in block_segments:
            w = count_words(seg.text)
            t = estimate_tokens(seg.text, budget.bytes_per_token)
            if current and (words + w > budget.max_words or tokens + t > budget.max_tokens):
                flush()
            current.append(seg)
            words += w
            tokens += t
        if words >= budget.target_words:
            flush()
    flush()
    return chunks
