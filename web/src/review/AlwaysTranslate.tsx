import { useId, useState } from "react";
import { useAddToGuide, type GuideAddition } from "../api/review";
import { recheckFailureText, useRecheckSet } from "../api/sets";
import { ErrorMessage } from "../components/ErrorMessage";
import { emptyEntity, emptyTerm } from "../guide/entries";
import { plural } from "../lib/flags";
import { useRunLanguages } from "./languages";

const NAME_KINDS = [
  ["person", "Person"],
  ["place", "Place"],
  ["organization", "Organisation"],
  ["other", "Other"],
] as const;

type Props = { runId: string; projectId: string; spanId: string; prefill: string };

/** Spec §5.4 step 4: a fixed rule in the set's translation guide, then a recheck of every
 * finished file in the set. */
export function AlwaysTranslate({ runId, projectId, spanId, prefill }: Props) {
  const languages = useRunLanguages();
  const add = useAddToGuide(runId, projectId, languages.target);
  const recheck = useRecheckSet(projectId, languages.target);
  const [source, setSource] = useState(prefill);
  const [rendering, setRendering] = useState("");
  const [isName, setIsName] = useState(false);
  const [kind, setKind] = useState<string>("person");
  const [done, setDone] = useState<string | null>(null);
  const sourceId = useId();
  const renderingId = useId();
  // Text newly selected in the Original column replaces what is in the field.
  const [lastPrefill, setLastPrefill] = useState(prefill);
  if (prefill !== lastPrefill) {
    setLastPrefill(prefill);
    if (prefill) setSource(prefill);
  }
  const ready =
    source.trim() !== "" && rendering.trim() !== "" && !add.isPending && !recheck.isPending;

  async function apply() {
    setDone(null);
    const addition: GuideAddition = isName
      ? {
          entity: {
            ...emptyEntity(),
            name: source.trim(),
            kind,
            rendering: rendering.trim(),
            locked: true,
          },
        }
      : {
          glossary: {
            ...emptyTerm(),
            term: source.trim(),
            rendering: rendering.trim(),
            locked: true,
          },
        };
    try {
      await add.mutateAsync({ spanId, addition });
    } catch {
      return; // shown below
    }
    setSource("");
    setRendering("");
    setIsName(false);
    setKind("person");
    try {
      const result = await recheck.mutateAsync();
      const { started, failed } = result;
      if (started.length === 0 && failed.length > 0) {
        setDone("Added to the translation guide, but the recheck couldn't start.");
      } else {
        const text =
          started.length > 0
            ? `Added to the translation guide. Rechecking ${plural(started.length, "file")}…`
            : "Added to the translation guide. New translations in this set will follow it.";
        setDone(failed.length > 0 ? `${text} ${recheckFailureText(failed.length)}` : text);
      }
    } catch {
      setDone("Added to the translation guide, but the recheck couldn't start.");
    }
  }

  return (
    <section aria-label="Always translate" className="always">
      <h3>Fix a name or term everywhere</h3>
      <div className="field">
        <label htmlFor={sourceId}>Always translate</label>
        <input
          id={sourceId}
          dir="auto"
          lang={languages.source}
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor={renderingId}>as</label>
        <input
          id={renderingId}
          dir="auto"
          lang={languages.target}
          value={rendering}
          onChange={(e) => setRendering(e.target.value)}
        />
      </div>
      <div className="row">
        <label>
          <input type="checkbox" checked={isName} onChange={(e) => setIsName(e.target.checked)} />{" "}
          This is a name (person, place, organisation…)
        </label>
        <select
          aria-label="Kind of name"
          value={kind}
          disabled={!isName}
          onChange={(e) => setKind(e.target.value)}
        >
          {NAME_KINDS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="row">
        <button type="button" className="primary" disabled={!ready} onClick={() => void apply()}>
          Apply to all
        </button>
      </div>
      {done ? <p role="status">{done}</p> : null}
      {add.isError ? <ErrorMessage error={add.error} /> : null}
    </section>
  );
}
