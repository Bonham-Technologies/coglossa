import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { GlossaryImportPreview, GlossaryImportResult } from "../api/types";
import { guide, guideView } from "../test/fixtures";
import { nodeFormData } from "../test/node-web";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { GuidePanel } from "./GuidePanel";
import { importSummary } from "./TermImport";

const preview: GlossaryImportPreview = {
  format: "csv",
  target_lang: "en",
  source_lang: null,
  base_version: 1,
  new: 2,
  identical: 1,
  conflicts: 1,
  locked_conflicts: 1,
  duplicates: 1,
  skipped_entries: 0,
  terms: [
    {
      line: 2,
      term: "chat",
      rendering: "kitty",
      status: "conflict",
      current_rendering: "cat",
      current_locked: true,
      first_line: null,
    },
    { line: 3, term: "pluie", rendering: "rain", status: "new", current_locked: false },
    { line: 4, term: "vent", rendering: "wind", status: "new", current_locked: false },
    {
      line: 5,
      term: "pluie",
      rendering: "rainfall",
      status: "duplicate",
      current_rendering: "rain",
      current_locked: false,
      first_line: 3,
    },
  ],
};

const imported: GlossaryImportResult = {
  view: {
    guide: {
      ...guide,
      glossary: [
        { term: "chat", rendering: "kitty", notes: "", locked: true },
        { term: "pluie", rendering: "rain", notes: "", locked: true },
        { term: "vent", rendering: "wind", notes: "", locked: true },
      ],
    },
    info: { ...guideView.info, version: 2, status: "approved", author: "user" },
  },
  created_version: true,
  added: 2,
  updated: 1,
  locked: 2,
  kept: 0,
  duplicates: 1,
};

type Sent = Record<string, FormDataEntryValue | null>;
let previewed: Sent[];
let applied: Sent[];

function fields(form: FormData, url: string): Sent {
  const sent: Sent = { target_lang: new URL(url).searchParams.get("target_lang") };
  for (const key of ["file", "base_version", "lock_all", "overwrite_conflicts"]) {
    sent[key] = form.get(key);
  }
  return sent;
}

beforeEach(async () => {
  previewed = [];
  applied = [];
  vi.stubGlobal("FormData", await nodeFormData());
  server.use(
    http.get("*/api/projects/p-1/guide", () => HttpResponse.json(guideView)),
    http.post("*/api/projects/p-1/guide/import/preview", async ({ request }) => {
      previewed.push(fields(await request.formData(), request.url));
      return HttpResponse.json(preview);
    }),
    http.post("*/api/projects/p-1/guide/import", async ({ request }) => {
      applied.push(fields(await request.formData(), request.url));
      return HttpResponse.json(imported);
    }),
  );
});

async function openTerms() {
  const view = renderAt(
    <GuidePanel projectId="p-1" targetLang="en" mode={{ kind: "project" }} onClose={vi.fn()} />,
  );
  await screen.findByRole("list", { name: "Names" });
  await view.user.click(screen.getByRole("tab", { name: "Terms" }));
  return view;
}

const csv = () => new File(["term,translation\nchat,kitty\n"], "terms.csv", { type: "text/csv" });

it("previews a glossary file in plain words, then imports it locked", async () => {
  const { user } = await openTerms();
  const area = screen.getByRole("region", { name: "Import terms" });
  expect(within(area).getByRole("button", { name: "Import terms…" })).toBeEnabled();
  await user.upload(screen.getByLabelText("Choose a glossary file"), csv());

  const summary = await within(area).findByRole("list", { name: "What the file adds" });
  expect(summary).toHaveTextContent("2 new terms to add");
  expect(summary).toHaveTextContent("1 term already in the guide");
  expect(summary).toHaveTextContent(
    "1 term with a different translation in the guide (1 of them always used)",
  );
  expect(summary).toHaveTextContent("1 term listed twice in the file: the first is used");
  expect(within(area).getByRole("list", { name: "Different translations" })).toHaveTextContent(
    "chat: the guide says “cat” (always used), the file says “kitty”",
  );
  expect(previewed).toEqual([
    expect.objectContaining({ target_lang: "en", base_version: null, lock_all: null }),
  ]);
  expect((previewed[0]!["file"] as File).name).toBe("terms.csv");

  await user.click(
    within(area).getByLabelText("Always use the translations of the imported terms"),
  );
  await user.click(
    within(area).getByLabelText(
      "Use the file’s translation where it differs, even for terms the guide always uses",
    ),
  );
  await user.click(within(area).getByRole("button", { name: "Add the terms" }));

  expect(
    await screen.findByText(
      "Added 2 terms and replaced 1 translation. 2 terms will always be used.",
    ),
  ).toBeInTheDocument();
  expect(applied).toEqual([
    expect.objectContaining({
      target_lang: "en",
      base_version: "1",
      lock_all: "true",
      overwrite_conflicts: "true",
    }),
  ]);
  // The form shows the new version, and offers to bring the translations in line
  expect(screen.getByRole("list", { name: "Terms" })).toHaveTextContent("vent → wind");
  expect(screen.getByText("Saved. Update the translations to match?")).toBeInTheDocument();
});

