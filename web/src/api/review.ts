import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { type ScoreFilter } from "../review/filters";
import { client, passageChangedTo, unwrap } from "./client";
import { guideSaved } from "./guide";
import { isDocumentsKey, projectKeys } from "./projects";
import { runKeys } from "./runs";
import type { Entity, GlossaryEntry, ReviewSpan, SpanDetail, SpanPage } from "./types";

export function useSpanDetail(runId: string, spanId: string) {
  return useQuery({
    queryKey: runKeys.span(runId, spanId),
    queryFn: () =>
      unwrap(
        client.GET("/api/runs/{run_id}/spans/{span_id}", {
          params: { path: { run_id: runId, span_id: spanId } },
        }),
      ),
  });
}

/** After a review action: the answer is the passage's new detail; the page of passages and the
 * run's statistics change with it. */
function useSpanChanged(runId: string) {
  const queryClient = useQueryClient();
  return (detail: SpanDetail) => {
    queryClient.setQueryData(runKeys.span(runId, detail.id), detail);
    // The server filters before it pages, so a page's offset moves with every flag resolved:
    // the pages are fetched again (ReviewPage pins the selected passage meanwhile).
    void queryClient.invalidateQueries({ queryKey: runKeys.pages(runId) });
    void queryClient.invalidateQueries({ queryKey: runKeys.one(runId), exact: true });
    // the sidebar's "N to check" counts move with every flag resolved
    void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
    // and so do the language tabs', read from the set's list of files (CGL-73)
    void queryClient.invalidateQueries({ predicate: ({ queryKey }) => isDocumentsKey(queryKey) });
  };
}

/** A change refused because someone else changed the passage: fetch the passage again, so the
 * panel shows their text (CGL-36). */
function useSpanStale(runId: string) {
  const queryClient = useQueryClient();
  return (error: unknown, { spanId }: { spanId: string }) => {
    if (passageChangedTo(error) !== null) {
      void queryClient.invalidateQueries({ queryKey: runKeys.span(runId, spanId) });
      void queryClient.invalidateQueries({ queryKey: runKeys.pages(runId) });
    }
  };
}

const spanPath = (runId: string, spanId: string) => ({
  params: { path: { run_id: runId, span_id: spanId } },
});

/** `base` is the passage text the change started from: the server refuses the change if someone
 * else has changed the passage since. */
export function useEditSpan(runId: string) {
  const onSuccess = useSpanChanged(runId);
  const onError = useSpanStale(runId);
  return useMutation({
    mutationFn: ({
      spanId,
      text,
      reason,
      base,
    }: {
      spanId: string;
      text: string;
      reason: string | null;
      base: string;
    }) =>
      unwrap(
        client.POST("/api/runs/{run_id}/spans/{span_id}/edit", {
          ...spanPath(runId, spanId),
          body: { text, reason, base },
        }),
      ),
    onSuccess,
    onError,
  });
}

export function useChooseCandidate(runId: string) {
  const onSuccess = useSpanChanged(runId);
  const onError = useSpanStale(runId);
  return useMutation({
    mutationFn: ({
      spanId,
      translator,
      base,
    }: {
      spanId: string;
      translator: string;
      base: string;
    }) =>
      unwrap(
        client.POST("/api/runs/{run_id}/spans/{span_id}/use-candidate", {
          ...spanPath(runId, spanId),
          body: { translator, base },
        }),
      ),
    onSuccess,
    onError,
  });
}

export function useAcceptSpan(runId: string) {
  const onSuccess = useSpanChanged(runId);
  return useMutation({
    mutationFn: (spanId: string) =>
      unwrap(client.POST("/api/runs/{run_id}/spans/{span_id}/accept", spanPath(runId, spanId))),
    onSuccess,
  });
}

export type FlagChange = {
  flagId: number;
  status: "accepted" | "dismissed" | "open";
  reason: string | null;
};

export function useFlagStatus(runId: string) {
  const onSuccess = useSpanChanged(runId);
  return useMutation({
    mutationFn: ({ flagId, status, reason }: FlagChange) =>
      unwrap(
        client.POST("/api/runs/{run_id}/flags/{flag_id}", {
          params: { path: { run_id: runId, flag_id: flagId } },
          body: { status, reason },
        }),
      ),
    onSuccess,
  });
}

export type GuideAddition = { glossary: GlossaryEntry } | { entity: Entity };

/** The entry joins the set guide of the run's language (CGL-24). */
export function useAddToGuide(runId: string, projectId: string, targetLang: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ spanId, addition }: { spanId: string; addition: GuideAddition }) =>
      unwrap(
        client.POST("/api/runs/{run_id}/spans/{span_id}/guide", {
          ...spanPath(runId, spanId),
          body: addition,
        }),
      ),
    onSuccess: (view) => guideSaved(queryClient, projectId, targetLang, view),
  });
}

export const DOC_PAGE = 100;
export const FLAG_PAGE = 500; // the API's largest page

/** Spec §5.3: the document in pages that load as the reader nears the end. */
export function useDocumentPages(runId: string, flaggedOnly: boolean, scores: ScoreFilter) {
  return useInfiniteQuery({
    queryKey: [...runKeys.pages(runId), "document", { flaggedOnly, ...scores }],
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/api/runs/{run_id}/spans", {
          params: {
            path: { run_id: runId },
            query: {
              ...scores,
              ...(flaggedOnly ? { flagged: true } : {}),
              offset: pageParam,
              limit: DOC_PAGE,
            },
          },
        }),
      ),
    initialPageParam: 0,
    getNextPageParam: (last: SpanPage) => {
      const next = last.offset + last.spans.length;
      return last.spans.length > 0 && next < last.total ? next : undefined;
    },
  });
}

/** A walk (many n / p presses) reuses the flagged pages it has read; a review action
 * invalidates them with the other pages (useSpanChanged). */
const FLAGGED_FRESH_MS = 30_000;

export function fetchFlaggedPage(
  queryClient: QueryClient,
  runId: string,
  offset: number,
  scores: ScoreFilter = {},
): Promise<SpanPage> {
  return queryClient.fetchQuery({
    queryKey: [...runKeys.pages(runId), "flagged", { offset, ...scores }],
    staleTime: FLAGGED_FRESH_MS,
    queryFn: () =>
      unwrap(
        client.GET("/api/runs/{run_id}/spans", {
          params: {
            path: { run_id: runId },
            query: { ...scores, flagged: true, offset, limit: FLAG_PAGE },
          },
        }),
      ),
  });
}

/** The next (forward) or previous passage with an open flag after passage number `from`; null
 * when there is none. Reads the flagged list in pages of 500. */
export async function findFlagged(
  queryClient: QueryClient,
  runId: string,
  from: number,
  forward: boolean,
  scores: ScoreFilter = {},
): Promise<ReviewSpan | null> {
  let previous: ReviewSpan | null = null;
  for (let offset = 0; ; offset += FLAG_PAGE) {
    const page = await fetchFlaggedPage(queryClient, runId, offset, scores);
    for (const span of page.spans) {
      if (forward && span.number > from) return span;
      if (!forward) {
        if (span.number >= from) return previous;
        previous = span;
      }
    }
    if (page.spans.length === 0 || offset + page.spans.length >= page.total) {
      return forward ? null : previous;
    }
  }
}
