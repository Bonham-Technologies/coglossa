import { act, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setEventSourceFactory } from "../api/events";
import type { RunDetail } from "../api/types";
import {
  documentSummary,
  guideView,
  progress,
  reviewSpans,
  runDetail,
  spanDetail,
  spanPage,
} from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { RunView } from "./RunView";

beforeEach(() => {
  setEventSourceFactory(null); // poll instead of streaming
  document.title = "Coglossa";
  // the banner asks for the set's files; a test that cares serves its own
  server.use(http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])));
});
afterEach(() => setEventSourceFactory(undefined));

function serve(detail: RunDetail) {
  server.use(
    http.get("*/api/runs/r-1", () => HttpResponse.json(detail)),
    http.get("*/api/runs/r-1/spans", () => HttpResponse.json(spanPage(reviewSpans))),
  );
}

const finished = (state: RunDetail["run"]["state"]): RunDetail => ({
  ...runDetail,
  run: { ...runDetail.run, state, finished_at: "2026-10-01T09:05:00Z", error: null },
  progress: { ...runDetail.progress, state },
  report_available: state === "complete" || state === "complete_with_errors",
  stats: { spans: 3, open_flags: 2, flags_by_code: {}, edited_spans: 0 },
});

it("shows plain steps and the percentage while translating", async () => {
  serve(runDetail); // translating, 50%
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  const steps = await screen.findByRole("list", { name: "Steps" });
  const items = within(steps).getAllByRole("listitem");
  expect(items.map((i) => i.textContent)).toEqual([
    "✓ Waiting to start (done)",
    "✓ Reading your document (done)",
    "✓ Building the translation guide (done)",
    "● Translating (50%)",
    "○ Checking consistency (not started)",
    "○ Ready (not started)",
  ]);
  expect(items[3]).toHaveAttribute("aria-current", "step");
  expect(screen.getByText(/You can leave this page; it keeps going\./)).toBeInTheDocument();
  expect(document.title).toBe("50% – histoire.txt");
});

it("announces a step change politely, not every percentage tick", async () => {
  serve(runDetail); // translating, 50%
  const { queryClient } = renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  await screen.findByRole("list", { name: "Steps" });
  const live = within(screen.getByRole("region", { name: "Progress" })).getByRole("status");
  expect(live).toHaveAttribute("aria-live", "polite");
  expect(live).toHaveClass("visually-hidden");
  expect(live).toHaveTextContent("Translating");
  // a percentage tick leaves the announcement alone (and out of it)
  await act(async () =>
    queryClient.setQueryData<RunDetail>(["runs", "r-1"], {
      ...runDetail,
      progress: { ...runDetail.progress, chunks_done: { translate: 4, reconcile: 2, score: 1 } },
    }),
  );
  await screen.findByText(/Translating \(58%\)/);
  expect(live).toHaveTextContent(/^Translating$/);
  // the next step is announced by name
  queryClient.setQueryData<RunDetail>(["runs", "r-1"], {
    ...runDetail,
    run: { ...runDetail.run, state: "sweeping" },
  });
  await waitFor(() => expect(live).toHaveTextContent(/^Checking consistency$/));
});

it("stops translating after confirming", async () => {
  let cancelled = false;
  serve(runDetail);
  server.use(
    http.post("*/api/runs/r-1/cancel", () => {
      cancelled = true;
      return HttpResponse.json({ message: "cancelled", progress });
    }),
  );
  const { user } = renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "Stop translating" }));
  const dialog = screen.getByRole("dialog", { name: "Stop translating" });
  await user.click(within(dialog).getByRole("button", { name: "Stop" }));
  await vi.waitFor(() => expect(cancelled).toBe(true));
});

