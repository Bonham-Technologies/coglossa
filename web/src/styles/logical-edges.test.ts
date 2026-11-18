import { readFileSync } from "node:fs";

// Passages run in their own direction (dir="auto"), so the flagged bar must sit on the passage's
// start edge: a logical property follows the text, a physical inset shadow stays on the left.
it("puts the flagged passage bar on the start edge", () => {
  const css = readFileSync("src/styles/review.css", "utf8");
  const rule = /\.document tr\.flagged td\.source\s*\{([^}]*)\}/.exec(css);
  expect(rule).not.toBeNull();
  expect(rule![1]).toMatch(/border-inline-start:\s*3px solid var\(--warn\)/);
  expect(rule![1]).not.toMatch(/box-shadow|border-left|border-right/);
});
