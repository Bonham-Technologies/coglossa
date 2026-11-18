import { highlight } from "./highlight";

it("marks every occurrence of each excerpt", () => {
  expect(highlight("The cat sleeps. The cat sleeps.", ["sleeps"])).toEqual([
    { text: "The cat ", mark: false },
    { text: "sleeps", mark: true },
    { text: ". The cat ", mark: false },
    { text: "sleeps", mark: true },
    { text: ".", mark: false },
  ]);
});

it("merges overlapping excerpts and ignores empty ones", () => {
  expect(highlight("abcdef", ["bcd", "cde", ""])).toEqual([
    { text: "a", mark: false },
    { text: "bcde", mark: true },
    { text: "f", mark: false },
  ]);
  expect(highlight("abc", [])).toEqual([{ text: "abc", mark: false }]);
  expect(highlight("", ["x"])).toEqual([]);
});

it("keeps astral-plane characters whole and handles Arabic text", () => {
  expect(highlight("a😀b", ["😀"])).toEqual([
    { text: "a", mark: false },
    { text: "😀", mark: true },
    { text: "b", mark: false },
  ]);
  expect(highlight("مرحبا بالعالم", ["بالعالم"])).toEqual([
    { text: "مرحبا ", mark: false },
    { text: "بالعالم", mark: true },
  ]);
});

it("treats regex metacharacters in an excerpt literally", () => {
  expect(highlight("cost (a+b)* ? $5", ["(a+b)*", "$5", "."])).toEqual([
    { text: "cost ", mark: false },
    { text: "(a+b)*", mark: true },
    { text: " ? ", mark: false },
    { text: "$5", mark: true },
  ]);
});
