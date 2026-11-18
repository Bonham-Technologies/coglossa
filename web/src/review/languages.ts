import { createContext, useContext } from "react";
import type { RunDetail } from "../api/types";

/** The languages of a run's passages, for `lang` attributes. A browser chooses glyph forms and
 * fonts by `lang`: without it, Traditional Chinese and Japanese show Simplified forms. */
export type RunLanguagesValue = { source: string | undefined; target: string };

export const RunLanguages = createContext<RunLanguagesValue | null>(null);

export function runLanguages(detail: RunDetail): RunLanguagesValue {
  return {
    source: detail.profile?.source_lang ?? detail.run.source_lang ?? undefined,
    target: detail.profile?.target_lang ?? detail.run.target_lang,
  };
}

export function useRunLanguages(): RunLanguagesValue {
  const value = useContext(RunLanguages);
  if (value === null) throw new Error("useRunLanguages needs a RunLanguages provider");
  return value;
}
