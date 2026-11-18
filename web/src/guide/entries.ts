import type { Entity, GlossaryEntry, Guide } from "../api/types";

export const EMPTY_GUIDE: Guide = {
  summary: "",
  style_guide: "",
  glossary: [],
  entities: [],
  cultural_references: [],
};

export const KIND_WORDS: Record<string, string> = {
  person: "Person",
  place: "Place",
  organisation: "Organisation",
  work: "Title of a work",
  other: "Other",
};

export function emptyTerm(): GlossaryEntry {
  return { term: "", rendering: "", notes: "", locked: false };
}

export function sameGuide(a: Guide, b: Guide): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** What stops a guide from being saved, in plain words (the API refuses the same). */
export function guideProblems(guide: Guide): string[] {
  const found: string[] = [];
  if (!guide.summary.trim()) found.push("Add a short summary on the Style tab.");
  if (guide.glossary.some((g) => !g.term.trim())) found.push("Every term needs its words.");
  else if (guide.glossary.some((g) => !g.rendering.trim()))
    found.push("Every term needs a translation.");
  if (guide.entities.some((e) => !e.name.trim())) found.push("Every name needs its words.");
  else if (guide.entities.some((e) => !e.rendering.trim()))
    found.push("Every name needs a translation.");
  return found;
}

export const ENTITY_KINDS = ["person", "place", "organisation", "work", "other"];

export function emptyEntity(): Entity {
  return {
    name: "",
    kind: "person",
    rendering: "",
    gender: null,
    forms_of_address: "",
    speech_style: "",
    locked: false,
  };
}
