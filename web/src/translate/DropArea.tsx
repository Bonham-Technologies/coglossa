import { useId, useState } from "react";

export const ACCEPTED = [
  ".txt",
  ".md",
  ".markdown",
  ".docx",
  ".html",
  ".htm",
  ".xhtml",
  ".epub",
  ".pdf",
  ".pptx",
  ".xlsx",
];

/** The kinds of file Coglossa reads, in words. */
export const FILE_KINDS = "Word, PowerPoint, Excel, PDF, text, Markdown, HTML or EPUB";

export const MB = 1024 * 1024;

export function sizeText(bytes: number): string {
  if (bytes < MB) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / MB).toFixed(1).replace(/\.0$/, "")} MB`;
}

function accepted(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && ACCEPTED.includes(name.slice(dot).toLowerCase());
}

/** Spec §4.1: what is wrong with a file, before it is uploaded; null when nothing is. */
export function fileProblem(file: File, maxMb: number | null): string | null {
  if (!accepted(file.name)) {
    return `${file.name} isn't a file type Coglossa reads (${FILE_KINDS}).`;
  }
  if (file.size === 0) return `${file.name} is empty.`;
  if (maxMb !== null && file.size > maxMb * MB) {
    return `${file.name} is ${sizeText(file.size)}, and the limit is ${maxMb} MB.`;
  }
  return null;
}

/** The same file dropped twice has the same name, size and modified time. */
const fileKey = (file: File) => `${file.name}\n${file.size}\n${file.lastModified}`;

type Props = {
  files: File[];
  onFiles: (files: File[]) => void;
  maxMb: number | null;
  /** False while another tab is showing: the notice is stale by the time it comes back. */
  active?: boolean;
};

export function DropArea({ files, onFiles, maxMb, active = true }: Props) {
  const [over, setOver] = useState(false);
  const inputId = useId();
  const [skipped, setSkipped] = useState<string[]>([]);
  // The notice is cleared when the area is hidden, during render so it never shows stale.
  const [wasActive, setWasActive] = useState(active);
  if (active !== wasActive) {
    setWasActive(active);
    if (!active) setSkipped([]);
  }
  const add = (list: FileList | File[] | null) => {
    if (!list || list.length === 0) return;
    const seen = new Set(files.map(fileKey));
    const fresh: File[] = [];
    const again: string[] = [];
    for (const file of Array.from(list)) {
      if (seen.has(fileKey(file))) {
        if (!again.includes(file.name)) again.push(file.name);
      } else {
        seen.add(fileKey(file));
        fresh.push(file);
      }
    }
    setSkipped(again);
    if (fresh.length > 0) onFiles([...files, ...fresh]);
  };
  return (
    <div className="drop">
      <label
        htmlFor={inputId}
        className={over ? "drop-area over" : "drop-area"}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          add(e.dataTransfer.files);
        }}
      >
        <strong>Drop files here, or click to choose</strong>
        <span className="muted">
          {FILE_KINDS}
          {maxMb !== null ? ` · up to ${maxMb} MB each` : ""}
        </span>
      </label>
      <input
        id={inputId}
        type="file"
        multiple
        accept={ACCEPTED.join(",")}
        aria-label="Choose files"
        className="visually-hidden"
        onChange={(e) => {
          add(e.target.files);
          e.target.value = "";
        }}
      />
      {/* Always mounted, so screen readers announce what is added to the live regions. */}
      <p role="status" aria-live="polite" className="muted drop-status">
        {skipped.length > 0 ? `Already added: ${skipped.join(", ")}` : null}
      </p>
      <ul aria-label="Chosen files" aria-live="polite" aria-relevant="additions" className="chips">
        {files.map((file, i) => {
          const problem = fileProblem(file, maxMb);
          return (
            <li key={`${file.name}-${i}`} className={problem ? "chip bad" : "chip"}>
              <span dir="auto">{file.name}</span>{" "}
              <span className="muted">{sizeText(file.size)}</span>
              {problem ? <span className="error"> {problem}</span> : null}
              <button
                type="button"
                className="quiet"
                aria-label={`Remove ${file.name}`}
                onClick={() => {
                  setSkipped([]);
                  onFiles(files.filter((_, k) => k !== i));
                }}
              >
                ×
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
