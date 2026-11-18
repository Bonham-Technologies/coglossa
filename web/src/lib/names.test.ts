import { firstWords, numbered, pastedFile, setNameFor, withoutExtension } from "./names";

it("names a set after its files", () => {
  expect(setNameFor([new File(["x"], "Welcome letter.docx")])).toBe("Welcome letter");
  const three = ["a.txt", "b.txt", "c.txt"].map((n) => new File(["x"], n));
  expect(setNameFor(three)).toBe("a and 2 more");
  expect(setNameFor([new File(["x"], ".env")])).toBe(".env");
  expect(setNameFor([])).toBe("Translation");
  expect(setNameFor([new File(["x"], `${"x".repeat(300)}.txt`)])).toHaveLength(180);
});

it("makes a text file from pasted text, named after its first five words", () => {
  const file = pastedFile("  Welcome to the Riverside: Community Centre!\nMore text");
  expect(file.name).toBe("Welcome to the Riverside Community.txt");
  expect(file.type).toBe("text/plain");
  expect(pastedFile("??? ***").name).toBe("Pasted text.txt");
  expect(firstWords(" one  two\nthree ", 2)).toBe("one two");
  expect(withoutExtension("a.b.c")).toBe("a.b");
});

it("numbers repeated names", () => {
  expect(numbered("welcome", 1)).toBe("welcome");
  expect(numbered("welcome", 2)).toBe("welcome (2)");
});
