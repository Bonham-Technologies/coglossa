import { QueryObserver } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { projectKeys } from "../api/projects";
import type { SpanDetail } from "../api/types";
import {
  documentRun,
  documentSummary,
  guideView,
  progress,
  runSummary,
  spanDetail,
} from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { RunLanguages } from "./languages";
import {
  CHANGED,
  CHANGED_STILL_OPEN,
  CHECKED,
  PassagePanel,
  SPLIT_DIFFERENTLY,
} from "./PassagePanel";

const resolved: SpanDetail = {
  ...spanDetail,
  flags: spanDetail.flags.map((f) => ({ ...f, status: "accepted" as const })),
};

function renderPanel(prefill = "") {
  const onNext = vi.fn();
  const onClose = vi.fn();
  const view = renderAt(
    <RunLanguages.Provider value={{ source: "fr", target: "en" }}>
      <PassagePanel
        runId="r-1"
        projectId="p-1"
        spanId="s-2"
        prefill={prefill}
        onNext={onNext}
        onClose={onClose}
      />
    </RunLanguages.Provider>,
  );
  return { ...view, onNext, onClose };
}

const accepted: string[] = [];

beforeEach(() => {
  accepted.length = 0;
  server.use(
    http.get("*/api/runs/r-1/spans/s-2", () => HttpResponse.json(spanDetail)),
    http.post("*/api/runs/r-1/spans/s-2/accept", () => {
      accepted.push("s-2");
      return HttpResponse.json(resolved);
    }),
  );
});

it("explains each open flag in plain words", async () => {
  renderPanel();
  const panel = await screen.findByRole("complementary", { name: "Passage 2" });
  expect(
    within(panel).getByRole("heading", { name: "Passage 2 · worth a look" }),
  ).toBeInTheDocument();
  const why = within(panel).getByRole("region", { name: "Why it's flagged" });
  expect(why).toHaveTextContent("The meaning may have shifted");
  expect(why).toHaveTextContent("Meaning score 55.0 is below the 72 threshold.");
  expect(why).toHaveTextContent("The translators disagreed");
  expect(why).not.toHaveTextContent("low_meaning");
});

it("words a below-threshold flag in plain terms, with the judge's note", async () => {
  const flagged: SpanDetail = {
    ...spanDetail,
    flags: [
      {
        id: 21,
        code: "low_meaning",
        status: "open",
        explanation: "Meaning score 61.9 is below the 86 threshold. Wrong tense.",
        evidence: {
          meaning: 61.9,
          threshold: 86,
          judge_errors: [
            { category: "accuracy", severity: "minor", excerpt: "", explanation: "Minor slip." },
            {
              category: "accuracy",
              severity: "critical",
              excerpt: "",
              explanation: "Wrong tense.",
            },
          ],
        },
        dismiss_reason: null,
      },
    ],
  };
  server.use(http.get("*/api/runs/r-1/spans/s-2", () => HttpResponse.json(flagged)));
  renderPanel();
  const why = await screen.findByRole("region", { name: "Why it's flagged" });
  const sentence = within(why).getByText(
    "The checker rated the meaning 62 out of 100; passages under 86 get a second look.",
  );
  expect(sentence.tagName).toBe("P");
  const note = within(why).getByText("Wrong tense.");
  expect(note.tagName).toBe("P");
  expect(sentence.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(why).not.toHaveTextContent("Minor slip.");
  expect(why).not.toHaveTextContent(/threshold/i);
  expect(why).not.toHaveTextContent("61.9");
});

it("explains a number that doesn't match the original in plain words", async () => {
  const explanation =
    "The numbers don't match the original: the original has 1,200; the translation has 1 300. " +
    "Check the numbers, dates and amounts in this passage.";
  const flagged: SpanDetail = {
    ...spanDetail,
    flags: [
      {
        id: 22,
        code: "number_mismatch",
        status: "open",
        explanation,
        evidence: { source: ["1,200"], target: ["1 300"] },
        dismiss_reason: null,
      },
    ],
  };
  server.use(http.get("*/api/runs/r-1/spans/s-2", () => HttpResponse.json(flagged)));
  renderPanel();
  const why = await screen.findByRole("region", { name: "Why it's flagged" });
  expect(why).toHaveTextContent("A number doesn't match the original");
  expect(why).toHaveTextContent(explanation);
  expect(why).not.toHaveTextContent("number_mismatch");
});

it("uses a better version and marks the passage checked", async () => {
  let chosen: unknown;
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/use-candidate", async ({ request }) => {
      chosen = await request.json();
      return HttpResponse.json({ ...spanDetail, text: "The cat is asleep. It rains." });
    }),
  );
  const { user } = renderPanel();
  const versions = await screen.findByRole("region", { name: "Better versions" });
  expect(within(versions).getByText("The cat is asleep. It rains.")).toHaveAttribute("lang", "en");
  await user.click(within(versions).getByRole("button", { name: "Use version 1" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED);
  expect(chosen).toEqual({ translator: "contextual", base: spanDetail.text });
  expect(accepted).toEqual(["s-2"]);
});

