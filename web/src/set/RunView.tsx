import { useCallback, useEffect, useState } from "react";
import { useRunEvents } from "../api/events";
import { useRefreshAfterRecheck, useRun } from "../api/runs";
import { useApproveAsIs } from "../api/sets";
import type { RunDetail } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import type { GuideMode } from "../guide/mode";
import { isTerminal } from "../lib/format";
import { percentDone, keptRerun } from "../lib/status";
import { DocumentView } from "../review/DocumentView";
import { ProgressSteps } from "./ProgressSteps";
import { SummaryBanner } from "./SummaryBanner";

/** Spec §4.2: the tab title follows the open file. */
export function useRunTitle(detail: RunDetail | undefined): void {
  useEffect(() => {
    if (detail === undefined) return;
    const before = document.title;
    const { state, filename } = detail.run;
    const rerun = keptRerun(detail);
    if (state === "complete" || state === "complete_with_errors") {
      document.title = `✓ Ready – ${filename}`;
    } else if (state === "awaiting_guide_approval") {
      document.title = `Waiting – ${filename}`;
    } else if (rerun !== null) {
      document.title = `${rerun === "recheck" ? "Rechecking" : "Retrying"} – ${filename}`;
    } else if (!isTerminal(state)) {
      document.title = `${percentDone(detail.progress)}% – ${filename}`;
    }
    return () => {
      document.title = before;
    };
  }, [detail]);
}

function WaitingForGuide({
  detail,
  onOpenGuide,
}: {
  detail: RunDetail;
  onOpenGuide: (mode: GuideMode) => void;
}) {
  const approve = useApproveAsIs(detail.run.id, detail.run.project_id);
  return (
    <section aria-label="Waiting for you" className="notice">
      <p>Waiting for you to check the translation guide.</p>
      <div className="row">
        <button
          type="button"
          className="primary"
          disabled={approve.isPending}
          onClick={() => approve.mutate()}
        >
          Continue as is
        </button>
        <button type="button" onClick={() => onOpenGuide({ kind: "draft", runId: detail.run.id })}>
          Look at the guide first
        </button>
      </div>
      {approve.isError ? <ErrorMessage error={approve.error} /> : null}
    </section>
  );
}

/** One file's run, whatever its state. */
export function RunView({
  runId,
  onOpenGuide,
}: {
  runId: string;
  onOpenGuide: (mode: GuideMode) => void;
}) {
  const [streamFailed, setStreamFailed] = useState(false);
  const [documentShown, setDocumentShown] = useState(false);
  const onStreamFailed = useCallback(() => setStreamFailed(true), []);
  const run = useRun(runId, streamFailed);
  const detail = run.data;
  useRunEvents(runId, detail !== undefined && !isTerminal(detail.run.state), onStreamFailed);
  useRunTitle(detail);
  useRefreshAfterRecheck(runId, detail);
  if (run.isError) return <ErrorMessage error={run.error} />;
  if (detail === undefined) return <Loading />;
  const { state } = detail.run;
  if (state === "awaiting_guide_approval") {
    return <WaitingForGuide detail={detail} onOpenGuide={onOpenGuide} />;
  }
  // plan decision 5, amended: a recheck or a retry of a finished file keeps the document (and the
  // open passage) on screen; the summary banner is swapped for a notice until it is done.
  const rechecking = keptRerun(detail) !== null;
  const finishedOk = state === "complete" || state === "complete_with_errors";
  // The live progress event can say "complete" a moment before the refetch says the report is
  // ready; a document already on screen stays rather than being rebuilt (and losing its place).
  const showDocument = rechecking || (finishedOk && (detail.report_available || documentShown));
  if (showDocument && !documentShown) setDocumentShown(true);
  if (showDocument) return <DocumentView detail={detail} />;
  if (!isTerminal(state)) return <ProgressSteps detail={detail} />;
  return <SummaryBanner detail={detail} />;
}
