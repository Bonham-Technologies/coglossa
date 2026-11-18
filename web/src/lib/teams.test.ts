import { rememberedTeam, rememberTeam } from "./teams";

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
