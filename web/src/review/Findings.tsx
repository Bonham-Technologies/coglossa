import type { SpanDetail } from "../api/types";
import { categoryLabel, severityLabel } from "./labels";
import { highlight } from "./highlight";
import { useRunLanguages } from "./languages";

export function Findings({ detail }: { detail: SpanDetail }) {
  const errors = detail.judge_errors ?? [];
  const { source, target } = useRunLanguages();
  return (
    <>
      {detail.rationale ? (
        <>
          <h3>Why this version was chosen</h3>
          <p dir="auto">{detail.rationale}</p>
        </>
      ) : null}
      {detail.issues.length > 0 ? (
        <ul aria-label="Issues">
          {detail.issues.map((issue, i) => (
            <li key={i} dir="auto">
              {issue}
            </li>
          ))}
        </ul>
      ) : null}
      {errors.length > 0 ? (
        <>
          <h3>What the checker found</h3>
          <p dir="auto" lang={target} aria-label="Translation with the checker's findings">
            {highlight(
              detail.text,
              errors.map((e) => e.excerpt),
            ).map((piece, i) =>
              piece.mark ? <mark key={i}>{piece.text}</mark> : <span key={i}>{piece.text}</span>,
            )}
          </p>
          <ul aria-label="Checker findings">
            {errors.map((e, i) => (
              <li key={i}>
                <span className="badge">{severityLabel(e.severity)}</span>{" "}
                {categoryLabel(e.category)}: <span dir="auto">{e.explanation}</span>
                {e.excerpt ? (
                  <>
                    {" "}
                    <q dir="auto" lang={target}>
                      {e.excerpt}
                    </q>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : detail.judge_errors === null ? (
        <p className="muted">The checker didn&apos;t look at this passage.</p>
      ) : null}
      {detail.back_translation ? (
        <>
          <h3>Back translation</h3>
          <p dir="auto" lang={source} className="back-translation">
            {detail.back_translation}
          </p>
        </>
      ) : null}
    </>
  );
}
