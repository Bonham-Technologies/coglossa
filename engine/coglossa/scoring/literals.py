"""Literals check (spec §6.3 hard trigger `literal_mismatch`, CGL-111): web addresses, email
addresses and placeholders in the source must come through the translation exactly as written,
as a multiset. Pure functions; no I/O.

What is read, on both sides:

- **Web addresses**: ``http://``, ``https://`` and ``ftp://`` addresses and ones starting with
  ``www.``. An address is made of the characters a web address may hold unencoded (ASCII
  letters, digits and ``-._~:/?#[]@!$&'()*+,;=%``), so it ends at a space, a quotation mark, an
  angle bracket or any other character, such as Chinese text written right after it.
  Punctuation that ends a sentence (``.,;:!?'*``) is not part of an address, nor is a closing
  bracket the address did not open: in "(see https://example.com/a)" the address is
  https://example.com/a, in "https://example.com/a_(b)" the bracket is kept.
- **Email addresses**: name@domain with a dotted domain, outside web addresses.
- **Placeholders**: ``{name}``, ``{0}``, ``{}``, ``{0:.2f}``, ``{{var}}``, ``{{{var}}}``,
  ``${var}`` and printf ones such as ``%s``, ``%d``, ``%(name)s``, ``%1$s``, ``%.2f`` and ``%@``.
  A percent sign right after a letter or digit (50%, 100%d) or before a space is not one, nor
  is an ICU message ("{count, plural, one {# item} ...}"), whose words are translated.
- **Protected text**: words the file itself marks as code or not to translate inside a
  sentence (an inline ``<code>`` or ``translate="no"`` element in HTML, a `code span` in
  Markdown), passed in by the caller. Each one must appear in the translation as many times as
  in the source; next to an ASCII letter or digit it must be a whole word ("if" is not found in
  "different"), next to other scripts it need not be ("使用if语句" has it).

Every item of the source must be in the translation; an item found in the translation only is
not this check's concern. An item missing from the translation is paired with an item of the
same kind found only in the translation, in reading order, and reported as changed (both
values); otherwise as missing. Scores are never changed."""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

LINK = "link"
EMAIL = "email"
PLACEHOLDER = "placeholder"
CODE = "code"
_WORDS = {
    LINK: ("web address", "web addresses"),
    EMAIL: ("email address", "email addresses"),
    PLACEHOLDER: ("placeholder", "placeholders"),
    CODE: ("piece of code or text marked not to translate", "pieces of code"),
}

_URL_CHAR = r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]"
_URL = re.compile(rf"(?<![A-Za-z0-9+.\-])(?:(?:https?|ftp)://|www\.){_URL_CHAR}+", re.IGNORECASE)
_URL_START = re.compile(r"(?:(?:https?|ftp)://|www\.)", re.IGNORECASE)
_TRAILING = ".,;:!?'*"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
_EMAIL = re.compile(rf"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9][A-Za-z0-9._%+\-]*@{_LABEL}(?:\.{_LABEL})+")
_PLACEHOLDER = re.compile(
    r"\{\{\{[^{}\n]{1,80}\}\}\}"
    r"|\{\{[^{}\n]{1,80}\}\}"
    r"|\$\{[^{}\n]{1,80}\}"
    r"|\{(?:[A-Za-z0-9_][\w.\[\]]*)?(?:![rsa])?(?::[^{}\s]{0,20})?\}"
    r"|(?<![%A-Za-z0-9_])%(?:\d+\$)?(?:\([A-Za-z_]\w*\))?[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?"
    r"(?:hh|h|ll|l|L|q|j|z|t)?[sdiufFeEgGxXoc@](?![A-Za-z0-9])"
)
_ASCII_WORD = re.compile(r"[A-Za-z0-9_]")


@dataclass(frozen=True)
class Literal:
    kind: str  # link, email or placeholder
    text: str


@dataclass(frozen=True)
class Changed:
    kind: str
    source: str
    translation: str


@dataclass(frozen=True)
class Missing:
    kind: str
    source: str


_OPENER = {")": "(", "]": "["}


def _trim_url(text: str) -> str:
    """`text` without the sentence punctuation and the closing brackets it did not open at
    its end. Linear: the brackets are counted once (trimming never removes an opening one)."""
    opened = {c: text.count(o) for c, o in _OPENER.items()}
    closed = {c: text.count(c) for c in _OPENER}
    end = len(text)
    while end:
        last = text[end - 1]
        if last in _TRAILING:
            end -= 1
        elif last in _OPENER and closed[last] > opened[last]:
            closed[last] -= 1
            end -= 1
        else:
            break
    return text[:end]


