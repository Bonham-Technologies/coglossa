// Records the demo on coglossa.com from a running local Coglossa: screenshots, a short video
// and the exports of the run. See site/README.md, "Regenerating the demo".
//
//   node tools/capture-demo.mjs --base http://127.0.0.1:8781
//
// --base is a dev stack that serves the built web app (api/devstack.py --web-dir ../web/dist).
// Everything lands in git-ignored folders: PNG screenshots and walkthrough-raw.webm in
// tools/.raw/ (tools/optimise.sh makes the served files from them), and the run's exports in
// tools/.exports/ (tools/sanitise-report.mjs makes the served sample from them).
import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";

// Playwright records video with its own ffmpeg build. When the installed one belongs to another
// Playwright version, point PLAYWRIGHT_MODULE at that version's package (see site/README.md).
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? "playwright-core");

const { values: args } = parseArgs({
  options: {
    base: { type: "string", default: "http://127.0.0.1:8781" },
    out: { type: "string", default: "tools/.raw" },
    exports: { type: "string", default: "tools/.exports" },
    chromium: { type: "string", default: process.env.CHROMIUM ?? "/opt/pw-browsers/chromium" },
    only: { type: "string" }, // "video" or "shots"
  },
});

const OUT = resolve(args.out);
const EXPORTS = resolve(args.exports);

// A short original passage. The practice models put "[en]" in front of the source instead of
// translating; the practice judge flags any passage whose text contains MISTRANSLATED, which
// gives the walkthrough a passage to check.
const STORY = [
  "Le phare de Kerlann s'allume chaque soir à six heures. Anna, la gardienne, monte les cent douze marches sans se presser.",
  "Ce soir, la mer est calme. Un bateau MISTRANSLATED approche du port.",
  "Anna note l'heure dans son carnet, comme son père avant elle.",
].join("\n\n");
const FIX = "A boat is coming into the harbour.";

async function pickCode(page, label, code) {
  await page.getByLabel(label, { exact: true }).selectOption({ label: "Other language…" });
  await page.getByLabel(`${label}: language code`).fill(code);
}

async function pause(page, ms) {
  await page.waitForTimeout(ms);
}

/** Wait until the sidebar, which polls every few seconds, no longer shows the set as working. */
async function settled(page) {
  await page.waitForFunction(() => !document.body.innerText.includes("translating"), null, {
    timeout: 30_000,
  });
}

/** One pass through the app. `shot(name)` is called at each step worth a picture. */
async function journey(page, shot, slow) {
  const filename = "Le phare de Kerlann.txt";
  await page.goto(args.base + "/");
  await page.getByRole("heading", { name: "New translation" }).waitFor();
  await page.getByLabel("Choose files").setInputFiles({
    name: filename,
    mimeType: "text/plain",
    buffer: Buffer.from(STORY, "utf-8"),
  });
  await pickCode(page, "From", "fr");
  await pickCode(page, "To", "en");
  await pause(page, slow);
  await shot("01-new-translation");
  await page.getByRole("button", { name: "Translate" }).click();
  await page.waitForURL(/\/t\/[0-9a-f-]+$/);

  const summary = page.getByRole("region", { name: "Summary" });
  await summary.getByText("Looks good.").waitFor({ timeout: 90_000 }).catch(() => undefined);
  await page.getByRole("table", { name: "Document" }).waitFor({ timeout: 90_000 });
  // The sidebar catches up with the finished run a moment later.
  await settled(page);
  await pause(page, slow);
  await shot("02-summary");

  await page.getByRole("row").filter({ hasText: "MISTRANSLATED" }).first().click();
  const panel = page.getByRole("complementary", { name: /Passage \d+/ });
  await panel.getByRole("region", { name: "Why it's flagged" }).waitFor();
  await pause(page, slow);
  await shot("03-why-flagged");

  await panel.getByText("More details").click();
  await panel.getByRole("table", { name: "Signals" }).waitFor();
  await panel.getByText("More details").scrollIntoViewIfNeeded();
  await pause(page, slow);
  await shot("04-details", panel.locator("details.more-details"));
  await panel.getByText("More details").click();

  await panel.getByRole("button", { name: "Edit" }).click();
  await panel.getByLabel("Your translation").fill("");
  await panel.getByLabel("Your translation").pressSequentially(FIX, { delay: slow ? 25 : 0 });
  await pause(page, slow);
  await panel.getByRole("button", { name: "Save" }).click();
  await panel.getByRole("status").getByText("Changed and marked as checked.").waitFor();
  await pause(page, slow);
  await shot("05-edited");

  const always = panel.getByRole("region", { name: "Always translate" });
  await always.scrollIntoViewIfNeeded();
  await always.getByLabel("Always translate").fill("Kerlann");
  await always.getByLabel("as", { exact: true }).fill("Kerlann");
  await pause(page, slow);
  await always.getByRole("button", { name: "Apply to all" }).click();
  await always.getByRole("status").getByText("Added to the translation guide.").waitFor();
  await page.getByRole("button", { name: "Download" }).waitFor({ timeout: 90_000 });
  await settled(page);
  await pause(page, slow);

  await page.getByRole("button", { name: /^Translation guide/ }).click();
  const guide = page.getByRole("dialog", { name: "Translation guide" });
  await guide.getByRole("tab", { name: "Terms" }).click();
  await guide.getByRole("list", { name: "Terms" }).getByText("Kerlann").first().waitFor();
  await pause(page, slow);
  await shot("06-guide");
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Download" }).click();
  await page.getByRole("menuitem", { name: "Web page" }).waitFor();
  await pause(page, slow);
  await shot("07-download");
  await page.keyboard.press("Escape");
  await pause(page, slow);
  return page.url();
}

