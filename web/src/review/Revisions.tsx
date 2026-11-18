import type { RevisionEntry } from "../api/types";
import { revisionSourceLabel } from "./labels";
import { formatDate } from "../lib/format";
import { useRunLanguages } from "./languages";

export function Revisions({ revisions }: { revisions: RevisionEntry[] }) {
  const { target } = useRunLanguages();
  return (
    <>
      <h3>History</h3>
      <ol aria-label="History" className="history">
        {revisions.map((r, i) => (
          <li key={i}>
            <span className="muted">
              {revisionSourceLabel(r.source)} · {formatDate(r.created_at)}
              {r.reason ? (
                <>
                  {" · "}
                  <span dir="auto">{r.reason}</span>
                </>
              ) : null}
            </span>
            <div>
              {r.before !== null ? (
                <>
                  <del dir="auto" lang={target}>
                    {r.before}
                  </del>{" "}
                  →{" "}
                </>
              ) : null}
              <ins dir="auto" lang={target}>
                {r.after}
              </ins>
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}
