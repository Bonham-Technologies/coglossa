import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import {
  documentRun,
  documentSummary,
  progress,
  project,
  runDetail,
  runSummary,
} from "../test/fixtures";
import { queryWrapper } from "../test/render";
import { server } from "../test/server";
import { ApiError } from "./client";
import { projectKeys } from "./projects";
import { runKeys, useRunAction } from "./runs";
import type { DocumentRun, RunDetail } from "./types";
import {
  createSet,
  recheckSet,
  useAddLanguage,
  useApproveAsIs,
  useRecheckSet,
  useRenameProject,
  useTranslateAgain,
} from "./sets";

const body = { name: "welcome", target_lang: "es", source_lang: null, pipeline_profile: null };

it("createSet retries with a number on a 409", async () => {
  const names: string[] = [];
  server.use(
    http.post("*/api/projects", async ({ request }) => {
      const sent = (await request.json()) as { name: string };
      names.push(sent.name);
      if (names.length < 3) {
        return HttpResponse.json({ detail: "a project named it already exists" }, { status: 409 });
      }
      return HttpResponse.json({ ...project, name: sent.name }, { status: 201 });
    }),
  );
  const created = await createSet(body);
  expect(names).toEqual(["welcome", "welcome (2)", "welcome (3)"]);
  expect(created.name).toBe("welcome (3)");
});

it("createSet stops at any other error, and after 50 tries", async () => {
  server.use(
    http.post("*/api/projects", () =>
      HttpResponse.json({ detail: "target_lang: bad" }, { status: 422 }),
    ),
  );
  await expect(createSet(body)).rejects.toThrow("target_lang: bad");
  let calls = 0;
  server.use(
    http.post("*/api/projects", () => {
      calls += 1;
      return HttpResponse.json({ detail: "taken" }, { status: 409 });
    }),
  );
  await expect(createSet(body)).rejects.toBeInstanceOf(ApiError);
  expect(calls).toBe(50);
});

it("recheckSet skips runs that are not finished and 409s", async () => {
  const swept: string[] = [];
  const doc = (id: string, runs: DocumentRun[]) => ({ ...documentSummary, id, runs });
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([
        doc("d-a", [{ ...documentRun, id: "r-a", state: "complete" }]),
        doc("d-b", [{ ...documentRun, id: "r-b", state: "translating" }]),
        doc("d-c", [
          { ...documentRun, id: "r-c", state: "complete_with_errors" },
          { ...documentRun, id: "r-old", state: "complete" },
        ]),
        doc("d-d", []),
      ]),
    ),
    http.post("*/api/runs/:runId/sweep", ({ params }) => {
      swept.push(params["runId"] as string);
      if (params["runId"] === "r-c") {
        return HttpResponse.json({ detail: "the sweep is off" }, { status: 409 });
      }
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  expect(await recheckSet("p-1", "en")).toEqual({
    started: ["r-a"],
    skipped: ["r-b", "r-c"],
    failed: [],
  });
  expect(swept).toEqual(["r-a", "r-c"]); // never an older run of a file
});

it("recheckSet carries on past a real failure and reports it", async () => {
  const swept: string[] = [];
  const doc = (id: string, runId: string) => ({
    ...documentSummary,
    id,
    runs: [{ ...documentRun, id: runId, state: "complete" as const }],
  });
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([doc("d-a", "r-a"), doc("d-b", "r-b")]),
    ),
    http.post("*/api/runs/:runId/sweep", ({ params }) => {
      swept.push(params["runId"] as string);
      if (params["runId"] === "r-a") return HttpResponse.json({}, { status: 500 });
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  expect(await recheckSet("p-1", "en")).toEqual({ started: ["r-b"], skipped: [], failed: ["r-a"] });
  expect(swept).toEqual(["r-a", "r-b"]);
});

it("recheckSet fails when the files cannot be listed", async () => {
  server.use(
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json({}, { status: 502 })),
  );
  await expect(recheckSet("p-1", "en")).rejects.toThrow("The server is starting up or busy");
});

it("a partly failed recheck still marks the runs that started", async () => {
  const doc = (id: string, runId: string) => ({
    ...documentSummary,
    id,
    runs: [{ ...documentRun, id: runId, state: "complete" as const }],
  });
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([doc("d-a", "r-a"), doc("d-b", "r-b")]),
    ),
    http.post("*/api/runs/r-a/sweep", () => HttpResponse.json({}, { status: 500 })),
    http.post("*/api/runs/r-b/sweep", () =>
      HttpResponse.json({ message: "ok", progress }, { status: 202 }),
    ),
  );
  const { queryClient, wrapper } = queryWrapper();
  const finished = (id: string): RunDetail => ({
    ...runDetail,
    run: { ...runDetail.run, id, state: "complete", finished_at: "2026-10-01T09:05:00Z" },
    report_available: true,
  });
  queryClient.setQueryData(runKeys.one("r-a"), finished("r-a"));
  queryClient.setQueryData(runKeys.one("r-b"), finished("r-b"));
  const { result } = renderHook(() => useRecheckSet("p-1", "en"), { wrapper });
  const outcome = await act(() => result.current.mutateAsync());
  expect(outcome.failed).toEqual(["r-a"]);
  expect(queryClient.getQueryData<RunDetail>(runKeys.one("r-b"))?.run.state).toBe("sweeping");
  expect(queryClient.getQueryData<RunDetail>(runKeys.one("r-a"))?.run.state).toBe("complete");
});

