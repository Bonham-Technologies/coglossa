import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { project } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { Sidebar } from "./Sidebar";

// "Today" must not depend on when the suite runs (just after midnight, 0.1 h ago was yesterday).
const NOW = new Date("2026-10-16T15:00:00");
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"], now: NOW });
});
afterEach(() => vi.useRealTimers());

const hoursAgo = (h: number) => new Date(NOW.getTime() - h * 3600_000).toISOString();
const sets = [
  { ...project, id: "p-1", name: "Welcome letter", last_activity_at: hoursAgo(0.1), open_flags: 2 },
  { ...project, id: "p-2", name: "Notice", last_activity_at: hoursAgo(24 * 30), open_flags: 0 },
  {
    ...project,
    id: "p-3",
    name: "Volunteer guide",
    last_activity_at: hoursAgo(0.2),
    files_translating: 1,
  },
];

function list(projects: unknown[]) {
  server.use(http.get("*/api/projects", () => HttpResponse.json(projects)));
}

it("lists sets newest first, grouped by date, with a status in words", async () => {
  list(sets);
  renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  const today = await screen.findByRole("region", { name: "Today" });
  const links = within(today).getAllByRole("link");
  expect(links.map((l) => l.textContent)).toEqual([
    "Welcome letter⚠ 2 to check",
    "Volunteer guide… translating…",
  ]);
  expect(links[0]).toHaveAttribute("href", "/t/p-1");
  expect(
    within(screen.getByRole("region", { name: "Earlier" })).getByText("ready"),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "+ New translation" })).toHaveAttribute("href", "/");
  expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
});

it("offers search once there are more than 15 sets", async () => {
  const many = Array.from({ length: 16 }, (_, i) => ({
    ...project,
    id: `p-${i}`,
    name: i === 7 ? "Bishop's letter" : `Set ${i}`,
  }));
  list(many);
  const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  await user.type(
    await screen.findByRole("searchbox", { name: "Search your translations" }),
    "bishop",
  );
  expect(
    screen.getAllByRole("link").filter((l) => l.getAttribute("href")?.startsWith("/t/")),
  ).toHaveLength(1);
});

it("says so when the list of translations cannot be loaded", async () => {
  server.use(
    http.get("*/api/projects", () =>
      HttpResponse.json({ detail: "the database is unreachable" }, { status: 503 }),
    ),
  );
  renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("the database is unreachable");
  expect(screen.queryByText("Your translations will appear here.")).not.toBeInTheDocument();
});

it("says where translations will appear when there are none", async () => {
  renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  expect(await screen.findByText("Your translations will appear here.")).toBeInTheDocument();
});

it("renames a set from its menu", async () => {
  list(sets);
  let sent: unknown;
  server.use(
    http.patch("*/api/projects/p-1", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...sets[0], name: "Letters" });
    }),
  );
  const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "More actions for Welcome letter" }));
  await user.click(screen.getByRole("menuitem", { name: "Rename" }));
  const dialog = screen.getByRole("dialog", { name: "Rename" });
  const field = within(dialog).getByLabelText("Name");
  await user.clear(field);
  await user.type(field, "Letters");
  await user.click(within(dialog).getByRole("button", { name: "Rename" }));
  expect(sent).toEqual({ name: "Letters" });
  expect(screen.queryByRole("dialog", { name: "Rename" })).not.toBeInTheDocument();
});

it("rename conflict shows a plain message", async () => {
  list(sets);
  server.use(
    http.patch("*/api/projects/p-1", () =>
      HttpResponse.json({ detail: "a project named 'Notice' already exists" }, { status: 409 }),
    ),
  );
  const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "More actions for Welcome letter" }));
  await user.click(screen.getByRole("menuitem", { name: "Rename" }));
  const dialog = screen.getByRole("dialog", { name: "Rename" });
  await user.clear(within(dialog).getByLabelText("Name"));
  await user.type(within(dialog).getByLabelText("Name"), "Notice");
  await user.click(within(dialog).getByRole("button", { name: "Rename" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Another translation already has this name.",
  );
});

