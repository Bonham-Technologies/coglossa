import { useId, useRef, useState } from "react";
import { ApiError, isGuideChanged } from "../api/client";
import { useImportGlossary, usePreviewGlossaryImport } from "../api/guide";
import type { GlossaryImportPreview, GlossaryImportResult } from "../api/types";
import { messageOf } from "../components/ErrorMessage";
import { plural } from "../lib/flags";

export const GLOSSARY_FILES = [".csv", ".tsv", ".tab", ".txt", ".tbx", ".xml"];
const SHOWN_CONFLICTS = 20;

/** CGL-88: what an import did, in plain words. */
export function importSummary(result: GlossaryImportResult): string {
  if (!result.created_version) return "Nothing changed: the guide already has these terms.";
  const parts: string[] = [];
  if (result.added > 0) parts.push(`added ${plural(result.added, "term")}`);
  if (result.updated > 0) parts.push(`replaced ${plural(result.updated, "translation")}`);
  // Only locking terms the guide already had adds no "Added 0 terms".
  const sentences = parts.length > 0 ? [`${parts.join(" and ")}.`] : [];
  if (result.locked > 0) sentences.push(`${plural(result.locked, "term")} will always be used.`);
  if (result.kept > 0)
    sentences.push(`${plural(result.kept, "term")} kept the guide's translation.`);
  const text = sentences.join(" ") || "The guide was saved.";
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A refused file's reason, as the server words it, after a plain lead-in. */
function problemText(error: unknown): string {
  if (isGuideChanged(error)) {
    return "Someone else changed the guide while you were importing. Choose the file again.";
  }
  if (error instanceof ApiError && (error.status === 413 || error.status === 422)) {
    return `This file can't be imported: ${error.message}`;
  }
  return messageOf(error);
}

function PreviewSummary({ preview }: { preview: GlossaryImportPreview }) {
  const lines: string[] = [`${plural(preview.new, "new term")} to add`];
  if (preview.identical > 0) {
    lines.push(`${plural(preview.identical, "term")} already in the guide`);
  }
  if (preview.conflicts > 0) {
    const locked =
      preview.locked_conflicts > 0 ? ` (${preview.locked_conflicts} of them always used)` : "";
    lines.push(
      `${plural(preview.conflicts, "term")} with a different translation in the guide${locked}`,
    );
  }
  if (preview.duplicates > 0) {
    lines.push(`${plural(preview.duplicates, "term")} listed twice in the file: the first is used`);
  }
  if (preview.skipped_entries > 0) {
    const n = preview.skipped_entries;
    lines.push(`${n} ${n === 1 ? "entry" : "entries"} left out: no term in both languages`);
  }
  const conflicts = preview.terms.filter((t) => t.status === "conflict");
  return (
    <>
      <ul aria-label="What the file adds">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {conflicts.length > 0 ? (
        <ul aria-label="Different translations" className="muted">
          {conflicts.slice(0, SHOWN_CONFLICTS).map((t) => (
            <li key={t.line}>
              <span dir="auto">{t.term}</span>: the guide says “
              <span dir="auto">{t.current_rendering}</span>”
              {t.current_locked ? " (always used)" : ""}, the file says “
              <span dir="auto">{t.rendering}</span>”
            </li>
          ))}
          {conflicts.length > SHOWN_CONFLICTS ? (
            <li>and {conflicts.length - SHOWN_CONFLICTS} more</li>
          ) : null}
        </ul>
      ) : null}
    </>
  );
}

/** CGL-88: bring terms in from a spreadsheet (CSV or tab-separated) or a TBX termbase. */
export function TermImport({
  projectId,
  targetLang,
  blocked,
  onImported,
}: {
  projectId: string;
  targetLang: string;
  blocked: boolean; // unsaved edits: an import would make a new version under them
  onImported: (result: GlossaryImportResult) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [lockAll, setLockAll] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const preview = usePreviewGlossaryImport(projectId, targetLang);
  const apply = useImportGlossary(projectId, targetLang);

  function reset() {
    setFile(null);
    setLockAll(false);
    setOverwrite(false);
    preview.reset();
    apply.reset();
  }

  function choose(chosen: File | undefined) {
    if (!chosen) return;
    reset();
    setFile(chosen);
    preview.mutate(chosen);
  }

  async function submit(file: File, found: GlossaryImportPreview) {
    try {
      const result = await apply.mutateAsync({
        file,
        base_version: found.base_version,
        lock_all: lockAll,
        overwrite_conflicts: overwrite,
      });
      reset();
      onImported(result);
    } catch {
      return; // shown below
    }
  }

  const found = preview.data;
  const nothing =
    found !== undefined && found.new === 0 && !lockAll && !(overwrite && found.conflicts > 0);
  return (
    <section aria-label="Import terms" className="term-import">
      <input
        ref={input}
        id={inputId}
        type="file"
        accept={GLOSSARY_FILES.join(",")}
        aria-label="Choose a glossary file"
        className="visually-hidden"
        onChange={(e) => {
          choose(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {file === null ? (
        <>
          <div className="row">
            <button type="button" disabled={blocked} onClick={() => input.current?.click()}>
              Import terms…
            </button>
          </div>
          <p className="muted">
            {blocked
              ? "Save your changes before importing terms."
              : "From a spreadsheet saved as CSV or tab-separated text (the term in the first column, its translation in the second), or a TBX termbase."}
          </p>
        </>
      ) : (
        <div className="notice">
          <p>
            <strong dir="auto">{file.name}</strong>
          </p>
          {preview.isPending ? <p role="status">Reading the file…</p> : null}
          {preview.isError ? (
            <p role="alert" className="error">
              {problemText(preview.error)}
            </p>
          ) : null}
          {found ? (
            <>
              <PreviewSummary preview={found} />
              <label>
                <input
                  type="checkbox"
                  checked={lockAll}
                  onChange={(e) => setLockAll(e.target.checked)}
                />{" "}
                Always use the translations of the imported terms
              </label>
              {found.conflicts > 0 ? (
                <label>
                  <input
                    type="checkbox"
                    checked={overwrite}
                    onChange={(e) => setOverwrite(e.target.checked)}
                  />{" "}
                  Use the file’s translation where it differs
                  {found.locked_conflicts > 0 ? ", even for terms the guide always uses" : ""}
                </label>
              ) : null}
            </>
          ) : null}
          {apply.isError ? (
            <p role="alert" className="error">
              {problemText(apply.error)}
            </p>
          ) : null}
          {found && blocked ? (
            <p className="muted">Save your changes before importing terms.</p>
          ) : null}
          <div className="row end">
            <button type="button" onClick={reset}>
              Cancel
            </button>
            {found ? (
              <button
                type="button"
                className="primary"
                // An import reloads the guide: edits made since the preview would be lost.
                disabled={nothing || blocked || apply.isPending}
                onClick={() => void submit(file, found)}
              >
                {nothing ? "Nothing to add" : "Add the terms"}
              </button>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}
