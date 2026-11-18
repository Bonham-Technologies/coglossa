import { render, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createMemoryRouter, RouterProvider, type NonIndexRouteObject } from "react-router";
import { makeQueryClient, Providers } from "../Providers";
import { RouteError } from "../pages/RouteError";
import { routes } from "../routes";
import { configView, tiers } from "../test/fixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";
import { ErrorMessage } from "./ErrorMessage";
import { Loading } from "./Loading";

it("shows the header with the signed-in user, Settings and Help", async () => {
  server.use(http.get("*/api/me", () => HttpResponse.json({ user: "ana" })));
  renderApp("/nowhere");
  const header = screen.getByRole("banner");
  expect(await within(header).findByText("ana")).toBeInTheDocument();
  expect(within(header).getByRole("link", { name: "Coglossa" })).toHaveAttribute("href", "/");
  expect(within(header).getByRole("link", { name: "Settings" })).toHaveAttribute(
    "href",
    "/settings",
  );
  expect(screen.getByRole("navigation", { name: "Your translations" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
});

it("starts on New translation", async () => {
  server.use(
    http.get("*/api/system/tiers", () => HttpResponse.json(tiers)),
    http.get("*/api/system/config", () => HttpResponse.json(configView)),
  );
  renderApp("/");
  expect(await screen.findByRole("heading", { name: "New translation" })).toBeInTheDocument();
});

it("switches the theme from the header", async () => {
  localStorage.clear();
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  const { user } = renderApp("/nowhere");
  await user.click(screen.getByRole("button", { name: "Switch to the dark theme" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  await user.click(screen.getByRole("button", { name: "Switch to the light theme" }));
  expect(document.documentElement.dataset.theme).toBe("light");
});

it("opens help from the header and with the ? key", async () => {
  const { user } = renderApp("/nowhere");
  await user.click(screen.getByRole("button", { name: "Help" }));
  const help = screen.getByRole("dialog", { name: "Help" });
  expect(help).toHaveTextContent("Keyboard shortcuts");
  expect(help).toHaveTextContent("Something may be missing");
  await user.click(within(help).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog", { name: "Help" })).not.toBeInTheDocument();
  await user.keyboard("?");
  expect(screen.getByRole("dialog", { name: "Help" })).toBeInTheDocument();
});

it("styles Settings in the header like the other quiet items", () => {
  renderApp("/nowhere");
  const settings = within(screen.getByRole("banner")).getByRole("link", { name: "Settings" });
  expect(settings).toHaveClass("quiet");
  expect(settings).not.toHaveAttribute("aria-current");
});

it("marks Settings as the current page in the header", async () => {
  server.use(http.get("*/api/system/config", () => HttpResponse.json(configView)));
  renderApp("/settings");
  const settings = within(screen.getByRole("banner")).getByRole("link", { name: "Settings" });
  expect(settings).toHaveClass("quiet");
  expect(settings).toHaveAttribute("aria-current", "page");
});

it("folds the sidebar behind a button on narrow screens", async () => {
  const { user } = renderApp("/nowhere");
  const toggle = screen.getByRole("button", { name: "Your translations" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("navigation", { name: "Your translations" })).toHaveClass("open");
});

it("shows nobody when sign-in is off", async () => {
  renderApp("/nowhere");
  expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  expect(screen.queryByText(/Signed in as/)).not.toBeInTheDocument();
});

it("explains a missing sign-in header", async () => {
  server.use(
    http.get("*/api/me", () =>
      HttpResponse.json(
        { detail: "missing X-Remote-User header; sign in through your organisation's proxy" },
        { status: 401 },
      ),
    ),
  );
  renderApp("/nowhere");
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "You're signed out. Reload the page to sign in again.",
  );
});

it("keeps the top bar when a page fails to render", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {}); // React reports the thrown error
  function Thrower(): never {
    throw new Error("kaput");
  }
  // the app's own routes, with one more page that throws
  const root = routes[0] as NonIndexRouteObject;
  const frame = root.children![0] as NonIndexRouteObject;
  const pages = [...frame.children!, { path: "boom", element: <Thrower /> }];
  const router = createMemoryRouter([{ ...root, children: [{ ...frame, children: pages }] }], {
    initialEntries: ["/boom"],
  });
  render(
    <Providers client={makeQueryClient({ retry: false })}>
      <RouterProvider router={router} />
    </Providers>,
  );
  expect(await screen.findByRole("heading", { name: "Something went wrong" })).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("kaput");
  expect(screen.getByRole("banner")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Coglossa" })).toBeInTheDocument();
});

it("shows the error page alone when the layout itself fails", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  function Thrower(): never {
    throw new Error("no frame");
  }
  const router = createMemoryRouter(
    [{ path: "/", element: <Thrower />, errorElement: <RouteError /> }],
    { initialEntries: ["/"] },
  );
  render(
    <Providers client={makeQueryClient({ retry: false })}>
      <RouterProvider router={router} />
    </Providers>,
  );
  expect(await screen.findByRole("heading", { name: "Something went wrong" })).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("no frame");
  expect(screen.queryByRole("banner")).not.toBeInTheDocument();
});

it("shows a loading status", () => {
  render(<Loading />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading…");
});

it("gives a generic message for something that is not an Error", () => {
  render(<ErrorMessage error="s" />);
  expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong.");
});

it("offers Sign out when the proxy has a sign-out page", async () => {
  server.use(http.get("*/api/me", () => HttpResponse.json({ user: "ana", logout_url: "/logout" })));
  renderApp("/nowhere");
  const button = await screen.findByRole("button", { name: "Sign out" });
  const form = button.closest("form");
  expect(form).toHaveAttribute("action", "/logout");
  expect(form).toHaveAttribute("method", "post");
});

it("has no Sign out without a sign-out page", async () => {
  server.use(http.get("*/api/me", () => HttpResponse.json({ user: "ana", logout_url: null })));
  renderApp("/nowhere");
  expect(await screen.findByText("ana")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
});
