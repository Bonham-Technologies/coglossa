import { useId, useState } from "react";
import { useNavigate } from "react-router";
import { useRunAction } from "../api/runs";
import { useSetBusy, useTranslateAgain } from "../api/sets";
import type { RunDetail } from "../api/types";
import { Dialog } from "../components/Dialog";
import { ErrorMessage } from "../components/ErrorMessage";
import { flagTitle } from "../lib/flags";
import { languageName } from "../lib/languages";
import { bannerFor, keptRerun } from "../lib/status";

/** Spec §5.2: one plain sentence about the result, and the one action that fits it. */
export function SummaryBanner({ detail, onNext }: { detail: RunDetail; onNext?: () => void }) {
  const { run } = detail;
  const action = useRunAction(run.id, run.project_id);
  const again = useTranslateAgain(run.project_id);
  const { busy, reason } = useSetBusy(run.project_id);
  const reasonId = useId();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const rerun = keptRerun(detail);
  if (rerun !== null) {
    // a recheck or retry of a finished file: the banner returns when it is done
    return (
      <p role="status" className="notice">
        {rerun === "recheck"
          ? "Rechecking against your translation guide…"
          : "Retrying the parts that couldn't be translated…"}
      </p>
    );
  }
  const banner = bannerFor(detail, languageName(run.target_lang));
  // a retry of a finished file translates its failed sections again, replacing their passages,
  // edits and all; a retry of the sweep alone, or of a failed run, replaces no passage
  const replacesEdits =
    run.state === "complete_with_errors" &&
    detail.progress.chunks_with_errors > 0 &&
    detail.stats.edited_spans > 0;
  // with no failed section, a retry of a finished file only reruns the guide check (the sweep),
  // the one thing it can redo then; "those" would name nothing, so the button says what it does
  const recheckOnly =
    run.state === "complete_with_errors" && detail.progress.chunks_with_errors === 0;
  const notices = detail.notices.filter(
    (n) => !(banner.careful && n.code === "low_reliability_language"),
  );
  return (
    <section aria-label="Summary" className={`banner banner-${banner.tone}`}>
      <p className="banner-text">{banner.text}</p>
      {banner.careful ? <p>{banner.careful}</p> : null}
      {notices.map((notice) => (
        <p key={notice.code} className="muted">
          <strong>{flagTitle(notice.code)}.</strong> <span dir="auto">{notice.explanation}</span>
        </p>
      ))}
      <div className="row">
        {banner.action === "next" && onNext ? (
          <button type="button" className="primary" onClick={onNext}>
            Next to check →
          </button>
        ) : null}
        {banner.action === "retry" ? (
          <button
            type="button"
            className="primary"
            disabled={action.isPending}
            onClick={() => {
              if (replacesEdits) setConfirming(true);
              else action.mutate("retry");
            }}
          >
            {run.state === "failed" ? "Try again" : recheckOnly ? "Recheck" : "Try those again"}
          </button>
        ) : null}
        {banner.action === "again" ? (
          <button
            type="button"
            className="primary"
            disabled={again.isPending}
            aria-disabled={busy || undefined}
            aria-describedby={busy ? reasonId : undefined}
            onClick={() => {
              if (busy) return; // stays focusable so its reason is read
              again.mutate(
                { documentId: run.document_id, targetLang: run.target_lang },
                {
                  onSuccess: (next) => navigate(`/t/${run.project_id}/${next.id}`),
                },
              );
            }}
          >
            Translate again
          </button>
        ) : null}
        {banner.action === "again" && busy ? (
          <p id={reasonId} className="muted">
            {reason}
          </p>
        ) : null}
      </div>
      {run.state === "failed" && run.error ? (
        <details>
          <summary>Details</summary>
          <p dir="auto">{run.error}</p>
        </details>
      ) : null}
      <Dialog open={confirming} label="Try those again?" onClose={() => setConfirming(false)}>
        <h2>Try those again?</h2>
        <p>
          Any edits you made to the passages being redone will be replaced with new translations.
          This can&apos;t be undone.
        </p>
        <div className="row end">
          <button type="button" onClick={() => setConfirming(false)}>
            Cancel
          </button>
          <button
            type="button"
            className="danger"
            onClick={() => {
              setConfirming(false);
              action.mutate("retry");
            }}
          >
            Replace and try again
          </button>
        </div>
      </Dialog>
      {action.isError ? <ErrorMessage error={action.error} /> : null}
      {again.isError ? <ErrorMessage error={again.error} /> : null}
    </section>
  );
}
