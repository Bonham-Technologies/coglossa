import { expect, test } from "@playwright/test";
import { ROLES_PORT } from "../playwright.config";

test.use({ baseURL: `http://127.0.0.1:${ROLES_PORT}` });

const STORY = "Le chat dort près de la fenêtre.";

test("a set of one team is not found by another, and an admin sees it", async ({ browser }) => {
  const ana = await browser.newContext({ extraHTTPHeaders: { "X-Remote-User": "ana" } });
  const page = await ana.newPage();
  await page.goto("/");
  await page.getByLabel("Choose files").setInputFiles({
    name: `desk-a-${Date.now()}.txt`,
    mimeType: "text/plain",
    buffer: Buffer.from(STORY, "utf-8"),
  });
  await page.getByLabel("To", { exact: true }).selectOption({ label: "Other language…" });
  await page.getByLabel("To: language code").fill("en");
  await expect(page.getByLabel("Team")).toHaveCount(0); // one team: no question
  await page.getByRole("button", { name: "Translate" }).click();
  await expect(page).toHaveURL(/\/t\/[0-9a-f-]+$/);
  const address = page.url();
  await page.goto("/settings");
  await expect(page.getByText("Technical details")).toHaveCount(0);

  const ben = await browser.newContext({ extraHTTPHeaders: { "X-Remote-User": "ben" } });
  const other = await ben.newPage();
  await other.goto(address);
  await expect(other.getByText(/not found/i).first()).toBeVisible();

  const admin = await browser.newContext({ extraHTTPHeaders: { "X-Remote-User": "e2e-admin" } });
  const third = await admin.newPage();
  await third.goto(address);
  await expect(third.getByText(/desk-a · /)).toBeVisible();
  for (const context of [ana, ben, admin]) await context.close();
});