it("offers each version as the server would write it, and only those it accepts", async () => {
  // CGL-44: pieces arrive out of order and padded; some do not line up with the passage
  const piece = (translator: string, start: string, end: string, text: string) => ({
    translator,
    model: "m",
    chunk_id: "c-1",
    start,
    end,
    text,
    notes: "",
  });
  const detail: SpanDetail = {
    ...spanDetail,
    text: "The cat sleeps, it rains.",
    candidates: [
      piece("late", "s3", "s3", "  It rains.\n"),
      piece("short", "s2", "s2", "The cat naps."),
      piece("late", "s2", "s2", "\tThe cat sleeps. "),
      piece("blank", "s2", "s3", " \u0001 "),
    ],
  };
  let chosen: unknown;
  server.use(
    http.get("*/api/runs/r-1/spans/s-2", () => HttpResponse.json(detail)),
    http.post("*/api/runs/r-1/spans/s-2/use-candidate", async ({ request }) => {
      chosen = await request.json();
      return HttpResponse.json({ ...resolved, text: "The cat sleeps. It rains." });
    }),
  );
  const { user } = renderPanel();
  const versions = await screen.findByRole("region", { name: "Better versions" });
  const items = within(versions).getAllByRole("listitem");
  expect(items.map((li) => li.querySelector("p")?.textContent)).toEqual([
    "The cat sleeps. It rains.",
    "The cat naps.",
  ]);
  expect(within(versions).getByRole("button", { name: "Use version 1" })).toBeEnabled();
  const split = within(versions).getByRole("button", { name: "Use version 2" });
  expect(split).toBeDisabled();
  expect(split).toHaveAccessibleDescription(SPLIT_DIFFERENTLY);
  await user.click(within(versions).getByRole("button", { name: "Use version 1" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED);
  expect(chosen).toEqual({ translator: "late", base: detail.text });
});

it("offers no versions when the only other one has no words once cleaned", async () => {
  const blank = { ...spanDetail.candidates[2]!, translator: "blank", text: " \u0001 \u000b" };
  server.use(
    http.get("*/api/runs/r-1/spans/s-2", () =>
      HttpResponse.json({
        ...spanDetail,
        candidates: [...spanDetail.candidates.slice(0, 2), blank],
      }),
    ),
  );
  renderPanel();
  await screen.findByRole("complementary", { name: "Passage 2" });
  expect(screen.queryByRole("region", { name: "Better versions" })).not.toBeInTheDocument();
});

it("saves an edit, and says so when marking it checked fails", async () => {
  let sent: unknown;
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/edit", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...spanDetail, text: "The cat is asleep." });
    }),
    http.post("*/api/runs/r-1/spans/s-2/accept", () =>
      HttpResponse.json({ detail: "busy" }, { status: 503 }),
    ),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  const box = screen.getByLabelText("Your translation");
  expect(box).toHaveAttribute("lang", "en");
  await user.clear(box);
  await user.type(box, "The cat is asleep.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED_STILL_OPEN);
  expect(sent).toEqual({ text: "The cat is asleep.", reason: null, base: spanDetail.text });
});

const changed = (current: string) =>
  HttpResponse.json(
    { detail: "someone else changed this passage", code: "passage_changed", current_text: current },
    { status: 409 },
  );

it("shows another reviewer's save and lets the reviewer keep their own edit", async () => {
  // CGL-36: the first save started from text someone else has changed since
  const sent: unknown[] = [];
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/edit", async ({ request }) => {
      const body = (await request.json()) as { text: string; base: string };
      sent.push(body);
      return body.base === "Their cat naps."
        ? HttpResponse.json({ ...spanDetail, text: body.text })
        : changed("Their cat naps.");
    }),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  const box = screen.getByLabelText("Your translation");
  await user.clear(box);
  await user.type(box, "My cat sleeps.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  const clash = await screen.findByRole("region", { name: "Someone else changed this passage" });
  expect(clash).toHaveTextContent("Their cat naps.");
  expect(clash).not.toHaveTextContent("passage_changed");
  expect(screen.getByLabelText("Your translation")).toHaveValue("My cat sleeps.");
  expect(screen.getByRole("button", { name: "Save edit" })).toBeDisabled();
  expect(accepted).toEqual([]);
  await user.click(within(clash).getByRole("button", { name: "Keep my version" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED);
  expect(sent).toEqual([
    { text: "My cat sleeps.", reason: null, base: spanDetail.text },
    { text: "My cat sleeps.", reason: null, base: "Their cat naps." },
  ]);
  expect(screen.queryByLabelText("Your translation")).not.toBeInTheDocument();
});

it("takes the other reviewer's version instead of saving over it", async () => {
  let edits = 0;
  let fetches = 0;
  server.use(
    http.get("*/api/runs/r-1/spans/s-2", () => {
      fetches += 1;
      return HttpResponse.json(
        fetches === 1 ? spanDetail : { ...spanDetail, text: "Their cat naps." },
      );
    }),
    http.post("*/api/runs/r-1/spans/s-2/edit", () => {
      edits += 1;
      return changed("Their cat naps.");
    }),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.type(screen.getByLabelText("Your translation"), " Mine.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  const clash = await screen.findByRole("region", { name: "Someone else changed this passage" });
  await user.click(within(clash).getByRole("button", { name: "Use their version" }));
  expect(screen.queryByLabelText("Your translation")).not.toBeInTheDocument();
  expect(edits).toBe(1);
  await waitFor(() => expect(fetches).toBeGreaterThan(1));
  await user.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Your translation")).toHaveValue("Their cat naps.");
});

it("says so when another reviewer changed the passage before a version was picked", async () => {
  server.use(http.post("*/api/runs/r-1/spans/s-2/use-candidate", () => changed("Their cat naps.")));
  const { user } = renderPanel();
  const versions = await screen.findByRole("region", { name: "Better versions" });
  await user.click(within(versions).getByRole("button", { name: "Use version 1" }));
  const alert = await within(versions).findByRole("alert");
  expect(alert).toHaveTextContent("Someone else changed this passage");
  expect(alert).toHaveTextContent("Their cat naps.");
  expect(accepted).toEqual([]);
});

it("says in plain words when an edit is too long to save (CGL-69)", async () => {
  const tooLong = "This passage is too long: keep it to 20,000 characters or fewer.";
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/edit", () =>
      HttpResponse.json(
        { detail: [{ type: "too_long", loc: ["body", "text"], msg: tooLong }] },
        { status: 422 },
      ),
    ),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.type(screen.getByLabelText("Your translation"), " Mine.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(new RegExp(`^${tooLong}$`));
  expect(screen.getByLabelText("Your translation")).toBeInTheDocument();
  expect(accepted).toEqual([]);
});

it("saves an edit and marks the passage checked", async () => {
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/edit", () =>
      HttpResponse.json({ ...spanDetail, text: "The cat is asleep." }),
    ),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.type(screen.getByLabelText("Your translation"), " Mine.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED);
  expect(screen.getByRole("status")).not.toHaveTextContent("still marked");
  expect(accepted).toEqual(["s-2"]);
});

it("does not mark a passage checked again when the change already resolved its flags", async () => {
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/edit", () =>
      HttpResponse.json({ ...resolved, text: "The cat is asleep." }),
    ),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.type(screen.getByLabelText("Your translation"), " Mine.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED);
  expect(accepted).toEqual([]);
});

it("fetches the sidebar's list of sets again after a review action", async () => {
  let lists = 0;
  server.use(
    http.get("*/api/projects", () => {
      lists += 1;
      return HttpResponse.json([]);
    }),
  );
  const { user, queryClient } = renderPanel();
  // the sidebar is what watches the list; an observer stands in for it
  const watcher = new QueryObserver(queryClient, {
    queryKey: projectKeys.all,
    queryFn: async () => (await fetch("/api/projects")).json(),
  });
  const stop = watcher.subscribe(() => undefined);
  await waitFor(() => expect(lists).toBe(1));
  await user.click(await screen.findByRole("button", { name: "It's fine" }));
  await waitFor(() => expect(lists).toBe(2));
  expect(await screen.findByRole("status")).toHaveTextContent(CHECKED);
  stop();
});

it("fetches the set's files again after a review action, so each language's count to check moves", async () => {
  // CGL-73: the language tabs read "N to check" from the documents list
  let lists = 0;
  server.use(
    http.get("*/api/projects/p-1/documents", () => {
      lists += 1;
      const toCheck = lists === 1 ? 1 : 0;
      return HttpResponse.json([
        { ...documentSummary, runs: [{ ...documentRun, to_check: toCheck }] },
      ]);
    }),
  );
  const { user, queryClient } = renderPanel();
  // the set page is what watches the list; an observer stands in for it
  const watcher = new QueryObserver(queryClient, {
    queryKey: projectKeys.documents("p-1"),
    queryFn: async () => (await fetch("/api/projects/p-1/documents")).json(),
  });
  const stop = watcher.subscribe(() => undefined);
  await waitFor(() => expect(lists).toBe(1));
  await user.click(await screen.findByRole("button", { name: "It's fine" }));
  await waitFor(() => expect(lists).toBe(2));
  expect(queryClient.getQueryData(projectKeys.documents("p-1"))).toEqual([
    { ...documentSummary, runs: [{ ...documentRun, to_check: 0 }] },
  ]);
  stop();
});

it("keeps a half-typed edit when the passage is fetched again with new text", async () => {
  const { user, queryClient } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  const box = screen.getByLabelText("Your translation");
  await user.type(box, " Mine.");
  const typed = (box as HTMLTextAreaElement).value;
  server.use(
    http.get("*/api/runs/r-1/spans/s-2", () =>
      HttpResponse.json({ ...spanDetail, text: "Changed elsewhere." }),
    ),
  );
  await queryClient.invalidateQueries();
  // the refetched text is on screen once every other version is offered
  await screen.findByRole("button", { name: "Use version 2" });
  expect(screen.getByLabelText("Your translation")).toHaveValue(typed);
});

it("focuses the text box on Edit, and the Edit button again after Cancel or Save", async () => {
  server.use(http.post("*/api/runs/r-1/spans/s-2/edit", () => HttpResponse.json(resolved)));
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Your translation")).toHaveFocus();
  await user.click(screen.getByRole("button", { name: "Cancel edit" }));
  expect(screen.getByRole("button", { name: "Edit" })).toHaveFocus();
  await user.click(screen.getByRole("button", { name: "Edit" }));
  await user.type(screen.getByLabelText("Your translation"), " Mine.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "Edit" })).toHaveFocus());
});

it("gives the edit and the dismiss-reason buttons different names", async () => {
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByText("More details"));
  await user.click(
    screen.getByRole("button", { name: "Not a problem: The meaning may have shifted" }),
  );
  for (const name of ["Save edit", "Cancel edit", "Save reason", "Cancel reason"]) {
    expect(screen.getAllByRole("button", { name })).toHaveLength(1);
  }
  expect(screen.getByRole("button", { name: "Save edit" })).toHaveTextContent("Save");
});

it("turns Use, Save and It's fine off while the follow-up check is in flight", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/use-candidate", () =>
      HttpResponse.json({ ...spanDetail, text: "The cat is asleep. It rains." }),
    ),
    http.post("*/api/runs/r-1/spans/s-2/accept", async () => {
      await gate;
      return HttpResponse.json(resolved);
    }),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.type(screen.getByLabelText("Your translation"), " Mine.");
  expect(screen.getByRole("button", { name: "Save edit" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Use version 1" }));
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "It's fine" })).toBeDisabled());
  expect(screen.getByRole("button", { name: "Use version 1" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save edit" })).toBeDisabled();
  release();
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED);
});

it("turns Cancel off while a save and its follow-up check are in flight", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/edit", () =>
      HttpResponse.json({ ...spanDetail, text: "The cat is asleep." }),
    ),
    http.post("*/api/runs/r-1/spans/s-2/accept", async () => {
      await gate;
      return HttpResponse.json({ ...resolved, text: "The cat is asleep." });
    }),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.type(screen.getByLabelText("Your translation"), " Mine.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  await vi.waitFor(() =>
    expect(screen.getByRole("button", { name: "Cancel edit" })).toBeDisabled(),
  );
  release();
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED);
});