it("offers Continue as is or a look at the guide when a run waits", async () => {
  let approved: unknown;
  serve({ ...runDetail, run: { ...runDetail.run, state: "awaiting_guide_approval" } });
  server.use(
    http.post("*/api/runs/r-1/guide/approve", async ({ request }) => {
      approved = await request.json();
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const onOpenGuide = vi.fn();
  const { user } = renderAt(<RunView runId="r-1" onOpenGuide={onOpenGuide} />);
  expect(
    await screen.findByText("Waiting for you to check the translation guide."),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Look at the guide first" }));
  expect(onOpenGuide).toHaveBeenCalledWith({ kind: "draft", runId: "r-1" });
  await user.click(screen.getByRole("button", { name: "Continue as is" }));
  await vi.waitFor(() => expect(approved).toEqual({}));
});

it("shows the banner and the document when finished, and says Ready in the tab", async () => {
  serve(finished("complete"));
  const { unmount } = renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  expect(await screen.findByRole("table", { name: "Document" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent("Looks good.");
  expect(document.title).toBe("✓ Ready – histoire.txt");
  unmount();
  expect(document.title).toBe("Coglossa");
});

it("words the tab title for a run that waits, a recheck and a retry", async () => {
  serve({ ...runDetail, run: { ...runDetail.run, state: "awaiting_guide_approval" } });
  const first = renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  await screen.findByText("Waiting for you to check the translation guide.");
  expect(document.title).toBe("Waiting – histoire.txt");
  first.unmount();
  serve({ ...finished("sweeping"), report_available: false });
  const second = renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  await screen.findByText("Rechecking against your translation guide…");
  expect(document.title).toBe("Rechecking – histoire.txt");
  second.unmount();
  serve({ ...finished("translating"), report_available: false });
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  await screen.findByText("Retrying the parts that couldn't be translated…");
  expect(document.title).toBe("Retrying – histoire.txt");
});

it("shows only the banner when nothing usable came out", async () => {
  serve({ ...finished("failed"), run: { ...finished("failed").run, error: "boom" } });
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  expect(await screen.findByRole("region", { name: "Summary" })).toHaveTextContent(
    "This translation couldn't finish.",
  );
  expect(screen.queryByRole("table", { name: "Document" })).not.toBeInTheDocument();
});

it("keeps the document on screen and says it is rechecking during a re-sweep", async () => {
  serve({ ...finished("sweeping"), report_available: false });
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  expect(await screen.findByRole("table", { name: "Document" })).toBeInTheDocument();
  expect(screen.getByText("Rechecking against your translation guide…")).toHaveAttribute(
    "role",
    "status",
  );
  expect(screen.queryByRole("region", { name: "Summary" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Stop translating" })).not.toBeInTheDocument();
});

it("keeps the document on screen and says it is retrying when a finished file is re-run", async () => {
  serve({ ...finished("translating"), report_available: false });
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  expect(await screen.findByRole("table", { name: "Document" })).toBeInTheDocument();
  expect(screen.getByText("Retrying the parts that couldn't be translated…")).toHaveAttribute(
    "role",
    "status",
  );
  expect(screen.queryByText("Rechecking against your translation guide…")).not.toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Steps" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Summary" })).not.toBeInTheDocument();
});

it("keeps the open passage and its status when Apply to all starts a recheck", async () => {
  let current = finished("complete");
  server.use(
    http.get("*/api/runs/r-1", () => HttpResponse.json(current)),
    http.get("*/api/runs/r-1/spans", () => HttpResponse.json(spanPage(reviewSpans))),
    http.get("*/api/runs/r-1/spans/s-2", () => HttpResponse.json(spanDetail)),
    http.post("*/api/runs/r-1/spans/s-2/guide", () => HttpResponse.json(guideView)),
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])),
    http.post("*/api/runs/r-1/sweep", () => {
      current = { ...finished("sweeping"), report_available: false };
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const { user, queryClient } = renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  const table = await screen.findByRole("table", { name: "Document" });
  await user.click(within(table).getByText("Le chat dort. Il pleut."));
  const box = await screen.findByRole("region", { name: "Always translate" });
  await user.type(within(box).getByLabelText("Always translate"), "chat");
  await user.type(within(box).getByLabelText("as"), "cat");
  await user.click(within(box).getByRole("button", { name: "Apply to all" }));
  expect(await within(box).findByRole("status")).toHaveTextContent(
    "Added to the translation guide. Rechecking 1 file…",
  );
  expect(await screen.findByText("Rechecking against your translation guide…")).toBeInTheDocument();
  expect(screen.getByRole("table", { name: "Document" })).toBeInTheDocument();
  expect(screen.getByRole("complementary", { name: "Passage 2" })).toBeInTheDocument();
  expect(within(box).getByRole("status")).toHaveTextContent("Rechecking 1 file…");
  // the recheck ends: the banner returns, the open passage and its status stay
  // the live event says "complete" before the refetch says the report is ready
  queryClient.setQueryData<RunDetail>(["runs", "r-1"], {
    ...finished("complete"),
    report_available: false,
  });
  await screen.findByRole("region", { name: "Summary" });
  expect(screen.getByRole("complementary", { name: "Passage 2" })).toBeInTheDocument();
  current = finished("complete");
  await queryClient.invalidateQueries({ queryKey: ["runs", "r-1"] });
  expect(await screen.findByRole("region", { name: "Summary" })).toBeInTheDocument();
  expect(screen.getByRole("complementary", { name: "Passage 2" })).toBeInTheDocument();
  expect(within(box).getByRole("status")).toHaveTextContent("Rechecking 1 file…");
});

it("fetches the passages again when a recheck ends, without live updates", async () => {
  let current: RunDetail = { ...finished("sweeping"), report_available: false };
  let spans = reviewSpans;
  server.use(
    http.get("*/api/runs/r-1", () => HttpResponse.json(current)),
    http.get("*/api/runs/r-1/spans", () => HttpResponse.json(spanPage(spans))),
  );
  const { queryClient } = renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  const table = await screen.findByRole("table", { name: "Document" });
  expect(within(table).getByText("The cat sleeps. It rains.")).toBeInTheDocument();
  // the recheck rewrote passage 2; the next poll (the run alone) says it is done
  current = finished("complete");
  spans = reviewSpans.map((s) =>
    s.id === "s-2" ? { ...s, text: "The cat is sleeping. It is raining." } : s,
  );
  await queryClient.refetchQueries({ queryKey: ["runs", "r-1"], exact: true });
  expect(await within(table).findByText("The cat is sleeping. It is raining.")).toBeInTheDocument();
});

it("shows the steps, not an empty document, when a finished file with no passages is retried", async () => {
  const none = { ...finished("translating"), report_available: false };
  serve({ ...none, stats: { ...none.stats, spans: 0 } });
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  expect(await screen.findByRole("list", { name: "Steps" })).toBeInTheDocument();
  expect(screen.queryByRole("table", { name: "Document" })).not.toBeInTheDocument();
  expect(screen.queryByText(/Retrying the parts/)).not.toBeInTheDocument();
  expect(document.title).toBe("50% – histoire.txt");
});

it("fetches the passages again when a retry ends, without live updates", async () => {
  let current: RunDetail = { ...finished("translating"), report_available: false };
  let spans = reviewSpans;
  server.use(
    http.get("*/api/runs/r-1", () => HttpResponse.json(current)),
    http.get("*/api/runs/r-1/spans", () => HttpResponse.json(spanPage(spans))),
  );
  const { queryClient } = renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  const table = await screen.findByRole("table", { name: "Document" });
  expect(within(table).getByText("The cat sleeps. It rains.")).toBeInTheDocument();
  current = finished("complete");
  spans = reviewSpans.map((s) =>
    s.id === "s-2" ? { ...s, text: "The cat is sleeping. It is raining." } : s,
  );
  await queryClient.refetchQueries({ queryKey: ["runs", "r-1"], exact: true });
  expect(await within(table).findByText("The cat is sleeping. It is raining.")).toBeInTheDocument();
});

it("shows why a run could not be loaded", async () => {
  server.use(
    http.get("*/api/runs/r-1", () =>
      HttpResponse.json({ detail: "run r-1 not found" }, { status: 404 }),
    ),
  );
  renderAt(<RunView runId="r-1" onOpenGuide={() => {}} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("run r-1 not found");
});
