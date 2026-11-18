"""Spot a model that repeats itself while its answer streams in (CGL-71).

A model that falls into a loop writes the same phrase over and over until it reaches its output
limit. The guard watches the streamed text and says when its end has become one phrase repeated
back to back: at least `min_repeats` times and over at least `min_chars` characters.

It works on characters, not words or tokens, so it needs no spaces and treats every script
alike (Amharic, Chinese, Thai). Only exact repetition counts. Ordinary repetition in a
translation varies from one item to the next: list items and table rows differ in their text,
and JSON arrays of similar objects differ at least in their ids. A refrain is a few lines long,
far below the thresholds.

Repetition the source has too is allowed, as far as the source goes. The prompt is read once
for its own back-to-back runs. A run the answer copies from the prompt (a long rule of dashes,
say) is not a loop until it outgrows the prompt's. A run the answer translates (a form whose
identical lines come back in other words) has another phrase, so it is measured by its count:
it is not a loop until it repeats clearly more often than a run of a similar phrase length in
the prompt (`_MARGIN_TIMES` times plus `_MARGIN_PLUS`).

Cost: the prompt is read once, at the guard's creation. The answer is checked every
`check_every` characters, over a tail window of fixed size, with substring searches and slice
comparisons that run in C; nothing scans the whole answer again.
"""

from dataclasses import dataclass

# Defaults, tested in tests/llm/test_loops.py. 1,200 characters is about 300 to 1,200 tokens
# depending on the script, a small part of a 16,384-token output limit. A phrase repeated four
# times in a row over that many characters does not occur in a translation that is going well.
MIN_CHARS = 1200
MIN_REPEATS = 4
MAX_PERIOD = 1000  # the longest repeated phrase looked for, in characters
CHECK_EVERY = 256  # characters of new text between checks
_PROBE = 24  # the end of the text searched for earlier, to find candidate phrase lengths
_CANDIDATES = 64  # most candidate phrase lengths tried per check

# Reading the prompt: runs shorter than this could not make an answer reach MIN_CHARS.
_SOURCE_MIN_CHARS = 64
_SOURCE_MIN_REPEATS = 3
_SOURCE_STEP = 32  # characters between the prompt's checks
_SOURCE_KEEP = 256  # most prompt runs kept
# A translated run may repeat up to this many times as often as the prompt's run...
_MARGIN_TIMES = 2
_MARGIN_PLUS = 4
# ...when its phrase is no more than this many times longer or shorter (plus a little): a line
# of English is about a third as long in Chinese, and a third longer in German...
_PHRASE_RATIO = 3
_PHRASE_SLACK = 8
# ...and its whole run no more than this many times longer, plus `min_chars`.
_LENGTH_TIMES = 3


@dataclass(frozen=True)
class Repetition:
    period: int  # length of the repeated phrase, in characters
    repeats: int  # times it was repeated in a row, at least
    at_chars: int  # characters of text seen when the loop was spotted


@dataclass(frozen=True)
class _Run:
    period: int
    length: int

    @property
    def repeats(self) -> int:
        return self.length // self.period


class LoopContext:
    """The prompt, and the back-to-back runs it contains. Built once per call and shared by
    the guards of that call."""

    def __init__(self, text: str = "", *, max_period: int = MAX_PERIOD) -> None:
        self.text = text
        self.runs = _source_runs(text, max_period=max_period)

    def allows_copy(self, run: str, period: int, length: int, measured: bool) -> bool:
        """Whether a run the prompt also contains is still no longer than the prompt's."""
        if measured:
            return run in self.text
        # the answer's run is longer than its window: compare with the prompt's own run
        return any(r.period == period and r.length >= length for r in self.runs)

    def allows_translation(self, period: int, length: int, min_chars: int) -> bool:
        """Whether a run is within the margin of a run of a similar phrase in the prompt."""
        repeats = length // period
        return any(
            repeats <= _MARGIN_TIMES * r.repeats + _MARGIN_PLUS
            and period <= _PHRASE_RATIO * r.period + _PHRASE_SLACK
            and r.period <= _PHRASE_RATIO * period + _PHRASE_SLACK
            and length <= _LENGTH_TIMES * r.length + min_chars
            for r in self.runs
        )


