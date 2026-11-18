import type { QueryClient } from "@tanstack/react-query";
import { useMemo, useSyncExternalStore } from "react";
import { ApiError } from "../api/client";
import { projectKeys, uploadDocument } from "../api/projects";
import type { PipelineProfile } from "../api/types";
import { messageOf } from "../components/ErrorMessage";

export type UploadOptions = {
  target_lang: string;
  source_lang: string | null;
  pipeline_profile: PipelineProfile | null;
};

export type UploadItem = {
  key: string;
  projectId: string;
  name: string;
  size: number;
  status: "waiting" | "uploading" | "done" | "failed";
  error: string | null;
  runId: string | null;
};

type Entry = UploadItem & { file: File; options: UploadOptions; queryClient: QueryClient };

// Spec §4.1: uploads outlive the New translation page (it navigates away at once), so they live
// here, in memory, one at a time (plan decision 7).
let entries: Entry[] = [];
let snapshot: UploadItem[] = [];
let running = false;
let counter = 0;
const listeners = new Set<() => void>();

// CGL-64: while a file is waiting or uploading, leaving the page makes the browser ask first.
function warnBeforeLeaving(event: BeforeUnloadEvent): void {
  event.preventDefault();
  event.returnValue = "";
}
let guarded = false;

function syncLeaveGuard(): void {
  const pending = entries.some((e) => e.status === "waiting" || e.status === "uploading");
  if (pending && !guarded) window.addEventListener("beforeunload", warnBeforeLeaving);
  else if (!pending && guarded) window.removeEventListener("beforeunload", warnBeforeLeaving);
  guarded = pending;
}

function publish(): void {
  syncLeaveGuard();
  snapshot = entries.map(({ key, projectId, name, size, status, error, runId }) => ({
    key,
    projectId,
    name,
    size,
    status,
    error,
    runId,
  }));
  listeners.forEach((listener) => listener());
}

function update(entry: Entry, patch: Partial<UploadItem>): void {
  Object.assign(entry, patch);
  publish();
}

export function uploadErrorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 413) return "Too large for this server.";
  return messageOf(error);
}

async function drain(): Promise<void> {
  if (running) return;
  running = true;
  try {
    for (
      let next = entries.find((e) => e.status === "waiting");
      next !== undefined;
      next = entries.find((e) => e.status === "waiting")
    ) {
      const entry = next;
      update(entry, { status: "uploading" });
      try {
        const run = await uploadDocument(entry.projectId, {
          file: entry.file,
          ...entry.options,
          // CGL-24: every language of the set, as it is when the file arrives; for a set in one
          // language this is just that one
          all_languages: true,
          checkpoint: false,
          use_project_guide: false,
        });
        update(entry, { status: "done", runId: run.id });
        void entry.queryClient.invalidateQueries({
          queryKey: projectKeys.documents(entry.projectId),
        });
        void entry.queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
        // the set's own counts and languages: a set's first upload can give it its language
        void entry.queryClient.invalidateQueries({
          queryKey: projectKeys.one(entry.projectId),
          exact: true,
        });
      } catch (error) {
        update(entry, { status: "failed", error: uploadErrorText(error) });
      }
    }
  } finally {
    running = false;
  }
}

export function enqueueUploads(
  projectId: string,
  files: File[],
  options: UploadOptions,
  queryClient: QueryClient,
): void {
  for (const file of files) {
    counter += 1;
    entries.push({
      key: `upload-${counter}`,
      projectId,
      name: file.name,
      size: file.size,
      status: "waiting",
      error: null,
      runId: null,
      file,
      options,
      queryClient,
    });
  }
  publish();
  void drain();
}

export function retryUpload(key: string): void {
  const entry = entries.find((e) => e.key === key);
  if (entry?.status !== "failed") return;
  update(entry, { status: "waiting", error: null });
  void drain();
}

export function dismissUpload(key: string): void {
  entries = entries.filter((e) => e.key !== key);
  publish();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useUploads(projectId: string): UploadItem[] {
  const all = useSyncExternalStore(subscribe, () => snapshot);
  return useMemo(() => all.filter((item) => item.projectId === projectId), [all, projectId]);
}

export function resetUploads(): void {
  entries = [];
  running = false;
  publish();
}