it("shows the server's new text, not the old draft, when editing again after a save", async () => {
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/edit", () =>
      HttpResponse.json({ ...resolved, text: "Saved by the server." }),
    ),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.type(screen.getByLabelText("Your translation"), " Mine.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "Edit" })).toHaveFocus());
  await user.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Your translation")).toHaveValue("Saved by the server.");
});

it("It's fine marks the passage checked and moves on", async () => {
  const { user, onNext } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "It's fine" }));
  await vi.waitFor(() => expect(onNext).toHaveBeenCalled());
  expect(accepted).toEqual(["s-2"]);
  expect(screen.getByRole("status")).toHaveTextContent(CHECKED);
});

it("Always translate saves a fixed name to the guide and rechecks the set", async () => {
  let added: unknown;
  const swept: string[] = [];
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/guide", async ({ request }) => {
      added = await request.json();
      return HttpResponse.json(guideView);
    }),
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])),
    http.post("*/api/runs/:runId/sweep", ({ params }) => {
      swept.push(params["runId"] as string);
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const { user } = renderPanel("Marie");
  const box = await screen.findByRole("region", { name: "Always translate" });
  expect(within(box).getByLabelText("Always translate")).toHaveValue("Marie");
  expect(within(box).getByLabelText("Always translate")).toHaveAttribute("lang", "fr");
  await user.type(within(box).getByLabelText("as"), "Mary");
  await user.click(within(box).getByLabelText("This is a name (person, place, organisation…)"));
  await user.click(within(box).getByRole("button", { name: "Apply to all" }));
  expect(await within(box).findByRole("status")).toHaveTextContent(
    "Added to the translation guide. Rechecking 1 file…",
  );
  expect(added).toMatchObject({
    entity: { name: "Marie", rendering: "Mary", kind: "person", locked: true },
  });
  expect(swept).toEqual([runSummary.id]);
  expect(within(box).getByLabelText("Always translate")).toHaveValue("");
});

