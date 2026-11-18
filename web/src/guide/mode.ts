/** What the translation guide panel edits: the set's guide, or a waiting run's draft (§4.4). */
export type GuideMode = { kind: "project" } | { kind: "draft"; runId: string };
