import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { recentLanguages, resetRecentForTests } from "../lib/languages";
import { project, runSummary, tiers } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { AddLanguageDialog } from "./AddLanguageDialog";

const onClose = vi.fn();

function openDialog(files = 2) {
  server.use(http.get("*/api/system/tiers", () => HttpResponse.json(tiers)));
  return renderAt(<AddLanguageDialog project={project} files={files} open onClose={onClose} />, {
    path: "/t/p-1",
    pattern: "/t/:projectId",
  });
}

async function pick(user: ReturnType<typeof openDialog>["user"], code: string) {
  await user.selectOptions(await screen.findByLabelText("Language"), "Other language…");
  await user.type(screen.getByLabelText("Language: language code"), code);
}

beforeEach(() => {
  localStorage.clear();
  resetRecentForTests();
  onClose.mockReset();
});

it("starts every file in the chosen language and opens it", async () => {
  let sent: unknown;
  server.use(
    http.post("*/api/projects/p-1/languages", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json([{ ...runSummary, id: "r-es", target_lang: "es" }], {
        status: 202,
      });
    }),
  );
  const { user, router } = openDialog();
  expect(screen.getByRole("button", { name: "Translate" })).toBeDisabled();
  await pick(user, "es");
  expect(
    screen.getByText(
      "All 2 files will be translated into Spanish. It gets its own translation guide.",
    ),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Translate" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/t/p-1/r-es"));
  expect(sent).toEqual({ target_lang: "es" });
  expect(onClose).toHaveBeenCalled();
  expect(recentLanguages()).toContain("es");
});

it("says 'the file' for a set of one", async () => {
  const { user } = openDialog(1);
  await pick(user, "es");
  expect(
    screen.getByText(
      "The file will be translated into Spanish. It gets its own translation guide.",
    ),
  ).toBeInTheDocument();
});

it("refuses a language the set already has", async () => {
  const { user } = openDialog();
  await pick(user, "EN");
  expect(screen.getByText("This set is already translated into English.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Translate" })).toBeDisabled();
});

it("announces the refusal in an always-mounted status the picker points at", async () => {
  const { user } = openDialog();
  const status = await screen.findByRole("status");
  expect(status).toBeEmptyDOMElement();
  const select = screen.getByLabelText("Language");
  expect(select).not.toHaveAttribute("aria-invalid");
  expect(select).not.toHaveAttribute("aria-describedby");
  await pick(user, "EN");
  expect(screen.getByRole("status")).toHaveTextContent(
    "This set is already translated into English.",
  );
  expect(status.id).not.toBe("");
  const code = screen.getByLabelText("Language: language code");
  for (const el of [select, code]) {
    expect(el).toHaveAttribute("aria-invalid", "true");
    expect(el.getAttribute("aria-describedby")?.split(" ")).toContain(status.id);
  }
});

it("explains a language someone else added meanwhile, and clears it on a new choice", async () => {
  server.use(
    http.post("*/api/projects/p-1/languages", () =>
      HttpResponse.json({ detail: "already", code: "language_exists" }, { status: 409 }),
    ),
  );
  const { user } = openDialog();
  await pick(user, "de");
  await user.click(screen.getByRole("button", { name: "Translate" }));
  expect(
    await screen.findByText("This set is already translated into German."),
  ).toBeInTheDocument();
  await user.type(screen.getByLabelText("Language: language code"), "-AT");
  expect(screen.queryByText(/already translated/)).not.toBeInTheDocument();
});

it("shows other failures as they are", async () => {
  server.use(
    http.post("*/api/projects/p-1/languages", () =>
      HttpResponse.json({ detail: "the server is busy" }, { status: 503 }),
    ),
  );
  const { user } = openDialog();
  await pick(user, "de");
  await user.click(screen.getByRole("button", { name: "Translate" }));
  expect(await screen.findByText(/the server is busy/)).toBeInTheDocument();
});
