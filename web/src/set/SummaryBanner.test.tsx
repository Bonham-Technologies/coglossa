import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { RunDetail } from "../api/types";
import { documentRun, documentSummary, progress, runDetail, runSummary } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { SummaryBanner } from "./SummaryBanner";

// the banner asks for the set's files; a test that cares serves its own
beforeEach(() => {
  server.use(http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])));
});

function detail(
  state: RunDetail["run"]["state"],
  patch: Partial<RunDetail> = {},
  retryable = false,
): RunDetail {
  return {
    ...runDetail,
    ...patch,
    run: { ...runDetail.run, state, error: state === "failed" ? "translator: timeout" : null },
    progress: { ...runDetail.progress, ...patch.progress, state, retryable },
    stats: { spans: 6, open_flags: 2, flags_by_code: {}, edited_spans: 0, ...patch.stats },
  };
}

it("offers the next passage to check", async () => {
  const onNext = vi.fn();
  const { user } = renderAt(<SummaryBanner detail={detail("complete")} onNext={onNext} />);
  expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent(
    "Looks good. 2 are worth a quick look before you download.",
  );
  await user.click(screen.getByRole("button", { name: "Next to check →" }));
  expect(onNext).toHaveBeenCalled();
});

it("lists run notices in plain words, without repeating the practice note", () => {
  renderAt(
    <SummaryBanner
      detail={detail("complete", {
        language_tier: "C",
        notices: [
          { code: "low_reliability_language", explanation: "tier C", evidence: {}, status: "open" },
          {
            code: "unscored",
            explanation: "The scorer was offline.",
            evidence: {},
            status: "open",
          },
        ],
      })}
    />,
  );
  const banner = screen.getByRole("region", { name: "Summary" });
  expect(banner).toHaveTextContent("Check carefully: Coglossa has less practice with English.");
  expect(banner).toHaveTextContent("Couldn't be checked. The scorer was offline.");
  expect(banner).not.toHaveTextContent("tier C");
});

it("says what was left out of a finished file without offering to translate it again", () => {
  const leftOut = "Some parts of this file couldn't be read, so they aren't translated: 1 chart.";
  renderAt(
    <SummaryBanner
      detail={detail("complete", {
        stats: { spans: 6, open_flags: 0, flags_by_code: {}, edited_spans: 0 },
        notices: [{ code: "unread_content", explanation: leftOut, evidence: {}, status: "open" }],
      })}
    />,
  );
  const banner = screen.getByRole("region", { name: "Summary" });
  expect(banner).toHaveTextContent("Looks good. 6 passages translated, nothing left to check.");
  expect(banner).toHaveTextContent(`Not everything was translated. ${leftOut}`);
  expect(screen.queryByRole("button", { name: "Translate again" })).toBeNull();
});

it("tries unfinished checking again", async () => {
  let retried = false;
  server.use(
    http.post("*/api/runs/r-1/retry", () => {
      retried = true;
      return HttpResponse.json({ message: "retrying", progress }, { status: 202 });
    }),
  );
  const { user } = renderAt(
    <SummaryBanner
      detail={detail(
        "complete_with_errors",
        {
          stats: { spans: 6, open_flags: 1, flags_by_code: { engine_error: 1 }, edited_spans: 0 },
          progress: { ...runDetail.progress, chunks_with_errors: 1 },
        },
        true,
      )}
    />,
  );
  expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent(
    "Ready. Some checking didn't finish on 1 passage; they're marked.",
  );
  await user.click(screen.getByRole("button", { name: "Try those again" }));
  await vi.waitFor(() => expect(retried).toBe(true));
});

