import { fireEvent, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { runKeys } from "../api/runs";
import type { Guide } from "../api/types";
import { documentSummary, guide, guideView, progress, runDetail } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { GuidePanel } from "./GuidePanel";
import type { GuideMode } from "./mode";

function renderPanel(mode: GuideMode = { kind: "project" }, runId?: string) {
  const onClose = vi.fn();
  const view = renderAt(
    <GuidePanel projectId="p-1" targetLang="en" mode={mode} runId={runId} onClose={onClose} />,
  );
  return { ...view, onClose };
}

let saved: { guide: Guide; base_version: number | null } | undefined;

beforeEach(() => {
  saved = undefined;
  server.use(
    http.get("*/api/projects/p-1/guide", () => HttpResponse.json(guideView)),
    http.put("*/api/projects/p-1/guide", async ({ request }) => {
      saved = (await request.json()) as typeof saved;
      return HttpResponse.json({ guide: saved!.guide, info: { ...guideView.info, version: 2 } });
    }),
  );
});

it("lists names and terms as readable rows", async () => {
  const { user } = renderPanel();
  const panel = await screen.findByRole("dialog", { name: "Translation guide" });
  const names = await within(panel).findByRole("list", { name: "Names" });
  expect(names).toHaveTextContent("Marie → Marie · always");
  await user.click(within(panel).getByRole("tab", { name: "Terms" }));
  expect(within(panel).getByRole("list", { name: "Terms" })).toHaveTextContent("chat → cat");
});

it("edits a name, saves it, and offers to update the translations", async () => {
  const swept: string[] = [];
  server.use(
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])),
    http.post("*/api/runs/:runId/sweep", ({ params }) => {
      swept.push(params["runId"] as string);
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  const form = screen.getByRole("group", { name: "Name 1" });
  await user.clear(within(form).getByLabelText("Translation"));
  await user.type(within(form).getByLabelText("Translation"), "Mary");
  await user.click(within(form).getByText("More"));
  await user.clear(within(form).getByLabelText("Forms of address"));
  await user.type(within(form).getByLabelText("Forms of address"), "usted");
  await user.click(within(form).getByRole("button", { name: "Done" }));
  expect(screen.getByRole("list", { name: "Names" })).toHaveTextContent("Marie → Mary · always");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Saved. Update the translations to match?")).toBeInTheDocument();
  expect(saved?.base_version).toBe(1);
  expect(saved?.guide.entities[0]).toMatchObject({ rendering: "Mary", forms_of_address: "usted" });
  await user.click(screen.getByRole("button", { name: "Update the translations" }));
  expect(await screen.findByText("Rechecking 1 file…")).toBeInTheDocument();
  expect(swept).toEqual(["r-1"]);
});

it("adds a term and will not save it without a translation", async () => {
  const { user } = renderPanel();
  await screen.findByRole("list", { name: "Names" });
  await user.click(screen.getByRole("tab", { name: "Terms" }));
  await user.click(screen.getByRole("button", { name: "+ Add a term" }));
  const form = screen.getByRole("group", { name: "Term 2" });
  await user.type(within(form).getByLabelText("Term"), "fenêtre");
  expect(screen.getByText("Every term needs a translation.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  await user.type(within(form).getByLabelText("Translation"), "window");
  await user.click(within(form).getByLabelText("Always use this translation"));
  await user.click(screen.getByRole("button", { name: "Save" }));
  await vi.waitFor(() =>
    expect(saved?.guide.glossary[1]).toEqual({
      term: "fenêtre",
      rendering: "window",
      notes: "",
      locked: true,
    }),
  );
});

it("removes a name", async () => {
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.click(
    within(screen.getByRole("group", { name: "Name 1" })).getByRole("button", { name: "Remove" }),
  );
  expect(screen.getByText("No names yet.")).toBeInTheDocument();
});

it("edits the style fields, one cultural reference per line", async () => {
  const { user } = renderPanel();
  await screen.findByRole("list", { name: "Names" });
  await user.click(screen.getByRole("tab", { name: "Style" }));
  const style = screen.getByLabelText("Style guide");
  await user.clear(style);
  await user.type(style, "Warm and plain.");
  const culture = screen.getByLabelText("Cultural references (one per line)");
  await user.type(culture, "{Enter}le 14 juillet{Enter}");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await vi.waitFor(() => expect(saved?.guide.style_guide).toBe("Warm and plain."));
  expect(saved?.guide.cultural_references).toEqual([...guide.cultural_references, "le 14 juillet"]);
});

it("explains a conflict and reloads the current guide", async () => {
  let reads = 0;
  server.use(
    http.get("*/api/projects/p-1/guide", () => {
      reads += 1;
      const rendering = reads === 1 ? "Marie" : "Maria";
      return HttpResponse.json({
        ...guideView,
        guide: { ...guide, entities: [{ ...guide.entities[0]!, rendering }] },
        info: { ...guideView.info, version: reads },
      });
    }),
    http.put("*/api/projects/p-1/guide", () =>
      HttpResponse.json({ detail: "the guide changed", code: "guide_changed" }, { status: 409 }),
    ),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.type(
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"),
    "x",
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(
    await screen.findByText("Someone else changed the guide. Reload to see their changes."),
  ).toBeInTheDocument();
  // The edit is still there until the person chooses to reload.
  expect(
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"),
  ).toHaveValue("Mariex");
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await vi.waitFor(() =>
    expect(screen.getByRole("list", { name: "Names" })).toHaveTextContent("Marie → Maria · always"),
  );
});

it("sends the newest version on each save", async () => {
  const bases: (number | null)[] = [];
  server.use(
    http.put("*/api/projects/p-1/guide", async ({ request }) => {
      const body = (await request.json()) as { guide: Guide; base_version: number | null };
      bases.push(body.base_version);
      return HttpResponse.json({
        guide: body.guide,
        info: { ...guideView.info, version: bases.length + 1 },
      });
    }),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  const field = () =>
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation");
  await user.type(field(), "x");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved. Update the translations to match?");
  await user.type(field(), "y");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await vi.waitFor(() => expect(bases).toEqual([1, 2]));
});

it("saves on top of the reloaded version after a conflict", async () => {
  let reads = 0;
  let puts = 0;
  const bases: (number | null)[] = [];
  server.use(
    http.get("*/api/projects/p-1/guide", () => {
      reads += 1;
      return HttpResponse.json({ ...guideView, info: { ...guideView.info, version: reads + 4 } });
    }),
    http.put("*/api/projects/p-1/guide", async ({ request }) => {
      puts += 1;
      const body = (await request.json()) as { guide: Guide; base_version: number | null };
      bases.push(body.base_version);
      if (puts === 1) {
        return HttpResponse.json({ detail: "changed", code: "guide_changed" }, { status: 409 });
      }
      return HttpResponse.json({ guide: body.guide, info: { ...guideView.info, version: 9 } });
    }),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  const field = () =>
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation");
  await user.type(field(), "x");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await user.click(await screen.findByRole("button", { name: "Reload" }));
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.type(field(), "z");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved. Update the translations to match?");
  // The next save builds on what the previous one returned (9), not on the reload-time version.
  await user.type(field(), "w");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await vi.waitFor(() => expect(bases).toEqual([5, 6, 9]));
});

it("keeps the edits and says so when the reload fails", async () => {
  let reads = 0;
  server.use(
    http.get("*/api/projects/p-1/guide", () => {
      reads += 1;
      return reads === 1
        ? HttpResponse.json(guideView)
        : HttpResponse.json({ detail: "the server fell over" }, { status: 503 });
    }),
    http.put("*/api/projects/p-1/guide", () =>
      HttpResponse.json({ detail: "changed", code: "guide_changed" }, { status: 409 }),
    ),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.type(
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"),
    "x",
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  await user.click(await screen.findByRole("button", { name: "Reload" }));
  expect(await screen.findByText("the server fell over")).toBeInTheDocument();
  expect(
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"),
  ).toHaveValue("Mariex");
  expect(
    screen.getByText("Someone else changed the guide. Reload to see their changes."),
  ).toBeInTheDocument();
});

it("starts a guide when the set has none", async () => {
  server.use(
    http.get("*/api/projects/p-1/guide", () =>
      HttpResponse.json({ detail: "no guide yet" }, { status: 404 }),
    ),
  );
  const { user } = renderPanel();
  expect(await screen.findByText("No names yet.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "+ Add a name" }));
  const form = screen.getByRole("group", { name: "Name 1" });
  await user.type(within(form).getByLabelText("Name"), "Riverside Community Centre");
  await user.type(within(form).getByLabelText("Translation"), "Centro Comunitario Riverside");
  // a summary is optional (CGL-115): a guide of names alone saves
  expect(screen.queryByText(/summary/i)).toBeNull();
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Save" }));
  await vi.waitFor(() => expect(saved?.base_version).toBeNull());
  expect(saved?.guide.summary).toBe("");
});

it("asks before closing with unsaved changes", async () => {
  const { user, onClose } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.type(
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"),
    "x",
  );
  fireEvent(
    screen.getByRole("dialog", { name: "Translation guide" }),
    new Event("cancel", { cancelable: true }),
  );
  const ask = screen.getByRole("alertdialog", { name: "Unsaved changes" });
  await user.click(within(ask).getByRole("button", { name: "Keep editing" }));
  expect(onClose).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Close the translation guide" }));
  await user.click(screen.getByRole("button", { name: "Close without saving" }));
  expect(onClose).toHaveBeenCalled();
});

it("closes at once when nothing changed", async () => {
  const { user, onClose } = renderPanel();
  await screen.findByRole("list", { name: "Names" });
  await user.click(screen.getByRole("button", { name: "Close the translation guide" }));
  expect(onClose).toHaveBeenCalled();
});

it("approves a waiting run's draft guide and closes", async () => {
  let approved: unknown;
  server.use(
    http.get("*/api/runs/r-1/guide", () => HttpResponse.json(guideView)),
    http.post("*/api/runs/r-1/guide/approve", async ({ request }) => {
      approved = await request.json();
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const { user, onClose } = renderPanel({ kind: "draft", runId: "r-1" });
  expect(await screen.findByText(/then start translating/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Save and translate" }));
  await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(approved).toEqual({ guide, base_version: 1 });
});

it("says when the open file's guide wasn't built or was a light one (CGL-80)", async () => {
  const light =
    "Only a shorter translation guide could be built for this file, so some names and terms may vary.";
  const settings =
    "The settings changed while this file waited; the translation used the new ones.";
  const { queryClient } = renderPanel({ kind: "project" }, "r-1");
  const panel = await screen.findByRole("dialog", { name: "Translation guide" });
  await within(panel).findByRole("list", { name: "Names" });
  expect(panel).not.toHaveTextContent("could be built");
  queryClient.setQueryData(runKeys.one("r-1"), {
    ...runDetail,
    problems: [
      { stage: "config", text: settings },
      { stage: "guide", text: light },
    ],
  });
  expect(await within(panel).findByText(light)).toBeInTheDocument();
  expect(panel).not.toHaveTextContent(settings); // not about the guide
});

it("offers a way out while the guide loads and when it cannot be loaded", async () => {
  server.use(
    http.get("*/api/projects/p-1/guide", () =>
      HttpResponse.json({ detail: "guide unavailable" }, { status: 503 }),
    ),
  );
  const { user, onClose } = renderPanel();
  const panel = await screen.findByRole("dialog", { name: "Translation guide" });
  expect(await within(panel).findByRole("alert")).toHaveTextContent("guide unavailable");
  await user.click(within(panel).getByRole("button", { name: "Close the translation guide" }));
  expect(onClose).toHaveBeenCalled();
});

async function saveAndRecheck(failing: string[]) {
  const doc = (id: string, runId: string) => ({
    ...documentSummary,
    id,
    runs: [{ ...documentSummary.runs[0]!, id: runId, state: "complete" as const }],
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
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  const form = screen.getByRole("group", { name: "Name 1" });
  await user.type(within(form).getByLabelText("Translation"), "!");
  await user.click(within(form).getByRole("button", { name: "Done" }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  await user.click(await screen.findByRole("button", { name: "Update the translations" }));
}

it("says how many files could not be rechecked", async () => {
  await saveAndRecheck(["r-2"]);
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Rechecking 1 file… The recheck couldn't start for 1 file.",
  );
});

it("says when no file could be rechecked", async () => {
  await saveAndRecheck(["r-1", "r-2"]);
  expect(await screen.findByRole("status")).toHaveTextContent(
    "The recheck couldn't start for 2 files.",
  );
});

it("clears a failed reload's error once a later save succeeds", async () => {
  let reads = 0;
  let puts = 0;
  server.use(
    http.get("*/api/projects/p-1/guide", () => {
      reads += 1;
      return reads === 1
        ? HttpResponse.json(guideView)
        : HttpResponse.json({ detail: "the server fell over" }, { status: 503 });
    }),
    http.put("*/api/projects/p-1/guide", async ({ request }) => {
      puts += 1;
      if (puts === 1) {
        return HttpResponse.json({ detail: "changed", code: "guide_changed" }, { status: 409 });
      }
      const body = (await request.json()) as { guide: Guide };
      return HttpResponse.json({ guide: body.guide, info: { ...guideView.info, version: 2 } });
    }),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.type(
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"),
    "x",
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  await user.click(await screen.findByRole("button", { name: "Reload" }));
  expect(await screen.findByText("the server fell over")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved. Update the translations to match?");
  expect(screen.queryByText("the server fell over")).not.toBeInTheDocument();
});

it("makes Names, Terms and Style a proper tab set, moved by the arrow keys", async () => {
  const { user } = renderPanel();
  await screen.findByRole("list", { name: "Names" });
  const names = screen.getByRole("tab", { name: "Names" });
  const terms = screen.getByRole("tab", { name: "Terms" });
  const style = screen.getByRole("tab", { name: "Style" });
  expect(names).toHaveAttribute("tabindex", "0");
  expect(terms).toHaveAttribute("tabindex", "-1");
  const panel = screen.getByRole("tabpanel", { name: "Names" });
  expect(names).toHaveAttribute("aria-controls", panel.id);
  expect(panel).toHaveAttribute("aria-labelledby", names.id);
  names.focus();
  await user.keyboard("{ArrowRight}");
  expect(terms).toHaveFocus();
  expect(terms).toHaveAttribute("aria-selected", "true");
  expect(terms).toHaveAttribute("tabindex", "0");
  expect(names).toHaveAttribute("tabindex", "-1");
  expect(screen.getByRole("tabpanel", { name: "Terms" })).toContainElement(
    screen.getByRole("list", { name: "Terms" }),
  );
  await user.keyboard("{End}");
  expect(style).toHaveFocus();
  await user.keyboard("{ArrowRight}"); // wraps
  expect(names).toHaveFocus();
  await user.keyboard("{ArrowLeft}"); // wraps back
  expect(style).toHaveFocus();
  await user.keyboard("{Home}");
  expect(names).toHaveFocus();
  expect(names).toHaveAttribute("aria-selected", "true");
});

it("focuses the least destructive button in the unsaved-changes question and gives focus back", async () => {
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.type(
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"),
    "x",
  );
  const close = screen.getByRole("button", { name: "Close the translation guide" });
  await user.click(close);
  const ask = screen.getByRole("alertdialog", { name: "Unsaved changes" });
  expect(within(ask).getByRole("button", { name: "Keep editing" })).toHaveFocus();
  await user.click(within(ask).getByRole("button", { name: "Keep editing" }));
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(close).toHaveFocus();
});

it("offers Try again for the files whose recheck could not start, and retries only those", async () => {
  const swept: string[] = [];
  let failing = ["r-2"];
  const doc = (id: string, runId: string) => ({
    ...documentSummary,
    id,
    runs: [{ ...documentSummary.runs[0]!, id: runId, state: "complete" as const }],
  });
  server.use(
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json([doc("d-a", "r-1"), doc("d-b", "r-2")]),
    ),
    http.post("*/api/runs/:runId/sweep", ({ params }) => {
      swept.push(params["runId"] as string);
      return failing.includes(params["runId"] as string)
        ? HttpResponse.json({}, { status: 500 })
        : HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.type(
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"),
    "!",
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  await user.click(await screen.findByRole("button", { name: "Update the translations" }));
  await screen.findByText(/couldn't start for 1 file/);
  failing = [];
  await user.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Rechecking 2 files…");
  expect(swept).toEqual(["r-1", "r-2", "r-2"]);
  expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  expect(screen.queryByText(/couldn't start/)).not.toBeInTheDocument();
});

it("keeps the recheck prompt while the saved change is unrechecked, until a newer save replaces it", async () => {
  const { user, onClose } = renderPanel();
  const field = () =>
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation");
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.type(field(), "x");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved. Update the translations to match?");
  await user.type(field(), "y");
  // Editing again does not take the question away, and Save is back for the new edit.
  expect(screen.getByText("Saved. Update the translations to match?")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  // "Not now" with edits under way dismisses the question without closing the panel.
  await user.click(screen.getByRole("button", { name: "Not now" }));
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.queryByText("Saved. Update the translations to match?")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Saved. Update the translations to match?")).toBeInTheDocument();
});

it("treats Esc while the unsaved-changes question is open as Keep editing, and restores focus", async () => {
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  const field = within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation");
  await user.type(field, "x");
  const dialog = screen.getByRole("dialog", { name: "Translation guide" });
  fireEvent(dialog, new Event("cancel", { cancelable: true }));
  const ask = screen.getByRole("alertdialog", { name: "Unsaved changes" });
  expect(within(ask).getByRole("button", { name: "Keep editing" })).toHaveFocus();
  fireEvent(dialog, new Event("cancel", { cancelable: true }));
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(field).toHaveFocus();
});

it("describes the unsaved-changes question and makes the form behind it inert", async () => {
  const { user } = renderPanel();
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.type(
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation"),
    "x",
  );
  const close = screen.getByRole("button", { name: "Close the translation guide" });
  expect(close.closest("[inert]")).toBeNull();
  await user.click(close);
  const ask = screen.getByRole("alertdialog", { name: "Unsaved changes" });
  expect(ask).toHaveAccessibleDescription("You have unsaved changes. Close without saving them?");
  expect(close.closest("[inert]")).not.toBeNull();
  await user.click(within(ask).getByRole("button", { name: "Keep editing" }));
  expect(close.closest("[inert]")).toBeNull();
  expect(close).toHaveFocus();
});

it("still rechecks from the prompt while the form has unsaved edits, and Close then only hides it", async () => {
  const swept: string[] = [];
  server.use(
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])),
    http.post("*/api/runs/:runId/sweep", ({ params }) => {
      swept.push(params["runId"] as string);
      return HttpResponse.json({ message: "ok", progress }, { status: 202 });
    }),
  );
  const { user, onClose } = renderPanel();
  const field = () =>
    within(screen.getByRole("group", { name: "Name 1" })).getByLabelText("Translation");
  await user.click(await screen.findByRole("button", { name: /^Edit Marie\b/ }));
  await user.type(field(), "x");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved. Update the translations to match?");
  await user.type(field(), "y");
  await user.click(screen.getByRole("button", { name: "Update the translations" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Rechecking 1 file…");
  expect(swept).toEqual(["r-1"]);
  await user.click(screen.getByRole("button", { name: "Close" }));
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.queryByText("Saved. Update the translations to match?")).not.toBeInTheDocument();
  expect(field()).toHaveValue("Mariexy");
});
