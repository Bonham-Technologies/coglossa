import type { Candidate, SpanDetail } from "../api/types";

/** `usable`: the API's use-candidate action would accept this choice and write `text`. */
export type Choice = { translator: string; model: string; text: string; usable: boolean };

// The engine's `clean_text` (coglossa/text.py): vertical tabs and form feeds become line breaks;
// the other C0 controls except tab, line feed and carriage return, U+FFFE, U+FFFF and lone
// surrogates go. With the `u` flag a surrogate range matches only unpaired surrogates.
const LINE_BREAKS = /[\v\f]/g;
// eslint-disable-next-line no-control-regex -- removing control characters is the point
const ILLEGAL = /[\u0000-\u0008\u000e-\u001f\ufffe\uffff\ud800-\udfff]/gu;
// Python's `str.strip()` whitespace, which differs from JavaScript's `trim()`: it strips U+0085
// and U+001C–U+001F, and keeps U+FEFF.
const SPACE =
  "\\t\\n\\v\\f\\r\\u001c-\\u001f \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const EDGES = new RegExp(`^[${SPACE}]+|[${SPACE}]+$`, "gu");

function clean(text: string): string {
  return text.replace(LINE_BREAKS, "\n").replace(ILLEGAL, "");
}

function strip(text: string): string {
  return text.replace(EDGES, "");
}

/** Segment ids are s1, s2, … in reading order; anything else has no position. */
function position(segment: string): number {
  const match = /^s(\d+)$/.exec(segment);
  return match ? Number(match[1]) : NaN;
}

/** The pieces cover the passage exactly: from its first segment to its last, with no gap or
 * overlap between them. */
function tiles(pieces: Candidate[], detail: SpanDetail): boolean {
  if (pieces[0]?.start !== detail.start || pieces.at(-1)?.end !== detail.end) return false;
  return pieces.every((p, i) => i === 0 || position(p.start) === position(pieces[i - 1]!.end) + 1);
}

/** A translator's candidate can come in pieces (its spans, aligned differently). Using it joins
 * them as the API's use-candidate action does (`coglossa.review.use_candidate`): in reading
 * order, each cleaned and trimmed, with single spaces between. It is usable only when the pieces
 * cover the passage exactly and leave some text. Checked against the engine's own cases in
 * byTranslator.test.ts. */
export function byTranslator(detail: SpanDetail): Choice[] {
  const groups = new Map<string, Candidate[]>();
  for (const c of detail.candidates) {
    const pieces = groups.get(c.translator);
    if (pieces) pieces.push(c);
    else groups.set(c.translator, [c]);
  }
  return [...groups.values()].map((arrived) => {
    // a stable sort, as the engine's, so pieces at the same position keep their order
    const pieces = [...arrived].sort((a, b) => position(a.start) - position(b.start));
    const text = pieces.map((p) => strip(clean(p.text))).join(" ");
    return {
      translator: arrived[0]!.translator,
      model: arrived[0]!.model,
      text,
      usable: tiles(pieces, detail) && strip(text) !== "",
    };
  });
}