describe("retrying when passages have been edited", () => {
  const edited = { spans: 6, open_flags: 1, flags_by_code: { engine_error: 1 }, edited_spans: 2 };
  const failedSections = { ...runDetail.progress, chunks_with_errors: 1 };
  let posts = 0;
  beforeEach(() => {
    posts = 0;
    server.use(
      http.post("*/api/runs/r-1/retry", () => {
        posts += 1;
        return HttpResponse.json({ message: "retrying", progress }, { status: 202 });
      }),
    );
  });

  it("asks first, and only retries once confirmed", async () => {
    const { user } = renderAt(
      <SummaryBanner
        detail={detail("complete_with_errors", { stats: edited, progress: failedSections }, true)}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Try those again" }));
    const dialog = await screen.findByRole("dialog", { name: "Try those again?" });
    expect(dialog).toHaveTextContent(
      "Any edits you made to the passages being redone will be replaced with new translations. This can't be undone.",
    );
    expect(posts).toBe(0);
    await user.click(within(dialog).getByRole("button", { name: "Replace and try again" }));
    await vi.waitFor(() => expect(posts).toBe(1));
  });

  it("sends nothing when cancelled", async () => {
    const { user } = renderAt(
      <SummaryBanner
        detail={detail("complete_with_errors", { stats: edited, progress: failedSections }, true)}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Try those again" }));
    const dialog = await screen.findByRole("dialog", { name: "Try those again?" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(posts).toBe(0);
  });

  it("sends nothing when Escape closes the question", async () => {
    const { user } = renderAt(
      <SummaryBanner
        detail={detail("complete_with_errors", { stats: edited, progress: failedSections }, true)}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Try those again" }));
    const dialog = await screen.findByRole("dialog", { name: "Try those again?" });
    // jsdom has no modal dialogs: Escape on a real one fires this cancel event
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(posts).toBe(0);
  });

  it("retries at once when nothing was edited", async () => {
    const { user } = renderAt(
      <SummaryBanner
        detail={detail(
          "complete_with_errors",
          { stats: { ...edited, edited_spans: 0 }, progress: failedSections },
          true,
        )}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Try those again" }));
    await vi.waitFor(() => expect(posts).toBe(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("rechecks at once when only the guide check is redone, as no passage is replaced", async () => {
    const { user } = renderAt(
      <SummaryBanner
        detail={detail(
          "complete_with_errors",
          {
            stats: { ...edited, flags_by_code: {} },
            problems: [
              {
                stage: "sweep",
                text: "Some names and terms couldn't be brought in line with the translation guide.",
              },
            ],
          },
          true,
        )}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Recheck" }));
    await vi.waitFor(() => expect(posts).toBe(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("retries a failed run at once, as it replaces no passage", async () => {
    const { user } = renderAt(
      <SummaryBanner
        detail={detail("failed", { stats: edited, progress: failedSections }, true)}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await vi.waitFor(() => expect(posts).toBe(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

it("says when some passages weren't fully checked, and offers to try them again", () => {
  const text = "Some passages weren't fully checked, so mistakes in them may not be marked.";
  const problems = [{ stage: "score", text }];
  const stats = { spans: 6, open_flags: 0, flags_by_code: {}, edited_spans: 0 };
  // a check that failed on a passage is an error of its section, so the section counts as failed
  const failed = { ...runDetail.progress, chunks_with_errors: 1 };
  renderAt(
    <SummaryBanner
      detail={detail("complete_with_errors", { problems, stats, progress: failed }, true)}
    />,
  );
  const banner = screen.getByRole("region", { name: "Summary" });
  expect(banner).toHaveTextContent(`Ready. ${text}`);
  expect(banner).not.toHaveTextContent("Looks good");
  expect(screen.getByRole("button", { name: "Try those again" })).toBeInTheDocument();
});

describe("the guide check", () => {
  // the engine's GUIDE_CHANGED_AGAIN (engine/coglossa/problems.py)
  const text =
    "The translation guide changed again while this file was being checked, so its latest change " +
    "isn't applied yet. Recheck the file to apply it.";
  const stats = { spans: 6, open_flags: 0, flags_by_code: {}, edited_spans: 0 };

  it("offers Recheck, not Try those again, when only the sweep needs rerunning", async () => {
    let retried = false;
    server.use(
      http.post("*/api/runs/r-1/retry", () => {
        retried = true;
        return HttpResponse.json({ message: "retrying", progress }, { status: 202 });
      }),
    );
    const problems = [{ stage: "sweep", text }];
    const { user } = renderAt(
      <SummaryBanner detail={detail("complete_with_errors", { problems, stats }, true)} />,
    );
    expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent(text);
    expect(screen.queryByRole("button", { name: "Try those again" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Recheck" }));
    await vi.waitFor(() => expect(retried).toBe(true));
  });

  it("offers Recheck when a guide problem a retry can't fix comes with it", () => {
    const guide =
      "Part of the translation guide for this file couldn't be built, so some names and terms " +
      "may vary.";
    const problems = [
      { stage: "guide", text: guide },
      { stage: "sweep", text },
    ];
    renderAt(<SummaryBanner detail={detail("complete_with_errors", { problems, stats }, true)} />);
    expect(screen.getByRole("button", { name: "Recheck" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try those again" })).not.toBeInTheDocument();
  });

  it("keeps Try those again when passages failed too", () => {
    const problems = [{ stage: "sweep", text }];
    const failed = { ...runDetail.progress, chunks_with_errors: 1 };
    renderAt(
      <SummaryBanner
        detail={detail("complete_with_errors", { problems, stats, progress: failed }, true)}
      />,
    );
    expect(screen.getByRole("button", { name: "Try those again" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Recheck" })).not.toBeInTheDocument();
  });
});

it("says when the translation guide couldn't be built, and offers to translate again", async () => {
  const text =
    "The translation guide for this file couldn't be built, so names and terms may vary.";
  const problems = [{ stage: "guide", text }];
  const stats = { spans: 6, open_flags: 0, flags_by_code: {}, edited_spans: 0 };
  const { unmount } = renderAt(
    <SummaryBanner detail={detail("complete_with_errors", { problems, stats })} />,
  );
  const banner = screen.getByRole("region", { name: "Summary" });
  expect(banner).toHaveTextContent(text);
  expect(banner).not.toHaveTextContent("Looks good");
  expect(await screen.findByRole("button", { name: "Translate again" })).toBeInTheDocument();
  unmount();
  // passages still worth a look keep the way to them
  const onNext = vi.fn();
  renderAt(<SummaryBanner detail={detail("complete_with_errors", { problems })} onNext={onNext} />);
  expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent(
    `Ready. ${text} 2 are worth a quick look before you download.`,
  );
  expect(screen.getByRole("button", { name: "Next to check →" })).toBeInTheDocument();
});

it("explains a failure and keeps the raw error under Details", () => {
  renderAt(<SummaryBanner detail={detail("failed", {}, true)} />);
  const banner = screen.getByRole("region", { name: "Summary" });
  expect(banner).toHaveTextContent("This translation couldn't finish.");
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  expect(screen.getByText("Details").closest("details")).toHaveTextContent("translator: timeout");
});

it("translates a stopped file again and opens the new version", async () => {
  server.use(
    http.post("*/api/documents/d-1/runs", () =>
      HttpResponse.json({ ...runSummary, id: "r-2", state: "created" }, { status: 202 }),
    ),
  );
  server.use(http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])));
  const { user, router } = renderAt(<SummaryBanner detail={detail("cancelled")} />);
  expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent(
    "Stopped before it finished.",
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Translate again" })).not.toHaveAttribute(
      "aria-disabled",
    ),
  );
  await user.click(screen.getByRole("button", { name: "Translate again" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-1/r-2"));
});

it("does not offer Translate again while another file of the set is running", async () => {
  const running = { ...documentRun, id: "r-2", state: "translating" as const, finished_at: null };
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([documentSummary, { ...documentSummary, id: "d-2", runs: [running] }]),
    ),
  );
  renderAt(<SummaryBanner detail={detail("cancelled")} />);
  const button = screen.getByRole("button", { name: "Translate again" });
  await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"));
  expect(button).toHaveAccessibleDescription("Available when this translation finishes");
  expect(screen.getByText("Available when this translation finishes")).toBeVisible();
});

it("keeps Translate again off while the set's files are not known yet", async () => {
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json({ detail: "files are unavailable" }, { status: 500 }),
    ),
  );
  renderAt(<SummaryBanner detail={detail("cancelled")} />);
  const button = screen.getByRole("button", { name: "Translate again" });
  expect(button).toHaveAttribute("aria-disabled", "true");
  await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"));
  // the files failed to list, so "when this translation finishes" would be wrong
  expect(await screen.findByText("Available once the files are listed")).toBeVisible();
  expect(button).toHaveAccessibleDescription("Available once the files are listed");
});
