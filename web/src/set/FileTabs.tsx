import { Link } from "react-router";
import type { DocumentSummary } from "../api/types";
import { StatusText } from "../components/StatusText";
import { runStateStatus, type Status } from "../lib/status";
import { dismissUpload, retryUpload, type UploadItem } from "../upload/queue";
import { newestIn } from "./languages";
import type { Picked } from "./SetPage";

/** A file translated into other languages but not the one shown (CGL-24). */
const NOT_STARTED: Status = { text: "not started", symbol: "○", tone: "none" };

function UploadTab({ item }: { item: UploadItem }) {
  if (item.status === "failed") {
    return (
      <div role="group" aria-label={item.name} className="tab failed">
        <span dir="auto">{item.name}</span> <span className="error">{item.error}</span>{" "}
        <button type="button" onClick={() => retryUpload(item.key)}>
          Try again
        </button>
        <button
          type="button"
          className="quiet"
          aria-label={`Remove ${item.name}`}
          onClick={() => dismissUpload(item.key)}
        >
          ×
        </button>
      </div>
    );
  }
  return (
    <div className="tab">
      <span dir="auto">{item.name}</span>{" "}
      <span className="status tone-busy">
        {item.status === "waiting"
          ? "waiting to upload"
          : item.status === "done"
            ? "uploaded"
            : "uploading…"}
      </span>
    </div>
  );
}

type Props = {
  projectId: string;
  docs: DocumentSummary[];
  uploads: UploadItem[];
  picked: Picked | null;
  lang: string;
};

/** Spec §5.1: one tab per file, shown when there is more than one file or an upload is under way. */
export function FileTabs({ projectId, docs, uploads, picked, lang }: Props) {
  if (docs.length <= 1 && uploads.length === 0) return null;
  return (
    <nav aria-label="Files" className="file-tabs">
      <ul>
        {docs.map((doc) => {
          const run = newestIn(doc, lang);
          const current = picked?.doc.id === doc.id;
          return (
            <li key={doc.id}>
              <Link
                to={
                  run
                    ? `/t/${projectId}/${run.id}`
                    : `/t/${projectId}?file=${doc.id}&lang=${encodeURIComponent(lang)}`
                }
                className={current ? "tab current" : "tab"}
                aria-current={current ? "page" : undefined}
              >
                <span dir="auto">{doc.filename}</span>
                {run ? (
                  <StatusText status={runStateStatus(run)} />
                ) : doc.runs.length > 0 ? (
                  <StatusText status={NOT_STARTED} />
                ) : null}
              </Link>
            </li>
          );
        })}
        {uploads.map((item) => (
          <li key={item.key}>
            <UploadTab item={item} />
          </li>
        ))}
      </ul>
    </nav>
  );
}
