import { act, screen, waitFor, within } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { setEventSourceFactory } from "../api/events";
import type { DocumentSummary, RunDetail } from "../api/types";
import {
  configView,
  me,
  documentRun,
  documentSummary,
  guideView,
  project,
  reviewSpans,
  runDetail,
  runSummary,
  spanPage,
  tiers,
} from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { nodeFormData } from "../test/node-web";
import { enqueueUploads, resetUploads } from "../upload/queue";
import { DownloadMenu, teamLabel } from "./SetHeader";
import { pickRun, SetPage } from "./SetPage";

vi.mock("../upload/queue", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../upload/queue")>()),
  enqueueUploads: vi.fn(),
}));

const complete = (id: string): RunDetail => ({
  ...runDetail,
  run: { ...runDetail.run, id, state: "complete", finished_at: "2026-10-01T09:05:00Z" },
  progress: { ...runDetail.progress, run_id: id, state: "complete" },
  report_available: true,
  stats: { spans: 3, open_flags: 0, flags_by_code: {}, edited_spans: 0 },
});

function serve(docs: DocumentSummary[]) {
  server.use(
    http.get("*/api/projects/p-1", () => HttpResponse.json(project)),
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json(docs)),
    http.get("*/api/projects/p-1/guide", () => HttpResponse.json(guideView)),
    http.get("*/api/runs/:runId", ({ params }) =>
      HttpResponse.json(complete(params["runId"] as string)),
    ),
    http.get("*/api/runs/:runId/spans", () => HttpResponse.json(spanPage(reviewSpans))),
    http.get("*/api/system/config", () => HttpResponse.json(configView)),
    http.get("*/api/system/tiers", () => HttpResponse.json(tiers)),
  );
}

const second = {
  ...documentSummary,
  id: "d-2",
  filename: "b.txt",
  created_at: "2026-10-02T09:00:00Z",
  runs: [{ ...documentRun, id: "r-2", document_id: "d-2" }],
};

const PATTERN = "/t/:projectId/:runId?";
const open = (path = "/t/p-1") => renderAt(<SetPage />, { path, pattern: PATTERN });

beforeEach(() => {
  setEventSourceFactory(null);
  resetUploads(); // nothing is mounted between tests, so this updates no component
});
afterEach(() => setEventSourceFactory(undefined));

it("opens a single file straight into its document", async () => {
  serve([documentSummary]);
  const { user } = open();
  expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
  expect(screen.getByText(/French → English · finished/)).toBeInTheDocument();
  expect(await screen.findByRole("table", { name: "Document" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Files" })).not.toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "Download" }));
  const menu = screen.getByRole("menu", { name: "Download" });
  const items = within(menu).getAllByRole("menuitem");
  expect(items.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
    ["Word document", "/api/runs/r-1/exports/docx"],
    ["Side-by-side Word file with reviewer notes", "/api/runs/r-1/exports/bilingual-docx"],
    ["Web page", "/api/runs/r-1/exports/html"],
    ["Data file (JSON)", "/api/runs/r-1/exports/json"],
    ["Translation memory (TMX)", "/api/runs/r-1/exports/tmx"],
    [
      "Translation memory, only passages a person checked (TMX)",
      "/api/runs/r-1/exports/tmx?reviewed_only=true",
    ],
    ["Quality report", "/api/runs/r-1/report"],
  ]);
  items.forEach((a) => expect(a).toHaveAttribute("download"));
  expect(within(menu).getByText("Other formats")).toBeInTheDocument();
});

it.each([
  ["Rapport annuel.PPTX", "PowerPoint file", "pptx"],
  ["Budget.xlsx", "Excel file", "xlsx"],
])("offers %s back in its own format first (CGL-110)", async (filename, label, kind) => {
  server.use(
    http.get("*/api/runs/r-9", () => {
      const run = complete("r-9");
      return HttpResponse.json({ ...run, run: { ...run.run, filename } });
    }),
  );
  const { user } = renderAt(<DownloadMenu runId="r-9" />);
  await user.click(await screen.findByRole("button", { name: "Download" }));
  const items = within(screen.getByRole("menu", { name: "Download" })).getAllByRole("menuitem");
  expect(items.slice(0, 2).map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
    [label, `/api/runs/r-9/exports/${kind}`],
    ["Word document", "/api/runs/r-9/exports/docx"],
  ]);
  expect(items[0]).toHaveAttribute("download");
});

const ITEM_ROLES = ["menuitem", "menuitemcheckbox", "menuitemradio"];
const isNone = (el: Element) => ["none", "presentation"].includes(el.getAttribute("role") ?? "");

/** ARIA 1.2: a menu owns group, menuitem* and separator; a `none` element's children count as its parent's. */
function ownedBy(el: Element): Element[] {
  return Array.from(el.children).flatMap((child) => (isNone(child) ? ownedBy(child) : [child]));
}

function expectAllowedMenuChildren(menu: HTMLElement) {
  for (const child of ownedBy(menu)) {
    const role = child.getAttribute("role") ?? "";
    expect([...ITEM_ROLES, "group", "separator"], child.outerHTML).toContain(role);
    if (role === "group") {
      expect(
        child.getAttribute("aria-label") || child.getAttribute("aria-labelledby"),
      ).toBeTruthy();
      for (const inner of ownedBy(child)) {
        expect(ITEM_ROLES, inner.outerHTML).toContain(inner.getAttribute("role") ?? "");
      }
    }
  }
}

it("keeps every direct child of the Download menu to roles ARIA allows", async () => {
  serve([documentSummary]);
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "Download" }));
  const menu = screen.getByRole("menu", { name: "Download" });
  expectAllowedMenuChildren(menu);
  const group = within(menu).getByRole("group", { name: "Other formats" });
  expect(within(group).getAllByRole("menuitem")).toHaveLength(4);
});

