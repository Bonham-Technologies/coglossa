// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { FONT_FAMILIES } from "./fonts";

const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")) as {
  dependencies: Record<string, string>;
};
const fontsSource = readFileSync(new URL("./fonts.ts", import.meta.url), "utf8");
const scriptFontsSource = readFileSync(new URL("./script-fonts.ts", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("./main.tsx", import.meta.url), "utf8");
const fontImports = (source: string) =>
  [...source.matchAll(/import "(@fontsource\/[^/]+)\//g)].map((m) => m[1]);
const css = readFileSync(new URL("./styles/base.css", import.meta.url), "utf8");
const fontPackages = Object.keys(pkg.dependencies).filter((d) => d.startsWith("@fontsource/"));

describe("bundled fonts (spec §8: the app works offline)", () => {
  it("covers the scripts the customer translates", () => {
    for (const family of [
      "Noto Sans",
      "Noto Sans SC",
      "Noto Sans TC",
      "Noto Sans Arabic",
      "Noto Nastaliq Urdu",
      "Noto Sans Armenian",
      "Noto Sans Georgian",
      "Noto Sans Tamil",
      "Noto Sans Khmer",
      "Noto Sans Myanmar",
      "Noto Serif Tibetan",
      "Noto Sans Ethiopic",
      "Noto Sans Tifinagh",
      "Noto Sans NKo",
      "Noto Sans Canadian Aboriginal",
    ]) {
      expect(FONT_FAMILIES).toContain(family);
    }
  });

  it("imports every font package it depends on, and nothing else", () => {
    const imported = new Set([...fontImports(fontsSource), ...fontImports(scriptFontsSource)]);
    expect([...imported].sort()).toEqual([...fontPackages].sort());
  });

  it("blocks first paint on the app's own face only, and loads the other scripts' after", () => {
    // CGL-39: the other faces' rules are most of the app's CSS. The build's budget measures
    // the result; this keeps the split itself in place.
    expect(new Set(fontImports(fontsSource))).toEqual(new Set(["@fontsource/noto-sans"]));
    expect(fontImports(scriptFontsSource)).not.toContain("@fontsource/noto-sans");
    expect(mainSource).toMatch(/^import "\.\/fonts";$/m);
    expect(mainSource).toMatch(/^import\("\.\/script-fonts"\)\.catch\(/m);
    expect(mainSource).not.toMatch(/^import "\.\/script-fonts";$/m);
  });

  it("names every family in the page's font stack, Noto Sans first", () => {
    const stack = /--font-stack:([^;]+);/.exec(css)?.[1] ?? "";
    const names = [...stack.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    expect(names).toEqual([...FONT_FAMILIES]);
    expect(names[0]).toBe("Noto Sans");
    expect(stack.trim().endsWith("sans-serif")).toBe(true);
    expect(FONT_FAMILIES.length).toBe(fontPackages.length);
  });

  it("gives each CJK language its own face first, then the whole stack", () => {
    const flat = css.replace(/\s+/g, " "); // prettier may break the selector list across lines
    const rules: [string, string][] = [
      [":lang(ja)", '"Noto Sans JP"'],
      [":lang(ko)", '"Noto Sans KR"'],
      [":lang(zh-Hant), :lang(zh-TW), :lang(zh-MO)", '"Noto Sans TC"'],
      [":lang(zh-HK)", '"Noto Sans HK"'],
    ];
    for (const [selector, face] of rules) {
      const start = flat.indexOf(`${selector} {`);
      const rule = start < 0 ? "" : flat.slice(start, flat.indexOf("}", start));
      expect(rule).toContain(`font-family: ${face}`);
      expect(rule).toMatch(/, (?:"[^"]+", )*var\(--font-stack\)/);
    }
  });
});
