import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import type { ReviewSpan, RunDetail } from "../api/types";
import { documentSummary, reviewSpans, runDetail, spanDetail } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { DocumentView } from "./DocumentView";

// the panel's Always translate asks for the set's files; a test that cares serves its own
beforeEach(() => {
  server.use(http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])));
});

const complete: RunDetail = {
  ...runDetail,
  run: { ...runDetail.run, state: "complete", finished_at: "2026-10-01T09:05:00Z" },
  progress: { ...runDetail.progress, state: "complete" },
  report_available: true,
  stats: { spans: 3, open_flags: 2, flags_by_code: {}, edited_spans: 1 },
};

const plain = (n: number): ReviewSpan => ({
  ...reviewSpans[0]!,
  id: `s-${n}`,
  number: n,
  source: `Source ${n}`,
  text: `Text ${n}`,
  edited: false,
  flags: [],
});

const flagged = (span: ReviewSpan): ReviewSpan => ({ ...span, flags: reviewSpans[1]!.flags });

function serveSpans(all: ReviewSpan[]): URL[] {
  const requests: URL[] = [];
  server.use(
    http.get("*/api/runs/r-1/spans", ({ request }) => {
      const url = new URL(request.url);
      requests.push(url);
      const list =
        url.searchParams.get("flagged") === "true"
          ? all.filter((s) => s.flags.some((f) => f.status === "open"))
          : all;
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 100);
      return HttpResponse.json({
        spans: list.slice(offset, offset + limit),
        total: list.length,
        offset,
        limit,
      });
    }),
    http.get("*/api/runs/r-1/spans/:spanId", ({ params }) => {
      const span = all.find((s) => s.id === params["spanId"])!;
      return HttpResponse.json({ ...spanDetail, ...span });
    }),
  );
  return requests;
}

const rows = () =>
  within(screen.getByRole("table", { name: "Document" }))
    .getAllByRole("row")
    .slice(1);

it("shows the original beside the translation, marked and labelled", async () => {
  serveSpans(reviewSpans);
  renderAt(<DocumentView detail={complete} />);
  const table = await screen.findByRole("table", { name: "Document" });
  expect(
    within(table)
      .getAllByRole("columnheader")
      .map((h) => h.textContent),
  ).toEqual(["Original", "English"]);
  const [first, second, third] = rows();
  expect(within(first!).getByText("Chapitre premier").closest("td")).toHaveAttribute("lang", "fr");
  expect(within(first!).getByText("Chapter One")).toHaveAttribute("lang", "en");
  expect(second).toHaveClass("flagged");
  expect(second).toHaveTextContent("Worth a look:");
  expect(third).toHaveTextContent("edited");
  expect(screen.getByText("3 of 3 passages shown")).toBeInTheDocument();
});

it("leaves the source unmarked when the run has no source language", async () => {
  serveSpans(reviewSpans);
  renderAt(
    <DocumentView
      detail={{ ...complete, profile: null, run: { ...complete.run, source_lang: null } }}
    />,
  );
  await screen.findByRole("table", { name: "Document" });
  expect(within(rows()[0]!).getByText("Chapitre premier").closest("td")).not.toHaveAttribute(
    "lang",
  );
});

it("walks the passages to check with Next, n and p", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByRole("button", { name: "Next to check →" }));
  expect(await screen.findByRole("complementary", { name: "Passage 2" })).toBeInTheDocument();
  await user.keyboard("n");
  expect(await screen.findByRole("complementary", { name: "Passage 3" })).toBeInTheDocument();
  await user.keyboard("n");
  expect(await screen.findByText("No later passage to check.")).toBeInTheDocument();
  await user.keyboard("p");
  expect(await screen.findByRole("complementary", { name: "Passage 2" })).toBeInTheDocument();
});

it("leaves the keys to an open dialog", async () => {
  const requests = serveSpans(reviewSpans);
  const { user } = renderAt(
    <>
      <DocumentView detail={complete} />
      <dialog open aria-label="Elsewhere">
        <p tabIndex={0}>Inside</p>
      </dialog>
    </>,
  );
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("Chapter One"));
  expect(await screen.findByRole("complementary", { name: "Passage 1" })).toBeInTheDocument();
  await user.click(screen.getByText("Inside"));
  await user.keyboard("n");
  await act(async () => {}); // a walk, had n started one, would be under way by now
  expect(flaggedGets(requests)).toBe(0);
  await user.keyboard("{Enter}");
  await user.keyboard("{Escape}");
  // the document did not move: the same passage is open and current
  expect(screen.getByRole("complementary", { name: "Passage 1" })).toBeInTheDocument();
  expect(rows()[0]).toHaveAttribute("aria-current", "true");
  expect(rows()[1]).not.toHaveAttribute("aria-current");
});

