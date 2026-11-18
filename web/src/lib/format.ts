import type { RunState } from "../api/types";

export const TERMINAL_STATES: readonly RunState[] = [
  "complete",
  "complete_with_errors",
  "failed",
  "cancelled",
];

export function isTerminal(state: RunState): boolean {
  return TERMINAL_STATES.includes(state);
}

export function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : Math.round(value).toString();
}

export function formatDate(iso: string | null): string {
  return iso === null ? "—" : new Date(iso).toLocaleString();
}

// The API's LANGUAGE_CODE_PATTERN (coglossa.schema.views): ISO 639 code, optional subtags.
export const LANGUAGE_CODE = /^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8})*$/;

export function isLanguageCode(text: string): boolean {
  return text.length <= 35 && LANGUAGE_CODE.test(text);
}
