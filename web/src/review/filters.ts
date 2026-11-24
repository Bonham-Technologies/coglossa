import type { FlagCode } from "../api/types";

export const FLAG_CODES: FlagCode[] = [
  "low_meaning",
  "style_drift",
  "agent_disagreement",
  "terminology_violation",
  "number_mismatch",
  "possible_omission",
  "cultural_or_wordplay",
  "engine_error",
  "low_reliability_language",
  "unscored",
  "unread_content",
  "formatting_lost",
];

export function score(text: string | null): number | null {
  if (text === null || text.trim() === "") return null;
  const value = Number(text);
  if (!Number.isFinite(value)) return null;
  return Math.min(100, Math.max(0, value));
}

export type ScoreFilter = {
  min_meaning?: number;
  max_meaning?: number;
  min_style?: number;
  max_style?: number;
};

export const SCORE_KEYS = ["min_meaning", "max_meaning", "min_style", "max_style"] as const;

/** Settings ▸ Technical details opens a translation with score bounds in its address (§6.1). */
export function scoreFilterFrom(params: URLSearchParams): ScoreFilter {
  const filter: ScoreFilter = {};
  for (const key of SCORE_KEYS) {
    const value = score(params.get(key));
    if (value !== null) filter[key] = value;
  }
  return filter;
}
