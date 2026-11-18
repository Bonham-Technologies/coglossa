import { act, renderHook, waitFor } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { ApiError } from "../api/client";
import { runSummary } from "../test/fixtures";
import { nodeFormData } from "../test/node-web";
import { queryWrapper } from "../test/render";
import { server } from "../test/server";
import {
  dismissUpload,
  enqueueUploads,
  resetUploads,
  retryUpload,
  uploadErrorText,
  useUploads,
} from "./queue";

const options = { target_lang: "es", source_lang: null, pipeline_profile: null };
const file = (name: string) => new File(["Il pleut."], name, { type: "text/plain" });

beforeEach(async () => {
  resetUploads();
  vi.stubGlobal("FormData", await nodeFormData());
});

it("uploads one file at a time with checkpoint=false and reports each", async () => {
  const seen: string[] = [];
  server.use(
    http.post("*/api/projects/p-1/runs", async ({ request }) => {
      const form = await request.formData();
      seen.push(`${(form.get("file") as File).name}:${form.get("checkpoint")}`);
      if (seen.length === 2) {
        return HttpResponse.json({ detail: "b.txt is larger than the limit" }, { status: 413 });
      }
      return HttpResponse.json({ ...runSummary, id: `r-${seen.length}` }, { status: 202 });
    }),
  );
  const { queryClient, wrapper } = queryWrapper();
  const { result } = renderHook(() => useUploads("p-1"), { wrapper });
  act(() =>
    enqueueUploads("p-1", [file("a.txt"), file("b.txt"), file("c.txt")], options, queryClient),
  );
  await waitFor(() =>
    expect(result.current.map((u) => u.status)).toEqual(["done", "failed", "done"]),
  );
  expect(seen).toEqual(["a.txt:false", "b.txt:false", "c.txt:false"]);
  expect(result.current[0]!.runId).toBe("r-1");
  expect(result.current[1]).toMatchObject({ name: "b.txt", error: "Too large for this server." });

  const served = seen.length;
  act(() => retryUpload(result.current[0]!.key)); // a finished upload is not sent again
  act(() => retryUpload("no-such-upload"));
  expect(result.current[0]!.status).toBe("done");
  act(() => retryUpload(result.current[1]!.key));
  await waitFor(() => expect(result.current[1]!.status).toBe("done"));
  expect(seen).toHaveLength(served + 1); // only the failed one
  act(() => dismissUpload(result.current[1]!.key));
  expect(result.current.map((u) => u.name)).toEqual(["a.txt", "c.txt"]);
});

it("never has two uploads in flight at once, within a set or across sets", async () => {
  let inFlight = 0;
  let peak = 0;
  let served = 0;
  server.use(
    http.post("*/api/projects/:id/runs", async () => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await delay(20);
      inFlight -= 1;
      served += 1;
      return HttpResponse.json(runSummary, { status: 202 });
    }),
  );
  const { queryClient, wrapper } = queryWrapper();
  const { result } = renderHook(() => useUploads("p-1"), { wrapper });
  act(() =>
    enqueueUploads("p-1", [file("a.txt"), file("b.txt"), file("c.txt")], options, queryClient),
  );
  act(() => enqueueUploads("p-2", [file("z.txt")], options, queryClient));
  await waitFor(() =>
    expect(result.current.map((u) => u.status)).toEqual(["done", "done", "done"]),
  );
  await waitFor(() => expect(served).toBe(4));
  expect(peak).toBe(1);
});

it("keeps each set's uploads apart", async () => {
  server.use(
    http.post("*/api/projects/:id/runs", () => HttpResponse.json(runSummary, { status: 202 })),
  );
  const { queryClient, wrapper } = queryWrapper();
  const { result } = renderHook(() => useUploads("p-2"), { wrapper });
  act(() => enqueueUploads("p-1", [file("a.txt")], options, queryClient));
  act(() => enqueueUploads("p-2", [file("z.txt")], options, queryClient));
  await waitFor(() =>
    expect(result.current.map((u) => [u.name, u.status])).toEqual([["z.txt", "done"]]),
  );
});

