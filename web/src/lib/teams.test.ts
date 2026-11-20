import { rememberedTeam, rememberTeam, teamKey } from "./teams";

beforeEach(() => localStorage.clear());

it("remembers the last team while the person is still in it", () => {
  expect(rememberedTeam(["a", "b"])).toBe("a");
  rememberTeam("b");
  expect(rememberedTeam(["a", "b"])).toBe("b");
  expect(rememberedTeam(["a"])).toBe("a");
  rememberTeam("");
  expect(rememberedTeam(["a", "b"])).toBe(""); // Only me was the last choice
  expect(rememberedTeam([])).toBe("");
});

it("works without storage", () => {
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  expect(rememberedTeam(["a"])).toBe("a");
  expect(() => rememberTeam("a")).not.toThrow();
  get.mockRestore();
  set.mockRestore();
});

it("compares names as the server does: NFC, ASCII trim, A-Z only in any case", () => {
  expect(teamKey(" Ana\t")).toBe("ana");
  expect(teamKey("JOSÉ")).toBe("josÉ"); // only A-Z fold: É stays É, as in people.team_key
  expect(teamKey("José")).not.toBe(teamKey("JOSÉ"));
  expect(teamKey("Jose\u0301")).toBe(teamKey("José")); // decomposed é is the same name
  expect(teamKey("Straße")).not.toBe(teamKey("strasse"));
  expect(teamKey("ana\u00a0")).not.toBe("ana"); // only ASCII spaces are trimmed
  expect(teamKey("ana\u3000")).not.toBe("ana");
  expect(teamKey("\u212a")).toBe("k"); // NFC turns the Kelvin sign into K, then K folds
});