it("opens with a click, closes with Esc, and reopens with Enter", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("Chapter One"));
  expect(await screen.findByRole("complementary", { name: "Passage 1" })).toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  await user.keyboard("{Enter}");
  expect(await screen.findByRole("complementary", { name: "Passage 1" })).toBeInTheDocument();
});

it("shows only the passages to check when asked", async () => {
  const requests = serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.selectOptions(screen.getByLabelText("Show"), "To check");
  await vi.waitFor(() => expect(rows()).toHaveLength(2));
  expect(requests.at(-1)!.searchParams.get("flagged")).toBe("true");
});

it("says when nothing is left to check", async () => {
  serveSpans([plain(1)]);
  const { user } = renderAt(
    <DocumentView detail={{ ...complete, stats: { ...complete.stats, open_flags: 0 } }} />,
  );
  await screen.findByRole("table", { name: "Document" });
  await user.selectOptions(screen.getByLabelText("Show"), "To check");
  expect(await screen.findByText("Nothing left to check.")).toBeInTheDocument();
});

it("loads more passages on request", async () => {
  serveSpans(Array.from({ length: 120 }, (_, i) => plain(i + 1)));
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  expect(rows()).toHaveLength(100);
  await user.click(screen.getByRole("button", { name: "Load more passages" }));
  await vi.waitFor(() => expect(rows()).toHaveLength(120));
  expect(screen.queryByRole("button", { name: "Load more passages" })).not.toBeInTheDocument();
});

it("loads more by itself when the end comes into view", async () => {
  let callback: IntersectionObserverCallback | undefined;
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(cb: IntersectionObserverCallback) {
        callback = cb;
      }
      observe() {}
      disconnect() {}
    },
  );
  const requests = serveSpans(Array.from({ length: 120 }, (_, i) => plain(i + 1)));
  renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  act(() => {
    callback!([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
  });
  expect(await screen.findByText("120 of 120 passages shown")).toBeInTheDocument();
  expect(requests.some((u) => u.searchParams.get("offset") === "100")).toBe(true);
});

it("reveals a passage to check beyond what is loaded", async () => {
  const all = Array.from({ length: 120 }, (_, i) => plain(i + 1));
  all[109] = flagged(all[109]!);
  serveSpans(all);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByRole("button", { name: "Next to check →" }));
  expect(await screen.findByRole("complementary", { name: "Passage 110" })).toBeInTheDocument();
  expect(rows()).toHaveLength(120);
});

it("fills Always translate from words selected in the original", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("The cat sleeps. It rains."));
  await screen.findByRole("complementary", { name: "Passage 2" });
  const cell = rows()[1]!.querySelector("td.source")!;
  const text = [...cell.childNodes].find((n) => n.nodeType === Node.TEXT_NODE)!;
  const range = document.createRange();
  range.setStart(text, 0);
  range.setEnd(text, 7);
  document.getSelection()!.removeAllRanges();
  document.getSelection()!.addRange(range);
  fireEvent.mouseUp(cell);
  const box = await screen.findByRole("region", { name: "Always translate" });
  expect(within(box).getByLabelText("Always translate")).toHaveValue("Le chat");
});

it("filters by score from the address, and clears it", async () => {
  const requests = serveSpans(reviewSpans);
  const { user, router } = renderAt(<DocumentView detail={complete} />, {
    path: "/t/p-1/r-1?min_meaning=0&max_meaning=60",
  });
  await screen.findByRole("table", { name: "Document" });
  expect(requests[0]!.searchParams.get("min_meaning")).toBe("0");
  expect(requests[0]!.searchParams.get("max_meaning")).toBe("60");
  expect(screen.getByText(/Showing passages by score/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Show everything" }));
  expect(router.state.location.search).toBe("");
});

it("shows the error and stops when a page fails to load while revealing", async () => {
  const all = Array.from({ length: 120 }, (_, i) => plain(i + 1));
  all[109] = flagged(all[109]!);
  const requests = serveSpans(all);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  server.use(
    http.get("*/api/runs/r-1/spans", ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get("offset") === "100" && !url.searchParams.get("flagged")) {
        requests.push(url);
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }
      return undefined;
    }),
  );
  await user.click(screen.getByRole("button", { name: "Next to check →" }));
  await waitFor(() => expect(screen.getByRole("status")).not.toBeEmptyDOMElement());
  // the failed page was asked for once: the walk stopped instead of looping
  expect(
    requests.filter(
      (u) => u.searchParams.get("offset") === "100" && !u.searchParams.get("flagged"),
    ),
  ).toHaveLength(1);
});