it("words upload errors", () => {
  expect(uploadErrorText(new ApiError(413, "x"))).toBe("Too large for this server.");
  expect(uploadErrorText(new ApiError(422, "notes.rtf: unsupported file type '.rtf'"))).toBe(
    "notes.rtf: unsupported file type '.rtf'",
  );
});

it("refreshes the set itself once a file is in, so its counts and languages catch up", async () => {
  // CGL-30: a set with no language gets one from its first upload
  server.use(
    http.post("*/api/projects/p-1/runs", () => HttpResponse.json(runSummary, { status: 202 })),
  );
  const { queryClient, wrapper } = queryWrapper();
  const refreshed = vi.spyOn(queryClient, "invalidateQueries");
  const { result } = renderHook(() => useUploads("p-1"), { wrapper });
  act(() => enqueueUploads("p-1", [file("a.txt")], options, queryClient));
  await waitFor(() => expect(result.current[0]?.status).toBe("done"));
  expect(refreshed).toHaveBeenCalledWith({ queryKey: ["projects", "p-1"], exact: true });
});

it("asks for every language of the set, so a language added meanwhile is not missed", async () => {
  let all: FormDataEntryValue | null = null;
  server.use(
    http.post("*/api/projects/p-1/runs", async ({ request }) => {
      all = (await request.formData()).get("all_languages");
      return HttpResponse.json(runSummary, { status: 202 });
    }),
  );
  const { queryClient, wrapper } = queryWrapper();
  const { result } = renderHook(() => useUploads("p-1"), { wrapper });
  act(() => enqueueUploads("p-1", [file("a.txt")], options, queryClient));
  await waitFor(() => expect(result.current[0]?.status).toBe("done"));
  expect(all).toBe("true");
});

describe("leave-page guard", () => {
  const guards = (spy: { mock: { calls: unknown[][] } }) =>
    spy.mock.calls.filter((call) => call[0] === "beforeunload");

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("is on while files are waiting or uploading, and off once they finish", async () => {
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");
    server.use(
      http.post("*/api/projects/p-1/runs", async () => {
        await delay(20);
        return HttpResponse.json(runSummary, { status: 202 });
      }),
    );
    const { queryClient } = queryWrapper();
    act(() => enqueueUploads("p-1", [file("a.txt"), file("b.txt")], options, queryClient));
    expect(guards(add)).toHaveLength(1); // one guard however many files
    expect(guards(remove)).toHaveLength(0);

    const handler = guards(add)[0]![1] as (event: BeforeUnloadEvent) => void;
    // a plain object: jsdom's Event.returnValue is the legacy boolean, not the browser's string
    const event = { preventDefault: vi.fn(), returnValue: "unset" };
    handler(event as unknown as BeforeUnloadEvent);
    expect(event.preventDefault).toHaveBeenCalled();
    expect(event.returnValue).toBe("");

    await waitFor(() => expect(guards(remove)).toHaveLength(1));
    expect(guards(remove)[0]![1]).toBe(handler);
    expect(guards(add)).toHaveLength(1);
  });

  it("is off once every file has failed, and back on for a retry", async () => {
    server.use(
      http.post("*/api/projects/p-1/runs", () =>
        HttpResponse.json({ detail: "nope" }, { status: 500 }),
      ),
    );
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");
    const { queryClient, wrapper } = queryWrapper();
    const { result } = renderHook(() => useUploads("p-1"), { wrapper });
    act(() => enqueueUploads("p-1", [file("a.txt"), file("b.txt")], options, queryClient));
    expect(guards(add)).toHaveLength(1);
    await waitFor(() => expect(result.current.map((u) => u.status)).toEqual(["failed", "failed"]));
    expect(guards(remove)).toHaveLength(1);

    act(() => retryUpload(result.current[0]!.key));
    expect(guards(add)).toHaveLength(2);
    await waitFor(() => expect(result.current[0]!.status).toBe("failed"));
    expect(guards(remove)).toHaveLength(2);
  });

  it("is never on for an empty queue", () => {
    const add = vi.spyOn(window, "addEventListener");
    const { queryClient } = queryWrapper();
    act(() => enqueueUploads("p-1", [], options, queryClient));
    expect(guards(add)).toHaveLength(0);
  });
});
