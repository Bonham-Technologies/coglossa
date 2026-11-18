import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { configView, doctorReport, me, project, tiers } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { resetThemeForTests } from "../theme/theme";
import { SettingsPage } from "./SettingsPage";

let probes: string[] = [];

beforeEach(() => {
  probes = [];
  localStorage.clear();
  resetThemeForTests();
  server.use(
    http.get("*/api/system/doctor", ({ request }) => {
      probes.push(new URL(request.url).searchParams.get("probe") ?? "");
      return HttpResponse.json(doctorReport);
    }),
    http.get("*/api/system/config", () => HttpResponse.json(configView)),
    http.get("*/api/system/tiers", () => HttpResponse.json(tiers)),
    http.get("*/api/projects", () => HttpResponse.json([project])),
  );
});

it("chooses the theme", async () => {
  const { user } = renderAt(<SettingsPage />);
  expect(screen.getByRole("radio", { name: "Match my computer" })).toBeChecked();
  await user.click(screen.getByRole("radio", { name: "Dark" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem("coglossa.theme")).toBe("dark");
});

it("says how the installation is doing, and runs a full check", async () => {
  const { user } = renderAt(<SettingsPage />);
  const status = await screen.findByRole("region", { name: "Status" });
  expect(await within(status).findByText(/The quality checker is offline/)).toBeInTheDocument();
  await user.click(within(status).getByRole("button", { name: "Run a full check" }));
  await vi.waitFor(() => expect(probes).toEqual(["false", "true"]));
});

it("says when the status cannot be checked, and while and why a full check fails", async () => {
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.get("*/api/system/doctor", async ({ request }) => {
      if (new URL(request.url).searchParams.get("probe") !== "true") {
        return HttpResponse.json({ detail: "the database is unreachable" }, { status: 503 });
      }
      await held;
      return HttpResponse.json({ detail: "the full check timed out" }, { status: 503 });
    }),
  );
  const { user } = renderAt(<SettingsPage />);
  const status = await screen.findByRole("region", { name: "Status" });
  expect(await within(status).findByRole("alert")).toHaveTextContent("the database is unreachable");
  await user.click(within(status).getByRole("button", { name: "Run a full check" }));
  expect(await within(status).findByRole("button", { name: "Checking…" })).toBeDisabled();
  release();
  expect(await within(status).findByText("the full check timed out")).toBeInTheDocument();
  expect(within(status).getByRole("button", { name: "Run a full check" })).toBeEnabled();
});

it("keeps the technical details folded away", async () => {
  const { user } = renderAt(<SettingsPage />);
  const details = (await screen.findByText("Technical details")).closest("details")!;
  expect(details).not.toHaveAttribute("open");
  await user.click(await screen.findByText("Technical details"));
  expect(await within(details).findByText("/etc/coglossa/coglossa.yaml")).toBeInTheDocument();
  expect(within(details).getByRole("table", { name: "Checks" })).toHaveTextContent(
    "http://scorer:8080 is not reachable",
  );
  expect(within(details).getByRole("table", { name: "Languages" })).toHaveTextContent("Amharic");
});

it("finds passages by score", async () => {
  const { user, router } = renderAt(<SettingsPage />);
  await user.click(await screen.findByText("Technical details"));
  const form = screen.getByRole("form", { name: "Find passages by score" });
  await within(form).findByRole("option", { name: "Atlas" });
  await user.selectOptions(within(form).getByLabelText("Translation"), "p-1");
  await user.type(within(form).getByLabelText("Meaning from"), "0");
  await user.type(within(form).getByLabelText("Meaning to"), "60");
  await user.click(within(form).getByRole("button", { name: "Show passages" }));
  expect(router.state.location.pathname).toBe("/t/p-1");
  expect(router.state.location.search).toBe("?min_meaning=0&max_meaning=60");
});

const fullReport = {
  ok: true,
  checks: [{ name: "scorer", status: "pass", message: "answered the probe", fix: "" }],
};

it("loads the configuration and language tiers only when the details are first opened", async () => {
  const calls: string[] = [];
  server.use(
    http.get("*/api/system/config", () => {
      calls.push("config");
      return HttpResponse.json(configView);
    }),
    http.get("*/api/system/tiers", () => {
      calls.push("tiers");
      return HttpResponse.json(tiers);
    }),
  );
  const { user } = renderAt(<SettingsPage />);
  await screen.findByText(/The quality checker is offline/);
  expect(calls).toEqual([]);
  await user.click(await screen.findByText("Technical details"));
  expect(await screen.findByText("/etc/coglossa/coglossa.yaml")).toBeInTheDocument();
  await user.click(await screen.findByText("Technical details")); // fold
  await user.click(await screen.findByText("Technical details")); // unfold again
  expect(calls.sort()).toEqual(["config", "tiers"]);
});

it("shows a full check's results in the Checks table and announces them", async () => {
  server.use(
    http.get("*/api/system/doctor", ({ request }) =>
      HttpResponse.json(
        new URL(request.url).searchParams.get("probe") === "true" ? fullReport : doctorReport,
      ),
    ),
  );
  const { user } = renderAt(<SettingsPage />);
  const status = await screen.findByRole("region", { name: "Status" });
  await within(status).findByText(/The quality checker is offline/);
  // The live region is there before the result arrives, so the change is announced.
  const live = within(status).getByRole("status");
  expect(live).toBeEmptyDOMElement(); // the quick check is not announced on load
  await user.click(await screen.findByText("Technical details"));
  await user.click(within(status).getByRole("button", { name: "Run a full check" }));
  await vi.waitFor(() => expect(live).toHaveTextContent("Everything is working."));
  const checks = screen.getByRole("table", { name: "Checks" });
  expect(checks).toHaveTextContent("answered the probe");
  expect(checks).not.toHaveTextContent("is not reachable");
});

async function openScoreSearch() {
  const view = renderAt(<SettingsPage />);
  await view.user.click(await screen.findByText("Technical details"));
  const form = screen.getByRole("form", { name: "Find passages by score" });
  await within(form).findByRole("option", { name: "Atlas" });
  return { ...view, form };
}

it("opens the translation without a bare ? when no bounds are set", async () => {
  const { user, router, form } = await openScoreSearch();
  const navigate = vi.spyOn(router, "navigate");
  await user.selectOptions(within(form).getByLabelText("Translation"), "p-1");
  await user.click(within(form).getByRole("button", { name: "Show passages" }));
  // The router tidies a trailing "?" away, so look at what was asked for.
  expect(navigate.mock.calls[0]?.[0]).toBe("/t/p-1");
});

it("encodes the translation's id in the address", async () => {
  server.use(http.get("*/api/projects", () => HttpResponse.json([{ ...project, id: "a b/c?d" }])));
  const { user, router, form } = await openScoreSearch();
  await user.selectOptions(within(form).getByLabelText("Translation"), "a b/c?d");
  await user.click(within(form).getByRole("button", { name: "Show passages" }));
  expect(router.state.location.pathname).toBe("/t/a%20b%2Fc%3Fd");
});

it("refuses a lowest score above the highest, and says so", async () => {
  const { user, router, form } = await openScoreSearch();
  await user.selectOptions(within(form).getByLabelText("Translation"), "p-1");
  await user.type(within(form).getByLabelText("Meaning from"), "80");
  await user.type(within(form).getByLabelText("Meaning to"), "20");
  await user.click(within(form).getByRole("button", { name: "Show passages" }));
  expect(within(form).getByRole("alert")).toHaveTextContent(
    "The lowest score must not be higher than the highest.",
  );
  expect(router.state.location.pathname).toBe("/");
  await user.clear(within(form).getByLabelText("Meaning from"));
  await user.type(within(form).getByLabelText("Meaning from"), "10");
  await user.click(within(form).getByRole("button", { name: "Show passages" }));
  expect(router.state.location.pathname).toBe("/t/p-1");
});

it("shows a translator only Appearance, and never asks for the checks", async () => {
  server.use(
    http.get("*/api/me", () => HttpResponse.json({ ...me, admin: false, teams: ["desk"] })),
  );
  renderAt(<SettingsPage />);
  expect(await screen.findByRole("radio", { name: "Dark" })).toBeInTheDocument();
  await vi.waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());
  expect(screen.queryByRole("region", { name: "Status" })).not.toBeInTheDocument();
  expect(screen.queryByText("Technical details")).not.toBeInTheDocument();
  expect(probes).toEqual([]);
});
