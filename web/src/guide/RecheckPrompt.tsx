import { useState } from "react";
import { recheckFailureText, useRecheckSet, type RecheckResult } from "../api/sets";
import { ErrorMessage } from "../components/ErrorMessage";
import { plural } from "../lib/flags";

function recheckSummary({ started, failed }: RecheckResult): string {
  const parts: string[] = [];
  if (started.length > 0) parts.push(`Rechecking ${plural(started.length, "file")}…`);
  if (failed.length > 0) parts.push(recheckFailureText(failed.length));
  if (parts.length === 0) {
    parts.push("Nothing to recheck yet. New translations in this set will follow the guide.");
  }
  return parts.join(" ");
}

/** After a guide change: offer to bring the set's finished translations in line with it. */
export function RecheckPrompt({
  projectId,
  targetLang,
  onDone,
}: {
  projectId: string;
  targetLang: string;
  onDone: () => void;
}) {
  const recheck = useRecheckSet(projectId, targetLang);
  // Kept across a retry, so the summary counts every file that started.
  const [result, setResult] = useState<RecheckResult | null>(null);
  function run(only?: string[]) {
    recheck.mutate(only, {
      onSuccess: (next) =>
        setResult((prev) =>
          only !== undefined && prev !== null
            ? {
                started: [...prev.started, ...next.started],
                skipped: [...prev.skipped, ...next.skipped],
                failed: next.failed,
              }
            : next,
        ),
    });
  }
  return (
    <div className="notice">
      <p>Saved. Update the translations to match?</p>
      {result ? <p role="status">{recheckSummary(result)}</p> : null}
      {recheck.isError ? <ErrorMessage error={recheck.error} /> : null}
      <div className="row end">
        <button type="button" onClick={onDone}>
          {result ? "Close" : "Not now"}
        </button>
        {result && result.failed.length > 0 ? (
          <button
            type="button"
            className="primary"
            disabled={recheck.isPending}
            onClick={() => run(result.failed)}
          >
            Try again
          </button>
        ) : null}
        {!result ? (
          <button
            type="button"
            className="primary"
            disabled={recheck.isPending}
            onClick={() => run()}
          >
            Update the translations
          </button>
        ) : null}
      </div>
    </div>
  );
}
