/** Plain words for the engine's enum values shown in the passage details (CGL-66). */
import type { JudgeError, Reliability, RevisionEntry, SpanDetail } from "../api/types";

/** A map typed by an API enum must name every value, so a regenerated schema with a new value
 * fails the type check; an unknown value at run time (a newer engine) still shows its raw key. */
const lookup =
  <K extends string>(map: Record<K, string>) =>
  (key: string): string =>
    (Object.hasOwn(map, key) ? (map as Record<string, string>)[key] : undefined) ?? key;

/** Score signal names are free-form keys in the API; these are the engine's (scoring/aggregate.py). */
export const signalLabel = lookup({
  qe: "Quality estimate",
  xsim: "Closeness to the original",
  roundtrip: "Back translation match",
  judge: "Checker's rating",
  agreement: "Translator agreement score",
  guide_check: "Translation guide check",
});

export const revisionSourceLabel = lookup<RevisionEntry["source"]>({
  engine: "Translated",
  sweep: "Translation guide applied",
  // any reviewer's edit or choice of another version, not only the viewer's
  user: "Changed by a reviewer",
});

export const agreementLabel = lookup<SpanDetail["agreement"]>({
  agree: "Translators agreed",
  minor: "Wording differed",
  major: "Meaning differed",
  unknown: "Not compared",
});

export const reliabilityLabel = lookup<Reliability>({
  A: "High",
  B: "Fair",
  C: "Low",
  unscored: "Not scored",
});

export const severityLabel = lookup<JudgeError["severity"]>({
  minor: "Minor",
  major: "Major",
  critical: "Critical",
});

export const categoryLabel = lookup<JudgeError["category"]>({
  "accuracy/mistranslation": "Wrong meaning",
  "accuracy/omission": "Something left out",
  "accuracy/addition": "Something added",
  terminology: "Wrong term",
  fluency: "Reads awkwardly",
  "style/register": "Tone or style",
  locale: "Local conventions",
});
