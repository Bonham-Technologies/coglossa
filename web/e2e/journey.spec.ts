import { expect, test, type Page, type Request } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { expectNoA11yViolations } from "./a11y";

const STORY = [
  "Le chat dort près de la fenêtre. Il pleut sur la ville.",
  "Marie ouvre la porte. Le chat MISTRANSLATED se réveille.",
].join("\n\n");

function watchRequests(page: Page, origin: string): Request[] {
  const outside: Request[] = [];
  page.on("request", (request) => {
    if (!request.url().startsWith(origin) && !request.url().startsWith("data:"))
      outside.push(request);
  });
  return outside;
}

async function pickCode(page: Page, label: "From" | "To", code: string) {
  await page.getByLabel(label, { exact: true }).selectOption({ label: "Other language…" });
  await page.getByLabel(`${label}: language code`).fill(code);
}

/** Drop a new file on the start page and wait for its set to open. */
async function translateStory(page: Page, filename: string) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "New translation" })).toBeVisible();
  await page.getByLabel("Choose files").setInputFiles({
    name: filename,
    mimeType: "text/plain",
    buffer: Buffer.from(STORY, "utf-8"),
  });
  await pickCode(page, "From", "fr");
  await pickCode(page, "To", "en");
  await page.getByRole("button", { name: "Translate" }).click();
  await expect(page).toHaveURL(/\/t\/[0-9a-f-]+$/);
}

// CGL-35: the API sends a Content-Security-Policy; nothing the app does may break it. Every
// page of every journey reports its violations here, across reloads and navigations.
let violations: string[] = [];

test.beforeEach(async ({ page }) => {
  violations = [];
  await page.exposeFunction("reportCspViolation", (v: string) => violations.push(v));
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => {
      const report = (window as unknown as { reportCspViolation: (v: string) => void })
        .reportCspViolation;
      report(`${e.effectiveDirective} blocked ${e.blockedURI || "inline"} (${e.sourceFile})`);
    });
  });
  page.on("console", (message) => {
    if (message.type() === "error" && message.text().includes("Content Security Policy"))
      violations.push(message.text());
  });
});

test.afterEach(() => {
  expect(violations, "the page broke its Content-Security-Policy").toEqual([]);
});

const SETTLE_MS = 60_000;

/** Wait for the recheck to end, with its own limit and a message that says which step is missing. */
async function settledWithin(settled: Promise<void>, ms: number, sweepStarted: () => boolean) {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () =>
        reject(
          new Error(
            sweepStarted()
              ? `the recheck started but the file was not finished again within ${ms / 1000} s`
              : `Apply to all never started a recheck (no POST to /sweep within ${ms / 1000} s)`,
          ),
        ),
      ms,
    );
  });
  try {
    await Promise.race([settled, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

test("drop a file, check a passage, fix a term everywhere and download", async ({
  page,
  baseURL,
}) => {
  // Two 60 s waits (the translation, then the recheck) must fit, so the specific recheck message
  // fires before Playwright's generic timeout does.
  test.setTimeout(180_000);
  const outside = watchRequests(page, baseURL!);
  const filename = `histoire-${Date.now()}.txt`;

  // CGL-40: every main page is audited with axe, in light and dark.
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "New translation" })).toBeVisible();
  await expectNoA11yViolations(page, "the start page");

  await translateStory(page, filename);

  // The steps, then the summary banner (the fake models take seconds).
  const summary = page.getByRole("region", { name: "Summary" });
  await expect(summary).toContainText("Looks good.", { timeout: 60_000 });
  await expect(page.getByRole("table", { name: "Document" })).toBeVisible();
  await expectNoA11yViolations(page, "a finished set");

  // Open the flagged passage and fix it.
  await page.getByRole("row").filter({ hasText: "MISTRANSLATED" }).first().click();
  const panel = page.getByRole("complementary", { name: /Passage \d+/ });
  await expect(panel.getByRole("region", { name: "Why it's flagged" })).toBeVisible();
  await expectNoA11yViolations(page, "an open flagged passage");
  await panel.getByRole("button", { name: "Edit" }).click();
  await panel.getByLabel("Your translation").fill("Marie opens the door. The cat wakes up.");
  await panel.getByRole("button", { name: "Save" }).click();
  await expect(panel.getByRole("status")).toContainText("Changed and marked as checked.");

  // Always translate a term, everywhere in the set.
  const always = panel.getByRole("region", { name: "Always translate" });
  await always.getByLabel("Always translate").fill("fenêtre");
  await always.getByLabel("as", { exact: true }).fill("window");
  // Don't assert the transient "Rechecking" state (the fake models finish in milliseconds): wait
  // for the sweep to start, then for the first run detail after it that is finished again.
  let sweepSeen = false;
  const settled = new Promise<void>((resolve) => {
    page.on("response", (r) => {
      const path = new URL(r.url()).pathname;
      if (r.request().method() === "POST" && /^\/api\/runs\/[^/]+\/sweep$/.test(path)) {
        sweepSeen = true;
      } else if (sweepSeen && r.request().method() === "GET" && /^\/api\/runs\/[^/]+$/.test(path)) {
        void r
          .json()
          .then((detail: { run: { state: string }; report_available: boolean }) => {
            if (detail.run.state !== "sweeping" && detail.report_available) resolve();
          })
          .catch(() => undefined);
      }
    });
  });
  await always.getByRole("button", { name: "Apply to all" }).click();
  // The open passage stays put during the recheck, with its confirmation.
  await expect(always.getByRole("status")).toContainText("Added to the translation guide.");
  await settledWithin(settled, SETTLE_MS, () => sweepSeen);
  await expect(always.getByRole("status")).toContainText("Added to the translation guide.");

  // Download is not offered until the recheck finishes.
  await expect(page.getByRole("table", { name: "Document" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Download" })).toBeVisible({ timeout: 60_000 });

  // Download: the edit is in the files.
  await page.getByRole("button", { name: "Download" }).click();
  const [html] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: "Web page" }).click(),
  ]);
  expect(html.suggestedFilename()).toMatch(/\.html$/);
  expect(await readFile((await html.path())!, "utf-8")).toContain(
    "Marie opens the door. The cat wakes up.",
  );
  await page.getByRole("button", { name: "Download" }).click();
  const [docx] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: "Word document" }).click(),
  ]);
  expect(docx.suggestedFilename()).toMatch(/\.docx$/);

  // The translation guide now holds the term.
  await page.getByRole("button", { name: /^Translation guide/ }).click();
  const guide = page.getByRole("dialog", { name: "Translation guide" });
  await guide.getByRole("tab", { name: "Terms" }).click();
  await expect(guide.getByRole("list", { name: "Terms" })).toContainText(
    "fenêtre → window · always",
  );
  await expectNoA11yViolations(page, "the translation guide");

  // Spec §8: the app works offline: nothing was requested from anywhere else.
  expect(outside.map((r) => r.url())).toEqual([]);
});

