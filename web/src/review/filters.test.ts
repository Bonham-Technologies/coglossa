import { score, scoreFilterFrom } from "./filters";

it("clamps score bounds and ignores nonsense", () => {
  expect(score(null)).toBeNull();
  expect(score("  ")).toBeNull();
  expect(score("abc")).toBeNull();
  expect(score("150")).toBe(100);
  expect(score("-5")).toBe(0);
  expect(score("40")).toBe(40);
});

it("reads score bounds from an address", () => {
  expect(scoreFilterFrom(new URLSearchParams("min_meaning=-5&max_style=x"))).toEqual({
    min_meaning: 0,
  });
  expect(
    scoreFilterFrom(new URLSearchParams("min_meaning=10&max_meaning=90&min_style=20&max_style=80")),
  ).toEqual({ min_meaning: 10, max_meaning: 90, min_style: 20, max_style: 80 });
});