it("walks the passages to check within the score filter", async () => {
  const requests = serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />, {
    path: "/t/p-1/r-1?min_meaning=0&max_meaning=60",
  });
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByRole("button", { name: "Next to check →" }));
  await screen.findByRole("complementary", { name: "Passage 2" });
  const walk = requests.filter((u) => u.searchParams.get("flagged") === "true");
  expect(walk.length).toBeGreaterThan(0);
  for (const u of walk) {
    expect(u.searchParams.get("min_meaning")).toBe("0");
    expect(u.searchParams.get("max_meaning")).toBe("60");
  }
});

it("keeps its place when the selected passage leaves the list", async () => {
  const all = [1, 2, 3, 4, 5].map((n) => ([2, 3, 4].includes(n) ? flagged(plain(n)) : plain(n)));
  serveSpans(all);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("Text 3"));
  await screen.findByRole("complementary", { name: "Passage 3" });
  all[2] = plain(3);
  await user.selectOptions(screen.getByLabelText("Show"), "To check");
  await vi.waitFor(() => expect(rows()).toHaveLength(2));
  await user.click(document.body);
  await user.keyboard("n");
  expect(await screen.findByRole("complementary", { name: "Passage 4" })).toBeInTheDocument();
});

it("does not carry selected words over to another passage", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("The cat sleeps. It rains."));
  await screen.findByRole("complementary", { name: "Passage 2" });
  const cell = rows()[1]!.querySelector("td.source")!;
  const text = [...cell.childNodes].find((n) => n.nodeType === Node.TEXT_NODE)!;
  const range = document.createRange();
  range.setStart(text, 0);
  range.setEnd(text, 7);
  document.getSelection()!.removeAllRanges();
  document.getSelection()!.addRange(range);
  fireEvent.mouseUp(cell);
  const box = await screen.findByRole("region", { name: "Always translate" });
  expect(within(box).getByLabelText("Always translate")).toHaveValue("Le chat");
  document.getSelection()!.removeAllRanges();
  await user.click(document.body);
  await user.keyboard("n");
  const next = await screen.findByRole("complementary", { name: "Passage 3" });
  expect(
    within(within(next).getByRole("region", { name: "Always translate" })).getByLabelText(
      "Always translate",
    ),
  ).toHaveValue("");
});

it("drops a half-typed edit when another passage is opened", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("The cat sleeps. It rains."));
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.type(screen.getByLabelText("Your translation"), " More");
  await user.click(screen.getByText("Hello"));
  await screen.findByRole("complementary", { name: "Passage 3" });
  expect(screen.queryByLabelText("Your translation")).not.toBeInTheDocument();
  await user.click(within(rows()[1]!).getByText("The cat sleeps. It rains."));
  await screen.findByRole("complementary", { name: "Passage 2" });
  expect(screen.queryByLabelText("Your translation")).not.toBeInTheDocument();
});

const flaggedGets = (requests: URL[]) =>
  requests.filter((u) => u.searchParams.get("flagged") === "true").length;

it("lets the last of two quick presses win over a slower earlier walk", async () => {
  const all = Array.from({ length: 120 }, (_, i) => plain(i + 1));
  all[4] = flagged(all[4]!);
  all[109] = flagged(all[109]!);
  serveSpans(all);
  server.use(
    http.get("*/api/runs/r-1/spans", async ({ request }) => {
      const url = new URL(request.url);
      // revealing passage 110 needs the second page, which is slow
      if (url.searchParams.get("offset") === "100" && !url.searchParams.get("flagged")) {
        await delay(100);
      }
      return undefined;
    }),
  );
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(document.body);
  await user.keyboard("p"); // the last flagged passage, 110: slow to reveal
  await user.keyboard("p"); // one more back, 5: quick
  expect(await screen.findByRole("complementary", { name: "Passage 5" })).toBeInTheDocument();
  await waitFor(() => expect(rows()).toHaveLength(120));
  await act(async () => {}); // let the slower walk finish, if it was going to land
  expect(screen.getByRole("complementary", { name: "Passage 5" })).toBeInTheDocument();
});

