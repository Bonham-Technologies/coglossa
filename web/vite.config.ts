/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { gzipSync } from "node:zlib";
import { defineConfig, type Plugin } from "vite";

// CGL-39: the stylesheets index.html links block the first paint, so a first visit downloads and
// parses all of them before anything shows. The build fails when they pass this budget (raw
// bytes), or when any built stylesheet still names a .woff font. Only Noto Sans, the face of the
// app's own words, is in them; the other scripts' faces load after first paint (src/fonts.ts).
const ENTRY_CSS_BUDGET = 150_000;

// Each @fontsource rule lists a .woff2 and a .woff source. Every browser Coglossa supports reads
// woff2, so the .woff half only made the CSS longer and put unused files in the build.
function woff2Only(): Plugin {
  return {
    name: "coglossa:woff2-only",
    enforce: "pre",
    transform(code, id) {
      if (!/[\\/]@fontsource[\\/][^?]+\.css(\?|$)/.test(id)) return null;
      return {
        code: code.replace(/,\s*url\([^)]+\.woff\)\s*format\(["']woff["']\)/g, ""),
        map: null,
      };
    },
  };
}

function entryCssBudget(budget: number): Plugin {
  let base = "/";
  return {
    name: "coglossa:entry-css-budget",
    apply: "build",
    configResolved(config) {
      base = config.base;
    },
    writeBundle(_options, bundle) {
      const html = bundle["index.html"];
      if (html?.type !== "asset") return this.error("the build has no index.html to measure");
      const sheets = [...String(html.source).matchAll(/<link\b[^>]*>/g)]
        .map((m) => m[0])
        .filter((tag) => /\brel="stylesheet"/.test(tag))
        .map((tag) => /\bhref="([^"]+)"/.exec(tag)?.[1] ?? "")
        .map((href) => (href.startsWith(base) ? href.slice(base.length) : href));
      let raw = 0;
      let gzip = 0;
      for (const file of sheets) {
        const asset = bundle[file];
        if (asset?.type !== "asset") this.error(`index.html links ${file}, which the build lacks`);
        raw += Buffer.byteLength(asset.source);
        gzip += gzipSync(asset.source).byteLength;
      }
      const kb = (n: number) => `${(n / 1000).toFixed(1)} kB`;
      this.info(`render-blocking CSS ${kb(raw)} (gzip ${kb(gzip)}), budget ${kb(budget)}`);
      if (raw > budget) {
        this.error(
          `the stylesheets that block first paint (${sheets.join(", ")}) are ${kb(raw)}, over ` +
            `the ${kb(budget)} budget (CGL-39): load large font rules in a separate stylesheet`,
        );
      }
      for (const asset of Object.values(bundle)) {
        if (asset.type !== "asset" || !asset.fileName.endsWith(".css")) continue;
        if (/format\(["']?woff["']?\)/.test(String(asset.source))) {
          this.error(`${asset.fileName} names a .woff font; ship woff2 only (CGL-39)`);
        }
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), woff2Only(), entryCssBudget(ENTRY_CSS_BUDGET)],
  build: {
    // Vite would inline small fonts into the CSS as data: URLs, so every visit downloaded them
    // whether or not the page used them. As files, a font downloads only when the page shows
    // characters in its range.
    assetsInlineLimit: (file) => (/\.woff2?$/.test(file) ? false : undefined),
  },
  server: {
    // `pnpm dev` talks to a local API (`make dev-stack`); server-sent events pass through as is.
    proxy: { "/api": { target: "http://127.0.0.1:8080", changeOrigin: false } },
  },
  test: {
    globals: true,
    environment: "./src/test/jsdom-env.ts",
    setupFiles: ["./src/test/setup.ts"],
    // Several page tests click through a whole document; under coverage on a busy machine they
    // can pass 5 s (the default) while being correct. 15 s still fails a hung test quickly.
    testTimeout: 15_000,
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/api/schema.d.ts", // generated from schema/openapi.json; types only
        "src/main.tsx", // mounts the app into the page: exercised by the Playwright journeys
        "src/test/**", // the test harness itself
        "src/**/*.test.{ts,tsx}", // tests are not product code
      ],
      reporter: ["text", "lcov"],
      thresholds: { lines: 95, branches: 95, functions: 95, statements: 95 },
    },
  },
});