class RepetitionGuard:
    def __init__(
        self,
        *,
        context: str | LoopContext = "",
        min_chars: int = MIN_CHARS,
        min_repeats: int = MIN_REPEATS,
        max_period: int = MAX_PERIOD,
        check_every: int = CHECK_EVERY,
    ) -> None:
        if isinstance(context, str):
            context = LoopContext(context, max_period=max_period)
        self._context = context
        self._min_chars = min_chars
        self._min_repeats = min_repeats
        self._max_period = max_period
        self._check_every = check_every
        # a loop of the longest phrase needs min_repeats * max_period characters to be seen
        self._window = _window(min_chars, min_repeats, max_period)
        self._tail = ""
        self._seen = 0
        self._unchecked = 0
        self._track = _Tracker()

    def feed(self, text: str) -> Repetition | None:
        """Add streamed text; returns the repetition once the text has fallen into a loop."""
        self._tail += text
        self._seen += len(text)
        self._unchecked += len(text)
        if len(self._tail) > 2 * self._window:
            self._tail = self._tail[-self._window :]
        if self._unchecked < self._check_every:
            return None
        self._unchecked = 0
        return self._check()

    def _check(self) -> Repetition | None:
        s = self._tail[-self._window :]
        n = len(s)
        found = _find_run(
            s,
            min_chars=self._min_chars,
            min_repeats=self._min_repeats,
            max_period=self._max_period,
        )
        length = self._track.update(found, self._seen, n)
        if found is None:
            return None
        period, run = found
        ctx = self._context
        if s[n - 2 * period :] in ctx.text:
            # the phrase is the prompt's own: allowed as far as the prompt's run goes
            if ctx.allows_copy(s[n - run :], period, length, measured=length == run):
                return None
        elif ctx.allows_translation(period, length, self._min_chars):
            return None
        return Repetition(period=period, repeats=length // period, at_chars=self._seen)


class _Tracker:
    """Follows one run from check to check, so that a run longer than the window is measured
    whole: while each window is all one run, the run started where it was first seen."""

    def __init__(self) -> None:
        self._last: tuple[int, int, int] | None = None  # period, start, end

    def update(self, found: tuple[int, int] | None, end: int, window: int) -> int:
        if found is None:
            self._last = None
            return 0
        period, run = found
        start = end - run
        last = self._last
        # a window that is all this run and overlaps the last one by a phrase: the run goes on
        if run == window and last is not None and last[0] == period:
            start = last[1] if end - window <= last[2] - period else start
        self._last = (period, start, end)
        return end - start


def _window(min_chars: int, min_repeats: int, max_period: int) -> int:
    return max(min_chars, min_repeats * max_period) + _PROBE


def _source_runs(text: str, *, max_period: int) -> list[_Run]:
    """The back-to-back runs in the prompt: checked every few characters, like an answer."""
    window = _window(_SOURCE_MIN_CHARS, _SOURCE_MIN_REPEATS, max_period)
    track = _Tracker()
    runs: dict[tuple[int, int], int] = {}  # (period, start) -> longest length seen
    for end in range(_SOURCE_STEP, len(text) + _SOURCE_STEP, _SOURCE_STEP):
        end = min(end, len(text))
        s = text[max(0, end - window) : end]
        found = _find_run(
            s,
            min_chars=_SOURCE_MIN_CHARS,
            min_repeats=_SOURCE_MIN_REPEATS,
            max_period=max_period,
        )
        length = track.update(found, end, len(s))
        if found is not None:
            runs[(found[0], end - length)] = length
    # one entry per phrase length and count, the longest; the longest runs if there are many
    best: dict[tuple[int, int], _Run] = {}
    for (period, _), length in runs.items():
        run = _Run(period, length)
        key = (period, run.repeats)
        if key not in best or best[key].length < length:
            best[key] = run
    return sorted(best.values(), key=lambda r: r.length, reverse=True)[:_SOURCE_KEEP]


def _find_run(
    s: str, *, min_chars: int, min_repeats: int, max_period: int
) -> tuple[int, int] | None:
    """The shortest phrase that the end of `s` repeats back to back, at least `min_repeats`
    times over at least `min_chars` characters: (phrase length, run length within `s`)."""
    n = len(s)
    if n < min_chars:
        return None
    probe = s[-_PROBE:]
    m = len(probe)
    end = n - 1  # earlier occurrences of the probe end before the text's last character
    for _ in range(_CANDIDATES):
        i = s.rfind(probe, 0, end)
        if i < 0:
            return None
        period = n - m - i
        if period > max_period:
            return None
        end = i + m - 1
        need = max(min_chars, min_repeats * period)
        if need > n:
            continue
        start = n - need
        # quick rejections before comparing the whole run: the probe one more phrase back,
        # and the run's first characters against the phrase after them
        if i >= period and not s.startswith(probe, i - period):
            continue
        if s[start : start + m] != s[start + period : start + period + m]:
            continue
        if not _periodic(s, need, period):
            continue
        return period, _run_length(s, need, period)
    return None


def _periodic(s: str, length: int, period: int) -> bool:
    """Whether the last `length` characters of `s` repeat with this period."""
    n = len(s)
    return s[n - length + period :] == s[n - length : n - period]


def _run_length(s: str, known: int, period: int) -> int:
    """How far back from the end the repetition goes (within `s`); `known` is periodic."""
    lo, hi = known, len(s)  # periodic for lo characters; a shorter run is periodic too
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _periodic(s, mid, period):
            lo = mid
        else:
            hi = mid - 1
    return lo
