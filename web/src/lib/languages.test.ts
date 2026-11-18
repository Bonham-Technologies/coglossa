import { tiers } from "../test/fixtures";
import {
  knownLanguages,
  languageName,
  lessPractised,
  RECENT_KEY,
  recentLanguages,
  rememberLanguage,
  resetRecentForTests,
  tierFor,
} from "./languages";

beforeEach(() => {
  localStorage.clear();
  resetRecentForTests();
});

it("names languages in the reader's language, falling back to the code", () => {
  expect(languageName("es", "en")).toBe("Spanish");
  expect(languageName("pt_BR", "en")).toBe("Brazilian Portuguese");
  expect(languageName("es", "fr")).toBe("espagnol");
  expect(languageName("zz", "en")).toBe("zz");
  expect(languageName("english!", "en")).toBe("english!");
});

it("finds the tier from the pair, then the language, then the default", () => {
  // fixture: default B; languages en A, fr A, ar B, am C; pairs fr>en A, en>am C
  expect(tierFor(tiers, "en", "fr")).toBe("A");
  expect(tierFor(tiers, "am", null)).toBe("C");
  expect(tierFor(tiers, "de", null)).toBe("B");
  expect(lessPractised(tiers, "am", "en")).toBe(true);
  expect(lessPractised(tiers, "fr", null)).toBe(false);
  expect(lessPractised(undefined, "am", null)).toBe(false);
  expect(lessPractised(tiers, "", null)).toBe(false);
});

it("lists the install's languages by name", () => {
  expect(knownLanguages(tiers, "en")).toEqual(["am", "ar", "en", "fr"]); // Amharic, Arabic, …
  expect(knownLanguages(undefined)).toEqual([]);
});

it("keeps the five most recent languages, newest first", () => {
  for (const code of ["es", "fr", "de", "it", "pt", "es"]) rememberLanguage(code);
  expect(recentLanguages()).toEqual(["es", "pt", "it", "de", "fr"]);
  localStorage.setItem(RECENT_KEY, JSON.stringify(["en", "not a code!", 7]));
  resetRecentForTests();
  expect(recentLanguages()).toEqual(["en"]);
  localStorage.setItem(RECENT_KEY, "{broken");
  expect(recentLanguages()).toEqual([]);
});

it("recent languages survive storage that throws", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  rememberLanguage("es");
  expect(recentLanguages()).toEqual(["es"]);
});

it("builds the name formatter once per locale, not once per comparison", () => {
  const Real = Intl.DisplayNames;
  const made = vi.spyOn(Intl, "DisplayNames").mockImplementation(function (
    ...args: ConstructorParameters<typeof Real>
  ) {
    return new Real(...args);
  } as never);
  const codes = knownLanguages(tiers, "it");
  expect(codes).toEqual(["am", "ar", "fr", "en"]); // amarico, arabo, francese, inglese
  expect(languageName("fr", "it")).toBe("francese");
  expect(made).toHaveBeenCalledTimes(1);
});

it("hands out a copy of the recent languages, at most five valid ones", () => {
  localStorage.setItem(
    RECENT_KEY,
    JSON.stringify(["en", "fr", "not a code!", "de", "it", "pt", "es", "nl"]),
  );
  const first = recentLanguages();
  expect(first).toEqual(["en", "fr", "de", "it", "pt"]);
  first.push("zz");
  expect(recentLanguages()).toEqual(["en", "fr", "de", "it", "pt"]);
  // the in-memory fallback is copied too
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  rememberLanguage("es");
  recentLanguages().push("zz");
  expect(recentLanguages()).toEqual(["es", "en", "fr", "de", "it"]);
});

it("counts a repeated recent language once before capping at five", () => {
  localStorage.setItem(
    RECENT_KEY,
    JSON.stringify(["en", "en", "fr", "en", "de", "it", "pt", "es"]),
  );
  expect(recentLanguages()).toEqual(["en", "fr", "de", "it", "pt"]);
});