it("imports a file without conflicts, chosen with the button", async () => {
  server.use(
    http.post("*/api/projects/p-1/guide/import/preview", () =>
      HttpResponse.json({
        ...preview,
        format: "tbx",
        identical: 0,
        conflicts: 0,
        locked_conflicts: 0,
        duplicates: 0,
        skipped_entries: 1,
        terms: preview.terms.slice(1, 3),
      }),
    ),
  );
  const { user } = await openTerms();
  const area = screen.getByRole("region", { name: "Import terms" });
  await user.click(within(area).getByRole("button", { name: "Import terms…" }));
  await user.upload(screen.getByLabelText("Choose a glossary file"), csv());
  const summary = await within(area).findByRole("list", { name: "What the file adds" });
  expect(summary).toHaveTextContent("1 entry left out: no term in both languages");
  expect(within(area).queryByRole("list", { name: "Different translations" })).toBeNull();
  expect(within(area).queryByLabelText(/Use the file’s translation/)).toBeNull();
  await user.click(within(area).getByRole("button", { name: "Add the terms" }));
  expect(await screen.findByText(/^Added 2 terms and replaced 1 translation/)).toBeInTheDocument();
  expect(applied).toEqual([expect.objectContaining({ lock_all: "false" })]);
});

it("keeps conflicts unless asked, and has nothing to add when the file adds nothing", async () => {
  server.use(
    http.post("*/api/projects/p-1/guide/import/preview", () =>
      HttpResponse.json({
        ...preview,
        new: 0,
        locked_conflicts: 0,
        terms: [{ ...preview.terms[0]!, current_locked: false }],
      }),
    ),
  );
  const { user } = await openTerms();
  await user.upload(screen.getByLabelText("Choose a glossary file"), csv());
  const area = screen.getByRole("region", { name: "Import terms" });
  const add = await within(area).findByRole("button", { name: "Nothing to add" });
  expect(add).toBeDisabled();
  await user.click(within(area).getByLabelText(/Use the file’s translation where it differs/));
  expect(within(area).getByRole("button", { name: "Add the terms" })).toBeEnabled();
  await user.click(within(area).getByRole("button", { name: "Cancel" }));
  expect(within(area).getByRole("button", { name: "Import terms…" })).toBeInTheDocument();
  expect(applied).toEqual([]);
});

it("won't add the terms over unsaved edits made after the preview", async () => {
  const { user } = await openTerms();
  await user.upload(screen.getByLabelText("Choose a glossary file"), csv());
  const area = screen.getByRole("region", { name: "Import terms" });
  expect(await within(area).findByRole("button", { name: "Add the terms" })).toBeEnabled();
  // An edit after choosing the file: importing would reload the guide and lose it.
  await user.click(screen.getByRole("button", { name: "Remove chat" }));
  expect(within(area).getByRole("button", { name: "Add the terms" })).toBeDisabled();
  expect(within(area).getByText("Save your changes before importing terms.")).toBeInTheDocument();
  expect(applied).toEqual([]);
});

