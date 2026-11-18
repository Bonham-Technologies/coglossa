import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client, formData, unwrap } from "./client";
import type { PipelineProfile, RunSummary } from "./types";

export const projectKeys = {
  all: ["projects"] as const,
  one: (id: string) => ["projects", id] as const,
  documents: (id: string) => ["projects", id, "documents"] as const,
};

/** Whether a query key is a set's list of files (projectKeys.documents), whichever set. */
export function isDocumentsKey(key: readonly unknown[]): boolean {
  return key.length === 3 && key[0] === "projects" && key[2] === "documents";
}

export function useProject(projectId: string) {
  return useQuery({
    queryKey: projectKeys.one(projectId),
    queryFn: () =>
      unwrap(
        client.GET("/api/projects/{project_id}", {
          params: { path: { project_id: projectId } },
        }),
      ),
  });
}

export type Upload = {
  file: File;
  target_lang: string;
  source_lang: string | null;
  pipeline_profile: PipelineProfile | null;
  checkpoint: boolean | null;
  use_project_guide: boolean;
  all_languages: boolean; // CGL-24: also start the file in every other language of the set
};

/** POST the file as multipart form data; fields left null are not sent (the API's defaults). */
export function uploadDocument(projectId: string, upload: Upload): Promise<RunSummary> {
  return unwrap(
    client.POST("/api/projects/{project_id}/runs", {
      params: { path: { project_id: projectId } },
      // The generated type calls the file a string (binary form field); FormData carries it.
      body: { ...upload, file: upload.file as unknown as string },
      bodySerializer: formData,
    }),
  );
}

export function useDeleteProject(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (confirm: string) =>
      unwrap(
        client.DELETE("/api/projects/{project_id}", {
          params: { path: { project_id: projectId }, query: { confirm } },
        }),
      ),
    onSuccess: () => {
      // The deleted set's queries go (the prefix covers its files and guide), and only the list is
      // refreshed: nothing may ask the server for the set again. Not awaited, so the caller can
      // leave the set's page at once.
      queryClient.removeQueries({ queryKey: projectKeys.one(projectId) });
      void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
    },
  });
}

/** CGL-26: an admin moves a set into a team (null: personal to whoever made it). */
export function useMoveProject(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (team: string | null) =>
      unwrap(
        client.PATCH("/api/projects/{project_id}", {
          params: { path: { project_id: projectId } },
          body: { team },
        }),
      ),
    onSuccess: (info) => {
      queryClient.setQueryData(projectKeys.one(projectId), info);
      return queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
    },
  });
}
