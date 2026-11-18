import createClient from "openapi-fetch";
import type { paths } from "./schema";
import type { ExportKind } from "./types";

// openapi-fetch keeps the fetch it is given; a late-bound wrapper lets tests (msw) patch fetch.
const lateFetch: typeof fetch = (input, init) => globalThis.fetch(input, init);

// The app always talks to the server it was loaded from. location.origin rather than "" because
// Node's fetch (tests) refuses relative URLs; in a browser the two are the same.
export const client = createClient<paths>({ baseUrl: location.origin, fetch: lateFetch });

export const NETWORK_ERROR =
  "The Coglossa server could not be reached; check that it is running and try again.";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code: string | null = null,
    readonly currentText: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The API's machine-readable conflict code, sent beside `detail` for the conflicts the app
 * handles specially. */
export function errorCode(body: unknown): string | null {
  const code = (body as { code?: unknown } | null | undefined)?.code;
  return typeof code === "string" ? code : null;
}

/** Someone else changed a passage after the reviewer opened it (CGL-36): the passage's text
 * now, or null for any other error. */
export function passageChangedTo(error: unknown): string | null {
  return error instanceof ApiError && error.status === 409 && error.code === "passage_changed"
    ? error.currentText
    : null;
}

/** The project guide changed under an edit or a draft: the answer is to reload it. */
export function isGuideChanged(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.code === "guide_changed";
}

function currentTextOf(body: unknown): string | null {
  const text = (body as { current_text?: unknown } | null | undefined)?.current_text;
  return typeof text === "string" ? text : null;
}

type ValidationItem = { loc?: (string | number)[]; msg?: string };
const PLACES = new Set(["body", "query", "path"]);

/** The one line to show for a failed request: the API's `detail` (spec: errors are one line). */
export function errorText(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null | undefined)?.detail;
  // The API's catch-all 500 says only "internal error"; its deliberate 500s (a broken setup, an
  // unreadable stored file) already say what to do, so those keep their words.
  if (status === 500 && (typeof detail !== "string" || !detail || detail === "internal error")) {
    return "Something went wrong on the server. Try again; if it keeps happening, tell your administrator.";
  }
  // The API's 401 names a header, which means nothing to a reader; operators still see it in logs.
  if (status === 401) return "You're signed out. Reload the page to sign in again.";
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as ValidationItem;
    const field = (first.loc ?? []).filter((part) => !PLACES.has(String(part))).join(".");
    return field ? `${field}: ${first.msg}` : String(first.msg);
  }
  // A reverse proxy answers these with its own HTML page while the API restarts or upgrades.
  if (status === 502 || status === 503 || status === 504) {
    return "The server is starting up or busy. Try again in a minute.";
  }
  if (status === 413) return "The file is too large to upload here.";
  return `The server answered ${status} without an explanation.`;
}

/** The data of an API call, or an ApiError with the message to show. */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response };
  try {
    result = await call;
  } catch {
    throw new ApiError(0, NETWORK_ERROR);
  }
  if (!result.response.ok) {
    throw new ApiError(
      result.response.status,
      errorText(result.response.status, result.error),
      errorCode(result.error),
      currentTextOf(result.error),
    );
  }
  return result.data as T;
}

/** A multipart body; fields left null are not sent (the API's defaults). */
export function formData(body: object): FormData {
  const form = new FormData();
  for (const [key, value] of Object.entries(body)) {
    if (value === null || value === undefined) continue;
    // typeof, not instanceof Blob: the file may come from another realm (tests).
    if (typeof value === "object") form.append(key, value as Blob);
    else form.append(key, String(value));
  }
  return form; // no Content-Type: the browser adds the multipart boundary
}

/** URLs the browser loads itself (an EventSource, a download link) rather than through fetch. */
export function eventsUrl(runId: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/events`;
}

export type ExportOptions = {
  comments?: boolean; // bilingual-docx: the flags as Word comments (the default)
  reviewedOnly?: boolean; // tmx: only the passages a person accepted or edited (CGL-89)
};

export function exportUrl(runId: string, kind: ExportKind, options: ExportOptions = {}): string {
  const base = `/api/runs/${encodeURIComponent(runId)}/exports/${kind}`;
  const query = new URLSearchParams();
  if (options.comments === false) query.set("comments", "false");
  if (options.reviewedOnly) query.set("reviewed_only", "true");
  const search = query.toString();
  return search ? `${base}?${search}` : base;
}

export function reportUrl(runId: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/report`;
}
