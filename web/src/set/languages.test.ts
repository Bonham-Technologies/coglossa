import type { DocumentSummary, RunState } from "../api/types";
import { documentRun, documentSummary, project } from "../test/fixtures";
import {
  languageKey,
  languageStatus,
  languagesOf,
  listLanguages,
  newestIn,
  newestRuns,
  runsIn,
  sameLanguage,
} from "./languages";

const es = (id: string, state: RunState, created: string) => ({
  ...documentRun,
  id,
  target_lang: "es",
  state,
  created_at: created,
  finished_at: state === "complete" ? created : null,
});
const doc: DocumentSummary = {
  ...documentSummary,
  runs: [
    es("r-3", "translating", "2026-10-03T00:00:00Z"),
    es("r-2", "complete", "2026-10-02T00:00:00Z"),
    documentRun,
  ],
};

it("compares language codes the way BCP 47 does", () => {
  expect(languageKey("pt_BR")).toBe("pt-br");
  expect(sameLanguage("ES", "es")).toBe(true);
  expect(sameLanguage("es", "en")).toBe(false);
});

it("lists a set's languages, falling back to its default", () => {
  const two = [
    { target_lang: "en", guide_version: 1 },
    { target_lang: "es", guide_version: null },
  ];
  expect(languagesOf({ ...project, languages: two })).toEqual(["en", "es"]);
  expect(languagesOf({ ...project, languages: [] })).toEqual(["en"]);
  expect(languagesOf({ ...project, languages: [], target_lang: null })).toEqual([]);
});

it("finds a file's runs in one language, newest first", () => {
  expect(runsIn(doc, "es").map((r) => r.id)).toEqual(["r-3", "r-2"]);
  expect(newestIn(doc, "ES")?.id).toBe("r-3");
  expect(newestIn(doc, "de")).toBeUndefined();
  expect(newestRuns(doc).map((r) => r.id)).toEqual(["r-3", "r-1"]);
});

it("sums up one language's files", () => {
  const failed: DocumentSummary = { ...doc, runs: [{ ...documentRun, state: "failed" }] };
  expect(languageStatus([doc], "es")?.text).toBe("translating…");
  expect(languageStatus([doc], "en")?.text).toBe("ready");
  expect(languageStatus([doc, failed], "en")?.text).toBe("needs attention");
  expect(languageStatus([failed, doc], "en")?.text).toBe("needs attention");
  expect(languageStatus([doc], "de")).toBeNull();
  const stopped: DocumentSummary = { ...doc, runs: [{ ...documentRun, state: "cancelled" }] };
  expect(languageStatus([doc, stopped], "en")?.text).toBe("needs attention"); // as the sidebar
});

it("counts the passages left to check in a language's ready files", () => {
  // CGL-73: the newest run of each file in the language, as the documents list counts them
  const ready = (id: string, toCheck: number): DocumentSummary => ({
    ...documentSummary,
    id,
    runs: [
      { ...documentRun, to_check: toCheck },
      { ...documentRun, to_check: 9 },
    ],
  });
  const both = [ready("d-1", 3), ready("d-2", 1)];
  expect(languageStatus(both, "en")).toEqual({ text: "4 to check", symbol: "⚠", tone: "warn" });
  expect(languageStatus([ready("d-1", 1)], "en")?.text).toBe("1 to check");
  expect(languageStatus([ready("d-1", 0)], "en")?.text).toBe("ready"); // nothing to check
  // a file still translating or one that failed comes first, as in the sidebar
  const busy: DocumentSummary = {
    ...doc,
    runs: [{ ...documentRun, state: "translating", finished_at: null }],
  };
  expect(languageStatus([...both, busy], "en")?.text).toBe("translating…");
  const failed: DocumentSummary = { ...doc, runs: [{ ...documentRun, state: "failed" }] };
  expect(languageStatus([...both, failed], "en")?.text).toBe("needs attention");
});

it("names languages in a sentence", () => {
  expect(listLanguages([])).toBe("");
  expect(listLanguages(["es"])).toBe("Spanish");
  expect(listLanguages(["es", "de"])).toBe("Spanish and German");
  expect(listLanguages(["es", "de", "fr"])).toBe("Spanish, German and French");
});
