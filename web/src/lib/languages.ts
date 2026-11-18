import type { Reliability, TierTable } from "../api/types";
import { isLanguageCode } from "./format";

const normal = (code: string) => code.replace(/_/g, "-");

const formatters = new Map<string, Intl.DisplayNames>();

/** One formatter per locale: building one is the slow part of naming a language. */
function formatter(locale: string): Intl.DisplayNames {
  let known = formatters.get(locale);
  if (known === undefined) {
    known = new Intl.DisplayNames([locale], { type: "language" });
    formatters.set(locale, known);
  }
  return known;
}

/** Spec §4.1: a language's name in the reader's language, offline (Intl). The code when the
 * browser has no name for it. */
export function languageName(code: string, locale: string = navigator.language): string {
  try {
    const name = formatter(locale).of(normal(code));
    return name && name !== normal(code) ? name : code;
  } catch {
    return code;
  }
}

/** The pair's tier when a source is known, else the target's, else the table's default. */
export function tierFor(tiers: TierTable, target: string, source: string | null): Reliability {
  const pair = source === null ? undefined : tiers.pairs[`${source}>${target}`];
  return pair ?? tiers.languages[target] ?? tiers.default;
}

/** Tier C: the tier at which the engine adds its low-reliability flag. */
export function lessPractised(
  tiers: TierTable | undefined,
  target: string,
  source: string | null,
): boolean {
  return tiers !== undefined && target !== "" && tierFor(tiers, target, source) === "C";
}

/** Every language the tier table names, sorted by name. */
export function knownLanguages(tiers: TierTable | undefined, locale?: string): string[] {
  if (tiers === undefined) return [];
  const codes = new Set(Object.keys(tiers.languages));
  for (const pair of Object.keys(tiers.pairs)) for (const code of pair.split(">")) codes.add(code);
  const names = new Map([...codes].map((code) => [code, languageName(code, locale)]));
  return [...codes].sort((a, b) => names.get(a)!.localeCompare(names.get(b)!));
}

export const RECENT_KEY = "coglossa.recentLanguages";
const RECENT_MAX = 5;
let memory: string[] | null = null; // when the browser refuses storage

export function recentLanguages(): string[] {
  if (memory !== null) return [...memory];
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    const valid = parsed.filter((c): c is string => typeof c === "string" && isLanguageCode(c));
    return [...new Set(valid)].slice(0, RECENT_MAX); // de-duplicated before the cap
  } catch {
    return [];
  }
}

export function rememberLanguage(code: string): void {
  const next = [code, ...recentLanguages().filter((c) => c !== code)].slice(0, RECENT_MAX);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    memory = null;
  } catch {
    memory = next;
  }
}

export function resetRecentForTests(): void {
  memory = null;
}
