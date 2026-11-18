import { screen, waitFor, within } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { setEventSourceFactory } from "../api/events";
import { configView, documentSummary, project, tiers } from "../test/fixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

beforeEach(() => setEventSourceFactory(null));
afterEach(() => setEventSourceFactory(undefined));

it("deleting the set you are viewing never asks for it again", async () => {
  let deleted = false;
  let listRefreshed = false;
  const asked: string[] = [];
  const askedAfter: string[] = []; // every GET of the deleted set (or its files, guide…) afterwards
  const watch = ({ request }: { request: Request }) => {
    const { pathname } = new URL(request.url);
    if (deleted && request.method === "GET" && pathname.startsWith("/api/projects/p-1")) {
      askedAfter.push(pathname);
    }
  };
  server.events.on("request:start", watch);
  onTestFinished(() => server.events.removeListener("request:start", watch));
  server.use(
    http.get("*/api/projects", async () => {
      if (deleted) {
        await delay(100); // the sidebar's refresh is slow
        listRefreshed = true;
      }
      return HttpResponse.json(deleted ? [] : [project]);
    }),
    http.get("*/api/projects/p-1/guide", () => {
      asked.push(deleted ? "after" : "before");
      return HttpResponse.json({ detail: "project p-1 not found" }, { status: 404 });
    }),
    http.get("*/api/projects/p-1", () => {
      asked.push(deleted ? "after" : "before");
      return deleted
        ? HttpResponse.json({ detail: "project p-1 not found" }, { status: 404 })
        : HttpResponse.json(project);
    }),
    http.get("*/api/projects/p-1/documents", () => {
      asked.push(deleted ? "after" : "before");
      return deleted
        ? HttpResponse.json({ detail: "project p-1 not found" }, { status: 404 })
        : HttpResponse.json([{ ...documentSummary, runs: [] }]);
    }),
    http.get("*/api/system/config", () => HttpResponse.json(configView)),
    http.get("*/api/system/tiers", () => HttpResponse.json(tiers)),
    http.delete("*/api/projects/p-1", () => {
      deleted = true;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const { user, router } = renderApp("/t/p-1");
  await screen.findByRole("heading", { name: project.name });
  await user.click(await screen.findByRole("button", { name: `More actions for ${project.name}` }));
  await user.click(screen.getByRole("menuitem", { name: "Delete…" }));
  const dialog = screen.getByRole("dialog", { name: "Delete" });
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/"));
  // a refetch of the set would start right after the delete; the list's refresh is the last request
  await waitFor(() => expect(listRefreshed).toBe(true));
  expect(screen.queryByText(/not found/)).not.toBeInTheDocument();
  expect(asked).not.toContain("after");
  expect(askedAfter).toEqual([]);
});
