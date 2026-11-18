import { useState } from "react";
import { useRunAction } from "../api/runs";
import type { RunDetail } from "../api/types";
import { Dialog } from "../components/Dialog";
import { ErrorMessage } from "../components/ErrorMessage";
import { percentDone, stepIndex, STEPS } from "../lib/status";

const TRANSLATING = 3;

/** Spec §4.2: plain steps, a tick on each finished one, and a quiet Stop. */
export function ProgressSteps({ detail }: { detail: RunDetail }) {
  const action = useRunAction(detail.run.id, detail.run.project_id);
  const [confirming, setConfirming] = useState(false);
  const current = stepIndex(detail.run.state);
  const percent = percentDone(detail.progress);
  return (
    <section aria-label="Progress" className="progress-steps">
      <ol aria-label="Steps">
        {STEPS.map((label, i) => {
          const state = i < current ? "done" : i === current ? "current" : "todo";
          const mark = state === "done" ? "✓" : state === "current" ? "●" : "○";
          return (
            <li
              key={label}
              className={state}
              aria-current={state === "current" ? "step" : undefined}
            >
              <span aria-hidden="true">{mark} </span>
              {label}
              {i === TRANSLATING && state === "current" ? ` (${percent}%)` : ""}
              {state === "done" ? <span className="visually-hidden"> (done)</span> : null}
              {state === "todo" ? <span className="visually-hidden"> (not started)</span> : null}
            </li>
          );
        })}
      </ol>
      {/* named by step, not by percent, so a tick of progress is not read out again */}
      <p role="status" aria-live="polite" className="visually-hidden">
        {STEPS[current]}
      </p>
      <p className="muted">
        This usually takes a few minutes. You can leave this page; it keeps going.
      </p>
      <button type="button" className="quiet" onClick={() => setConfirming(true)}>
        Stop translating
      </button>
      {action.isError ? <ErrorMessage error={action.error} /> : null}
      <Dialog open={confirming} label="Stop translating" onClose={() => setConfirming(false)}>
        <h2>Stop translating?</h2>
        <p>A stopped translation can&apos;t be resumed. You can translate the file again later.</p>
        <div className="row end">
          <button type="button" onClick={() => setConfirming(false)}>
            Keep going
          </button>
          <button
            type="button"
            className="danger"
            disabled={action.isPending}
            onClick={() => action.mutate("cancel", { onSettled: () => setConfirming(false) })}
          >
            Stop
          </button>
        </div>
      </Dialog>
    </section>
  );
}
