import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

// CGL-40: WCAG 2.1 A and AA, the level the app promises. No rule is disabled.
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

/**
 * Run axe on the page as it stands, in light and then dark, and fail on any violation with the
 * rule id, impact and the elements in the message. The theme follows the emulated colour scheme
 * live, so the same page and state are checked twice; the scheme is put back afterwards.
 */
export async function expectNoA11yViolations(page: Page, label: string): Promise<void> {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await expect(page.locator("html")).toHaveAttribute("data-theme", scheme);
    const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
    const report = violations
      .map(
        (v) =>
          `${v.id} (${v.impact ?? "unknown"}): ${v.help}\n` +
          v.nodes.map((n) => `    ${n.target.join(" ")}`).join("\n"),
      )
      .join("\n");
    expect(violations, `accessibility violations on ${label} (${scheme}):\n${report}`).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: null });
}
