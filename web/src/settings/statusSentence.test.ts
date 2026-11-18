import type { DoctorCheck } from "../api/types";
import { reportFailed, statusSentence } from "./statusSentence";

const check = (name: string, status: DoctorCheck["status"]): DoctorCheck => ({
  name,
  status,
  message: "",
  fix: "",
});
const report = (...checks: DoctorCheck[]) => ({
  ok: checks.every((c) => c.status !== "fail"),
  checks,
});

it("says everything is working when nothing failed", () => {
  expect(statusSentence(report(check("config", "pass"), check("scorer", "warn")))).toBe(
    "Everything is working.",
  );
  expect(reportFailed(report(check("config", "pass")))).toBe(false);
});

it("explains what a failure means for the reader", () => {
  expect(statusSentence(report(check("scorer", "fail")))).toBe(
    "The quality checker is offline. Translations still finish but aren't double-checked.",
  );
  expect(statusSentence(report(check("scorer", "fail"), check("backend local", "fail")))).toBe(
    "Coglossa can't reach the language models. Translations can't start until an administrator fixes it.",
  );
  expect(statusSentence(report(check("database", "fail")))).toBe(
    "Coglossa can't reach its database. Translations can't start until an administrator fixes it.",
  );
  expect(statusSentence(report(check("storage", "fail")))).toMatch(/can't reach its file storage/);
  expect(statusSentence(report(check("prompts", "fail")))).toBe(
    "Something needs an administrator's attention: prompts.",
  );
  expect(reportFailed(report(check("prompts", "fail")))).toBe(true);
});
