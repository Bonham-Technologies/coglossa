import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { plural } from "../lib/flags";
import { isTerminal } from "../lib/format";
import { numbered } from "../lib/names";
import { ApiError, client, unwrap } from "./client";
import { guideChanged } from "./guide";
import { projectKeys } from "./projects";
import { markSweeping, POLL_MS, runKeys } from "./runs";
import { newestIn, newestRuns } from "../set/languages";
import type { CreateProject, ProjectInfo, RunState } from "./types";

export const MAX_NAME_TRIES = 50;
export const SET_LIST_POLL_MS = 5000;

/** Create a set (a project) under the first free name: plan decision 1. */
export async function createSet(body: CreateProject): Promise<ProjectInfo> {
  for (let n = 1; n <= MAX_NAME_TRIES; n++) {
    try {
      return await unwrap(
        client.POST("/api/projects", { body: { ...body, name: numbered(body.name, n) } }),
      );
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 409)) throw error;
    }
  }
  throw new ApiError(409, "Too many translations share this name. Rename one and try again.");
}

/** The sidebar's list: refreshed while any set is translating. */
export function useSetList() {
  return useQuery({
    queryKey: projectKeys.all,
    queryFn: () => unwrap(client.GET("/api/projects")),
    refetchInterval: (query) =>
      query.state.data?.some((p) => p.files_translating > 0) ? SET_LIST_POLL_MS : false,
  });
}

/** A set's files: refreshed while the newest run of any file, in any language, is under way. */
export function useSetDocuments(projectId: string) {
  return useQuery({
    queryKey: projectKeys.documents(projectId),
    queryFn: () =>
      unwrap(
        client.GET("/api/projects/{project_id}/documents", {
          params: { path: { project_id: projectId } },
        }),
      ),
    refetchInterval: (query) =>
      query.state.data?.some((d) => newestRuns(d).some((r) => !isTerminal(r.state)))
        ? POLL_MS
        : false,
  });
}

/** Whether a new run of a file must wait: while any file of the set has a run under way (a new
 * run then would compete with it), and while the files are not known yet. `reason` is the plain
 * sentence that says why. */
export function useSetBusy(projectId: string): { busy: boolean; reason: string } {
  const documents = useSetDocuments(projectId);
  const unlisted = documents.data === undefined;
  return {
    busy: unlisted || documents.data.some((d) => newestRuns(d).some((r) => !isTerminal(r.state))),
    reason:
      unlisted && documents.isError
        ? "Available once the files are listed"
        : "Available when this translation finishes",
  };
}

export function useRenameProject(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      unwrap(
        client.PATCH("/api/projects/{project_id}", {
          params: { path: { project_id: projectId } },
          body: { name },
        }),
      ),
    onSuccess: (info) => {
      queryClient.setQueryData(projectKeys.one(projectId), info);
      return queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
    },
  });
}

/** Spec §4.3: a fresh run of a file in one language, never pausing for the guide. */
export function useTranslateAgain(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ documentId, targetLang }: { documentId: string; targetLang: string }) =>
      unwrap(
        client.POST("/api/documents/{document_id}/runs", {
          params: { path: { document_id: documentId } },
          // false still builds on the set's latest guide (prior_guide), so the user's guide edits carry over.
          body: { checkpoint: false, use_project_guide: false, target_lang: targetLang },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
      return queryClient.invalidateQueries({ queryKey: projectKeys.documents(projectId) });
    },
  });
}

/** Spec §4.4: Continue as is, approving the guide the engine drafted. */
export function useApproveAsIs(runId: string, projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        client.POST("/api/runs/{run_id}/guide/approve", {
          params: { path: { run_id: runId } },
          body: {},
        }),
      ),
    onSuccess: () => {
      guideChanged(queryClient, projectId);
      return queryClient.invalidateQueries({ queryKey: runKeys.one(runId) });
    },
  });
}

const RECHECKABLE: readonly RunState[] = ["complete", "complete_with_errors"];

export type RecheckResult = { started: string[]; skipped: string[]; failed: string[] };

export function recheckFailureText(failed: number): string {
  return `The recheck couldn't start for ${plural(failed, "file")}.`;
}

/** Spec §5.4 step 4: re-run the sweep on the newest finished run of every file in the set, in
 * one language (CGL-24: each language has its own guide). A
 * file still running, or one whose sweep cannot start (409), is skipped: it picks the guide up
 * when its own translation finishes. A failure on one file does not stop the rest: it is listed in
 * `failed` (only a failure to list the files throws). `only` limits it to those runs: a retry of
 * the failed ones. */
export async function recheckSet(
  projectId: string,
  targetLang: string,
  only?: string[],
): Promise<RecheckResult> {
  const documents = await unwrap(
    client.GET("/api/projects/{project_id}/documents", {
      params: { path: { project_id: projectId } },
    }),
  );
  const result: RecheckResult = { started: [], skipped: [], failed: [] };
  for (const doc of documents) {
    const run = newestIn(doc, targetLang);
    if (run === undefined || (only !== undefined && !only.includes(run.id))) continue;
    if (!RECHECKABLE.includes(run.state)) {
      result.skipped.push(run.id);
      continue;
    }
    try {
      await unwrap(
        client.POST("/api/runs/{run_id}/sweep", { params: { path: { run_id: run.id } } }),
      );
      result.started.push(run.id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) result.skipped.push(run.id);
      else result.failed.push(run.id);
    }
  }
  return result;
}

export function useRecheckSet(projectId: string, targetLang: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (only?: string[]) => recheckSet(projectId, targetLang, only),
    onSuccess: (result) => {
      for (const runId of result.started) {
        markSweeping(queryClient, runId);
        void queryClient.invalidateQueries({ queryKey: runKeys.one(runId), exact: true });
      }
      void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
      return queryClient.invalidateQueries({ queryKey: projectKeys.documents(projectId) });
    },
  });
}

/** CGL-24: every file of the set in another language. */
export function useAddLanguage(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (targetLang: string) =>
      unwrap(
        client.POST("/api/projects/{project_id}/languages", {
          params: { path: { project_id: projectId } },
          body: { target_lang: targetLang },
        }),
      ),
    // settled, not success: after a refusal (the language exists) the set shows it too
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
      void queryClient.invalidateQueries({ queryKey: projectKeys.one(projectId), exact: true });
      return queryClient.invalidateQueries({ queryKey: projectKeys.documents(projectId) });
    },
  });
}
