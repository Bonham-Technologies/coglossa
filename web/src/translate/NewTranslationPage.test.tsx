import { fireEvent, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { recentLanguages, resetRecentForTests } from "../lib/languages";
import { me, project, tiers } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { enqueueUploads } from "../upload/queue";
import { NewTranslationPage, uploadLimit } from "./NewTranslationPage";

vi.mock("../upload/queue", () => ({ enqueueUploads: vi.fn() }));

let created: unknown[] = [];

beforeEach(() => {
  created = [];
  localStorage.clear();
  resetRecentForTests();
  vi.mocked(enqueueUploads).mockClear();
  server.use(
    http.get("*/api/system/tiers", () => HttpResponse.json(tiers)),
    http.get("*/api/me", () => HttpResponse.json({ ...me, max_upload_mb: 4 })),
    http.post("*/api/projects", async ({ request }) => {
      const body = (await request.json()) as { name: string };
      created.push(body);
      return HttpResponse.json({ ...project, id: "p-9", name: body.name }, { status: 201 });
    }),
  );
});

it("starts a translation from chosen files and opens it", async () => {
  const { user, router } = renderAt(<NewTranslationPage />);
  expect(screen.getByRole("heading", { name: "New translation" })).toBeInTheDocument();
  const translate = screen.getByRole("button", { name: "Translate" });
  expect(translate).toBeDisabled();
  await user.upload(screen.getByLabelText("Choose files"), [
    new File(["Il pleut."], "Welcome letter.docx"),
  ]);
  await screen.findAllByRole("option", { name: "English" });
  await user.selectOptions(screen.getByLabelText("To"), "en");
  await user.click(translate);
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-9"));
  expect(created).toEqual([
    { name: "Welcome letter", target_lang: "en", source_lang: null, pipeline_profile: null },
  ]);
  expect(enqueueUploads).toHaveBeenCalledWith(
    "p-9",
    [expect.objectContaining({ name: "Welcome letter.docx" })],
    { target_lang: "en", source_lang: null, pipeline_profile: null },
    expect.anything(),
  );
  expect(recentLanguages()).toEqual(["en"]);
});

it("translates pasted text with a chosen source and length", async () => {
  const { user, router } = renderAt(<NewTranslationPage />);
  await user.click(screen.getByRole("tab", { name: "Paste text" }));
  await user.type(screen.getByLabelText("Text to translate"), "Bienvenue au centre communautaire");
  await screen.findAllByRole("option", { name: "English" });
  await user.selectOptions(screen.getByLabelText("From"), "fr");
  await user.selectOptions(screen.getByLabelText("To"), "en");
  await user.click(screen.getByText("More options"));
  await user.selectOptions(screen.getByLabelText("Length"), "Long document");
  await user.click(screen.getByRole("button", { name: "Translate" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-9"));
  expect(created).toEqual([
    {
      name: "Bienvenue au centre communautaire",
      target_lang: "en",
      source_lang: "fr",
      pipeline_profile: "long-form",
    },
  ]);
  const [, files] = vi.mocked(enqueueUploads).mock.calls[0]!;
  expect(files[0]!.name).toBe("Bienvenue au centre communautaire.txt");
});

it("warns about a less-practised language and leaves unusable files out", async () => {
  const { user } = renderAt(<NewTranslationPage />);
  await user.upload(screen.getByLabelText("Choose files"), [
    new File(["x"], "a.txt"),
    new File([new Uint8Array(5 * 1024 * 1024)], "big.pdf"),
  ]);
  await screen.findAllByRole("option", { name: "Amharic" });
  await user.selectOptions(screen.getByLabelText("To"), "am");
  expect(
    screen.getByText("Coglossa has less practice with Amharic, so check carefully."),
  ).toBeInTheDocument();
  expect(screen.getByText("Files marked in red will be left out.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Translate" }));
  await vi.waitFor(() => expect(enqueueUploads).toHaveBeenCalled());
  const [, files] = vi.mocked(enqueueUploads).mock.calls[0]!;
  expect(files.map((f) => f.name)).toEqual(["a.txt"]);
});

it("shows why the set could not be created", async () => {
  server.use(
    http.post("*/api/projects", () =>
      HttpResponse.json({ detail: "The server is busy." }, { status: 503 }),
    ),
  );
  const { user } = renderAt(<NewTranslationPage />);
  await user.upload(screen.getByLabelText("Choose files"), [new File(["x"], "a.txt")]);
  await screen.findAllByRole("option", { name: "English" });
  await user.selectOptions(screen.getByLabelText("To"), "en");
  await user.click(screen.getByRole("button", { name: "Translate" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The server is busy.");
  expect(enqueueUploads).not.toHaveBeenCalled();
});

it("reads the upload limit from who is signed in", () => {
  expect(uploadLimit({ ...me, max_upload_mb: 4 })).toBe(4);
  expect(uploadLimit(undefined)).toBeNull();
});

it("says why pasted text over the limit cannot be translated", async () => {
  server.use(http.get("*/api/me", () => HttpResponse.json({ ...me, max_upload_mb: 1 })));
  const { user } = renderAt(<NewTranslationPage />);
  await user.click(screen.getByRole("tab", { name: "Paste text" }));
  await screen.findAllByRole("option", { name: "English" });
  await user.selectOptions(screen.getByLabelText("To"), "en");
  const box = screen.getByLabelText("Text to translate");
  await vi.waitFor(() => {
    fireEvent.change(box, { target: { value: "a".repeat(1024 * 1024 + 1) } });
    expect(screen.getByRole("button", { name: "Translate" })).toBeDisabled();
    const note = within(screen.getByRole("tabpanel")).getByRole("status");
    expect(note).toHaveTextContent(
      "This text is too long (the limit is 1 MB). Split it into shorter parts and translate each one.",
    );
    expect(box).toHaveAttribute("aria-describedby", note.id);
  });
  // Both problems at once: each keeps its own message.
  await user.selectOptions(screen.getByLabelText("From"), "en");
  expect(screen.getByText(/From and To are the same language/)).toBeInTheDocument();
  expect(within(screen.getByRole("tabpanel")).getByRole("status")).toHaveTextContent(
    "This text is too long",
  );
  await user.selectOptions(screen.getByLabelText("From"), "Detect automatically");
  fireEvent.change(box, { target: { value: "short" } });
  expect(within(screen.getByRole("tabpanel")).getByRole("status")).toBeEmptyDOMElement();
  expect(box).not.toHaveAttribute("aria-describedby");
  expect(screen.getByRole("button", { name: "Translate" })).toBeEnabled();
});

it("makes Files and Paste text a proper tab set, moved by the arrow keys", async () => {
  const { user } = renderAt(<NewTranslationPage />);
  const files = screen.getByRole("tab", { name: "Files" });
  const paste = screen.getByRole("tab", { name: "Paste text" });
  expect(files).toHaveAttribute("aria-selected", "true");
  expect(files).toHaveAttribute("tabindex", "0");
  expect(paste).toHaveAttribute("tabindex", "-1");
  const panel = screen.getByRole("tabpanel", { name: "Files" });
  expect(files).toHaveAttribute("aria-controls", panel.id);
  expect(panel).toHaveAttribute("aria-labelledby", files.id);
  expect(screen.getByLabelText("Text to translate")).not.toBeVisible();
  files.focus();
  await user.keyboard("{ArrowRight}");
  expect(paste).toHaveFocus();
  expect(paste).toHaveAttribute("aria-selected", "true");
  expect(paste).toHaveAttribute("tabindex", "0");
  expect(files).toHaveAttribute("tabindex", "-1");
  expect(screen.getByRole("tabpanel", { name: "Paste text" })).toContainElement(
    screen.getByLabelText("Text to translate"),
  );
  await user.keyboard("{ArrowRight}"); // wraps
  expect(files).toHaveFocus();
  await user.keyboard("{ArrowLeft}"); // wraps back
  expect(paste).toHaveFocus();
  await user.keyboard("{Home}");
  expect(files).toHaveFocus();
  expect(files).toHaveAttribute("aria-selected", "true");
  await user.keyboard("{End}");
  expect(paste).toHaveFocus();
  expect(paste).toHaveAttribute("aria-selected", "true");
});

async function startOne(user: ReturnType<typeof renderAt>["user"]) {
  await user.upload(screen.getByLabelText("Choose files"), [new File(["Il pleut."], "a.txt")]);
  await screen.findAllByRole("option", { name: "English" });
  await user.selectOptions(screen.getByLabelText("To"), "en");
}

it("asks which team when the person has two, remembers it, and sends it", async () => {
  server.use(
    http.get("*/api/me", () =>
      HttpResponse.json({ ...me, admin: false, teams: ["desk-a", "desk-b"] }),
    ),
  );
  const { user, router } = renderAt(<NewTranslationPage />);
  const team = await screen.findByLabelText("Team");
  expect(screen.getByRole("option", { name: "Only me" })).toBeInTheDocument();
  await user.selectOptions(team, "desk-b");
  await startOne(user);
  await user.click(screen.getByRole("button", { name: "Translate" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-9"));
  expect(created).toEqual([expect.objectContaining({ team: "desk-b" })]);
  expect(localStorage.getItem("coglossa.team")).toBe("desk-b");
});

it("sends no team for Only me", async () => {
  server.use(
    http.get("*/api/me", () =>
      HttpResponse.json({ ...me, admin: false, teams: ["desk-a", "desk-b"] }),
    ),
  );
  const { user, router } = renderAt(<NewTranslationPage />);
  await user.selectOptions(await screen.findByLabelText("Team"), "Only me");
  await startOne(user);
  await user.click(screen.getByRole("button", { name: "Translate" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-9"));
  expect(created[0]).not.toHaveProperty("team");
});

it("uses the one team without asking", async () => {
  server.use(
    http.get("*/api/me", () => HttpResponse.json({ ...me, admin: false, teams: ["desk-a"] })),
  );
  const { user, router } = renderAt(<NewTranslationPage />);
  await startOne(user);
  expect(screen.queryByLabelText("Team")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Translate" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-9"));
  expect(created).toEqual([expect.objectContaining({ team: "desk-a" })]);
});

it("waits for who is signed in before it can translate", async () => {
  // CGL-31: until /api/me answers, a one-team person's set would be made personal.
  let answer: () => void = () => {};
  const signedIn = new Promise<void>((resolve) => (answer = resolve));
  server.use(
    http.get("*/api/me", async () => {
      await signedIn;
      return HttpResponse.json({ ...me, admin: false, teams: ["desk-a"] });
    }),
  );
  const { user, router } = renderAt(<NewTranslationPage />);
  await startOne(user);
  const translate = screen.getByRole("button", { name: "Translate" });
  expect(translate).toBeDisabled();
  answer();
  await vi.waitFor(() => expect(translate).toBeEnabled());
  await user.click(translate);
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/t/p-9"));
  expect(created).toEqual([expect.objectContaining({ team: "desk-a" })]);
});

it("says so when it cannot tell who is signed in", async () => {
  server.use(
    http.get("*/api/me", () => HttpResponse.json({ detail: "it broke" }, { status: 500 })),
  );
  const { user } = renderAt(<NewTranslationPage />);
  await startOne(user);
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Translate" })).toBeDisabled();
});

it("won't translate a language into itself, but allows regional variants", async () => {
  const { user } = renderAt(<NewTranslationPage />);
  await user.upload(screen.getByLabelText("Choose files"), [new File(["Hello."], "Note.docx")]);
  await screen.findAllByRole("option", { name: "English" });
  const translate = screen.getByRole("button", { name: "Translate" });
  const note = screen.getAllByRole("status").find((el) => el.className.includes("error"))!;
  await user.selectOptions(screen.getByLabelText("From"), "en");
  await user.selectOptions(screen.getByLabelText("To"), "en");
  expect(translate).toBeDisabled();
  expect(note).toHaveTextContent(
    "From and To are the same language. Choose a different language to translate into.",
  );
  await user.selectOptions(screen.getByLabelText("To"), "fr");
  expect(translate).toBeEnabled();
  expect(note).toBeEmptyDOMElement();
});

it("treats en-US and en-GB as different, and Detect automatically as never the same", async () => {
  const { user } = renderAt(<NewTranslationPage />);
  await user.upload(screen.getByLabelText("Choose files"), [new File(["Hello."], "Note.docx")]);
  await screen.findAllByRole("option", { name: "English" });
  const translate = screen.getByRole("button", { name: "Translate" });
  await user.selectOptions(screen.getByLabelText("To"), "en");
  expect(translate).toBeEnabled();
  await user.selectOptions(screen.getByLabelText("From"), "en");
  expect(translate).toBeDisabled();
  await user.selectOptions(screen.getByLabelText("From"), "Other language…");
  await user.type(screen.getByLabelText("From: language code"), "en-US");
  await user.selectOptions(screen.getByLabelText("To"), "Other language…");
  await user.type(screen.getByLabelText("To: language code"), "en-GB");
  expect(translate).toBeEnabled();
  expect(screen.queryByText(/From and To are the same language/)).not.toBeInTheDocument();
  await user.clear(screen.getByLabelText("To: language code"));
  await user.type(screen.getByLabelText("To: language code"), "EN_us");
  expect(translate).toBeDisabled();
});
