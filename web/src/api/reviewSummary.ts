import { useQuery } from "@tanstack/react-query";
import { client, unwrap } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type SetReviewSummary = S["SetReviewSummary"];
export type LanguageReviewSummary = S["LanguageReviewSummary"];
export type FlagReviewFigures = S["FlagReviewFigures"];

/** CGL-112: how much reviewing each language of a set took (administrators only). Loaded when
 * the panel opens, and again each time it opens, since reviewing goes on meanwhile. */
export function useSetReviewSummary(projectId: string) {
  return useQuery({
    queryKey: ["projects", projectId, "review-summary"],
    queryFn: () =>
      unwrap(
        client.GET("/api/projects/{project_id}/review-summary", {
          params: { path: { project_id: projectId } },
        }),
      ),
  });
}

/** The CSV download of a set's passages, one language when `lang` is given. */
export function setReviewCsvUrl(projectId: string, lang?: string): string {
  const base = `/api/projects/${encodeURIComponent(projectId)}/review-summary.csv`;
  return lang ? `${base}?${new URLSearchParams({ target_lang: lang }).toString()}` : base;
}

/** The CSV download of one run's passages. */
export function runReviewCsvUrl(runId: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/review-summary.csv`;
}
