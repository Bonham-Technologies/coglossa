// Renders the site's fixed images with Chromium: public/og-image.png (1200 × 630, from
// tools/og.html), public/apple-touch-icon.png (180 × 180) and public/favicon.ico (48 × 48, a
// PNG inside an ICO wrapper), both from public/favicon.svg.
//
//   node tools/make-images.mjs           (CHROMIUM=/path/to/chrome to pick the browser)
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright-core";

const here = (p) => fileURLToPath(new URL(p, import.meta.url));
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM ?? "/opt/pw-browsers/chromium",
});

/** A one-image ICO file: modern browsers accept a PNG inside it. */
function ico(png, size) {
  const header = Buffer.alloc(22);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type: icon
  header.writeUInt16LE(1, 4); // one image
  header.writeUInt8(size, 6);
  header.writeUInt8(size, 7);
  header.writeUInt8(0, 8); // no palette
  header.writeUInt8(0, 9);
  header.writeUInt16LE(1, 10); // colour planes
  header.writeUInt16LE(32, 12); // bits per pixel
  header.writeUInt32LE(png.length, 14);
  header.writeUInt32LE(22, 18); // the PNG starts after this header
  return Buffer.concat([header, png]);
}

try {
  const og = await browser.newPage({ viewport: { width: 1200, height: 630 } });
  await og.goto(pathToFileURL(here("og.html")).href);
  await og.evaluate(() => document.fonts.ready);
  await og.screenshot({ path: here("../public/og-image.png") });

  const svg = await readFile(here("../public/favicon.svg"), "utf-8");
  for (const [size, out] of [
    [180, "../public/apple-touch-icon.png"],
    [48, null],
  ]) {
    const page = await browser.newPage({ viewport: { width: size, height: size } });
    await page.setContent(
      `<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`,
    );
    const png = await page.screenshot({ omitBackground: true });
    if (out) await writeFile(here(out), png);
    else await writeFile(here("../public/favicon.ico"), ico(png, size));
  }
  console.log("wrote og-image.png, apple-touch-icon.png and favicon.ico in public/");
} finally {
  await browser.close();
}
