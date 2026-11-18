import type { DoctorReport } from "../api/types";

/** The parts without which a translation cannot start, and what to call them. */
function blockingPart(name: string): string | null {
  if (name === "backends" || name.startsWith("backend ")) return "the language models";
  if (name === "database") return "its database";
  if (name === "storage") return "its file storage";
  return null;
}

export function reportFailed(report: DoctorReport): boolean {
  return report.checks.some((c) => c.status === "fail");
}

/** Spec §6.2: what the installation checks mean for the reader, in one sentence. */
export function statusSentence(report: DoctorReport): string {
  const failed = report.checks.filter((c) => c.status === "fail");
  if (failed.length === 0) return "Everything is working.";
  for (const check of failed) {
    const part = blockingPart(check.name);
    if (part)
      return `Coglossa can't reach ${part}. Translations can't start until an administrator fixes it.`;
  }
  const other = failed.find((c) => c.name !== "scorer");
  if (other) return `Something needs an administrator's attention: ${other.name}.`;
  return "The quality checker is offline. Translations still finish but aren't double-checked.";
}