it("reads the passages to check once for a walk, and again after a review action", async () => {
  const requests = serveSpans(reviewSpans);
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/accept", () =>
      HttpResponse.json({
        ...spanDetail,
        ...reviewSpans[1]!,
        flags: reviewSpans[1]!.flags.map((f) => ({ ...f, status: "accepted" as const })),
      }),
    ),
  );
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByRole("button", { name: "Next to check →" }));
  await screen.findByRole("complementary", { name: "Passage 2" });
  const first = flaggedGets(requests);
  expect(first).toBeGreaterThan(0);
  await user.keyboard("n");
  await screen.findByRole("complementary", { name: "Passage 3" });
  await user.keyboard("p");
  await screen.findByRole("complementary", { name: "Passage 2" });
  expect(flaggedGets(requests)).toBe(first);
  await user.click(screen.getByRole("button", { name: "It's fine" }));
  await vi.waitFor(() => expect(flaggedGets(requests)).toBeGreaterThan(first));
});

it("marks the current row with aria-current, not aria-selected", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("The cat sleeps. It rains."));
  await screen.findByRole("complementary", { name: "Passage 2" });
  const [first, second] = rows();
  expect(second).toHaveAttribute("aria-current", "true");
  expect(first).not.toHaveAttribute("aria-current");
  for (const row of rows()) expect(row).not.toHaveAttribute("aria-selected");
});

it("Enter on a focused row opens its passage, and keeps it open", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  act(() => rows()[1]!.focus());
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  await user.keyboard("{Enter}");
  expect(await screen.findByRole("complementary", { name: "Passage 2" })).toBeInTheDocument();
  await user.keyboard("{Enter}");
  expect(screen.getByRole("complementary", { name: "Passage 2" })).toBeInTheDocument();
});

it("Escape in the panel closes it and returns focus to the row", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("The cat sleeps. It rains."));
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("button", { name: "Cancel edit" }));
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  expect(rows()[1]).toHaveFocus();
});

it("the close button also returns focus to the row", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("The cat sleeps. It rains."));
  await user.click(await screen.findByRole("button", { name: "Close the passage" }));
  expect(rows()[1]).toHaveFocus();
});

it("moves one flagged passage per quick n press, and ends on the last", async () => {
  serveSpans(reviewSpans);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  let held = false;
  server.use(
    http.get("*/api/runs/r-1/spans", async ({ request }) => {
      if (new URL(request.url).searchParams.get("flagged") === "true" && !held) {
        held = true;
        await gate; // the first walk's answer is held until both presses are in
      }
      return undefined;
    }),
  );
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(document.body);
  await user.keyboard("n");
  await user.keyboard("n");
  release();
  expect(await screen.findByRole("complementary", { name: "Passage 3" })).toBeInTheDocument();
});

it("does not pull focus to the row when Escape is pressed outside the panel", async () => {
  serveSpans(reviewSpans);
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("The cat sleeps. It rains."));
  await screen.findByRole("complementary", { name: "Passage 2" });
  const next = screen.getByRole("button", { name: "Next to check →" });
  act(() => next.focus());
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  expect(next).toHaveFocus();
});

it("does not move a passage the reader tapped, but brings a walked-to one into view", async () => {
  // A phone tap focuses the row on the way down; scrolling then would move the page under the
  // finger and the tap would miss the row (the click lands on its parent).
  serveSpans(reviewSpans);
  const scrolled: string[] = [];
  const spy = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(function (
    this: Element,
  ) {
    scrolled.push(this.id);
  });
  const { user } = renderAt(<DocumentView detail={complete} />);
  await screen.findByRole("table", { name: "Document" });
  await user.click(screen.getByText("The cat sleeps. It rains."));
  expect(await screen.findByRole("complementary", { name: "Passage 2" })).toBeInTheDocument();
  expect(scrolled).toEqual([]);
  await user.keyboard("n");
  expect(await screen.findByRole("complementary", { name: "Passage 3" })).toBeInTheDocument();
  expect(scrolled).toEqual([`passage-${reviewSpans[2]!.id}`]);
  spy.mockRestore();
});
