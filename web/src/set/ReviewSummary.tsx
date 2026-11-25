import { useState } from "react";
import {
  runReviewCsvUrl,
  setReviewCsvUrl,
  useSetReviewSummary,
  type FlagReviewFigures,
  type LanguageReviewSummary,
} from "../api/reviewSummary";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { FLAG_STATUS_WORDS, flagTitle, plural } from "../lib/flags";
import { languageName } from "../lib/languages";
import { sameLanguage } from "./languages";

function percent(share: number): string {
  return `${Math.round(share * 100)}%`;
}

function oneDecimal(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

/** The edit-distance sentence, in plain words: characters changed per changed passage. */
function changeSize(s: LanguageReviewSummary): string {
  return (
    `A changed passage had ${oneDecimal(s.mean_edit_distance)} characters added, removed or ` +
    `replaced on average (${percent(s.mean_edit_ratio)} of its length).`
  );
}

function FlagRow({ flag }: { flag: FlagReviewFigures }) {
  return (
    <tr>
      <th scope="row">{flagTitle(flag.code)}</th>
      <td>{flag.raised}</td>
      <td>{flag.dismissed}</td>
      <td>
        {flag.edited_after} of {flag.passages} ({percent(flag.edited_after_share)})
      </td>
      <td>
        {flag.dismiss_reasons.length > 0 ? (
          <ul>
            {flag.dismiss_reasons.map((r) => (
              <li key={r.reason} dir="auto">
                {r.reason}
                {r.count > 1 ? ` (${r.count} times)` : null}
              </li>
            ))}
          </ul>
        ) : null}
      </td>
    </tr>
  );
}

function Figures({ summary, lang }: { summary: LanguageReviewSummary; lang: string }) {
  const name = languageName(lang);
  if (summary.passages === 0) {
    return <p>No {name} passages to review yet.</p>;
  }
  return (
    <>
      <ul>
        <li>
          {summary.checked} of {plural(summary.passages, "passage")} checked (
          {percent(summary.checked_share)}).
        </li>
        <li>
          {summary.edited} of {plural(summary.passages, "passage")} changed by a reviewer (
          {percent(summary.edited_share)}).
        </li>
        {summary.edited > 0 ? <li>{changeSize(summary)}</li> : null}
        {summary.estimated_distances > 0 ? (
          <li className="muted">
            {plural(summary.estimated_distances, "passage")} too long to compare letter by letter
            {summary.estimated_distances === 1 ? " is" : " are"} counted as fully rewritten where
            they differ.
          </li>
        ) : null}
      </ul>
      {summary.flags.length > 0 ? (
        <table aria-label="Flags">
          <thead>
            <tr>
              <th>Flag</th>
              <th>Raised</th>
              <th>Marked “{FLAG_STATUS_WORDS.dismissed}”</th>
              <th>Passages changed afterwards</th>
              <th>Reasons given</th>
            </tr>
          </thead>
          <tbody>
            {summary.flags.map((flag) => (
              <FlagRow key={flag.code} flag={flag} />
            ))}
          </tbody>
        </table>
      ) : (
        <p>No flags were raised.</p>
      )}
    </>
  );
}

function Summary({
  projectId,
  lang,
  runId,
}: {
  projectId: string;
  lang: string;
  runId: string | undefined;
}) {
  const summary = useSetReviewSummary(projectId);
  if (summary.isPending) return <Loading />;
  if (summary.isError) return <ErrorMessage error={summary.error} />;
  const figures = summary.data.languages.find((l) => sameLanguage(l.target_lang, lang));
  return (
    <>
      <p className="muted">
        How much reviewing the {languageName(lang)} translation took, over the latest version of
        each file. Nothing here changes how passages are translated or flagged.
      </p>
      {figures ? <Figures summary={figures} lang={lang} /> : <p>Nothing to show yet.</p>}
      <p>
        <a href={setReviewCsvUrl(projectId, lang)} download>
          Download every passage as a spreadsheet (CSV)
        </a>
        {runId !== undefined ? (
          <>
            {" · "}
            <a href={runReviewCsvUrl(runId)} download>
              Download this file only
            </a>
          </>
        ) : null}
      </p>
    </>
  );
}

/** CGL-112: the review summary of the language shown, for administrators, folded away under
 * Technical details. Fetched each time it is unfolded. */
export function ReviewSummary({
  projectId,
  lang,
  runId,
}: {
  projectId: string;
  lang: string;
  runId: string | undefined;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details className="technical" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Technical details</summary>
      <h3>Review summary</h3>
      {open ? <Summary projectId={projectId} lang={lang} runId={runId} /> : null}
    </details>
  );
}
