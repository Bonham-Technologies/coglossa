// Turns the exports that capture-demo.mjs saved into the sample files the site serves:
// public/sample/report.json (an allowlist of the report's fields: no prompts, model calls, config or
// usage) and public/sample/export.html (the web-page export as the app wrote it). Refuses to write
// anything that looks like a prompt, an address or a secret.
//
//   node tools/sanitise-report.mjs        (reads tools/.exports/)
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";

const { values: args } = parseArgs({
  options: {
    exports: { type: "string", default: "tools/.exports" },
    out: { type: "string", default: "public/sample" },
  },
});
const IN = resolve(args.exports);
const OUT = resolve(args.out);

// What the report keeps. Everything else (run_id, llm_calls with every prompt and response,
// config, usage, the document's checksum) is dropped.
const KEEP = [
  "schema_version",
  "coglossa_version",
  "status",
  "profile",
  "guide",
  "guide_proposals",
  "segments",
  "candidates",
  "spans",
  "revisions",
  "language_tier",
  "notices",
  "errors",
];

// Anything that would mean a prompt, an internal address or a credential slipped through.
const FORBIDDEN = [
  /INPUT_JSON/,
  /\bYou are\b/,
  /"role":\s*"system"/,
  /https?:\/\//i,
  /127\.0\.0\.1|localhost|\.internal\b|\.local\b/i,
  /api[_-]?key|secret|token|password|bearer/i,
  /<script/i,
];

function refuseLeaks(name, text) {
  for (const pattern of FORBIDDEN) {
    if (pattern.test(text)) throw new Error(`${name} matches ${pattern}: not writing it`);
  }
}

const report = JSON.parse(await readFile(join(IN, "report.json"), "utf-8"));
const clean = Object.fromEntries(KEEP.filter((k) => k in report).map((k) => [k, report[k]]));
clean.document = { source_path: report.document.source_path, format: report.document.format };
const json = JSON.stringify(clean, null, 2) + "\n";
refuseLeaks("report.json", json);

// The web-page export, unchanged. vercel.json allows its one inline <style> block by its hash,
// and check.mjs fails when the hash no longer matches.
// The export is the app's own page; the site adds only a canonical tag so search engines index
// it at its public address (bonham-ops seo-check SEO-9).
const CANONICAL = '<link rel="canonical" href="https://coglossa.tech/sample/export">';
const raw = await readFile(join(IN, "export.html"), "utf-8");
const html = raw.includes('rel="canonical"') ? raw : raw.replace("</title>", `</title>\n${CANONICAL}`);
refuseLeaks("export.html", html);

await mkdir(OUT, { recursive: true });
await writeFile(join(OUT, "report.json"), json);
await writeFile(join(OUT, "export.html"), html);
console.log(`wrote ${join(OUT, "report.json")} and ${join(OUT, "export.html")}`);