async function saveExports(page, url) {
  const set = await page.evaluate(async () => {
    const runs = await (await fetch("/api/runs")).json();
    return runs;
  });
  await mkdir(EXPORTS, { recursive: true });
  await writeFile(join(EXPORTS, "runs.json"), JSON.stringify(set, null, 2));
  const items = Array.isArray(set) ? set : (set.items ?? set.runs ?? []);
  const run = items[0];
  if (!run) throw new Error(`no run found after ${url}`);
  for (const [name, path] of [
    ["report.json", `/api/runs/${run.id}/report`],
    ["export.json", `/api/runs/${run.id}/exports/json`],
    ["export.html", `/api/runs/${run.id}/exports/html`],
  ]) {
    const r = await page.request.get(args.base + path);
    if (!r.ok()) throw new Error(`${path}: ${r.status()}`);
    await writeFile(join(EXPORTS, name), await r.body());
  }
  console.log(`exports of run ${run.id} in ${EXPORTS}`);
}

/** Delete the set the journey made, so that the next pass starts with an empty sidebar. */
async function deleteSet(page, url) {
  const id = new URL(url).pathname.split("/").pop();
  const project = await (await page.request.get(`${args.base}/api/projects/${id}`)).json();
  const name = encodeURIComponent(project.name ?? project.project?.name);
  const r = await page.request.delete(`${args.base}/api/projects/${id}?confirm=${name}`);
  if (!r.ok()) throw new Error(`deleting the set ${id}: ${r.status()}`);
}

const browser = await chromium.launch({ executablePath: args.chromium });
await mkdir(OUT, { recursive: true });
try {
  if (args.only !== "shots") {
    const size = { width: 1280, height: 760 };
    const context = await browser.newContext({
      viewport: size,
      colorScheme: "light",
      recordVideo: { dir: join(OUT, ".video"), size },
    });
    const page = await context.newPage();
    const url = await journey(page, async () => pause(page, 1600), 1100);
    await deleteSet(page, url);
    const video = page.video();
    await context.close();
    await rename(await video.path(), join(OUT, "walkthrough-raw.webm"));
    await rm(join(OUT, ".video"), { recursive: true, force: true });
    console.log("video: walkthrough-raw.webm");
  }
  if (args.only !== "video") {
    for (const scheme of ["light", "dark"]) {
      const context = await browser.newContext({
        viewport: { width: 1280, height: 760 },
        deviceScaleFactor: 2,
        colorScheme: scheme,
      });
      const page = await context.newPage();
      const url = await journey(
        page,
        async (name, locator) => {
          // The dark pass keeps two pictures for the site's dark theme.
          if (scheme === "dark" && !["02-summary", "03-why-flagged"].includes(name)) return;
          const file = join(OUT, `${name}-${scheme}.png`);
          if (locator) {
            // A tall element: grow the window so that the whole of it fits in one picture.
            const box = await locator.boundingBox();
            await page.setViewportSize({ width: 1280, height: Math.ceil(box.height) + 400 });
            await locator.scrollIntoViewIfNeeded();
            await locator.screenshot({ path: file, animations: "disabled" });
            await page.setViewportSize({ width: 1280, height: 760 });
          } else {
            await page.screenshot({ path: file, animations: "disabled" });
          }
          console.log(`shot: ${file}`);
        },
        0,
      );
      if (scheme === "light") await saveExports(page, url);
      await deleteSet(page, url);
      await context.close();
    }
  }
} finally {
  await browser.close();
}