it.each([
  ["Place", "place"],
  ["Organisation", "organization"],
  ["Other", "other"],
])("Always translate sends a name as kind %s", async (label, kind) => {
  let added: unknown;
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/guide", async ({ request }) => {
      added = await request.json();
      return HttpResponse.json(guideView);
    }),
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])),
    http.post("*/api/runs/:runId/sweep", () =>
      HttpResponse.json({ message: "ok", progress }, { status: 202 }),
    ),
  );
  const { user } = renderPanel();
  const box = await screen.findByRole("region", { name: "Always translate" });
  const kindBox = within(box).getByLabelText("Kind of name");
  expect(kindBox).toBeDisabled();
  expect(kindBox).toHaveValue("person");
  await user.type(within(box).getByLabelText("Always translate"), "Lyon");
  await user.type(within(box).getByLabelText("as"), "Lyons");
  await user.click(within(box).getByLabelText("This is a name (person, place, organisation…)"));
  await user.selectOptions(kindBox, label);
  await user.click(within(box).getByRole("button", { name: "Apply to all" }));
  await within(box).findByRole("status");
  expect(added).toMatchObject({ entity: { name: "Lyon", kind, locked: true } });
  expect(kindBox).toHaveValue("person");
});

it("Always translate says when nothing could be rechecked", async () => {
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/guide", () => HttpResponse.json(guideView)),
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([{ ...documentSummary, runs: [{ ...documentRun, state: "translating" }] }]),
    ),
  );
  const { user } = renderPanel();
  const box = await screen.findByRole("region", { name: "Always translate" });
  await user.type(within(box).getByLabelText("Always translate"), "chat");
  await user.type(within(box).getByLabelText("as"), "cat");
  await user.click(within(box).getByRole("button", { name: "Apply to all" }));
  expect(await within(box).findByRole("status")).toHaveTextContent(
    "Added to the translation guide. New translations in this set will follow it.",
  );
});

