import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { LanguageReviewSummary, SetReviewSummary } from "../api/reviewSummary";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { ReviewSummary } from "./ReviewSummary";

const zero: Omit<LanguageReviewSummary, "target_lang" | "run_ids"> = {
  passages: 0,
  checked: 0,
  checked_share: 0,
  edited: 0,
  edited_share: 0,
  edit_distance_total: 0,
  mean_edit_distance: 0,
  mean_edit_ratio: 0,
  estimated_distances: 0,
  flags: [],
};

const setSummary: SetReviewSummary = {
  project_id: "p-1",
  languages: [
    {
      ...zero,
      target_lang: "es",
      run_ids: ["r-1"],
      passages: 40,
      checked: 10,
      checked_share: 0.25,
      edited: 4,
      edited_share: 0.1,
      edit_distance_total: 50,
      mean_edit_distance: 12.5,
      mean_edit_ratio: 0.2,
      estimated_distances: 1,
      flags: [
        {
          code: "terminology_violation",
          raised: 6,
          passages: 5,
          open: 1,
          accepted: 1,
          dismissed: 3,
          resolved: 1,
          edited_after: 2,
          edited_after_share: 0.4,
          dismiss_reasons: [
            { reason: "The client prefers this word", count: 2 },
            { reason: "A name", count: 1 },
          ],
        },
      ],
    },
    { ...zero, target_lang: "de", run_ids: ["r-9"] },
  ],
};

let requests = 0;

beforeEach(() => {
  requests = 0;
  server.use(
    http.get("*/api/projects/p-1/review-summary", () => {
      requests += 1;
      return HttpResponse.json(setSummary);
    }),
  );
});

it("is folded away and loads only when opened, again on each opening", async () => {
  const { user } = renderAt(<ReviewSummary projectId="p-1" lang="es" runId="r-1" />);
  const toggle = screen.getByText("Technical details");
  expect(toggle.closest("details")).not.toHaveAttribute("open");
  expect(requests).toBe(0);
  await user.click(toggle);
  expect(await screen.findByText(/10 of 40 passages checked \(25%\)/)).toBeInTheDocument();
  await user.click(toggle);
  await user.click(toggle);
  await vi.waitFor(() => expect(requests).toBe(2));
});

it("says in plain words how much reviewing the language took", async () => {
  const { user } = renderAt(<ReviewSummary projectId="p-1" lang="es" runId="r-1" />);
  await user.click(screen.getByText("Technical details"));
  expect(await screen.findByText(/4 of 40 passages changed by a reviewer \(10%\)/)).toBeVisible();
  expect(
    screen.getByText(/12\.5 characters added, removed or replaced on average/),
  ).toHaveTextContent("(20% of its length)");
  expect(screen.getByText(/1 passage too long to compare/)).toBeInTheDocument();
  const table = screen.getByRole("table", { name: "Flags" });
  // the flag by its plain title, never its code
  expect(within(table).getByRole("rowheader")).toHaveTextContent(
    "Doesn't follow your translation guide",
  );
  expect(table).not.toHaveTextContent("terminology_violation");
  expect(table).toHaveTextContent("2 of 5 (40%)");
  expect(table).toHaveTextContent("The client prefers this word (2 times)");
  expect(table).toHaveTextContent("A name");
  expect(screen.getByRole("link", { name: /Download every passage/ })).toHaveAttribute(
    "href",
    "/api/projects/p-1/review-summary.csv?target_lang=es",
  );
  expect(screen.getByRole("link", { name: "Download this file only" })).toHaveAttribute(
    "href",
    "/api/runs/r-1/review-summary.csv",
  );
});

it("says when a language has nothing to review yet", async () => {
  const { user } = renderAt(<ReviewSummary projectId="p-1" lang="DE" runId={undefined} />);
  await user.click(screen.getByText("Technical details"));
  expect(await screen.findByText("No German passages to review yet.")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Download this file only" })).not.toBeInTheDocument();
});

it("shows why the summary could not be loaded", async () => {
  server.use(
    http.get("*/api/projects/p-1/review-summary", () =>
      HttpResponse.json(
        { detail: "only an administrator can see or change this" },
        { status: 403 },
      ),
    ),
  );
  const { user } = renderAt(<ReviewSummary projectId="p-1" lang="es" runId="r-1" />);
  await user.click(screen.getByText("Technical details"));
  expect(await screen.findByRole("alert")).toBeInTheDocument();
});
