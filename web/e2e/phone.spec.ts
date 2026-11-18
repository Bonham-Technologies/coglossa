import { expect, test } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y";

// CGL-25 groundwork: the demo path at phone size. Not a full phone design, just the parts a
// person on a phone meets first.
test.use({ viewport: { width: 360, height: 740 }, hasTouch: true });

// Enough passages that the page scrolls, as a real document does on a phone.
const STORY = Array.from(
  { length: 12 },
  (_, n) =>
    `Le paragraphe ${n + 1} raconte la ville sous la pluie. Les voisins arrivent avec du pain.`,
).join("\n\n");

test("the top bar fits, nothing scrolls sideways, and tapping a passage opens it", async ({
  page,
}) => {
  // as on the hosted preview: a signed-in name and Sign out share the bar
  await page.route("**/api/me", (route) =>
    route.fulfill({
      json: {
        user: "people-first",
        admin: false,
        teams: ["demo"],
        max_upload_mb: 4,
        logout_url: "/logout",
      },
    }),
  );
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  const bar = page.locator("header.topbar");
  const fits = async () => {
    const box = await bar.boundingBox();
    const children = await bar.evaluate((el) =>
      [...el.querySelectorAll("*")].every((c) => {
        const r = c.getBoundingClientRect();
        const b = el.getBoundingClientRect();
        return (
          r.width === 0 ||
          (r.top >= b.top - 1 && r.bottom <= b.bottom + 1 && r.right <= b.right + 1)
        );
      }),
    );
    expect(box!.height).toBeLessThan(60);
    expect(children).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      360,
    );
  };
  await fits();

  await page.getByLabel("Choose files").setInputFiles({
    name: `phone-${Date.now()}.txt`,
    mimeType: "text/plain",
    buffer: Buffer.from(STORY, "utf-8"),
  });
  await page.getByLabel("From", { exact: true }).selectOption({ label: "Other language…" });
  await page.getByLabel("From: language code").fill("fr");
  await page.getByLabel("To", { exact: true }).selectOption({ label: "Other language…" });
  await page.getByLabel("To: language code").fill("en");
  await page.getByRole("button", { name: "Translate" }).click();
  const table = page.getByRole("table", { name: "Document" });
  await expect(table).toBeVisible({ timeout: 60_000 });
  await fits();
  await expectNoA11yViolations(page, "a set at phone size");

  // Tap the original text of a passage below the fold: the page must not move under the finger.
  const cell = table.locator("td.source").nth(5);
  await cell.evaluate((el) => el.scrollIntoView({ block: "end" })); // at the bottom of the screen
  await cell.click(); // pointer down focuses the row; the page must not scroll before the release
  await expect(page.getByRole("complementary", { name: /Passage \d+/ })).toBeVisible();
});
