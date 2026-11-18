import type { ProjectInfo, RunDetail, RunProgress, RunState } from "../api/types";
import { plural } from "./flags";
import { isTerminal } from "./format";

export type Tone = "ok" | "warn" | "busy" | "bad" | "none";
/** A status always carries words and a symbol, never colour alone (spec §7.2). */
export type Status = { text: string; symbol: string; tone: Tone };

const NEEDS_ATTENTION: Status = { text: "needs attention", symbol: "!", tone: "bad" };
const TRANSLATING: Status = { text: "translating…", symbol: "…", tone: "busy" };
const STARTING: Status = { text: "starting…", symbol: "…", tone: "busy" };
const RECHECKING: Status = { text: "rechecking…", symbol: "…", tone: "busy" };
const RETRYING: Status = { text: "retrying…", symbol: "…", tone: "busy" };
const READY: Status = { text: "ready", symbol: "✓", tone: "ok" };

/** Spec §3.1: a set's row in the sidebar. */
export function setStatus(p: ProjectInfo): Status {
  if (p.files_failed > 0) return NEEDS_ATTENTION;
  if (p.files_translating > 0) return TRANSLATING;
  if (p.open_flags > 0) return { text: `${p.open_flags} to check`, symbol: "⚠", tone: "warn" };
  if (p.files_ready > 0) return READY;
  return { text: "no files yet", symbol: "", tone: "none" };
}

/** A file tab's status (spec §5.1): the run summary has its state only. */
export function runStateStatus(run: { state: RunState; finished_at: string | null }): Status {
  if (rerunKind(run) === "retry") return RETRYING;
  switch (run.state) {
    case "complete":
    case "complete_with_errors":
      return READY;
    case "failed":
      return NEEDS_ATTENTION;
    case "cancelled":
      return { text: "stopped", symbol: "■", tone: "none" };
    case "awaiting_guide_approval":
      return { text: "waiting for you", symbol: "⚠", tone: "warn" };
    case "sweeping":
      // a finished file being rechecked against the guide
      return run.finished_at !== null ? RECHECKING : TRANSLATING;
    case "created":
    case "ingesting":
      return STARTING;
    default:
      return TRANSLATING;
  }
}

/** A finished file being run again keeps its document on screen: a recheck (the sweep) or a
 * retry (anything else, e.g. the sections that failed). Null for a first run or a finished one. */
export function rerunKind(run: {
  state: RunState;
  finished_at: string | null;
}): "recheck" | "retry" | null {
  if (run.finished_at === null || isTerminal(run.state)) return null;
  if (run.state === "awaiting_guide_approval") return null; // the guide checkpoint takes over
  return run.state === "sweeping" ? "recheck" : "retry";
}

/** The one rule for keeping a document on screen during a re-run: there must be passages to keep.
 * Without any, the steps show instead. */
export function keptRerun(detail: RunDetail): "recheck" | "retry" | null {
  return detail.stats.spans > 0 ? rerunKind(detail.run) : null;
}

/** Spec §4.2. */
export const STEPS = [
  "Waiting to start",
  "Reading your document",
  "Building the translation guide",
  "Translating",
  "Checking consistency",
  "Ready",
] as const;

const STEP_OF: Record<RunState, number> = {
  created: 0,
  ingesting: 1,
  profiling: 1,
  building_guide: 2,
  awaiting_guide_approval: 2,
  translating: 3,
  sweeping: 4,
  complete: 5,
  complete_with_errors: 5,
  failed: -1,
  cancelled: -1,
};

export function stepIndex(state: RunState): number {
  return STEP_OF[state];
}

const STAGES = ["translate", "reconcile", "score"] as const;

/** One percentage over the three chunk stages. */
export function percentDone(p: RunProgress): number {
  if (p.chunks_total <= 0) return 0;
  const done = STAGES.reduce(
    (sum, stage) => sum + Math.min(p.chunks_done[stage] ?? 0, p.chunks_total),
    0,
  );
  return Math.floor((100 * done) / (STAGES.length * p.chunks_total));
}

export type Banner = {
  tone: "ok" | "neutral" | "bad";
  text: string;
  action: "next" | "retry" | "again" | null;
  careful: string | null;
};

function worthALook(flags: number): string {
  return `${flags} ${flags === 1 ? "is" : "are"} worth a quick look before you download.`;
}

/** Spec §5.2 (and plan decisions 3 and 6). */
export function bannerFor(detail: RunDetail, targetName: string): Banner {
  const { run, stats, progress } = detail;
  if (run.state === "failed") {
    return {
      tone: "bad",
      text: "This translation couldn't finish. Something went wrong while translating.",
      action: progress.retryable ? "retry" : "again",
      careful: null,
    };
  }
  if (run.state === "cancelled") {
    return { tone: "neutral", text: "Stopped before it finished.", action: "again", careful: null };
  }
  const careful =
    detail.language_tier === "C"
      ? `Check carefully: Coglossa has less practice with ${targetName}.`
      : null;
  const unfinished = stats.flags_by_code["engine_error"] ?? 0;
  const problems = detail.problems ?? [];
  if (run.state === "complete_with_errors" && problems.length > 0) {
    // CGL-80: a problem with the whole file (say its guide couldn't be built) is never "looks
    // good"; a retry only redoes passages, so without one the file is translated again. Passages
    // still worth a look keep the way to them: "Translate again" stays in the set's menu.
    const parts = ["Ready."];
    if (unfinished > 0) {
      parts.push(
        `Some checking didn't finish on ${plural(unfinished, "passage")}; they're marked.`,
      );
    }
    parts.push(...problems.map((p) => p.text));
    if (unfinished === 0 && stats.open_flags > 0) parts.push(worthALook(stats.open_flags));
    return {
      tone: "neutral",
      text: parts.join(" "),
      action: progress.retryable ? "retry" : stats.open_flags > 0 ? "next" : "again",
      careful,
    };
  }
  if (run.state === "complete_with_errors" && unfinished > 0) {
    return {
      tone: "neutral",
      text: `Ready. Some checking didn't finish on ${plural(unfinished, "passage")}; they're marked.`,
      action: progress.retryable ? "retry" : null,
      careful,
    };
  }
  if (stats.open_flags > 0) {
    return {
      tone: "ok",
      text: `Looks good. ${worthALook(stats.open_flags)}`,
      action: "next",
      careful,
    };
  }
  return {
    tone: "ok",
    text: `Looks good. ${plural(stats.spans, "passage")} translated, nothing left to check.`,
    action: null,
    careful,
  };
}