it("renames a set and updates the cached copy", async () => {
  let sent: unknown;
  server.use(
    http.patch("*/api/projects/p-1", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...project, name: "Letters" });
    }),
  );
  const { queryClient, wrapper } = queryWrapper();
  const { result } = renderHook(() => useRenameProject("p-1"), { wrapper });
  await act(() => result.current.mutateAsync("Letters"));
  expect(sent).toEqual({ name: "Letters" });
  expect(queryClient.getQueryData<typeof project>(projectKeys.one("p-1"))?.name).toBe("Letters");
});

it("translates a file again without pausing", async () => {
  let sent: unknown;
  server.use(
    http.post("*/api/documents/d-1/runs", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...runSummary, id: "r-2", state: "created" }, { status: 202 });
    }),
  );
  const { wrapper } = queryWrapper();
  const { result } = renderHook(() => useTranslateAgain("p-1"), { wrapper });
  const run = await act(() => result.current.mutateAsync({ documentId: "d-1", targetLang: "es" }));
  expect(sent).toEqual({ checkpoint: false, use_project_guide: false, target_lang: "es" });
  expect(run.id).toBe("r-2");
});

it("approves a waiting guide as it is", async () => {
  let sent: unknown;
  server.use(
    http.post("*/api/runs/r-1/guide/approve", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ message: "approved", progress }, { status: 202 });
    }),
  );
  const { wrapper } = queryWrapper();
  const { result } = renderHook(() => useApproveAsIs("r-1", "p-1"), { wrapper });
  await act(() => result.current.mutateAsync());
  await waitFor(() => expect(sent).toEqual({}));
});

it("a recheck marks each started run as sweeping at once, without waiting for a refetch", async () => {
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([
        { ...documentSummary, runs: [{ ...documentRun, id: "r-1", state: "complete" }] },
      ]),
    ),
    http.post("*/api/runs/r-1/sweep", () =>
      HttpResponse.json({ message: "ok", progress }, { status: 202 }),
    ),
  );
  const { queryClient, wrapper } = queryWrapper();
  const finished: RunDetail = {
    ...runDetail,
    run: { ...runDetail.run, id: "r-1", state: "complete", finished_at: "2026-10-01T09:05:00Z" },
    report_available: true,
  };
  queryClient.setQueryData(runKeys.one("r-1"), finished);
  const { result } = renderHook(() => useRecheckSet("p-1", "en"), { wrapper });
  await act(() => result.current.mutateAsync());
  const cached = queryClient.getQueryData<RunDetail>(runKeys.one("r-1"));
  expect(cached?.run.state).toBe("sweeping");
  expect(cached?.run.finished_at).toBe("2026-10-01T09:05:00Z");
  expect(cached?.report_available).toBe(false);
});

it("a single-file recheck marks the run as sweeping at once, but a retry does not", async () => {
  server.use(
    http.post("*/api/runs/r-1/sweep", () =>
      HttpResponse.json({ message: "ok", progress }, { status: 202 }),
    ),
    http.post("*/api/runs/r-1/retry", () =>
      HttpResponse.json({ message: "ok", progress }, { status: 202 }),
    ),
  );
  const { queryClient, wrapper } = queryWrapper();
  const finished: RunDetail = {
    ...runDetail,
    run: { ...runDetail.run, id: "r-1", state: "complete", finished_at: "2026-10-01T09:05:00Z" },
    report_available: true,
  };
  queryClient.setQueryData(runKeys.one("r-1"), finished);
  const { result } = renderHook(() => useRunAction("r-1", "p-1"), { wrapper });
  await act(() => result.current.mutateAsync("retry"));
  expect(queryClient.getQueryData<RunDetail>(runKeys.one("r-1"))?.report_available).toBe(true);
  await act(() => result.current.mutateAsync("sweep"));
  const cached = queryClient.getQueryData<RunDetail>(runKeys.one("r-1"));
  expect(cached?.run.state).toBe("sweeping");
  expect(cached?.run.finished_at).toBe("2026-10-01T09:05:00Z");
  expect(cached?.report_available).toBe(false);
});

it("rechecks only the files of one language", async () => {
  const swept: string[] = [];
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([
        {
          ...documentSummary,
          runs: [{ ...documentRun, id: "r-es", target_lang: "es" }, documentRun],
        },
      ]),
    ),
    http.post("*/api/runs/:id/sweep", ({ params }) => {
      swept.push(params["id"] as string);
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  expect((await recheckSet("p-1", "en")).started).toEqual(["r-1"]);
  expect(swept).toEqual(["r-1"]);
});

it("starts every file in another language", async () => {
  let sent: unknown;
  server.use(
    http.post("*/api/projects/p-1/languages", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json([{ ...runSummary, id: "r-es", target_lang: "es" }], {
        status: 202,
      });
    }),
  );
  const { result } = renderHook(() => useAddLanguage("p-1"), { wrapper: queryWrapper().wrapper });
  const started = await act(() => result.current.mutateAsync("es"));
  expect(sent).toEqual({ target_lang: "es" });
  expect(started.map((r) => r.id)).toEqual(["r-es"]);
});