it("keeps every direct child of the More actions menu to roles ARIA allows", async () => {
  const doc = { ...documentSummary, runs: [{ ...documentRun, id: "r-new" }, documentRun] };
  serve([doc]);
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  const menu = screen.getByRole("menu", { name: "More actions" });
  expectAllowedMenuChildren(menu);
  const group = within(menu).getByRole("group", { name: "Earlier versions" });
  expect(within(group).getAllByRole("menuitem")).toHaveLength(1);
});

it("shows a tab per file, in upload order", async () => {
  const later = {
    ...documentSummary,
    id: "d-2",
    filename: "b.txt",
    created_at: "2026-10-02T09:00:00Z",
    runs: [{ ...documentRun, id: "r-2", document_id: "d-2" }],
  };
  serve([later, documentSummary]);
  const { user, router } = open();
  const tabs = await screen.findByRole("navigation", { name: "Files" });
  expect(
    within(tabs)
      .getAllByRole("link")
      .map((l) => l.textContent),
  ).toEqual(["histoire.txt✓ ready", "b.txt✓ ready"]);
  await user.click(within(tabs).getByRole("link", { name: /b\.txt/ }));
  expect(router.state.location.pathname).toBe("/t/p-1/r-2");
});

it("marks an earlier version and lists versions in the ⋯ menu", async () => {
  const doc = { ...documentSummary, runs: [{ ...documentRun, id: "r-new" }, documentRun] };
  serve([doc]);
  const { user } = open("/t/p-1/r-1");
  const note = await screen.findByText(/An earlier version\./);
  expect(within(note).getByRole("link", { name: "See the latest" })).toHaveAttribute(
    "href",
    "/t/p-1/r-new",
  );
  await user.click(screen.getByRole("button", { name: "More actions" }));
  expect(screen.getByRole("menu", { name: "More actions" })).toHaveTextContent("Earlier versions");
});

it("translates the file again from the ⋯ menu", async () => {
  let sent: unknown;
  serve([documentSummary]);
  server.use(
    http.post("*/api/documents/d-1/runs", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...runSummary, id: "r-9", state: "created" }, { status: 202 });
    }),
  );
  const { user, router } = open();
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Translate again" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-1/r-9"));
  expect(sent).toEqual({ checkpoint: false, use_project_guide: false, target_lang: "en" });
});

