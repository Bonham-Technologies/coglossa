// Checks coglossa.tech before it ships. Run from site/: `pnpm install` once, then `node check.mjs`
// (`--launch` also fails on the owner placeholders, for the launch itself).
//
// Static: every internal link, fragment and asset resolves; each page has its metadata; the
// Open Graph image is 1200 × 630; the home page has one JSON-LD block that parses and names the
// Organization and the SoftwareApplication with their fields (CGL-85); vercel.json sends the
// security headers and a CSP that allows no inline or eval'd script, and no X-Robots-Tag;
// robots.txt and sitemap.xml agree with the canonical URL and robots.txt does not shut crawlers
// out; the site is under its size budget; nothing links to the source repository yet (CGL-78).
// In Chromium: public/ is served the way Vercel serves it (clean URLs and vercel.json's headers),
// and each page runs axe-core at 1280 px and 360 px, in the light and the dark theme, with no
// violations, no horizontal scrolling at 360 px, no failed request and no CSP violation.
import { createReadStream } from "node:fs";
import { readFile, readdir, stat } from "node:fs/promises";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { extname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const SITE = fileURLToPath(new URL(".", import.meta.url));
const PUBLIC = join(SITE, "public");
const ORIGIN = "https://coglossa.tech";
const REPO_URL = "https://github.com/Bonham-Technologies/coglossa";
const BUDGET_BYTES = 3 * 1024 * 1024;
const LAUNCH = process.argv.includes("--launch");
const PAGES = ["index.html", "404.html"]; // the site's own pages; sample/export.html is the app's
const failures = [];
const notes = [];
const fail = (msg) => failures.push(msg);

const config = JSON.parse(await readFile(join(SITE, "vercel.json"), "utf-8"));

// ---------------------------------------------------------------- files and HTML

async function walk(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await walk(path)));
    else out.push(path);
  }
  return out;
}

const files = await walk(PUBLIC);
const rel = (p) => relative(PUBLIC, p).split(sep).join("/");
const fileSet = new Set(files.map(rel));

function tags(html, name) {
  const re = new RegExp(`<${name}\\b([^>]*)>`, "gi");
  return [...html.matchAll(re)].map((m) => attributes(m[1]));
}

