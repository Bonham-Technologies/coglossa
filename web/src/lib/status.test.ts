import { progress, project, runDetail } from "../test/fixtures";
import type { RunDetail } from "../api/types";
import {
  bannerFor,
  percentDone,
  rerunKind,
  runStateStatus,
  setStatus,
  stepIndex,
  STEPS,
} from "./status";

it("words a set's status for the sidebar", () => {
  expect(setStatus({ ...project, files_failed: 1 })).toEqual({
    text: "needs attention",
    symbol: "!",
    tone: "bad",
  });
  expect(setStatus({ ...project, files_translating: 1 }).text).toBe("translating…");
  expect(setStatus({ ...project, open_flags: 2 })).toEqual({
    text: "2 to check",
    symbol: "⚠",
    tone: "warn",
  });
  expect(setStatus({ ...project, open_flags: 0 })).toEqual({
    text: "ready",
    symbol: "✓",
    tone: "ok",
  });
  expect(setStatus({ ...project, open_flags: 0, files_ready: 0 }).text).toBe("no files yet");
});

it("words a file's run state", () => {
  expect(runStateStatus({ state: "translating", finished_at: null }).text).toBe("translating…");
  expect(runStateStatus({ state: "created", finished_at: null })).toEqual({
    text: "starting…",
    symbol: "…",
    tone: "busy",
  });
  expect(runStateStatus({ state: "ingesting", finished_at: null }).text).toBe("starting…");
  expect(runStateStatus({ state: "profiling", finished_at: null }).text).toBe("translating…");
  expect(runStateStatus({ state: "awaiting_guide_approval", finished_at: null }).text).toBe(
    "waiting for you",
  );
  expect(runStateStatus({ state: "complete", finished_at: null }).text).toBe("ready");
  expect(runStateStatus({ state: "complete_with_errors", finished_at: null }).text).toBe("ready");
  expect(runStateStatus({ state: "failed", finished_at: null }).text).toBe("needs attention");
  expect(runStateStatus({ state: "cancelled", finished_at: null }).text).toBe("stopped");
  expect(runStateStatus({ state: "sweeping", finished_at: null }).text).toBe("translating…");
  expect(runStateStatus({ state: "sweeping", finished_at: "2026-10-01T09:05:00Z" })).toEqual({
    text: "rechecking…",
    symbol: "…",
    tone: "busy",
  });
  // a finished file run again for the sections that failed
  expect(runStateStatus({ state: "translating", finished_at: "2026-10-01T09:05:00Z" })).toEqual({
    text: "retrying…",
    symbol: "…",
    tone: "busy",
  });
});

it("maps every run state onto the step list", () => {
  expect(STEPS).toEqual([
    "Waiting to start",
    "Reading your document",
    "Building the translation guide",
    "Translating",
    "Checking consistency",
    "Ready",
  ]);
  expect(stepIndex("created")).toBe(0);
  expect(stepIndex("profiling")).toBe(1);
  expect(stepIndex("awaiting_guide_approval")).toBe(2);
  expect(stepIndex("translating")).toBe(3);
  expect(stepIndex("sweeping")).toBe(4);
  expect(stepIndex("complete_with_errors")).toBe(5);
  expect(stepIndex("failed")).toBe(-1);
});

it("averages the three stages into one percentage", () => {
  expect(
    percentDone({
      ...progress,
      chunks_total: 4,
      chunks_done: { translate: 3, reconcile: 2, score: 1 },
    }),
  ).toBe(50);
  expect(percentDone({ ...progress, chunks_total: 0, chunks_done: {} })).toBe(0);
  expect(percentDone({ ...progress, chunks_total: 2, chunks_done: { translate: 9 } })).toBe(33);
});

const finished = (
  patch: Partial<RunDetail["run"]>,
  stats: Partial<RunDetail["stats"]> = {},
  extra: Partial<RunDetail> = {},
): RunDetail => ({
  ...runDetail,
  ...extra,
  run: { ...runDetail.run, state: "complete", ...patch },
  stats: { spans: 6, open_flags: 0, flags_by_code: {}, edited_spans: 0, ...stats },
  progress: { ...runDetail.progress, retryable: false, ...(extra.progress ?? {}) },
});