it("does not offer Translate again while any file of the set is still running", async () => {
  let posted = false;
  server.use(
    http.post("*/api/documents/:id/runs", () => {
      posted = true;
      return HttpResponse.json(runSummary, { status: 202 });
    }),
  );
  const running = {
    ...second,
    runs: [{ ...second.runs[0]!, state: "translating" as const, finished_at: null }],
  };
  serve([documentSummary, running]);
  server.use(
    http.get("*/api/runs/r-2", () => HttpResponse.json({ ...runDetail, run: running.runs[0]! })),
  );
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  const item = screen.getByRole("menuitem", { name: "Translate again" });
  // aria-disabled, not disabled, so a keyboard user can still reach it and hear the reason
  expect(item).toHaveAttribute("aria-disabled", "true");
  expect(item).not.toBeDisabled();
  item.focus();
  expect(item).toHaveFocus();
  await user.click(item);
  expect(screen.getByRole("menu", { name: "More actions" })).toBeInTheDocument(); // not closed
  expect(posted).toBe(false);
  expectAllowedMenuChildren(screen.getByRole("menu", { name: "More actions" }));
  // a plain note, not the upper-case menu heading style
  const note = screen.getByText("Available when this translation finishes");
  expect(note).toHaveClass("menu-note");
  expect(note).not.toHaveClass("menu-heading");
  expect(item).toHaveAccessibleDescription("Available when this translation finishes");
});

it("a set with no files offers Add files", async () => {
  serve([]);
  const { user } = open();
  expect(await screen.findByText("No files yet.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "+ Add files" }));
  const dialog = screen.getByRole("dialog", { name: "Add files" });
  expect(dialog).toHaveTextContent("Translating into English.");
  await user.upload(within(dialog).getByLabelText("Choose files"), [new File(["x"], "c.txt")]);
  await user.click(within(dialog).getByRole("button", { name: "Add and translate" }));
  expect(enqueueUploads).toHaveBeenCalledWith(
    "p-1",
    [expect.objectContaining({ name: "c.txt" })],
    { target_lang: "en", source_lang: "fr", pipeline_profile: null },
    expect.anything(),
  );
  expect(screen.queryByRole("dialog", { name: "Add files" })).not.toBeInTheDocument();
});

it("offers Add files from the More actions menu too", async () => {
  serve([documentSummary]);
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  await user.click(screen.getByRole("menuitem", { name: /Add files/ }));
  expect(screen.getByRole("dialog", { name: "Add files" })).toBeInTheDocument();
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

it("asks for a language when the set has none", async () => {
  serve([]);
  server.use(
    http.get("*/api/projects/p-1", () =>
      HttpResponse.json({ ...project, target_lang: null, languages: [] }),
    ),
  );
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "+ Add files" }));
  const dialog = screen.getByRole("dialog", { name: "Add files" });
  expect(within(dialog).getByLabelText("To")).toBeInTheDocument();
});

it("renames from the title and opens the translation guide", async () => {
  serve([documentSummary]);
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "Rename Atlas" }));
  expect(screen.getByRole("dialog", { name: "Rename" })).toBeInTheDocument();
  await user.click(
    within(screen.getByRole("dialog", { name: "Rename" })).getByRole("button", { name: "Cancel" }),
  );
  await user.click(await screen.findByRole("button", { name: "Translation guide (2)" }));
  expect(await screen.findByRole("dialog", { name: "Translation guide" })).toBeInTheDocument();
});