test("a reload keeps the open translation", async ({ page }) => {
  await translateStory(page, `reload-${Date.now()}.txt`); // this test makes its own set
  await expect(page.getByRole("table", { name: "Document" })).toBeVisible({ timeout: 60_000 });
  const url = page.url();
  await page.reload();
  expect(page.url()).toBe(url);
  await expect(page.getByRole("table", { name: "Document" })).toBeVisible();
});

test("dark mode applies before the first paint", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => {
    // Observe the document itself: <html> does not exist yet when init scripts run.
    new MutationObserver((records, observer) => {
      if (records.some((r) => r.attributeName === "data-theme")) {
        (window as unknown as { firstTheme: unknown }).firstTheme = {
          theme: document.documentElement.dataset.theme,
          rendered: (document.getElementById("root")?.childElementCount ?? 0) > 0,
        };
        observer.disconnect();
      }
    }).observe(document, { attributes: true, subtree: true });
  });
  await page.goto("/settings");
  const first = await page.evaluate(
    () => (window as unknown as { firstTheme: unknown }).firstTheme,
  );
  expect(first).toEqual({ theme: "dark", rendered: false });
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(background).toBe("rgb(21, 23, 28)"); // --bg in the dark theme
  await expectNoA11yViolations(page, "Settings");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "New translation" })).toBeVisible();
});

test("translate a set into a second language and switch between them", async ({ page }) => {
  test.setTimeout(180_000);
  await translateStory(page, `deux-langues-${Date.now()}.txt`);
  const summary = page.getByRole("region", { name: "Summary" });
  await expect(summary).toContainText("Looks good.", { timeout: 60_000 });
  const table = page.getByRole("table", { name: "Document" });
  await expect(table).toContainText("[en] Le chat dort");

  await page.getByRole("button", { name: "More actions", exact: true }).click();
  await page.getByRole("menuitem", { name: "Translate into another language…" }).click();
  const dialog = page.getByRole("dialog", { name: "Translate into another language" });
  await dialog.getByLabel("Language", { exact: true }).selectOption({ label: "Other language…" });
  // CGL-99: a language the set already has is refused, and the reason is announced.
  await dialog.getByLabel("Language: language code").fill("en");
  await expect(dialog.getByRole("status")).toHaveText(
    "This set is already translated into English.",
  );
  await expect(dialog.getByRole("button", { name: "Translate" })).toBeDisabled();
  await expectNoA11yViolations(page, "the add-language dialog with a refusal");
  await dialog.getByLabel("Language: language code").fill("es");
  await expect(dialog).toContainText("The file will be translated into Spanish.");
  await dialog.getByRole("button", { name: "Translate" }).click();

  const languages = page.getByRole("navigation", { name: "Languages" });
  await expect(languages.getByRole("link", { name: /Spanish/ })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(summary).toContainText("Looks good.", { timeout: 60_000 });
  await expect(table).toContainText("[es] Le chat dort");

  // Each language has its own guide.
  await page.getByRole("button", { name: /^Translation guide/ }).click();
  await expect(page.getByRole("dialog", { name: "Translation guide" })).toBeVisible();
  await page.keyboard.press("Escape");

  await languages.getByRole("link", { name: /English/ }).click();
  await expect(languages.getByRole("link", { name: /English/ })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(table).toContainText("[en] Le chat dort");
});
