import { formatDate, formatScore, isLanguageCode, isTerminal } from "./format";

it("knows which run states are final", () => {
  expect(isTerminal("failed")).toBe(true);
  expect(isTerminal("sweeping")).toBe(false);
});

it("formats scores and dates", () => {
  expect(formatScore(null)).toBe("—");
  expect(formatScore(undefined)).toBe("—");
  expect(formatScore(71.6)).toBe("72");
  expect(formatDate(null)).toBe("—");
  expect(formatDate("2026-10-01T09:00:00Z")).toMatch(/2026/);
});

it("checks language codes like the API does", () => {
  for (const ok of ["en", "pt-BR", "zh-Hant-TW", "es_419", "fil"]) {
    expect(isLanguageCode(ok)).toBe(true);
  }
  for (const bad of ["", "e", "en--US", "1234", "english language"]) {
    expect(isLanguageCode(bad)).toBe(false);
  }
});