it("deletes a set after confirming, leaving its page", async () => {
  list(sets);
  let confirm: string | null = null;
  server.use(
    http.delete("*/api/projects/p-1", ({ request }) => {
      confirm = new URL(request.url).searchParams.get("confirm");
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const { user, router } = renderAt(<Sidebar open={false} onNavigate={() => {}} />, {
    path: "/t/p-1",
  });
  await user.click(await screen.findByRole("button", { name: "More actions for Welcome letter" }));
  await user.click(screen.getByRole("menuitem", { name: "Delete…" }));
  const dialog = screen.getByRole("dialog", { name: "Delete" });
  expect(dialog).toHaveTextContent("Delete “Welcome letter”?");
  expect(dialog).toHaveTextContent("its 1 file");
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/"));
  expect(confirm).toBe("Welcome letter");
});

async function deleteSet(user: ReturnType<typeof renderAt>["user"], name: string) {
  await user.click(await screen.findByRole("button", { name: `More actions for ${name}` }));
  await user.click(screen.getByRole("menuitem", { name: "Delete…" }));
  const dialog = screen.getByRole("dialog", { name: "Delete" });
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));
}

describe("focus after a delete", () => {
  // newest first: Welcome letter, Volunteer guide (today), then Notice (earlier)
  function serveList(initial: object[]) {
    let current = initial;
    server.use(
      http.get("*/api/projects", () => HttpResponse.json(current)),
      http.delete("*/api/projects/:id", ({ params }) => {
        current = current.filter((p) => (p as { id: string }).id !== params["id"]);
        return new HttpResponse(null, { status: 204 });
      }),
    );
  }

  it("moves to the next row's menu button", async () => {
    serveList(sets);
    const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
    await deleteSet(user, "Welcome letter");
    await vi.waitFor(() =>
      expect(
        screen.getByRole("button", { name: "More actions for Volunteer guide" }),
      ).toHaveFocus(),
    );
  });

  it("moves to the previous row's menu button when the last row goes", async () => {
    serveList(sets);
    const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
    await deleteSet(user, "Notice");
    await vi.waitFor(() =>
      expect(
        screen.getByRole("button", { name: "More actions for Volunteer guide" }),
      ).toHaveFocus(),
    );
  });

  it("moves to New translation when no rows remain", async () => {
    serveList([sets[0]!]);
    const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
    await deleteSet(user, "Welcome letter");
    await vi.waitFor(() =>
      expect(screen.getByRole("link", { name: "+ New translation" })).toHaveFocus(),
    );
  });

  it("leaves focus alone when the delete is cancelled", async () => {
    serveList(sets);
    const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "More actions for Notice" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete…" }));
    await user.click(
      within(screen.getByRole("dialog", { name: "Delete" })).getByRole("button", {
        name: "Cancel",
      }),
    );
    expect(screen.getByRole("link", { name: "+ New translation" })).not.toHaveFocus();
  });
});

it("mounts one Rename and one Delete dialog for the whole list", async () => {
  list(sets);
  const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  await screen.findByRole("region", { name: "Today" });
  expect(document.querySelectorAll("dialog")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "More actions for Notice" }));
  await user.click(screen.getByRole("menuitem", { name: "Delete…" }));
  expect(screen.getByRole("dialog", { name: "Delete" })).toHaveTextContent("Delete “Notice”?");
});

it("puts focus back on the renamed row's menu button after the list re-sorts", async () => {
  let current: object[] = sets;
  server.use(
    http.get("*/api/projects", () => HttpResponse.json(current)),
    http.patch("*/api/projects/p-2", () => {
      // Renaming bumps the set to the top, into another date group: its row is rebuilt.
      current = [
        { ...sets[1]!, name: "Notices", last_activity_at: hoursAgo(0) },
        sets[0]!,
        sets[2]!,
      ];
      return HttpResponse.json(current[0]!);
    }),
  );
  const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "More actions for Notice" }));
  await user.click(screen.getByRole("menuitem", { name: "Rename" }));
  const dialog = screen.getByRole("dialog", { name: "Rename" });
  await user.clear(within(dialog).getByLabelText("Name"));
  await user.type(within(dialog).getByLabelText("Name"), "Notices");
  await user.click(within(dialog).getByRole("button", { name: "Rename" }));
  await vi.waitFor(() =>
    expect(screen.getByRole("button", { name: "More actions for Notices" })).toHaveFocus(),
  );
});

it("falls back to the list when the renamed row is gone", async () => {
  let current: object[] = sets;
  server.use(
    http.get("*/api/projects", () => HttpResponse.json(current)),
    http.patch("*/api/projects/p-2", () => {
      current = [sets[0]!, sets[2]!]; // no longer listed
      return HttpResponse.json({ ...sets[1]!, name: "Notices" });
    }),
  );
  const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "More actions for Notice" }));
  await user.click(screen.getByRole("menuitem", { name: "Rename" }));
  const dialog = screen.getByRole("dialog", { name: "Rename" });
  await user.clear(within(dialog).getByLabelText("Name"));
  await user.type(within(dialog).getByLabelText("Name"), "Notices");
  await user.click(within(dialog).getByRole("button", { name: "Rename" }));
  await vi.waitFor(() =>
    expect(screen.getByRole("navigation", { name: "Your translations" })).toHaveFocus(),
  );
});

it("warns that deleting a set removes every language", async () => {
  const languages = [
    { target_lang: "en", guide_version: 1 },
    { target_lang: "es", guide_version: 1 },
  ];
  list([{ ...sets[0], languages, documents: 2 }]);
  const { user } = renderAt(<Sidebar open={false} onNavigate={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "More actions for Welcome letter" }));
  await user.click(screen.getByRole("menuitem", { name: "Delete…" }));
  expect(screen.getByRole("dialog", { name: "Delete" })).toHaveTextContent(
    "This deletes the translation in every language, its 2 files, its translation guides",
  );
});
