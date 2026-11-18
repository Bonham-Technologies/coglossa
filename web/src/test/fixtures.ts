import type {
  ConfigView,
  DoctorReport,
  DocumentRun,
  DocumentSummary,
  Guide,
  GuideView,
  Me,
  ProjectInfo,
  RunDetail,
  RunProgress,
  ReviewSpan,
  RunSummary,
  SpanDetail,
  SpanPage,
  TierTable,
} from "../api/types";

export const project: ProjectInfo = {
  id: "p-1",
  name: "Atlas",
  target_lang: "en",
  source_lang: "fr",
  pipeline_profile: null,
  team: null,
  created_by: null,
  guide_version: 1,
  languages: [{ target_lang: "en", guide_version: 1 }],
  documents: 1,
  runs: 1,
  created_at: "2026-10-01T09:00:00Z",
  last_activity_at: "2026-10-01T09:05:00Z",
  files_translating: 0,
  files_ready: 1,
  files_failed: 0,
  open_flags: 2,
};

export const runSummary: RunSummary = {
  id: "r-1",
  project_id: "p-1",
  document_id: "d-1",
  filename: "histoire.txt",
  state: "complete",
  source_lang: "fr",
  target_lang: "en",
  pipeline_profile: "short",
  created_at: "2026-10-01T09:00:00Z",
  started_at: "2026-10-01T09:00:01Z",
  finished_at: "2026-10-01T09:05:00Z",
  error: null,
};

/** A run as the set's documents list shows it (CGL-73: with its passages to check). */
export const documentRun: DocumentRun = { ...runSummary, to_check: 0 };

export const progress: RunProgress = {
  run_id: "r-1",
  state: "complete",
  chunks_total: 2,
  chunks_done: { translate: 2, reconcile: 2, score: 2 },
  chunks_with_errors: 0,
  error: null,
  retryable: false,
};

export const otherProject: ProjectInfo = {
  ...project,
  id: "p-2",
  name: "رواية",
  source_lang: null,
  target_lang: "ar",
  guide_version: null,
  languages: [{ target_lang: "ar", guide_version: null }],
  documents: 0,
  runs: 0,
  files_ready: 0,
  open_flags: 0,
};

export const documentSummary: DocumentSummary = {
  id: "d-1",
  project_id: "p-1",
  filename: "histoire.txt",
  format: "txt",
  ingested: true,
  created_at: "2026-10-01T09:00:00Z",
  runs: [documentRun],
};

export const runDetail: RunDetail = {
  run: { ...runSummary, state: "translating", finished_at: null },
  progress: {
    ...progress,
    state: "translating",
    chunks_total: 4,
    chunks_done: { translate: 3, reconcile: 2, score: 1 },
  },
  report_available: false,
  sweep_enabled: true,
  guide_version: 1,
  language_tier: "B",
  profile: {
    source_lang: "fr",
    target_lang: "en",
    genre: "literary_prose",
    tone_register: "literary, past tense",
    word_count: 1840,
    mixed_language: false,
    pipeline_profile: "standard",
  },
  summary: null,
  notices: [],
  stats: { spans: 0, open_flags: 0, flags_by_code: {}, edited_spans: 0 },
};

export const guide: Guide = {
  summary: "Une ville sous la pluie ; un chat endormi.",
  style_guide: "Plain, past tense.",
  glossary: [{ term: "chat", rendering: "cat", notes: "", locked: false }],
  entities: [
    {
      name: "Marie",
      kind: "person",
      rendering: "Marie",
      gender: "female",
      forms_of_address: "vous",
      speech_style: "",
      locked: true,
    },
  ],
  cultural_references: ["la ville lumière"],
};

export const guideView: GuideView = {
  guide,
  info: {
    version: 1,
    target_lang: "en",
    status: "draft",
    author: "engine",
    created_at: "2026-10-01T09:01:00Z",
    source_run_id: "r-1",
  },
};

const score = (meaning: number | null, style: number | null) => ({
  meaning,
  style,
  reliability: "B" as const,
  signals: {
    qe: meaning === null ? null : meaning / 100,
    xsim: 0.8,
    roundtrip: null,
    judge: 0.7,
    agreement: 0.5,
  },
});