it("chooses the banner for each outcome", () => {
  expect(bannerFor(finished({}), "Spanish")).toEqual({
    tone: "ok",
    text: "Looks good. 6 passages translated, nothing left to check.",
    action: null,
    careful: null,
  });
  expect(bannerFor(finished({}, { open_flags: 2 }), "Spanish")).toMatchObject({
    text: "Looks good. 2 are worth a quick look before you download.",
    action: "next",
  });
  expect(bannerFor(finished({}, { open_flags: 1 }), "Spanish").text).toBe(
    "Looks good. 1 is worth a quick look before you download.",
  );
  expect(bannerFor(finished({}, {}, { language_tier: "C" }), "Tongan").careful).toBe(
    "Check carefully: Coglossa has less practice with Tongan.",
  );
  expect(
    bannerFor(
      finished(
        { state: "complete_with_errors" },
        { open_flags: 2, flags_by_code: { engine_error: 2 } },
        { progress: { ...runDetail.progress, retryable: true } },
      ),
      "Spanish",
    ),
  ).toMatchObject({
    tone: "neutral",
    text: "Ready. Some checking didn't finish on 2 passages; they're marked.",
    action: "retry",
  });
  // every unfinished passage dealt with: worded like a complete run
  expect(bannerFor(finished({ state: "complete_with_errors" }), "Spanish").text).toMatch(
    /^Looks good/,
  );
  expect(
    bannerFor(
      finished({ state: "failed" }, {}, { progress: { ...runDetail.progress, retryable: true } }),
      "Spanish",
    ),
  ).toEqual({
    tone: "bad",
    text: "This translation couldn't finish. Something went wrong while translating.",
    action: "retry",
    careful: null,
  });
  expect(bannerFor(finished({ state: "failed" }), "Spanish").action).toBe("again");
  expect(bannerFor(finished({ state: "cancelled" }), "Spanish")).toMatchObject({
    tone: "neutral",
    text: "Stopped before it finished.",
    action: "again",
  });
});

it("names a problem with the whole file instead of saying all is well (CGL-80)", () => {
  const noGuide = {
    stage: "guide",
    text: "The translation guide for this file couldn't be built, so names and terms may vary.",
  };
  const settings = {
    stage: "config",
    text: "The settings changed while this file waited; the translation used the new ones.",
  };
  const banner = bannerFor(
    finished({ state: "complete_with_errors" }, {}, { problems: [noGuide] }),
    "Spanish",
  );
  expect(banner).toEqual({
    tone: "neutral",
    text: `Ready. ${noGuide.text}`,
    action: "again",
    careful: null,
  });
  expect(banner.text).not.toMatch(/Looks good/);
  // a retry can help when something else failed too; open flags and unfinished passages stay named
  expect(
    bannerFor(
      finished(
        { state: "complete_with_errors" },
        { open_flags: 3, flags_by_code: { engine_error: 1 } },
        { problems: [settings, noGuide], progress: { ...runDetail.progress, retryable: true } },
      ),
      "Spanish",
    ),
  ).toMatchObject({
    tone: "neutral",
    text: `Ready. Some checking didn't finish on 1 passage; they're marked. ${settings.text} ${noGuide.text}`,
    action: "retry",
  });
  expect(
    bannerFor(
      finished({ state: "complete_with_errors" }, { open_flags: 2 }, { problems: [settings] }),
      "Spanish",
    ),
  ).toMatchObject({
    text: `Ready. ${settings.text} 2 are worth a quick look before you download.`,
    action: "next", // the way to those passages stays; "Translate again" is in the set's menu
  });
});

it("says when some passages weren't fully checked instead of looking good (CGL-95)", () => {
  // the engine names checks (judge, back translation, scorer) that failed on some passages
  const unchecked = {
    stage: "score",
    text: "Some passages weren't fully checked, so mistakes in them may not be marked.",
  };
  const withErrors = { ...runDetail.progress, chunks_with_errors: 2, retryable: true };
  const banner = bannerFor(
    finished(
      { state: "complete_with_errors" },
      {},
      { problems: [unchecked], progress: withErrors },
    ),
    "Spanish",
  );
  expect(banner).toEqual({
    tone: "neutral",
    text: `Ready. ${unchecked.text}`,
    action: "retry",
    careful: null,
  });
  expect(
    bannerFor(
      finished(
        { state: "complete_with_errors" },
        { open_flags: 2 },
        { problems: [unchecked], progress: withErrors },
      ),
      "Spanish",
    ),
  ).toMatchObject({
    text: `Ready. ${unchecked.text} 2 are worth a quick look before you download.`,
    action: "retry",
  });
  // passages whose unfinished checking was dealt with still count as chunks with errors, so
  // the count alone doesn't decide: with no problem named, the file reads as finished
  expect(
    bannerFor(
      finished(
        { state: "complete_with_errors" },
        {},
        { progress: { ...withErrors, chunks_with_errors: 1 } },
      ),
      "Spanish",
    ).text,
  ).toMatch(/^Looks good/);
});

it("tells a recheck or retry of a finished file from a first run", () => {
  const done = "2026-10-01T09:05:00Z";
  expect(rerunKind({ state: "sweeping", finished_at: done })).toBe("recheck");
  expect(rerunKind({ state: "translating", finished_at: done })).toBe("retry");
  expect(rerunKind({ state: "sweeping", finished_at: null })).toBeNull();
  expect(rerunKind({ state: "complete", finished_at: done })).toBeNull();
  expect(rerunKind({ state: "awaiting_guide_approval", finished_at: done })).toBeNull();
});