function attributes(text) {
  const attrs = {};
  for (const m of text.matchAll(/([a-zA-Z:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
    attrs[m[1].toLowerCase()] = m[2] ?? m[3] ?? m[4] ?? "";
  }
  return attrs;
}

// A JSON-LD block is data the browser never runs, so the CSP needs no script-src for it; it is
// the one <script> the site may have.
const jsonLd = (text) => [...text.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/gi)];

/** The file Vercel serves for a path, with cleanUrls: /a → a.html, / → index.html. */
function served(pathname) {
  let p = decodeURIComponent(pathname).replace(/^\/+/, "");
  if (p === "" || p.endsWith("/")) p += "index.html";
  if (fileSet.has(p)) return p;
  if (fileSet.has(`${p}.html`)) return `${p}.html`;
  if (fileSet.has(`${p}/index.html`)) return `${p}/index.html`;
  return null;
}

const htmlCache = new Map();
async function html(file) {
  if (!htmlCache.has(file)) htmlCache.set(file, await readFile(join(PUBLIC, file), "utf-8"));
  return htmlCache.get(file);
}

async function ids(file) {
  return new Set([...(await html(file)).matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
}

/** Check one reference made by `from`. */
async function reference(from, ref, what) {
  if (!ref || ref.startsWith("mailto:")) return;
  if (/^https?:\/\//.test(ref)) {
    const url = new URL(ref);
    // The only GitHub link the site may carry is the public repository itself (CGL-78).
    if (/github\.com$/i.test(url.hostname) && url.href.replace(/\/$/, "") !== REPO_URL)
      fail(`${from}: links to ${ref}, a GitHub page other than the repository`);
    if (url.origin === ORIGIN) return reference(from, url.pathname + url.hash, what);
    notes.push(`${from}: external ${what} ${ref}`);
    return;
  }
  if (/^[a-z]+:/i.test(ref)) return fail(`${from}: unexpected ${what} ${ref}`);
  const base = new URL(`/${from}`, "https://x.invalid");
  const url = new URL(ref, base);
  const target = url.pathname === base.pathname && ref.startsWith("#") ? from : served(url.pathname);
  if (!target) return fail(`${from}: ${what} ${ref} does not resolve`);
  if (url.hash && target.endsWith(".html")) {
    const id = decodeURIComponent(url.hash.slice(1));
    if (!(await ids(target)).has(id)) fail(`${from}: ${ref} points at a missing #${id}`);
  }
}

async function checkPage(file) {
  const text = await html(file);
  const head = text.split(/<\/head>/i)[0];
  if (!/<html\b[^>]*\blang="[a-z]{2}/i.test(text)) fail(`${file}: <html> has no lang`);
  const title = head.match(/<title>([^<]*)<\/title>/i)?.[1]?.trim();
  if (!title) fail(`${file}: no <title>`);
  const metas = tags(head, "meta");
  const meta = (k, v) => metas.find((m) => m[k] === v)?.content;
  if (!meta("name", "description")) fail(`${file}: no meta description`);
  if (!metas.some((m) => m.name === "viewport")) fail(`${file}: no viewport meta`);
  let scriptless = text;
  for (const [block, data] of jsonLd(text)) {
    // A '<' lets the HTML parser end the block early (`</script >`, `<!--`), so markup after
    // it would hide inside what is stripped here; JSON writes it as <.
    if (data.includes("<")) fail(`${file}: a JSON-LD block contains a '<'`);
    scriptless = scriptless.replace(block, "");
  }
  if (/<script\b/i.test(scriptless))
    fail(`${file}: has a <script>: the site ships no JavaScript (JSON-LD data blocks only)`);
  if (/<style\b/i.test(text) || /\sstyle="/i.test(text))
    fail(`${file}: inline style: the CSP allows only styles.css`);
  if (/\son[a-z]+="/i.test(text)) fail(`${file}: an inline event handler`);

  if (file === "index.html") {
    const canonical = tags(head, "link").find((l) => l.rel === "canonical")?.href;
    if (canonical !== `${ORIGIN}/`) fail(`index.html: canonical is ${canonical}, not ${ORIGIN}/`);
    for (const p of ["og:title", "og:description", "og:url", "og:image", "og:type"]) {
      if (!meta("property", p)) fail(`index.html: no ${p}`);
    }
    if (meta("property", "og:url") !== `${ORIGIN}/`) fail("index.html: og:url is not the canonical URL");
    const og = meta("property", "og:image") ?? "";
    if (!og.startsWith(`${ORIGIN}/`)) fail(`index.html: og:image ${og} is not absolute on ${ORIGIN}`);
    const ogFile = served(new URL(og, ORIGIN).pathname);
    if (!ogFile) fail(`index.html: og:image ${og} does not resolve`);
    else {
      const png = await readFile(join(PUBLIC, ogFile));
      const [w, h] = [png.readUInt32BE(16), png.readUInt32BE(20)];
      if (w !== 1200 || h !== 630) fail(`${ogFile} is ${w} × ${h}, not 1200 × 630`);
      if (meta("property", "og:image:width") !== "1200" || meta("property", "og:image:height") !== "630")
        fail("index.html: og:image:width/height are not 1200/630");
    }
    if (!meta("name", "twitter:card")) fail("index.html: no twitter:card");
    if (!tags(head, "link").some((l) => l.rel === "icon")) fail("index.html: no favicon");
    checkJsonLd(jsonLd(text).map((m) => m[1]));
  }

  for (const a of tags(text, "a")) await reference(file, a.href, "link");
  for (const l of tags(text, "link")) if (l.rel !== "canonical") await reference(file, l.href, "link");
  for (const t of ["img", "source", "track", "video"]) {
    for (const e of tags(text, t)) {
      await reference(file, e.src, t);
      await reference(file, e.poster, "poster");
      for (const part of (e.srcset ?? "").split(",").filter(Boolean))
        await reference(file, part.trim().split(/\s+/)[0], "srcset");
    }
  }
  for (const img of tags(text, "img")) {
    if (!("alt" in img)) fail(`${file}: <img src="${img.src}"> has no alt`);
    if (!img.width || !img.height) fail(`${file}: <img src="${img.src}"> has no width/height`);
  }
  for (const m of text.matchAll(/data-owner-check="([^"]+)"/g)) {
    const msg = `${file}: owner placeholder "${m[1]}" is still on the page`;
    if (LAUNCH) fail(msg);
    else notes.push(`OWNER CHECK ${msg}`);
  }
}

/** The home page's structured data (CGL-85): one block, a @graph with the Organization and the
 * SoftwareApplication it publishes. Every claim in it must match the README. */
function checkJsonLd(blocks) {
  if (blocks.length !== 1) return fail(`index.html: ${blocks.length} JSON-LD blocks, not 1`);
  let ld;
  try {
    ld = JSON.parse(blocks[0]);
  } catch (e) {
    return fail(`index.html: the JSON-LD does not parse: ${e.message}`);
  }
  if (ld["@context"] !== "https://schema.org") fail("index.html: JSON-LD @context is not https://schema.org");
  const graph = Array.isArray(ld["@graph"]) ? ld["@graph"] : [];
  if (!graph.length) return fail("index.html: the JSON-LD has no @graph");
  const node = (type) => {
    const found = graph.filter((n) => n["@type"] === type);
    if (found.length !== 1) fail(`index.html: JSON-LD has ${found.length} ${type} nodes, not 1`);
    return found[0] ?? {};
  };
  const text = (v) => typeof v === "string" && v.trim() !== "";
  const org = node("Organization");
  if (!text(org["@id"])) fail("index.html: the JSON-LD Organization has no @id");
  if (org.name !== "Bonham Technologies") fail(`index.html: JSON-LD Organization name is ${org.name}`);
  if (org.url !== "https://bonham.tech/") fail(`index.html: JSON-LD Organization url is ${org.url}`);
  const logo = typeof org.logo === "string" ? org.logo : (org.logo?.url ?? "");
  if (!logo.startsWith(`${ORIGIN}/`)) fail(`index.html: JSON-LD logo ${logo} is not absolute on ${ORIGIN}`);
  else if (!served(new URL(logo).pathname)) fail(`index.html: JSON-LD logo ${logo} does not resolve`);

  const app = node("SoftwareApplication");
  if (app.name !== "Coglossa") fail(`index.html: JSON-LD SoftwareApplication name is ${app.name}`);
  if (app.url !== `${ORIGIN}/`) fail(`index.html: JSON-LD SoftwareApplication url is not ${ORIGIN}/`);
  for (const k of ["description", "applicationCategory", "operatingSystem"]) {
    if (!text(app[k])) fail(`index.html: JSON-LD SoftwareApplication has no ${k}`);
  }
  const license = typeof app.license === "string" ? app.license : "";
  if (!/apache\.org\/licenses\/LICENSE-2\.0/.test(license))
    fail(`index.html: JSON-LD license ${license} is not the Apache License 2.0 (README.md, LICENSE)`);
  if (!text(org["@id"]) || app.publisher?.["@id"] !== org["@id"])
    fail("index.html: JSON-LD publisher is not the Organization's @id");
  // Offers only for a price the README states: free to use is price 0, with a currency.
  for (const offer of [app.offers ?? []].flat()) {
    if (String(offer.price) !== "0" || !text(offer.priceCurrency))
      fail("index.html: a JSON-LD offer is not price 0 with a priceCurrency");
  }
}

for (const page of [...PAGES, "sample/export.html"]) {
  if (!fileSet.has(page)) fail(`public/${page} is missing`);
  else if (PAGES.includes(page)) await checkPage(page);
}
for (const css of files.filter((f) => f.endsWith(".css"))) {
  const text = await readFile(css, "utf-8");
  for (const m of text.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/g)) {
    if (!m[1].startsWith("data:")) await reference(rel(css), m[1], "url()");
  }
}

// ---------------------------------------------------------------- the sample matches the report

// The sample passage on the home page is copied from public/sample/report.json: it must still
// say what the report says after the demo is captured again.
{
  const report = JSON.parse(await readFile(join(PUBLIC, "sample/report.json"), "utf-8"));
  const page = (await html("index.html")).replace(/\s+/g, " ");
  const span = report.spans.find((s) => s.flags.length > 0);
  if (!span) fail("sample/report.json: no flagged passage for the sample");
  else {
    const source = report.segments.find((s) => s.id === span.start)?.text;
    const revision = report.revisions.find((r) => r.span_id === span.id);
    const expected = [
      source,
      span.text,
      span.back_translation,
      `meaning ${span.score.meaning} / 100`,
      `style ${span.score.style} / 100`,
      `tier ${span.score.reliability}`,
      ...span.flags.map((f) => f.explanation),
      ...span.judge_errors.map((e) => e.explanation),
      ...report.candidates.filter((c) => c.start === span.start).map((c) => c.text),
      revision?.before,
    ];
    for (const text of expected) {
      if (!page.includes(String(text))) fail(`index.html: the sample no longer matches the report: "${text}"`);
    }
  }
}

// ---------------------------------------------------------------- robots, sitemap, size

const robots = await readFile(join(PUBLIC, "robots.txt"), "utf-8").catch(() => "");
if (!robots.includes(`Sitemap: ${ORIGIN}/sitemap.xml`)) fail("robots.txt does not name the sitemap");
// The private preview shuts crawlers out (deploy/hosted/vercel, CGL-85); the public site must not.
if (/^\s*Disallow:\s*\/\s*$/im.test(robots)) fail("robots.txt disallows the whole site");
const sitemap = await readFile(join(PUBLIC, "sitemap.xml"), "utf-8").catch(() => "");
if (!sitemap.includes(`<loc>${ORIGIN}/</loc>`)) fail("sitemap.xml does not list the canonical URL");

let total = 0;
for (const f of files) total += (await stat(f)).size;
if (total > BUDGET_BYTES) fail(`public/ is ${(total / 1048576).toFixed(2)} MB, over the 3 MB budget`);

// ---------------------------------------------------------------- vercel.json

/** The headers Vercel sends for a path: every matching rule, later rules winning. */
function headersFor(pathname) {
  const out = {};
  for (const rule of config.headers ?? []) {
    if (new RegExp(`^${rule.source}$`).test(pathname)) {
      for (const h of rule.headers) out[h.key.toLowerCase()] = h.value;
    }
  }
  return out;
}

function directives(csp) {
  return Object.fromEntries(
    csp
      .split(";")
      .map((d) => d.trim().split(/\s+/))
      .filter((d) => d[0])
      .map(([k, ...v]) => [k.toLowerCase(), v]),
  );
}

if (config.cleanUrls !== true) fail("vercel.json: cleanUrls is not on");
if (config.outputDirectory !== "public") fail("vercel.json: outputDirectory is not public");
if (!/git diff --quiet HEAD\^ HEAD -- \./.test(config.ignoreCommand ?? ""))
  fail("vercel.json: ignoreCommand does not skip deploys without a change under site/");

const exportHtml = await html("sample/export.html");
if ((exportHtml.match(/<link rel="canonical"[^>]*>/g) ?? []).join() !== '<link rel="canonical" href="https://coglossa.tech/sample/export">')
  fail("sample/export.html: needs exactly one canonical tag for https://coglossa.tech/sample/export");
const styleHashes = [...exportHtml.matchAll(/<style>([\s\S]*?)<\/style>/g)].map(
  (m) => `'sha256-${createHash("sha256").update(m[1], "utf-8").digest("base64")}'`,
);
for (const path of ["/", "/404", "/styles.css", "/sample/export", "/sample/report.json"]) {
  const h = headersFor(path);
  const csp = h["content-security-policy"];
  if (!csp) {
    fail(`vercel.json: no Content-Security-Policy for ${path}`);
    continue;
  }
  const d = directives(csp);
  const scripts = d["script-src"] ?? d["default-src"] ?? [];
  for (const bad of ["'unsafe-inline'", "'unsafe-eval'", "*", "data:", "http:", "https:"]) {
    if (scripts.includes(bad)) fail(`vercel.json: scripts allow ${bad} on ${path}`);
  }
  if (!["'none'", "'self'"].includes(d["default-src"]?.join(" ")))
    fail(`vercel.json: default-src is not 'none' or 'self' on ${path}`);
  if (d["frame-ancestors"]?.join(" ") !== "'none'") fail(`vercel.json: frame-ancestors is not 'none' on ${path}`);
  if ((d["style-src"] ?? []).includes("'unsafe-inline'")) fail(`vercel.json: style-src allows 'unsafe-inline' on ${path}`);
  if (path === "/sample/export") {
    for (const hash of styleHashes)
      if (!(d["style-src"] ?? []).includes(hash))
        fail(`vercel.json: the export's <style> needs ${hash} in style-src (the export changed?)`);
  }
  if (!/max-age=\d{8,}/.test(h["strict-transport-security"] ?? "")) fail(`vercel.json: no HSTS of a year or more on ${path}`);
  if (h["x-content-type-options"] !== "nosniff") fail(`vercel.json: no nosniff on ${path}`);
  if (!h["referrer-policy"]) fail(`vercel.json: no Referrer-Policy on ${path}`);
  if (!h["permissions-policy"]) fail(`vercel.json: no Permissions-Policy on ${path}`);
  if (h["cross-origin-opener-policy"] !== "same-origin") fail(`vercel.json: no COOP same-origin on ${path}`);
  if (h["x-robots-tag"]) fail(`vercel.json: X-Robots-Tag on ${path}: only the private preview sends one`);
}

// ---------------------------------------------------------------- in a browser

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".txt": "text/plain; charset=utf-8",
  ".xml": "application/xml",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".webp": "image/webp",
  ".webm": "video/webm",
  ".vtt": "text/vtt; charset=utf-8",
  ".woff2": "font/woff2",
};

const server = createServer((req, res) => {
  const { pathname } = new URL(req.url, "http://localhost");
  if (pathname.endsWith(".html") && pathname !== "/404.html") {
    // cleanUrls: Vercel redirects /a.html to /a.
    res.writeHead(308, { location: pathname.replace(/(index)?\.html$/, "") });
    return res.end();
  }
  const file = served(pathname);
  const headers = headersFor(pathname);
  if (!file) {
    res.writeHead(404, { ...headers, "content-type": TYPES[".html"] });
    return createReadStream(join(PUBLIC, "404.html")).pipe(res);
  }
  res.writeHead(200, { ...headers, "content-type": TYPES[extname(file)] ?? "application/octet-stream" });
  createReadStream(join(PUBLIC, file)).pipe(res);
});
await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
const base = `http://127.0.0.1:${server.address().port}`;

const require = createRequire(import.meta.url);
const { chromium } = await import("playwright-core");
const axeSource = await readFile(require.resolve("axe-core/axe.min.js"), "utf-8");
const axeVersion = JSON.parse(await readFile(require.resolve("axe-core/package.json"), "utf-8")).version;
const executablePath =
  process.env.CHROMIUM ?? (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
const browser = await chromium.launch({ executablePath });
const browserVersion = browser.version();
const results = [];
try {
  for (const path of ["/", "/404", "/no-such-page", "/sample/export"]) {
    for (const width of [1280, 360]) {
      for (const scheme of ["light", "dark"]) {
        const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: scheme });
        const page = await context.newPage();
        const problems = [];
        page.on("console", (m) => {
          const expected = path === "/no-such-page" && m.text().includes("404");
          if (m.type() === "error" && !expected) problems.push(`console: ${m.text()}`);
        });
        page.on("response", (r) => {
          if (r.status() >= 400 && !(path === "/no-such-page" && r.url().endsWith(path)))
            problems.push(`${r.status()} ${r.url()}`);
        });
        page.on("requestfailed", (r) => problems.push(`failed ${r.url()}`));
        await page.goto(base + path, { waitUntil: "load" });
        // Load every lazy image, so a broken one shows up.
        await page.evaluate(() => document.querySelectorAll("img").forEach((i) => (i.loading = "eager")));
        await page.waitForLoadState("networkidle");
        await page.evaluate(() => document.fonts.ready);
        const broken = await page.evaluate(() =>
          [...document.images].filter((i) => !i.complete || i.naturalWidth === 0).map((i) => i.src),
        );
        problems.push(...broken.map((src) => `image did not load: ${src}`));
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        if (overflow > 0) problems.push(`scrolls sideways by ${overflow} px`);
        let violations = [];
        const axeRan = path !== "/sample/export"; // the app's own export, not the site's page
        if (axeRan) {
          // page.evaluate is not subject to the page's CSP, so axe runs under the real headers.
          await page.evaluate(axeSource);
          const axe = await page.evaluate(() => window.axe.run(document, { resultTypes: ["violations"] }));
          violations = axe.violations.map(
            (v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
          );
        }
        problems.push(...violations.map((v) => `axe ${v}`));
        results.push({ path, width, scheme, overflow, violations: axeRan ? violations.length : null });
        for (const p of problems) fail(`${path} at ${width} px, ${scheme}: ${p}`);
        await context.close();
      }
    }
  }
} finally {
  await browser.close();
  server.close();
}

// ---------------------------------------------------------------- report

console.log(`coglossa.tech check (${LAUNCH ? "launch" : "pre-launch"})`);
console.log(`  public/: ${files.length} files, ${(total / 1048576).toFixed(2)} MB (budget 3 MB)`);
console.log(`  axe-core ${axeVersion} in Chromium ${browserVersion}:`);
for (const r of results) {
  console.log(
    `    ${r.path.padEnd(14)} ${String(r.width).padStart(4)} px ${r.scheme.padEnd(5)}  ` +
      `${r.violations === null ? "axe not run (app export)" : `axe violations ${r.violations}`}  ` +
      `sideways scroll ${Math.max(0, r.overflow)} px`,
  );
}
for (const n of notes) console.log(`  note: ${n}`);
if (failures.length) {
  console.log(`\n${failures.length} problem(s):`);
  for (const f of failures) console.log(`  ✗ ${f}`);
  process.exit(1);
}
console.log("\nAll checks passed.");
