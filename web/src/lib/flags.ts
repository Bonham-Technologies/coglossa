import type { FlagCode, FlagStatus } from "../api/types";

/** Spec §5.4: the plain title of each engine flag. */
export const FLAG_TITLES: Record<FlagCode, string> = {
  possible_omission: "Something may be missing",
  low_meaning: "The meaning may have shifted",
  style_drift: "The style doesn't match the rest",
  terminology_violation: "Doesn't follow your translation guide",
  number_mismatch: "A number doesn't match the original",
  agent_disagreement: "The translators disagreed",
  cultural_or_wordplay: "Wordplay or a cultural reference",
  low_reliability_language: "Less-practised language: check carefully",
  unscored: "Couldn't be checked",
  engine_error: "Some checking didn't finish",
  unread_content: "Not everything was translated",
  formatting_lost: "Formatting couldn't be kept",
};

/** A code a newer engine added falls back to a neutral title, never breaking the panel. */
export function flagTitle(code: string): string {
  return (FLAG_TITLES as Record<string, string>)[code] ?? "Worth a look";
}

const SCORE_KINDS: Partial<Record<string, { key: string; word: string }>> = {
  low_meaning: { key: "meaning", word: "meaning" },
  style_drift: { key: "style", word: "style" },
};

const isNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The judge's note the engine appended to the flag: the first critical error, else the first. */
function judgeNote(evidence: Record<string, unknown>): string | null {
  const errors: unknown[] = Array.isArray(evidence.judge_errors) ? evidence.judge_errors : [];
  const notes = errors.filter(
    (e): e is { severity?: unknown; explanation: string } =>
      typeof e === "object" &&
      e !== null &&
      typeof (e as { explanation?: unknown }).explanation === "string",
  );
  const pick = notes.find((e) => e.severity === "critical") ?? notes[0];
  return pick?.explanation || null;
}

/**
 * What to show under a flag's title. A score-below-threshold flag is worded in plain terms from
 * its evidence (never by parsing the engine's sentence), with the judge's note, if any, kept apart
 * so it can sit on its own line; anything else, or evidence without the score or threshold, keeps
 * the engine's own text and has no separate note.
 */
export function flagExplanation(flag: {
  code: string;
  explanation: string;
  evidence?: Record<string, unknown>;
}): { text: string; note: string | null } {
  const kind = SCORE_KINDS[flag.code];
  const engine = { text: flag.explanation, note: null };
  if (!kind) return engine;
  const evidence = flag.evidence ?? {};
  const score = evidence[kind.key];
  const threshold = evidence.threshold;
  if (!isNumber(score) || !isNumber(threshold) || score >= threshold) return engine;
  // Never show a rounded score that reaches the threshold: 85.6 under 86 reads 85, not 86.
  const shown = Math.round(score) >= threshold ? Math.floor(score) : Math.round(score);
  return {
    text: `The checker rated the ${kind.word} ${shown} out of 100; passages under ${threshold} get a second look.`,
    note: judgeNote(evidence),
  };
}

/** Spec §3.3. */
export const FLAG_STATUS_WORDS: Record<FlagStatus, string> = {
  open: "to check",
  accepted: "checked",
  dismissed: "not a problem",
  resolved: "fixed by the recheck",
};

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