it("says once that the address names a file that is not part of the set, and picks none", async () => {
  serve([documentSummary, second]);
  open("/t/p-1/r-zzz");
  expect(await screen.findByText("This file isn't part of this translation.")).toBeInTheDocument();
  expect(screen.getAllByText(/isn't part of this translation/)).toHaveLength(1);
  const tabs = screen.getByRole("navigation", { name: "Files" });
  expect(within(tabs).getAllByRole("link")).toHaveLength(2);
  expect(tabs.querySelector("[aria-current]")).toBeNull();
  expect(screen.queryByRole("table", { name: "Document" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Download" })).not.toBeInTheDocument();
});

it("shows only that notice for an unknown file in a set with no files", async () => {
  serve([]);
  open("/t/p-1/r-zzz");
  expect(await screen.findByText("This file isn't part of this translation.")).toBeInTheDocument();
  expect(screen.queryByText("No files yet.")).not.toBeInTheDocument();
});

it("lets a file with no runs be selected and says it has not started", async () => {
  const empty = { ...second, runs: [] };
  serve([documentSummary, empty]);
  const { user, router } = open();
  await screen.findByRole("table", { name: "Document" });
  const tabs = screen.getByRole("navigation", { name: "Files" });
  await user.click(within(tabs).getByRole("link", { name: /b\.txt/ }));
  expect(router.state.location.search).toBe("?file=d-2&lang=en");
  expect(await screen.findByText("This file hasn't started yet.")).toBeInTheDocument();
  expect(within(tabs).getByRole("link", { name: /b\.txt/ })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.queryByRole("table", { name: "Document" })).not.toBeInTheDocument();
  expect(screen.queryByText("No files yet.")).not.toBeInTheDocument();
});

it("shows Loading, not a blank body, while the files load for a file in the address", async () => {
  serve([documentSummary]);
  server.use(
    http.get("*/api/projects/p-1/documents", async () => {
      await delay(100);
      return HttpResponse.json([documentSummary]);
    }),
  );
  open("/t/p-1/r-1");
  expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Loading…");
  expect(await screen.findByRole("table", { name: "Document" })).toBeInTheDocument();
});

it("shows why the set could not be loaded", async () => {
  server.use(
    http.get("*/api/projects/p-1", () =>
      HttpResponse.json({ detail: "project p-1 not found" }, { status: 404 }),
    ),
  );
  server.use(http.get("*/api/projects/p-1/documents", () => HttpResponse.json([])));
  open();
  expect(await screen.findByRole("alert")).toHaveTextContent("project p-1 not found");
});

it("picks the run to show", () => {
  const doc = { ...documentSummary, runs: [{ ...documentRun, id: "r-new" }, documentRun] };
  expect(pickRun([doc], undefined, null, "en")).toMatchObject({
    run: { id: "r-new" },
    latest: true,
  });
  expect(pickRun([doc], "r-1", null, "en")).toMatchObject({ run: { id: "r-1" }, latest: false });
  expect(pickRun([{ ...documentSummary, runs: [] }], undefined, null, "en")).toMatchObject({
    run: null,
  });
  expect(pickRun([], undefined, null, "en")).toBeNull();
  expect(pickRun([doc], "r-zzz", null, "en")).toBeNull(); // no fallback to the first file
  const empty = { ...documentSummary, id: "d-2", runs: [] };
  expect(pickRun([doc, empty], undefined, "d-2", "en")).toMatchObject({
    doc: { id: "d-2" },
    run: null,
  });
  expect(pickRun([doc, empty], undefined, "d-zzz", "en")).toMatchObject({ doc: { id: "d-1" } });
});

it("offers no download while the file is being rechecked", async () => {
  serve([documentSummary]);
  server.use(
    http.get("*/api/runs/:runId", ({ params }) =>
      HttpResponse.json({
        ...complete(params["runId"] as string),
        run: { ...complete("r-1").run, state: "sweeping" },
        report_available: true,
      }),
    ),
  );
  open();
  expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
  expect(await screen.findByText(/Rechecking against your translation guide/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Download" })).not.toBeInTheDocument();
});

it("never flashes 'No files yet' between an upload finishing and the file being listed", async () => {
  const real = await vi.importActual<typeof import("../upload/queue")>("../upload/queue");
  let uploaded = false;
  server.use(
    http.get("*/api/projects/p-1", () => HttpResponse.json(project)),
    http.get("*/api/projects/p-1/guide", () => HttpResponse.json(guideView)),
    http.get("*/api/projects/p-1/documents", async () => {
      if (!uploaded) return HttpResponse.json([]);
      await delay(150); // the refetch after the upload is slow
      return HttpResponse.json([documentSummary]);
    }),
    http.post("*/api/projects/p-1/runs", () => {
      uploaded = true;
      return HttpResponse.json(runSummary, { status: 202 });
    }),
    http.get("*/api/runs/:runId", ({ params }) =>
      HttpResponse.json(complete(params["runId"] as string)),
    ),
    http.get("*/api/runs/:runId/spans", () => HttpResponse.json(spanPage(reviewSpans))),
  );
  const { queryClient } = open();
  expect(await screen.findByText("No files yet.")).toBeInTheDocument();
  await act(async () => {
    real.enqueueUploads(
      "p-1",
      [new File(["x"], "histoire.txt")],
      { target_lang: "en", source_lang: "fr", pipeline_profile: null },
      queryClient,
    );
    await vi.waitFor(() => expect(uploaded).toBe(true));
    await delay(50); // the upload is done; the file is not listed yet
  });
  expect(screen.queryByText("No files yet.")).not.toBeInTheDocument();
  expect(await screen.findByRole("table", { name: "Document" })).toBeInTheDocument();
});

it("shows a queued file as waiting to upload while the one before it uploads", async () => {
  const real = await vi.importActual<typeof import("../upload/queue")>("../upload/queue");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  let posts = 0;
  server.use(
    http.get("*/api/projects/p-1", () => HttpResponse.json(project)),
    http.get("*/api/projects/p-1/guide", () => HttpResponse.json(guideView)),
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([])),
    http.post("*/api/projects/p-1/runs", async () => {
      posts += 1;
      if (posts === 1) await gate; // the first upload stays in flight
      return HttpResponse.json(runSummary, { status: 202 });
    }),
  );
  const { queryClient } = open();
  expect(await screen.findByText("No files yet.")).toBeInTheDocument();
  act(() =>
    real.enqueueUploads(
      "p-1",
      [new File(["x"], "a.txt"), new File(["y"], "b.txt")],
      { target_lang: "en", source_lang: "fr", pipeline_profile: null },
      queryClient,
    ),
  );
  const tabs = await screen.findByRole("navigation", { name: "Files" });
  expect(tabs).toHaveTextContent("a.txt uploading…");
  expect(tabs).toHaveTextContent("b.txt waiting to upload");
  await act(async () => release());
  await waitFor(() => expect(tabs).toHaveTextContent("b.txt uploaded"));
  expect(tabs).toHaveTextContent("a.txt uploaded");
});

it("says an upload is uploaded, and offers Try again, when the files cannot be listed", async () => {
  const real = await vi.importActual<typeof import("../upload/queue")>("../upload/queue");
  let uploaded = false;
  let listable = false;
  server.use(
    http.get("*/api/projects/p-1", () => HttpResponse.json(project)),
    http.get("*/api/projects/p-1/guide", () => HttpResponse.json(guideView)),
    http.get("*/api/projects/p-1/documents", () => {
      if (!uploaded) return HttpResponse.json([]);
      if (!listable) return HttpResponse.json({ detail: "files are unavailable" }, { status: 503 });
      return HttpResponse.json([documentSummary]);
    }),
    http.post("*/api/projects/p-1/runs", () => {
      uploaded = true;
      return HttpResponse.json(runSummary, { status: 202 });
    }),
    http.get("*/api/runs/:runId", ({ params }) =>
      HttpResponse.json(complete(params["runId"] as string)),
    ),
    http.get("*/api/runs/:runId/spans", () => HttpResponse.json(spanPage(reviewSpans))),
  );
  const { queryClient, user } = open();
  expect(await screen.findByText("No files yet.")).toBeInTheDocument();
  await act(async () => {
    real.enqueueUploads(
      "p-1",
      [new File(["x"], "histoire.txt")],
      { target_lang: "en", source_lang: "fr", pipeline_profile: null },
      queryClient,
    );
    await vi.waitFor(() => expect(uploaded).toBe(true));
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("files are unavailable");
  const tabs = screen.getByRole("navigation", { name: "Files" });
  expect(tabs).toHaveTextContent("uploaded");
  expect(tabs).not.toHaveTextContent("uploading…");
  listable = true;
  await user.click(screen.getByRole("button", { name: "Try again: load the files" }));
  expect(await screen.findByRole("table", { name: "Document" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

// ---------------------------------------------------------------- languages (CGL-24)

const bilingualProject = {
  ...project,
  languages: [
    { target_lang: "en", guide_version: 1 },
    { target_lang: "es", guide_version: 1 },
  ],
};
const spanishRun = { ...documentRun, id: "r-es", target_lang: "es" };
const bilingual = { ...documentSummary, runs: [spanishRun, documentRun] };

function serveBilingual(docs: DocumentSummary[] = [bilingual]) {
  serve(docs);
  server.use(http.get("*/api/projects/p-1", () => HttpResponse.json(bilingualProject)));
}

it("pickRun keeps to one language", () => {
  expect(pickRun([bilingual], undefined, null, "en")?.run?.id).toBe("r-1");
  expect(pickRun([bilingual], undefined, null, "es")?.run?.id).toBe("r-es");
  expect(pickRun([bilingual], "r-1", null, "es")).toMatchObject({
    run: { id: "r-1" },
    latest: true,
  });
});

it("shows no language tabs for one language", async () => {
  serve([documentSummary]);
  open();
  expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Languages" })).not.toBeInTheDocument();
});

it("switches language and keeps the file", async () => {
  serveBilingual();
  const { user, router } = open("/t/p-1/r-1");
  const tabs = await screen.findByRole("navigation", { name: "Languages" });
  expect(within(tabs).getByRole("link", { name: /English/ })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(within(tabs).getByRole("link", { name: /Spanish/ })).toHaveTextContent("ready");
  await user.click(within(tabs).getByRole("link", { name: /Spanish/ }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-1/r-es"));
  expect(await screen.findByText(/French → Spanish/)).toBeInTheDocument();
});

it("the run in the address decides the language over ?lang=", async () => {
  serveBilingual();
  open("/t/p-1/r-1?lang=es");
  const tabs = await screen.findByRole("navigation", { name: "Languages" });
  expect(within(tabs).getByRole("link", { name: /English/ })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

it("a language tab without a run of this file opens the language's first file", async () => {
  const onlyEnglish = { ...second, runs: [{ ...documentRun, id: "r-2", document_id: "d-2" }] };
  serveBilingual([bilingual, onlyEnglish]);
  open("/t/p-1/r-2");
  const tabs = await screen.findByRole("navigation", { name: "Languages" });
  expect(within(tabs).getByRole("link", { name: /Spanish/ })).toHaveAttribute(
    "href",
    "/t/p-1/r-es",
  );
});

it("offers to translate a file that is missing in this language", async () => {
  let sent: unknown;
  serveBilingual([bilingual, second]); // second has no Spanish run
  server.use(
    http.post("*/api/documents/d-2/runs", async ({ request }) => {
      sent = await request.json();
      const run = { ...runSummary, id: "r-new", document_id: "d-2", target_lang: "es" };
      return HttpResponse.json(run, { status: 202 });
    }),
  );
  const { user, router } = open("/t/p-1?file=d-2&lang=es");
  expect(
    await screen.findByText("This file hasn't been translated into Spanish yet."),
  ).toBeInTheDocument();
  const files = screen.getByRole("navigation", { name: "Files" });
  expect(within(files).getByRole("link", { name: /b\.txt/ })).toHaveTextContent("not started");
  expect(within(files).getByRole("link", { name: /b\.txt/ })).toHaveAttribute(
    "href",
    "/t/p-1?file=d-2&lang=es",
  );
  await user.click(screen.getByRole("button", { name: "Translate into Spanish" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-1/r-new"));
  expect(sent).toMatchObject({ target_lang: "es" });
});

it("opens the guide of the current language", async () => {
  const asked: (string | null)[] = [];
  serveBilingual();
  server.use(
    http.get("*/api/projects/p-1/guide", ({ request }) => {
      asked.push(new URL(request.url).searchParams.get("target_lang"));
      return HttpResponse.json(guideView);
    }),
  );
  const { user } = open("/t/p-1/r-es");
  await user.click(await screen.findByRole("button", { name: /Translation guide/ }));
  await vi.waitFor(() => expect(asked).toContain("es"));
  expect(asked).not.toContain("en");
});

it("lists earlier versions and translates again in the current language only", async () => {
  let sent: unknown;
  const olderSpanish = { ...spanishRun, id: "r-es0", created_at: "2026-09-01T00:00:00Z" };
  serveBilingual([{ ...bilingual, runs: [spanishRun, olderSpanish, documentRun] }]);
  server.use(
    http.post("*/api/documents/d-1/runs", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...spanishRun, id: "r-es9" }, { status: 202 });
    }),
  );
  const { user } = open("/t/p-1/r-es");
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  const menu = screen.getByRole("menu", { name: "More actions" });
  expect(within(menu).getAllByRole("menuitem", { name: /^Started/ })).toHaveLength(1);
  await user.click(within(menu).getByRole("menuitem", { name: "Translate again" }));
  await vi.waitFor(() => expect(sent).toMatchObject({ target_lang: "es" }));
});

it("adds files in every language of the set", async () => {
  serveBilingual();
  const { user } = open("/t/p-1/r-es");
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  await user.click(screen.getByRole("menuitem", { name: "+ Add files" }));
  const dialog = screen.getByRole("dialog", { name: "Add files" });
  expect(dialog).toHaveTextContent("Translated into English and Spanish.");
  await user.upload(within(dialog).getByLabelText("Choose files"), [new File(["x"], "c.txt")]);
  await user.click(within(dialog).getByRole("button", { name: "Add and translate" }));
  expect(enqueueUploads).toHaveBeenCalledWith(
    "p-1",
    [expect.objectContaining({ name: "c.txt" })],
    { target_lang: "es", source_lang: "fr", pipeline_profile: null },
    expect.anything(),
  );
});

it("says so when a file cannot be started in this language", async () => {
  serveBilingual([bilingual, second]);
  server.use(
    http.post("*/api/documents/d-2/runs", () =>
      HttpResponse.json({ detail: "the server is busy" }, { status: 503 }),
    ),
  );
  const { user } = open("/t/p-1?file=d-2&lang=es");
  await user.click(await screen.findByRole("button", { name: "Translate into Spanish" }));
  expect(await screen.findByText(/the server is busy/)).toBeInTheDocument();
});

it("offers Translate into another language when the set has files", async () => {
  serve([documentSummary]);
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Translate into another language…" }));
  expect(
    screen.getByRole("dialog", { name: "Translate into another language" }),
  ).toBeInTheDocument();
});

it("does not offer another language before the set has files", async () => {
  serve([]);
  server.use(http.get("*/api/projects/p-1", () => HttpResponse.json({ ...project, documents: 0 })));
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  expect(
    screen.queryByRole("menuitem", { name: "Translate into another language…" }),
  ).not.toBeInTheDocument();
});

it("offers another language as soon as the files list one, before the set's count catches up", async () => {
  serve([documentSummary]);
  server.use(http.get("*/api/projects/p-1", () => HttpResponse.json({ ...project, documents: 0 })));
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  expect(
    screen.getByRole("menuitem", { name: "Translate into another language…" }),
  ).toBeInTheDocument();
});

it("a set with no language yet asks for no blank language and shows the first upload's", async () => {
  // CGL-30: a CLI-made set with no default language and no runs
  const { enqueueUploads: upload } =
    await vi.importActual<typeof import("../upload/queue")>("../upload/queue");
  vi.stubGlobal("FormData", await nodeFormData());
  let uploaded = false;
  const asked: (string | null)[] = [];
  const bare = { ...project, target_lang: null, languages: [], documents: 0, runs: 0 };
  const german = { ...documentRun, id: "r-de", target_lang: "de" };
  serve([]);
  server.use(
    http.get("*/api/projects/p-1", () =>
      HttpResponse.json(
        uploaded
          ? { ...bare, languages: [{ target_lang: "de", guide_version: null }], documents: 1 }
          : bare,
      ),
    ),
    http.get("*/api/projects/p-1/documents", () =>
      HttpResponse.json(uploaded ? [{ ...documentSummary, runs: [german] }] : []),
    ),
    http.get("*/api/projects/p-1/guide", ({ request }) => {
      const query = new URL(request.url).searchParams;
      asked.push(query.has("target_lang") ? query.get("target_lang") : null);
      if (query.get("target_lang") === "") {
        return HttpResponse.json({ detail: "target_lang: bad" }, { status: 422 });
      }
      return HttpResponse.json({ detail: "no guide yet" }, { status: 404 });
    }),
    http.post("*/api/projects/p-1/runs", () => {
      uploaded = true;
      return HttpResponse.json(german, { status: 202 });
    }),
  );
  const { queryClient } = open();
  expect(await screen.findByText("No files yet.")).toBeInTheDocument();
  await waitFor(() => expect(asked).not.toHaveLength(0));
  act(() =>
    upload(
      "p-1",
      [new File(["Il pleut."], "a.txt", { type: "text/plain" })],
      { target_lang: "de", source_lang: null, pipeline_profile: null },
      queryClient,
    ),
  );
  expect(await screen.findByText(/French → German/)).toBeInTheDocument();
  expect(screen.queryByText(/hasn't been translated into/)).not.toBeInTheDocument();
  expect(asked).not.toContain("");
  vi.unstubAllGlobals();
});

it("reads ?lang= in the set's own spelling", async () => {
  serveBilingual([{ ...documentSummary, runs: [documentRun] }]);
  open("/t/p-1?lang=ES");
  const tabs = await screen.findByRole("navigation", { name: "Languages" });
  expect(within(tabs).getByRole("link", { name: /Spanish/ })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(
    await screen.findByText("This file hasn't been translated into Spanish yet."),
  ).toBeInTheDocument();
});

// ---------------------------------------------------------------- teams (CGL-26)

it("labels the set's team and lets an admin move it", async () => {
  let sent: unknown;
  serve([documentSummary]);
  server.use(
    http.get("*/api/me", () =>
      HttpResponse.json({ ...me, user: "ana", admin: true, teams: ["desk-a"] }),
    ),
    http.get("*/api/projects/p-1", () => HttpResponse.json({ ...project, team: "desk-a" })),
    http.get("*/api/teams", () => HttpResponse.json(["desk-a", "desk-b"])),
    http.patch("*/api/projects/p-1", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...project, team: "desk-b" });
    }),
  );
  const { user } = open();
  expect(await screen.findByText(/desk-a · French → English/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "More actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Move to team…" }));
  const dialog = screen.getByRole("dialog", { name: "Move to team" });
  const box = within(dialog).getByLabelText("Team");
  expect(box).toHaveValue("desk-a");
  await user.clear(box);
  await user.type(box, "desk-b");
  await user.click(within(dialog).getByRole("button", { name: "Move" }));
  await vi.waitFor(() => expect(sent).toEqual({ team: "desk-b" }));
  await vi.waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "Move to team" })).not.toBeInTheDocument(),
  );
});

it("does not offer Move to team to a translator, nor label a one-team person's sets", async () => {
  serve([documentSummary]);
  server.use(
    http.get("*/api/me", () => HttpResponse.json({ ...me, admin: false, teams: ["desk-a"] })),
  );
  const { user } = open();
  expect(await screen.findByText(/^French → English/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "More actions" }));
  expect(screen.queryByRole("menuitem", { name: "Move to team…" })).not.toBeInTheDocument();
});

it("names whose set it is", () => {
  const admin = { ...me, admin: true, user: "ana", teams: [] };
  expect(teamLabel({ ...project, team: "desk" }, admin)).toBe("desk");
  expect(teamLabel({ ...project, team: null, created_by: "ana" }, admin)).toBe("Only you");
  expect(teamLabel({ ...project, team: null, created_by: "ben" }, admin)).toBe("Personal to ben");
  expect(teamLabel({ ...project, team: null, created_by: null }, admin)).toBe("No team");
  const translator = { ...me, admin: false, teams: ["desk"] };
  expect(teamLabel({ ...project, team: "desk" }, translator)).toBeNull();
  const twoTeams = { ...me, admin: false, user: "ana", teams: ["desk", "other"] };
  expect(teamLabel({ ...project, team: "desk" }, twoTeams)).toBe("desk");
});

it("knows a set is your own when the server would: é and É are different names", () => {
  const jose = { ...me, admin: true, user: "José", teams: [] };
  const personal = { ...project, team: null };
  expect(teamLabel({ ...personal, created_by: "Jose\u0301 " }, jose)).toBe("Only you");
  expect(teamLabel({ ...personal, created_by: "JOSé" }, jose)).toBe("Only you");
  expect(teamLabel({ ...personal, created_by: "JosÉ" }, jose)).toBe("Personal to JosÉ");
  const upper = { ...jose, user: "JOSÉ" };
  expect(teamLabel({ ...personal, created_by: "josé" }, upper)).toBe("Personal to josé");
  expect(teamLabel({ ...personal, created_by: "José\u00a0" }, jose)).toBe("Personal to José\u00a0");
});

it("labels nothing on an install without sign-in, where everyone sees everything", () => {
  expect(teamLabel({ ...project, team: null, created_by: null }, me)).toBeNull(); // me: no sign-in
  expect(teamLabel({ ...project, team: "desk" }, me)).toBeNull();
});

// ---------------------------------------------------------------- review summary (CGL-112)

it("shows administrators the review summary under Technical details", async () => {
  serve([documentSummary]);
  server.use(
    http.get("*/api/projects/p-1/review-summary", () =>
      HttpResponse.json({ project_id: "p-1", languages: [] }),
    ),
  );
  const { user } = open();
  await user.click(await screen.findByText("Technical details"));
  expect(await screen.findByRole("heading", { name: "Review summary" })).toBeInTheDocument();
  expect(await screen.findByText("Nothing to show yet.")).toBeInTheDocument();
});

it("does not show the review summary to a translator", async () => {
  serve([documentSummary]);
  server.use(
    http.get("*/api/me", () => HttpResponse.json({ ...me, admin: false, teams: ["desk-a"] })),
  );
  const { queryClient } = open();
  expect(await screen.findByText(/^French → English/)).toBeInTheDocument();
  await vi.waitFor(() => expect(queryClient.getQueryData(["me"])).toBeDefined());
  expect(screen.queryByText("Technical details")).not.toBeInTheDocument();
});
