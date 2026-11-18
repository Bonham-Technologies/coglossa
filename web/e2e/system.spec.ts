import { expect, test } from "@playwright/test";

test("Settings runs the checks against the live stack", async ({ page }) => {
  await page.goto("/system");
  await expect(page).toHaveURL(/\/settings$/);
  const status = page.getByRole("region", { name: "Status" });
  await expect(status).toContainText(/Everything is working|offline|attention|can't reach/, {
    timeout: 60_000,
  });
  await page.getByText("Technical details").click();
  await expect(page.locator("code", { hasText: /coglossa\.yaml$/ })).toBeVisible();
  const checks = page.getByRole("table", { name: "Checks" });
  await expect(checks.getByRole("row", { name: /database/ })).toContainText("pass");
  await expect(page.getByRole("table", { name: "Language pairs" })).toBeVisible();
});

test("an unknown address shows the app's not-found page", async ({ page }) => {
  await page.goto("/no/such/page");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
});

test("fonts for other scripts come from the app itself", async ({ page }) => {
  const fonts: string[] = [];
  page.on("request", (r) => {
    if (r.resourceType() === "font") fonts.push(r.url());
  });
  await page.goto("/");
  for (const [family, sample] of [
    ["Noto Sans Armenian", "Հայերեն"],
    ["Noto Sans Georgian", "ქართული"],
    ["Noto Sans Tamil", "தமிழ்"],
    ["Noto Sans Khmer", "ភាសាខ្មែរ"],
    ["Noto Sans TC", "繁體中文"],
  ]) {
    // The other scripts' faces arrive in a stylesheet that loads after first paint (CGL-39), so
    // ask until it is in.
    await expect
      .poll(
        () =>
          page.evaluate(
            async ([f, s]) => (await document.fonts.load(`16px "${f}"`, s)).length,
            [family, sample],
          ),
        { message: family },
      )
      .toBeGreaterThan(0);
  }
  const base = new URL(page.url()).origin;
  expect(fonts.length).toBeGreaterThan(0);
  expect(fonts.filter((u) => !u.startsWith(base))).toEqual([]);
});