def find_literals(text: str) -> list[Literal]:
    """The web addresses, email addresses and placeholders in `text`, in reading order."""
    found: list[tuple[int, Literal]] = []
    masked = list(text)
    for m in _URL.finditer(text):
        url = _trim_url(m.group())
        start = _URL_START.match(url)
        if start is None or len(url) <= start.end():
            continue
        found.append((m.start(), Literal(LINK, url)))
        masked[m.start() : m.start() + len(url)] = " " * len(url)
    rest = "".join(masked)
    for m in _EMAIL.finditer(rest):
        found.append((m.start(), Literal(EMAIL, m.group())))
        masked[m.start() : m.end()] = " " * (m.end() - m.start())
    rest = "".join(masked)
    for m in _PLACEHOLDER.finditer(rest):
        found.append((m.start(), Literal(PLACEHOLDER, m.group())))
    return [item for _, item in sorted(found, key=lambda pair: pair[0])]


def _occurrences(text: str, word: str) -> int:
    """How often `word` is in `text`; next to an ASCII letter or digit only as a whole word."""
    count = 0
    pos = text.find(word)
    while pos != -1:
        end = pos + len(word)
        before = pos > 0 and _ASCII_WORD.match(word[0]) and _ASCII_WORD.match(text[pos - 1])
        after = end < len(text) and _ASCII_WORD.match(word[-1]) and _ASCII_WORD.match(text[end])
        if not before and not after:
            count += 1
            pos = text.find(word, end)
        else:
            pos = text.find(word, pos + 1)
    return count


@dataclass(frozen=True)
class LiteralMismatch:
    changed: tuple[Changed, ...] = ()  # in the source's order
    missing: tuple[Missing, ...] = ()

    def as_evidence(self) -> dict[str, object]:
        return {
            "changed": [
                {"kind": c.kind, "source": c.source, "translation": c.translation}
                for c in self.changed
            ],
            "missing": [{"kind": m.kind, "source": m.source} for m in self.missing],
        }

    def explanation(self) -> str:
        """Plain words for a reviewer (the web app shows them as they are)."""
        tail = (
            " Web addresses, email addresses, placeholders and code must be copied exactly as "
            "they are."
        )
        if len(self.changed) + len(self.missing) == 1:
            if self.changed:
                c = self.changed[0]
                head = (
                    f"A {_WORDS[c.kind][0]} was changed: the original has {c.source}; the "
                    f"translation has {c.translation}."
                )
            else:
                m = self.missing[0]
                head = (
                    f"A {_WORDS[m.kind][0]} is missing: the original has {m.source}; the "
                    "translation doesn't."
                )
            return head + tail
        kinds = list(dict.fromkeys([c.kind for c in self.changed] + [m.kind for m in self.missing]))
        names = [_WORDS[k][1] for k in kinds]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
        items = [f"{c.source} became {c.translation}" for c in self.changed]
        items += [f"{m.source} is missing" for m in self.missing]
        return f"Some {listed} were changed or left out: " + "; ".join(items) + "." + tail


def literal_check(
    source_text: str, final_text: str, protected: Sequence[str] = ()
) -> LiteralMismatch | None:
    """The web addresses, email addresses and placeholders of the source, and the `protected`
    text the file marks inside it (one entry per occurrence), that the translation does not
    have exactly as written (see the module docstring)."""
    source = find_literals(source_text)
    target = find_literals(final_text)
    available = Counter(t.text for t in target)
    missing: list[Literal] = []
    for item in source:
        if available[item.text] > 0:
            available[item.text] -= 1
        else:
            missing.append(item)
    wanted = Counter(s.text for s in source)
    extra: list[Literal] = []
    for item in target:
        if wanted[item.text] > 0:
            wanted[item.text] -= 1
        else:
            extra.append(item)
    changed: list[Changed] = []
    lost: list[Missing] = []
    for item in missing:
        pair = next((e for e in extra if e.kind == item.kind), None)
        if pair is not None:
            extra.remove(pair)
            changed.append(Changed(item.kind, item.text, pair.text))
        else:
            lost.append(Missing(item.kind, item.text))
    read = {s.text for s in source}
    counts = Counter(p for p in (p.strip() for p in protected) if p and p not in read)
    for text, n in counts.items():
        lost += [Missing(CODE, text)] * max(0, n - _occurrences(final_text, text))
    if not changed and not lost:
        return None
    return LiteralMismatch(changed=tuple(changed), missing=tuple(lost))
