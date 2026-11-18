import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { client, formData, unwrap } from "./client";
import { projectKeys } from "./projects";
import { runKeys } from "./runs";
import type { Guide, GuideView } from "./types";

/** CGL-24: each language of a set has its own guide. */
export const guideKeys = {
  all: (projectId: string) => ["projects", projectId, "guide"] as const,
  project: (projectId: string, lang: string) => ["projects", projectId, "guide", lang] as const,
  versions: (projectId: string, lang: string) =>
    ["projects", projectId, "guide", lang, "versions"] as const,
};

export function useRunGuide(runId: string, enabled = true) {
  return useQuery({
    queryKey: runKeys.guide(runId),
    queryFn: () =>
      unwrap(client.GET("/api/runs/{run_id}/guide", { params: { path: { run_id: runId } } })),
    enabled,
  });
}

/** Which language's guide. A set with no language yet sends none: the server then answers
 * that there is no guide, where an empty code would be refused as invalid (CGL-30). */
function languageQuery(lang: string): { target_lang?: string } {
  return lang ? { target_lang: lang } : {};
}

/** One language's project guide (its newest approved version or in-use engine draft), or one
 * version of it. */
export function fetchProjectGuide(
  projectId: string,
  lang: string,
  version?: number,
): Promise<GuideView> {
  return unwrap(
    client.GET("/api/projects/{project_id}/guide", {
      params: {
        path: { project_id: projectId },
        query: version === undefined ? languageQuery(lang) : { ...languageQuery(lang), version },
      },
    }),
  );
}

/** An approval adds a project guide version: refresh the run and what shows the project guide. */
export function useApproveGuide(runId: string, projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { guide: Guide; base_version: number }) =>
      unwrap(
        client.POST("/api/runs/{run_id}/guide/approve", {
          params: { path: { run_id: runId } },
          body,
        }),
      ),
    onSuccess: () => {
      guideChanged(queryClient, projectId);
      return queryClient.invalidateQueries({ queryKey: runKeys.one(runId) });
    },
  });
}

export function useProjectGuide(projectId: string, lang: string) {
  return useQuery({
    queryKey: guideKeys.project(projectId, lang),
    queryFn: () => fetchProjectGuide(projectId, lang),
  });
}

/** A new guide version exists: refresh the project guides (every language: cheap, and an
 * approval knows its run, not its language) and what lists or counts versions. */
export function guideChanged(queryClient: QueryClient, projectId: string) {
  void queryClient.invalidateQueries({ queryKey: guideKeys.all(projectId) });
  guideCounts(queryClient, projectId);
}

/** A new guide version exists and is known: show it, and refresh what lists or counts versions. */
export function guideSaved(
  queryClient: QueryClient,
  projectId: string,
  lang: string,
  view: GuideView,
) {
  queryClient.setQueryData(guideKeys.project(projectId, lang), view);
  void queryClient.invalidateQueries({ queryKey: guideKeys.versions(projectId, lang) });
  guideCounts(queryClient, projectId);
}

function guideCounts(queryClient: QueryClient, projectId: string) {
  void queryClient.invalidateQueries({ queryKey: projectKeys.one(projectId), exact: true });
  void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
}

/** CGL-88: what importing a glossary file would do; writes nothing. */
export function usePreviewGlossaryImport(projectId: string, lang: string) {
  return useMutation({
    mutationFn: (file: File) =>
      unwrap(
        client.POST("/api/projects/{project_id}/guide/import/preview", {
          params: { path: { project_id: projectId }, query: languageQuery(lang) },
          // The generated type calls the file a string (binary form field); FormData carries it.
          body: { file: file as unknown as string },
          bodySerializer: formData,
        }),
      ),
  });
}

export type GlossaryImport = {
  file: File;
  base_version: number | null; // the version the preview compared with
  lock_all: boolean;
  overwrite_conflicts: boolean;
};

/** CGL-88: import a glossary file as a new guide version. */
export function useImportGlossary(projectId: string, lang: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, ...options }: GlossaryImport) =>
      unwrap(
        client.POST("/api/projects/{project_id}/guide/import", {
          params: { path: { project_id: projectId }, query: languageQuery(lang) },
          body: { ...options, file: file as unknown as string },
          bodySerializer: formData,
        }),
      ),
    onSuccess: (result) => {
      if (result.created_version) guideSaved(queryClient, projectId, lang, result.view);
    },
  });
}

export function useSaveProjectGuide(projectId: string, lang: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { guide: Guide; base_version: number | null }) =>
      unwrap(
        client.PUT("/api/projects/{project_id}/guide", {
          params: { path: { project_id: projectId }, query: languageQuery(lang) },
          body,
        }),
      ),
    onSuccess: (view) => guideSaved(queryClient, projectId, lang, view),
  });
}