function twoFiles(failing: string[]) {
  const doc = (id: string, runId: string) => ({
    ...documentSummary,
    id,
    runs: [{ ...documentRun, id: runId, state: "complete" as const }],
  });
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([doc("d-a", "r-1"), doc("d-b", "r-2")]),
    ),
    http.post("*/api/runs/:runId/sweep", ({ params }) =>
      failing.includes(params["runId"] as string)
        ? HttpResponse.json({}, { status: 500 })
        : HttpResponse.json({ message: "ok", progress }, { status: 202 }),
    ),
  );
}

async function applyAlways(user: ReturnType<typeof renderPanel>["user"]) {
  const box = await screen.findByRole("region", { name: "Always translate" });
  await user.type(within(box).getByLabelText("Always translate"), "chat");
  await user.type(within(box).getByLabelText("as"), "cat");
  await user.click(within(box).getByRole("button", { name: "Apply to all" }));
  return box;
}

it("Always translate says which files could not be rechecked", async () => {
  server.use(http.post("*/api/runs/r-1/spans/s-2/guide", () => HttpResponse.json(guideView)));
  twoFiles(["r-1"]);
  const { user } = renderPanel();
  const box = await applyAlways(user);
  expect(await within(box).findByRole("status")).toHaveTextContent(
    "Added to the translation guide. Rechecking 1 file… The recheck couldn't start for 1 file.",
  );
});

