import { readFileSync } from "node:fs";

// Read from disk: Vitest serves stylesheets imported with ?raw as empty strings
// (tests run from web/).
const tokens = readFileSync("src/styles/tokens.css", "utf8");

/** The colour values of one theme block of tokens.css. */
function block(selector: string): Record<string, string> {
  const start = tokens.indexOf(selector);
  if (start < 0) throw new Error(`${selector} is missing from tokens.css`);
  const body = tokens.slice(tokens.indexOf("{", start) + 1, tokens.indexOf("}", start));
  return Object.fromEntries(
    [...body.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map((m) => [m[1]!, m[2]!]),
  );
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

// Every foreground / background pair the stylesheets use. Add a row when you add a pair.
const PAIRS: [string, string, number][] = [
  ["text", "bg", 4.5],
  ["text", "surface", 4.5],
  ["text", "surface-2", 4.5],
  ["text", "warn-tint", 4.5],
  ["text", "warn-tint-strong", 4.5],
  ["text", "selected", 4.5],
  ["text-muted", "bg", 4.5],
  ["text-muted", "surface", 4.5],
  ["text-muted", "surface-2", 4.5],
  ["text-muted", "selected", 4.5],
  ["accent", "bg", 4.5],
  ["accent", "surface", 4.5],
  ["accent", "surface-2", 4.5],
  ["accent-text", "accent", 4.5],
  ["ok", "bg", 4.5],
  ["ok", "surface", 4.5],
  ["warn", "bg", 4.5],
  ["warn", "surface", 4.5],
  ["warn", "warn-tint", 4.5],
  ["danger", "bg", 4.5],
  ["danger", "surface", 4.5],
  // statuses and links on the selected sidebar row, on headers, and on tinted passages
  ["ok", "selected", 4.5],
  ["warn", "selected", 4.5],
  ["danger", "selected", 4.5],
  ["accent", "selected", 4.5],
  ["ok", "surface-2", 4.5],
  ["warn", "surface-2", 4.5],
  ["danger", "surface-2", 4.5],
  ["warn", "warn-tint-strong", 4.5],
  ["text-muted", "warn-tint", 4.5],
  ["accent", "warn-tint", 4.5],
  ["border-strong", "bg", 3],
  ["border-strong", "surface", 3],
  ["border-strong", "surface-2", 3],
  ["text-muted", "warn-tint-strong", 4.5],
];

describe.each([
  ["light", ':root[data-theme="light"]'],
  ["dark", ':root[data-theme="dark"]'],
])("the %s theme", (_name, selector) => {
  const colours = block(selector);
  it.each(PAIRS)("%s on %s reaches %s:1", (fg, bg, min) => {
    expect(colours[fg], `--${fg}`).toBeDefined();
    expect(colours[bg], `--${bg}`).toBeDefined();
    expect(contrast(colours[fg]!, colours[bg]!)).toBeGreaterThanOrEqual(min);
  });
});