export const reviewSpans: ReviewSpan[] = [
  {
    id: "s-1",
    number: 1,
    chunk_id: "c-1",
    start: "s1",
    end: "s1",
    source: "Chapitre premier",
    text: "Chapter One",
    agreement: "agree",
    edited: false,
    score: score(92, 88),
    flags: [],
  },
  {
    id: "s-2",
    number: 2,
    chunk_id: "c-1",
    start: "s2",
    end: "s3",
    source: "Le chat dort. Il pleut.",
    text: "The cat sleeps. It rains.",
    agreement: "minor",
    edited: false,
    score: score(55, 70),
    flags: [
      {
        id: 11,
        code: "low_meaning",
        status: "open",
        explanation: "Meaning score 55.0 is below the 72 threshold.",
        evidence: {},
        dismiss_reason: null,
      },
      {
        id: 12,
        code: "agent_disagreement",
        status: "open",
        explanation: "The translators disagreed on the tense.",
        evidence: {},
        dismiss_reason: null,
      },
    ],
  },
  {
    id: "s-3",
    number: 3,
    chunk_id: "c-2",
    start: "s4",
    end: "s4",
    source: "مرحبا",
    text: "Hello",
    agreement: "agree",
    edited: true,
    score: score(null, null),
    flags: [
      {
        id: 13,
        code: "terminology_violation",
        status: "dismissed",
        explanation: "The locked term 'Fin' must be rendered 'The End'.",
        evidence: {},
        dismiss_reason: "intended",
      },
      {
        id: 14,
        code: "unscored",
        status: "open",
        explanation: "No scorer or judge produced a score.",
        evidence: {},
        dismiss_reason: null,
      },
    ],
  },
];

export function spanPage(spans: ReviewSpan[], total = spans.length, offset = 0): SpanPage {
  return { spans, total, offset, limit: 50 };
}

export const spanDetail: SpanDetail = {
  ...reviewSpans[1]!,
  chosen_from: "literal",
  rationale: "Kept the literal tense.",
  issues: ["tense of 'dort'"],
  back_translation: "Le chat dort. Il pleut.",
  judge_errors: [
    {
      category: "accuracy/mistranslation",
      severity: "major",
      excerpt: "sleeps",
      explanation: "'Dort' here means 'is asleep'.",
    },
  ],
  candidates: [
    {
      translator: "literal",
      model: "m1",
      chunk_id: "c-1",
      start: "s2",
      end: "s2",
      text: "The cat sleeps.",
      notes: "",
    },
    {
      translator: "literal",
      model: "m1",
      chunk_id: "c-1",
      start: "s3",
      end: "s3",
      text: "It rains.",
      notes: "",
    },
    {
      translator: "contextual",
      model: "m2",
      chunk_id: "c-1",
      start: "s2",
      end: "s3",
      text: "The cat is asleep. It rains.",
      notes: "",
    },
  ],
  revisions: [
    {
      source: "engine",
      before: null,
      after: "The cat sleeps. It rains.",
      reason: "reconciled",
      created_at: "2026-10-01T09:04:00Z",
    },
  ],
};

export const doctorReport: DoctorReport = {
  ok: false,
  checks: [
    { name: "config", status: "pass", message: "coglossa.yaml is valid", fix: "" },
    {
      name: "backend local",
      status: "warn",
      message: "only one model is configured; reliability is downgraded",
      fix: "add a second model for the judge",
    },
    {
      name: "scorer",
      status: "fail",
      message: "http://scorer:8080 is not reachable",
      fix: "start the scorer service or remove scorer: from the config",
    },
  ],
};

export const configView: ConfigView = {
  path: "/etc/coglossa/coglossa.yaml",
  hash: "3f2a9c",
  config: { backends: { local: { base_url: "http://llm:8000/v1" } }, api: { max_upload_mb: 200 } },
};

export const tiers: TierTable = {
  default: "B",
  languages: { en: "A", fr: "A", ar: "B", am: "C" },
  pairs: { "fr>en": "A", "en>am": "C" },
};

/** /api/me without sign-in (auth.mode none): everyone is an admin (CGL-26). */
export const me: Me = { user: null, admin: true, teams: [], max_upload_mb: 200, logout_url: null };
