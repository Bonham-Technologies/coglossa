import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setEventSourceFactory } from "./api/events";
import { configView, documentSummary, guideView, project, runDetail, tiers } from "./test/fixtures";
import { renderApp } from "./test/render";
import { server } from "./test/server";

beforeEach(() => {
  setEventSourceFactory(null);
  server.use(
    http.get("*/api/system/tiers", () => HttpResponse.json(tiers)),
    http.get("*/api/system/config", () => HttpResponse.json(configView)),
    http.get("*/api/projects/p-1", () => HttpResponse.json(project)),
    http.get("*/api/projects/p-1/documents", () => HttpResponse.json([documentSummary])),
    http.get("*/api/projects/p-1/guide", () => HttpResponse.json(guideView)),
    http.get("*/api/runs/r-1", () => HttpResponse.json(runDetail)),
  );
});
afterEach(() => setEventSourceFactory(undefined));

it("opens on New translation", async () => {
  renderApp("/");
  expect(await screen.findByRole("heading", { name: "New translation" })).toBeInTheDocument();
});

it("opens a set at its address", async () => {
  renderApp("/t/p-1/r-1");
  expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
});

it("opens Settings at its address", async () => {
  server.use(http.get("*/api/system/doctor", () => HttpResponse.json({ ok: true, checks: [] })));
  renderApp("/settings");
  expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
});

it.each([
  ["/projects", "/", "New translation"],
  ["/projects/p-1", "/t/p-1", "Atlas"],
  ["/projects/p-1/guide", "/t/p-1", "Atlas"],
  ["/runs/r-1", "/t/p-1/r-1", "Atlas"],
  ["/runs/r-1/review", "/t/p-1/r-1", "Atlas"],
  ["/system", "/settings", "Settings"],
])("sends the old address %s to %s", async (from, to, heading) => {
  server.use(http.get("*/api/system/doctor", () => HttpResponse.json({ ok: true, checks: [] })));
  const { router } = renderApp(from);
  expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  expect(router.state.location.pathname).toBe(to);
});

it("explains an old run address that no longer exists", async () => {
  server.use(
    http.get("*/api/runs/r-9", () =>
      HttpResponse.json({ detail: "run r-9 not found" }, { status: 404 }),
    ),
  );
  renderApp("/runs/r-9");
  expect(await screen.findByRole("alert")).toHaveTextContent("run r-9 not found");
});

it("offers a way back from an unknown address", async () => {
  renderApp("/no/such/page");
  expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Start a new translation" })).toHaveAttribute(
    "href",
    "/",
  );
});
