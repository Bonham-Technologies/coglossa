import { FLAG_CODES } from "../review/filters";
import { FLAG_STATUS_WORDS, FLAG_TITLES, flagExplanation, flagTitle, plural } from "./flags";

it("keeps the engine's text when a score flag lacks its score or threshold", () => {
  const base = { code: "low_meaning", explanation: "Engine text.", evidence: {} };
  const engine = { text: "Engine text.", note: null };
  expect(flagExplanation(base)).toEqual(engine);
  expect(flagExplanation({ ...base, evidence: { meaning: 50 } })).toEqual(engine);
  expect(flagExplanation({ ...base, evidence: { meaning: null, threshold: 86 } })).toEqual(engine);
  expect(flagExplanation({ ...base, evidence: { meaning: 90, threshold: 86 } })).toEqual(engine);
  expect(
    flagExplanation({ ...base, code: "agent_disagreement", evidence: { threshold: 9 } }),
  ).toEqual(engine);
});

it("words a style flag the same way, without a note when the judge left none", () => {
  expect(
    flagExplanation({
      code: "style_drift",
      explanation: "Engine text.",
      evidence: { style: 70.4, threshold: 75, judge_errors: [] },
    }),
  ).toEqual({
    text: "The checker rated the style 70 out of 100; passages under 75 get a second look.",
    note: null,
  });
});

it("never shows a rounded score that reaches the threshold", () => {
  const flag = (meaning: number, threshold: number) =>
    flagExplanation({ code: "low_meaning", explanation: "", evidence: { meaning, threshold } })
      .text;
  expect(flag(85.6, 86)).toBe(
    "The checker rated the meaning 85 out of 100; passages under 86 get a second look.",
  );
  expect(flag(72.6, 72.7)).toMatch(/meaning 72 out of 100; passages under 72.7 /);
  expect(flag(85.4, 86)).toMatch(/meaning 85 out of 100/);
  expect(flag(61.5, 86)).toMatch(/meaning 62 out of 100/);
});

it("takes the judge's note as the engine does: the first critical error, else the first", () => {
  const note = (judge_errors: unknown[]) =>
    flagExplanation({
      code: "low_meaning",
      explanation: "",
      evidence: { meaning: 50, threshold: 86, judge_errors },
    }).note;
  const minor = { severity: "minor", explanation: "Minor slip." };
  const critical = { severity: "critical", explanation: "Wrong tense." };
  expect(note([minor, critical])).toBe("Wrong tense.");
  expect(note([minor, { severity: "major", explanation: "Other." }])).toBe("Minor slip.");
  expect(note([{ severity: "minor", explanation: "" }])).toBeNull();
});

it("has a plain title for every engine flag code", () => {
  for (const code of FLAG_CODES) expect(FLAG_TITLES[code]).toMatch(/^[A-Z]/);
  expect(flagTitle("possible_omission")).toBe("Something may be missing");
  expect(flagTitle("engine_error")).toBe("Some checking didn't finish");
  expect(flagTitle("number_mismatch")).toBe("A number doesn't match the original");
  expect(flagTitle("untranslated")).toBe("This passage may not have been translated");
  expect(flagTitle("literal_mismatch")).toBe("A link, email or placeholder was changed");
});

it("keeps the engine's plain wording for a number that doesn't match", () => {
  const flag = {
    code: "number_mismatch",
    explanation:
      "The numbers don't match the original: the original has 1,200; the translation has 1 300. " +
      "Check the numbers, dates and amounts in this passage.",
    evidence: { source: ["1,200"], target: ["1 300"] },
  };
  expect(flagExplanation(flag)).toEqual({ text: flag.explanation, note: null });
});

it("falls back for a code the app does not know yet", () => {
  expect(flagTitle("brand_new_code")).toBe("Worth a look");
});

it("words flag statuses and counts", () => {
  expect(FLAG_STATUS_WORDS).toEqual({
    open: "to check",
    accepted: "checked",
    dismissed: "not a problem",
    resolved: "fixed by the recheck",
  });
  expect(plural(1, "passage")).toBe("1 passage");
  expect(plural(3, "file")).toBe("3 files");
});
