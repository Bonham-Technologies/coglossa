import {
  agreementLabel,
  categoryLabel,
  reliabilityLabel,
  revisionSourceLabel,
  severityLabel,
  signalLabel,
} from "./labels";

const cases: [string, (k: string) => string, [string, string][]][] = [
  [
    "signalLabel",
    signalLabel,
    [
      ["qe", "Quality estimate"],
      ["xsim", "Closeness to the original"],
      ["roundtrip", "Back translation match"],
      ["judge", "Checker's rating"],
      ["agreement", "Translator agreement score"],
      ["guide_check", "Translation guide check"],
    ],
  ],
  [
    "revisionSourceLabel",
    revisionSourceLabel,
    [
      ["engine", "Translated"],
      ["sweep", "Translation guide applied"],
      ["user", "Changed by a reviewer"],
    ],
  ],
  [
    "agreementLabel",
    agreementLabel,
    [
      ["agree", "Translators agreed"],
      ["minor", "Wording differed"],
      ["major", "Meaning differed"],
      ["unknown", "Not compared"],
    ],
  ],
  [
    "reliabilityLabel",
    reliabilityLabel,
    [
      ["A", "High"],
      ["B", "Fair"],
      ["C", "Low"],
      ["unscored", "Not scored"],
    ],
  ],
  [
    "severityLabel",
    severityLabel,
    [
      ["minor", "Minor"],
      ["major", "Major"],
      ["critical", "Critical"],
    ],
  ],
  [
    "categoryLabel",
    categoryLabel,
    [
      ["accuracy/mistranslation", "Wrong meaning"],
      ["accuracy/omission", "Something left out"],
      ["accuracy/addition", "Something added"],
      ["terminology", "Wrong term"],
      ["fluency", "Reads awkwardly"],
      ["style/register", "Tone or style"],
      ["locale", "Local conventions"],
    ],
  ],
];

describe.each(cases)("%s", (_name, fn, known) => {
  it.each(known)("maps %s", (key, label) => {
    expect(fn(key)).toBe(label);
  });
  it("falls back to the raw key", () => {
    expect(fn("something_new")).toBe("something_new");
  });
});