it("Always translate says so when no recheck could start", async () => {
  server.use(http.post("*/api/runs/r-1/spans/s-2/guide", () => HttpResponse.json(guideView)));
  twoFiles(["r-1", "r-2"]);
  const { user } = renderPanel();
  const box = await applyAlways(user);
  expect(await within(box).findByRole("status")).toHaveTextContent(
    "Added to the translation guide, but the recheck couldn't start.",
  );
});

it("keeps scores and flag history under More details", async () => {
  let change: unknown;
  server.use(
    http.post("*/api/runs/r-1/flags/:flagId", async ({ request }) => {
      change = await request.json();
      return HttpResponse.json(spanDetail);
    }),
  );
  const { user } = renderPanel();
  const more = (await screen.findByText("More details")).closest("details")!;
  expect(more).toHaveTextContent("Meaning match 55 / 100 · Style match 70 / 100");
  const flags = within(more).getByRole("list", { name: "Flags" });
  expect(flags).toHaveTextContent("The meaning may have shifted: to check");
  await user.click(within(more).getByText("More details"));
  await user.click(
    within(flags).getByRole("button", { name: "Not a problem: The meaning may have shifted" }),
  );
  await user.type(within(more).getByLabelText("Why isn't it a problem?"), "the tense is right");
  await user.click(within(more).getByRole("button", { name: "Save reason" }));
  await vi.waitFor(() =>
    expect(change).toEqual({ status: "dismissed", reason: "the tense is right" }),
  );
});

it("drops a refused version's error once another action succeeds (CGL-45)", async () => {
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/use-candidate", () =>
      HttpResponse.json({ detail: "busy" }, { status: 503 }),
    ),
  );
  const { user } = renderPanel();
  const versions = await screen.findByRole("region", { name: "Better versions" });
  await user.click(within(versions).getByRole("button", { name: "Use version 1" }));
  expect(await within(versions).findByRole("alert")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "It's fine" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHECKED);
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

it("drops a passage-changed message once the reviewer saves an edit (CGL-45, CGL-36)", async () => {
  server.use(
    http.post("*/api/runs/r-1/spans/s-2/use-candidate", () => changed("Their cat naps.")),
    http.post("*/api/runs/r-1/spans/s-2/edit", () =>
      HttpResponse.json({ ...spanDetail, text: "My cat sleeps." }),
    ),
  );
  const { user } = renderPanel();
  const versions = await screen.findByRole("region", { name: "Better versions" });
  await user.click(within(versions).getByRole("button", { name: "Use version 1" }));
  expect(await within(versions).findByRole("alert")).toHaveTextContent("Their cat naps.");
  await user.click(screen.getByRole("button", { name: "Edit" }));
  const box = screen.getByLabelText("Your translation");
  await user.clear(box);
  await user.type(box, "My cat sleeps.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHANGED);
  // the passage no longer reads "Their cat naps.", so the message would be wrong
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

it("keeps an unsaved edit's clash when another action succeeds (CGL-45, CGL-36)", async () => {
  server.use(http.post("*/api/runs/r-1/spans/s-2/edit", () => changed("Their cat naps.")));
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.type(screen.getByLabelText("Your translation"), " Mine.");
  await user.click(screen.getByRole("button", { name: "Save edit" }));
  const clash = await screen.findByRole("region", { name: "Someone else changed this passage" });
  await user.click(screen.getByRole("button", { name: "It's fine" }));
  expect(await screen.findByRole("status")).toHaveTextContent(CHECKED);
  expect(clash).toBeInTheDocument();
  expect(clash).toHaveTextContent("Their cat naps.");
  expect(screen.getByLabelText("Your translation")).toHaveValue(`${spanDetail.text} Mine.`);
});

it("closes", async () => {
  const { user, onClose } = renderPanel();
  await user.click(await screen.findByRole("button", { name: "Close the passage" }));
  expect(onClose).toHaveBeenCalled();
});
