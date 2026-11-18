import type { DocumentRun, DocumentSummary, ProjectInfo } from "../api/types";
import { languageName } from "../lib/languages";
import { runStateStatus, type Status } from "../lib/status";

/** Language codes compare case-insensitively, with - and _ alike (BCP 47), as the engine does. */
export function languageKey(code: string): string {
  return code.toLowerCase().replaceAll("_", "-");
}

export function sameLanguage(a: string, b: string): boolean {
  return languageKey(a) === languageKey(b);
}

/** CGL-24: the languages a set is translated into, its first language first. */
export function languagesOf(project: ProjectInfo): string[] {
  if (project.languages.length > 0) return project.languages.map((l) => l.target_lang);
  return project.target_lang ? [project.target_lang] : [];
}

/** A file's runs in one language, newest first (the API lists them newest first). */
export function runsIn(doc: DocumentSummary, lang: string): DocumentRun[] {
  return doc.runs.filter((r) => sameLanguage(r.target_lang, lang));
}

export function newestIn(doc: DocumentSummary, lang: string): DocumentRun | undefined {
  return doc.runs.find((r) => sameLanguage(r.target_lang, lang));
}

/** The newest run of a file in each of its languages. */
export function newestRuns(doc: DocumentSummary): DocumentRun[] {
  const seen = new Set<string>();
  return doc.runs.filter((r) => {
    const key = languageKey(r.target_lang);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

const URGENCY: Status["tone"][] = ["bad", "busy", "warn", "ok", "none"];

/** A language tab's status: the most pressing status of its files' newest runs. A stopped file
 * needs attention, as the sidebar counts it. When every file is ready, the passages still to
 * check across them, as the sidebar puts them before "ready" (CGL-73). */
export function languageStatus(docs: DocumentSummary[], lang: string): Status | null {
  const newest = docs.flatMap((d) => newestIn(d, lang) ?? []);
  if (newest.length === 0) return null;
  const worst = newest
    .map((run) => runStateStatus(run.state === "cancelled" ? { ...run, state: "failed" } : run))
    .reduce((worst, s) => (URGENCY.indexOf(s.tone) < URGENCY.indexOf(worst.tone) ? s : worst));
  const toCheck = newest.reduce((sum, run) => sum + run.to_check, 0);
  if (worst.tone === "ok" && toCheck > 0) {
    return { text: `${toCheck} to check`, symbol: "⚠", tone: "warn" };
  }
  return worst;
}

/** "Spanish", "Spanish and German", "Spanish, German and French". */
export function listLanguages(codes: string[]): string {
  const names = codes.map((c) => languageName(c));
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}
