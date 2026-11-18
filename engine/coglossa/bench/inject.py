"""Plant known errors in correct reference translations, so bench can measure which the flags catch.
Randomness comes only from the random.Random passed in."""

import random
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from coglossa.llm.agent import AgentRunner
from coglossa.schema import GlossaryEntry, InjectOutput


class InjectionKind(StrEnum):
    OMISSION = "omission"
    NUMBER = "number"
    NAME = "name"
    NEGATION = "negation"
    REGISTER = "register"
    LOCKED_TERM = "locked_term"


MEANING_KINDS = {
    InjectionKind.OMISSION,
    InjectionKind.NUMBER,
    InjectionKind.NAME,
    InjectionKind.NEGATION,
    InjectionKind.LOCKED_TERM,
}
STYLE_KINDS = {InjectionKind.REGISTER}

_SEPARATORS = ",;:，、；：،؛"
_CLAUSE_SPLIT = re.compile(f"([{_SEPARATORS}])")
_WORD = re.compile(r"[^\W\d_]{4,}")
_SENTENCE_END = ".!?。！？"
_KEEP_FRACTION = 0.6
_MIN_SHORTER = 0.25
_MIN_SIZE = 6  # characters without spaces; shorter text has nothing meaningful to drop


@dataclass(frozen=True)
class Injection:
    kind: InjectionKind
    text: str
    detail: str
    locked_term: GlossaryEntry | None = None


def _size(text: str) -> int:
    return len("".join(text.split()))


def _drop_longest_clause(reference: str) -> tuple[str, str] | None:
    pieces = _CLAUSE_SPLIT.split(reference)  # part, sep, part, sep, ...
    parts = [(i, p) for i, p in enumerate(pieces) if i % 2 == 0 and p.strip()]
    if len(parts) < 2:
        return None
    drop, removed = max(parts, key=lambda ip: len(ip[1].strip()))
    kept = ""
    for i, part in parts:
        if i == drop:
            continue
        kept += (pieces[i - 1] if kept else "") + part
    return kept.strip().rstrip(f"{_SEPARATORS} ").rstrip(), removed.strip()


def _drop_tail(reference: str) -> tuple[str, str]:
    if " " in reference.strip():
        words = reference.split()
        keep = max(1, int(len(words) * _KEEP_FRACTION))
        return " ".join(words[:keep]), " ".join(words[keep:])
    keep = max(1, int(len(reference) * _KEEP_FRACTION))
    return reference[:keep], reference[keep:]


def _shorter_enough(text: str, reference: str) -> bool:
    return bool(text) and _size(text) <= (1 - _MIN_SHORTER) * _size(reference)


def omit(reference: str) -> Injection | None:
    if _size(reference) < _MIN_SIZE:
        return None
    cut = _drop_longest_clause(reference)
    if cut is None or not _shorter_enough(cut[0], reference):
        cut = _drop_tail(reference.strip())
    text, removed = cut
    if not _shorter_enough(text, reference):
        return None
    return Injection(InjectionKind.OMISSION, text, f"dropped {removed!r}")


def swap_number(reference: str, rng: random.Random) -> Injection | None:
    runs = list(re.finditer(r"\d+", reference))
    if not runs:
        return None
    run = rng.choice(runs)
    old = run.group()
    digit = unicodedata.digit(old[-1])
    value = (digit + rng.randint(1, 9)) % 10
    # \d matches only category Nd, whose digits are contiguous 0-9 runs, so this stays in script
    new = old[:-1] + chr(ord(old[-1]) - digit + value)
    text = reference[: run.start()] + new + reference[run.end() :]
    return Injection(InjectionKind.NUMBER, text, f"{old} → {new}")


def _names(text: str) -> list[str]:
    """Words capitalized in a non-initial position; sentence openers are not told apart from
    ordinary capitalized words, so they are not names."""
    found = []
    for m in _WORD.finditer(text):
        before = text[: m.start()].rstrip()
        if m.group()[0].isupper() and before and before[-1] not in _SENTENCE_END:
            found.append(m.group())
    return list(dict.fromkeys(found))


def _swap_name(
    source: str, reference: str, others: list[str], rng: random.Random
) -> tuple[str, str, str] | None:
    in_source = set(_names(source))
    present = [n for n in _names(reference) if n in in_source]
    known = set(_names(reference))
    pool = list(dict.fromkeys(n for other in others for n in _names(other) if n not in known))
    if not present or not pool:
        return None
    name, other = rng.choice(present), rng.choice(pool)
    pattern = rf"(?<![^\W\d_]){re.escape(name)}(?![^\W\d_])"
    return name, other, re.sub(pattern, other, reference)


def swap_name(
    source: str, reference: str, others: list[str], rng: random.Random
) -> Injection | None:
    swapped = _swap_name(source, reference, others, rng)
    if swapped is None:
        return None
    name, other, text = swapped
    return Injection(InjectionKind.NAME, text, f"{name} → {other}")


def violate_locked_term(
    source: str, reference: str, others: list[str], rng: random.Random
) -> Injection | None:
    swapped = _swap_name(source, reference, others, rng)
    if swapped is None:
        return None
    name, other, text = swapped
    return Injection(
        InjectionKind.LOCKED_TERM,
        text,
        f"{name} → {other}",
        locked_term=GlossaryEntry(term=name, rendering=name, locked=True),
    )


async def inject_with_model(
    runner: AgentRunner,
    model_key: str,
    kind: InjectionKind,
    target_lang: str,
    reference: str,
) -> Injection | None:
    if kind not in (InjectionKind.NEGATION, InjectionKind.REGISTER):
        raise ValueError(f"only negation and register are model-injected, not '{kind.value}'")
    out = await runner.run(
        role="injector",
        model_key=model_key,
        prompt_id="inject",
        prompt_vars={"target_lang": target_lang},
        payload={
            "task": "inject",
            "kind": kind.value,
            "target_lang": target_lang,
            "text": reference,
        },
        output_type=InjectOutput,
        validate=lambda o: [] if o.text.strip() else ["text must not be empty"],
    )
    if out.text.strip() == reference.strip():
        return None
    return Injection(kind, out.text, f"model-injected {kind.value}")
