import {
  skipToken,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { isTerminal } from "../lib/format";
import { keptRerun } from "../lib/status";
import { ApiError, client, unwrap } from "./client";
import { liveUpdatesAvailable } from "./events";
import { projectKeys } from "./projects";
import type { RunDetail } from "./types";

export const runKeys = {
  one: (runId: string) => ["runs", runId] as const,
  guide: (runId: string) => ["runs", runId, "guide"] as const,
  spans: (runId: string) => ["runs", runId, "spans"] as const,
  pages: (runId: string) => ["runs", runId, "spans", "page"] as const,
  span: (runId: string, spanId: string) => ["runs", runId, "spans", spanId] as const,
};

export const POLL_MS = 3000;

/** Without server-sent events (spec §7.1's fallback), poll a run until it ends. */
export function pollInterval(
  detail: RunDetail | undefined,
  live: boolean,
  error?: unknown,
): number | false {
  if (error instanceof ApiError && error.status === 404) return false; // the run is gone
  if (live || detail === undefined || isTerminal(detail.run.state)) return false;
  return POLL_MS;
}

export function useRun(runId: string, streamFailed = false) {
  return useQuery({
    queryKey: runKeys.one(runId),
    queryFn: () =>
      unwrap(client.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } })),
    refetchInterval: (query) =>
      pollInterval(query.state.data, liveUpdatesAvailable() && !streamFailed, query.state.error),
  });
}

/** A run the screen has already loaded, read from the cache and kept current with it, but never
 * fetched: for a panel that only adds to what the run's own view shows. */
export function useLoadedRun(runId: string | undefined) {
  return useQuery<RunDetail>({ queryKey: runKeys.one(runId ?? ""), queryFn: skipToken });
}

/** A recheck or a retry of a finished file rewrites passages, so they are fetched again when the
 * run leaves that state. The live stream refreshes them on every state change; polling only
 * fetches the run, hence this. */
export function useRefreshAfterRecheck(runId: string, detail: RunDetail | undefined): void {
  const queryClient = useQueryClient();
  const previous = useRef(false);
  const rerunning = detail !== undefined && keptRerun(detail) !== null;
  useEffect(() => {
    if (previous.current && !rerunning) {
      void queryClient.invalidateQueries({ queryKey: runKeys.spans(runId) });
    }
    previous.current = rerunning;
  }, [runId, rerunning, queryClient]);
}

/** A started recheck: every view of the run changes at once (Download hides); the refetch then
 * confirms it. */
export function markSweeping(queryClient: QueryClient, runId: string): void {
  queryClient.setQueryData<RunDetail>(runKeys.one(runId), (detail) =>
    detail
      ? { ...detail, run: { ...detail.run, state: "sweeping" }, report_available: false }
      : detail,
  );
}

export type RunActionKind = "cancel" | "retry" | "sweep";

/** Cancel, retry or re-sweep a run; the run and the project screens that list its state refresh. */
export function useRunAction(runId: string, projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (kind: RunActionKind) => {
      const params = { params: { path: { run_id: runId } } };
      if (kind === "cancel") return unwrap(client.POST("/api/runs/{run_id}/cancel", params));
      if (kind === "retry") return unwrap(client.POST("/api/runs/{run_id}/retry", params));
      return unwrap(client.POST("/api/runs/{run_id}/sweep", params));
    },
    onSuccess: (_result, kind) => {
      if (kind === "sweep") markSweeping(queryClient, runId);
      void queryClient.invalidateQueries({ queryKey: projectKeys.documents(projectId) });
      void queryClient.invalidateQueries({ queryKey: projectKeys.one(projectId), exact: true });
      void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
      return queryClient.invalidateQueries({ queryKey: runKeys.one(runId) });
    },
  });
}
