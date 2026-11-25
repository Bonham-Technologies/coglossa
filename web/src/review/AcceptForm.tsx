import { useId, useState } from "react";
import { useAcceptForm } from "../api/review";
import type { ReviewFlag } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { useRunLanguages } from "./languages";

type Rule = { term: string; expected: string };

/** The terms and names a terminology flag says were not followed (the scorer's evidence). */
export function flagRules(flag: Pick<ReviewFlag, "code" | "evidence">): Rule[] {
  if (flag.code !== "terminology_violation") return [];
  const found: unknown = flag.evidence?.["violations"];
  if (!Array.isArray(found)) return [];
  return found.flatMap((v: unknown) => {
    if (typeof v !== "object" || v === null) return [];
    const { term, expected } = v as { term?: unknown; expected?: unknown };
    return typeof term === "string" && typeof expected === "string" ? [{ term, expected }] : [];
  });
}

/**
 * CGL-109: the passage follows the guide, just in another form of the word (a plural, or another
 * ending). The form joins the guide, so the same flag clears on other passages once the
 * translations are updated.
 */
export function AcceptForm({
  runId,
  projectId,
  flag,
  onAccepted,
}: {
  runId: string;
  projectId: string;
  flag: ReviewFlag;
  onAccepted: () => void;
}) {
  const rules = flagRules(flag);
  const accept = useAcceptForm(runId, projectId);
  const { target } = useRunLanguages();
  const [chosen, setChosen] = useState(0);
  const [form, setForm] = useState("");
  const id = useId();
  const rule = rules[chosen];
  if (!rule) return null;
  return (
    <div className="accept-form">
      <p className="muted">
        Or is the translation right, in another form of the word (for example a plural, or a
        different ending)?
      </p>
      {rules.length > 1 ? (
        <select
          aria-label="Which word"
          value={chosen}
          onChange={(e) => setChosen(Number(e.target.value))}
        >
          {rules.map((r, i) => (
            <option key={`${r.term}-${i}`} value={i}>
              {r.term} → {r.expected}
            </option>
          ))}
        </select>
      ) : null}
      <label htmlFor={id}>
        The form this passage uses instead of “<span dir="auto">{rule.expected}</span>”
      </label>
      <input
        id={id}
        dir="auto"
        lang={target}
        value={form}
        onChange={(e) => setForm(e.target.value)}
      />
      <button
        type="button"
        disabled={form.trim() === "" || accept.isPending}
        onClick={() =>
          accept.mutate(
            { flagId: flag.id, term: rule.term, form: form.trim() },
            { onSuccess: onAccepted },
          )
        }
      >
        This form is fine
      </button>
      {accept.isError ? <ErrorMessage error={accept.error} /> : null}
    </div>
  );
}
