import { useState } from "react";
import { useFlagStatus } from "../api/review";
import type { SpanDetail } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { FLAG_STATUS_WORDS, flagTitle } from "../lib/flags";
import { formatScore } from "../lib/format";
import { Findings } from "./Findings";
import { Revisions } from "./Revisions";
import { Signals } from "./Signals";

function FlagStatusList({ runId, detail }: { runId: string; detail: SpanDetail }) {
  const change = useFlagStatus(runId);
  const [dismissing, setDismissing] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  if (detail.flags.length === 0) return null;
  return (
    <>
      <h3>Flags</h3>
      <ul aria-label="Flags" className="flag-list">
        {detail.flags.map((flag) => {
          const title = flagTitle(flag.code);
          return (
            <li key={flag.id}>
              {title}: {FLAG_STATUS_WORDS[flag.status]}
              {flag.dismiss_reason ? (
                <>
                  {" "}
                  (<span dir="auto">{flag.dismiss_reason}</span>)
                </>
              ) : null}{" "}
              {flag.status === "open" ? (
                <button
                  type="button"
                  aria-label={`Not a problem: ${title}`}
                  onClick={() => {
                    setDismissing(flag.id);
                    setReason("");
                  }}
                >
                  Not a problem…
                </button>
              ) : (
                <button
                  type="button"
                  aria-label={`Reopen: ${title}`}
                  disabled={change.isPending}
                  onClick={() => change.mutate({ flagId: flag.id, status: "open", reason: null })}
                >
                  Reopen
                </button>
              )}
              {dismissing === flag.id ? (
                <div className="row">
                  <label htmlFor={`dismiss-${flag.id}`}>Why isn&apos;t it a problem?</label>
                  <input
                    id={`dismiss-${flag.id}`}
                    dir="auto"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                  <button
                    type="button"
                    aria-label="Save reason"
                    disabled={reason.trim() === "" || change.isPending}
                    onClick={() =>
                      change.mutate(
                        { flagId: flag.id, status: "dismissed", reason: reason.trim() },
                        { onSuccess: () => setDismissing(null) },
                      )
                    }
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    aria-label="Cancel reason"
                    onClick={() => setDismissing(null)}
                  >
                    Cancel
                  </button>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      {change.isError ? <ErrorMessage error={change.error} /> : null}
    </>
  );
}

/** Spec §5.4 step 5: scores, signals, history and every flag, folded away. */
export function MoreDetails({ runId, detail }: { runId: string; detail: SpanDetail }) {
  return (
    <details className="more-details">
      <summary>More details</summary>
      <p>
        Meaning match {formatScore(detail.score.meaning)} / 100 · Style match{" "}
        {formatScore(detail.score.style)} / 100
      </p>
      <FlagStatusList runId={runId} detail={detail} />
      <Findings detail={detail} />
      <h3>Score details</h3>
      <Signals detail={detail} />
      <Revisions revisions={detail.revisions} />
    </details>
  );
}