it("names the line of a file that can't be read, and imports nothing", async () => {
  server.use(
    http.post("*/api/projects/p-1/guide/import/preview", () =>
      HttpResponse.json(
        { detail: "line 3 has only one column: each line needs a term, then its translation" },
        { status: 422 },
      ),
    ),
  );
  const { user } = await openTerms();
  await user.upload(screen.getByLabelText("Choose a glossary file"), csv());
  expect(
    await screen.findByText(
      "This file can't be imported: line 3 has only one column: each line needs a term, then its translation",
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Add the terms" })).not.toBeInTheDocument();
  expect(applied).toEqual([]);
});

it("asks to choose the file again when the guide changed since the preview", async () => {
  server.use(
    http.post("*/api/projects/p-1/guide/import", () =>
      HttpResponse.json({ detail: "the guide changed", code: "guide_changed" }, { status: 409 }),
    ),
  );
  const { user } = await openTerms();
  await user.upload(screen.getByLabelText("Choose a glossary file"), csv());
  await user.click(await screen.findByRole("button", { name: "Add the terms" }));
  expect(
    await screen.findByText(
      "Someone else changed the guide while you were importing. Choose the file again.",
    ),
  ).toBeInTheDocument();
});

it("waits for unsaved edits to be saved before importing", async () => {
  const { user } = await openTerms();
  await user.click(screen.getByRole("button", { name: "+ Add a term" }));
  expect(screen.getByRole("button", { name: "Import terms…" })).toBeDisabled();
  expect(screen.getByText("Save your changes before importing terms.")).toBeInTheDocument();
});

it("is not offered on a waiting run's draft guide", async () => {
  server.use(http.get("*/api/runs/r-1/guide", () => HttpResponse.json(guideView)));
  const { user } = renderAt(
    <GuidePanel
      projectId="p-1"
      targetLang="en"
      mode={{ kind: "draft", runId: "r-1" }}
      onClose={vi.fn()}
    />,
  );
  await screen.findByRole("list", { name: "Names" });
  await user.click(screen.getByRole("tab", { name: "Terms" }));
  expect(screen.queryByRole("region", { name: "Import terms" })).not.toBeInTheDocument();
});

it("lists at most twenty conflicts, says what a termbase left out, and shows other errors", async () => {
  const conflicts = Array.from({ length: 22 }, (_, i) => ({
    line: i + 1,
    term: `t${i}`,
    rendering: `new ${i}`,
    status: "conflict" as const,
    current_rendering: `old ${i}`,
    current_locked: false,
  }));
  server.use(
    http.post("*/api/projects/p-1/guide/import/preview", () =>
      HttpResponse.json({
        ...preview,
        format: "tbx",
        new: 0,
        identical: 0,
        conflicts: 22,
        locked_conflicts: 0,
        duplicates: 0,
        skipped_entries: 3,
        terms: conflicts,
      }),
    ),
    http.post("*/api/projects/p-1/guide/import", () =>
      HttpResponse.json({ detail: "internal error" }, { status: 500 }),
    ),
  );
  const { user } = await openTerms();
  await user.upload(
    screen.getByLabelText("Choose a glossary file"),
    new File(["<martif/>"], "base.tbx"),
  );
  const area = screen.getByRole("region", { name: "Import terms" });
  const summary = await within(area).findByRole("list", { name: "What the file adds" });
  expect(summary).toHaveTextContent("3 entries left out: no term in both languages");
  expect(summary).not.toHaveTextContent("already in the guide");
  const listed = within(area).getByRole("list", { name: "Different translations" });
  expect(within(listed).getAllByRole("listitem")).toHaveLength(21);
  expect(listed).toHaveTextContent("and 2 more");
  await user.click(within(area).getByLabelText("Use the file’s translation where it differs"));
  await user.click(within(area).getByRole("button", { name: "Add the terms" }));
  expect(await within(area).findByRole("alert")).not.toHaveTextContent("can't be imported");
});

it("sums up what an import did", () => {
  expect(importSummary({ ...imported, created_version: false })).toBe(
    "Nothing changed: the guide already has these terms.",
  );
  expect(importSummary({ ...imported, added: 1, updated: 0, locked: 0, kept: 2 })).toBe(
    "Added 1 term. 2 terms kept the guide's translation.",
  );
  // Only locking terms the guide already had: no "Added 0 terms".
  expect(importSummary({ ...imported, added: 0, updated: 0, locked: 1, kept: 0 })).toBe(
    "1 term will always be used.",
  );
  expect(importSummary({ ...imported, added: 0, updated: 0, locked: 0, kept: 0 })).toBe(
    "The guide was saved.",
  );
});
