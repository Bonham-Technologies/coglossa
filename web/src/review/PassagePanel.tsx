import { useEffect, useEffectEvent, useId, useRef, useState } from "react";
import { useAcceptSpan, useChooseCandidate, useEditSpan, useSpanDetail } from "../api/review";
import { passageChangedTo } from "../api/client";
import type { SpanDetail } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { flagExplanation, flagTitle } from "../lib/flags";
import { AlwaysTranslate } from "./AlwaysTranslate";
import { byTranslator } from "./byTranslator";
import { useRunLanguages } from "./languages";
import { MoreDetails } from "./MoreDetails";

export const CHANGED = "Changed and marked as checked.";
export const CHANGED_STILL_OPEN = "Changed. The passage is still marked to check.";
export const CHECKED = "Marked as checked.";
export const SPLIT_DIFFERENTLY =
  "This version splits the text differently, so it can't replace the passage directly. Use Edit to copy in the parts you want.";

/** Resolves to whether the change itself went through (the follow-up check may still fail). */
type Action = "choose" | "edit" | "accept";
type Change = (change: () => Promise<SpanDetail>, from: Action) => Promise<boolean>;

/** The last action that succeeded; the other actions drop their old errors (CGL-45). */
type Success = { from: Action; n: number } | null;

/**
 * Clears a refused action's error once a different action succeeds: the reviewer has acted on
 * the passage since, so the old error no longer applies. With `keepPassageChanged`, a "someone
 * else changed this passage" error stays: the edit's clash holds the reviewer's unsaved text
 * until they answer it (CGL-36).
 */
function useClearOnOtherSuccess(
  self: Action,
  success: Success,
  mutation: { error: unknown; reset: () => void },
  keepPassageChanged = false,
) {
  // reads the mutation as it is when the success lands, without re-running on every render
  const clear = useEffectEvent(() => {
    const { error, reset } = mutation;
    if (error == null) return;
    if (keepPassageChanged && passageChangedTo(error) !== null) return;
    reset();
  });
  useEffect(() => {
    if (success === null || success.from === self) return;
    clear();
  }, [success, self]);
}

function Versions({
  runId,
  detail,
  busy,
  success,
  onChange,
}: {
  runId: string;
  detail: SpanDetail;
  busy: boolean;
  success: Success;
  onChange: Change;
}) {
  const choose = useChooseCandidate(runId);
  useClearOnOtherSuccess("choose", success, choose);
  const { target } = useRunLanguages();
  const noteId = useId();
  // the text shown is the text Use would write (CGL-44); a version with no words is left out
  const others = byTranslator(detail).filter(
    (c) => c.text !== detail.text && (c.usable || c.text.trim() !== ""),
  );
  if (others.length === 0) return null;
  return (
    <section aria-label="Better versions">
      <h3>Pick a better version</h3>
      <ul className="versions">
        {others.map((c, i) => (
          <li key={c.translator}>
            <div>
              <p dir="auto" lang={target}>
                {c.text}
              </p>
              {c.usable ? null : (
                <p id={`${noteId}-${i}`} className="muted">
                  {SPLIT_DIFFERENTLY}
                </p>
              )}
            </div>
            <button
              type="button"
              aria-label={`Use version ${i + 1}`}
              aria-describedby={c.usable ? undefined : `${noteId}-${i}`}
              disabled={!c.usable || busy || choose.isPending}
              onClick={() =>
                void onChange(
                  () =>
                    choose.mutateAsync({
                      spanId: detail.id,
                      translator: c.translator,
                      base: detail.text,
                    }),
                  "choose",
                )
              }
            >
              Use
            </button>
          </li>
        ))}
      </ul>
      {choose.isError ? <VersionError error={choose.error} /> : null}
    </section>
  );
}

export const CHANGED_BY_SOMEONE = "Someone else changed this passage";

/** A version picked after someone else changed the passage: the panel now shows their text. */
function VersionError({ error }: { error: unknown }) {
  const theirs = passageChangedTo(error);
  if (theirs === null) return <ErrorMessage error={error} />;
  return (
    <p role="alert" className="error">
      {CHANGED_BY_SOMEONE} just now, so your choice was not saved. It now reads: “
      <span dir="auto">{theirs}</span>”. Pick a version again if you still want to replace it.
    </p>
  );
}

/** Someone else saved the passage while this reviewer was editing it (CGL-36). */
function Clash({
  theirs,
  busy,
  onKeepMine,
  onUseTheirs,
}: {
  theirs: string;
  busy: boolean;
  onKeepMine: () => void;
  onUseTheirs: () => void;
}) {
  const { target } = useRunLanguages();
  return (
    <section role="region" aria-label={CHANGED_BY_SOMEONE} className="clash">
      <p role="alert">
        <strong>{CHANGED_BY_SOMEONE}</strong> while you were editing it. Your text is not saved yet.
        Their version:
      </p>
      <p dir="auto" lang={target}>
        {theirs}
      </p>
      <div className="row">
        <button type="button" className="primary" disabled={busy} onClick={onKeepMine}>
          Keep my version
        </button>
        <button type="button" disabled={busy} onClick={onUseTheirs}>
          Use their version
        </button>
      </div>
    </section>
  );
}

function EditText({
  runId,
  detail,
  busy,
  success,
  onChange,
}: {
  runId: string;
  detail: SpanDetail;
  busy: boolean;
  success: Success;
  onChange: Change;
}) {
  const edit = useEditSpan(runId);
  useClearOnOtherSuccess("edit", success, edit, true);
  const { target } = useRunLanguages();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(detail.text);
  // the passage text the edit started from; another reviewer's save since then is a clash
  const [base, setBase] = useState(detail.text);
  const id = useId();
  const field = useRef<HTMLTextAreaElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef(false);
  // Edit moves focus into the text box; Save or Cancel hands it back to Edit.
  useEffect(() => {
    if (editing) field.current?.focus();
    else if (returnFocus.current) {
      returnFocus.current = false;
      editButton.current?.focus();
    }
  }, [editing]);
  const finish = () => {
    returnFocus.current = true;
    edit.reset();
    setEditing(false);
  };
  const save = async (from: string) => {
    // the box stays open until the follow-up check is done, so Save shows it is busy
    const saved = await onChange(
      () => edit.mutateAsync({ spanId: detail.id, text, reason: null, base: from }),
      "edit",
    );
    if (saved) finish();
  };
  if (!editing) {
    return (
      <button
        ref={editButton}
        type="button"
        onClick={() => {
          setText(detail.text);
          setBase(detail.text);
          setEditing(true);
        }}
      >
        Edit
      </button>
    );
  }
  const theirs = edit.isError ? passageChangedTo(edit.error) : null;
  // while the clash is open, its own buttons decide: Save would only meet the same clash
  const changed = text.trim() !== "" && text !== detail.text && theirs === null;
  return (
    <div className="edit">
      <label htmlFor={id}>Your translation</label>
      <textarea
        id={id}
        ref={field}
        dir="auto"
        lang={target}
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="row">
        <button
          type="button"
          className="primary"
          aria-label="Save edit"
          disabled={!changed || busy || edit.isPending}
          onClick={() => void save(base)}
        >
          Save
        </button>
        <button type="button" aria-label="Cancel edit" disabled={busy} onClick={finish}>
          Cancel
        </button>
      </div>
      {theirs !== null ? (
        <Clash
          theirs={theirs}
          busy={busy || edit.isPending}
          onKeepMine={() => {
            field.current?.focus(); // the clash and its buttons go while the save runs
            setBase(theirs);
            void save(theirs);
          }}
          onUseTheirs={finish}
        />
      ) : edit.isError ? (
        <ErrorMessage error={edit.error} />
      ) : null}
    </div>
  );
}

type Props = {
  runId: string;
  projectId: string;
  spanId: string;
  prefill: string;
  onNext: () => void;
  onClose: () => void;
};

/** Spec §5.4. */
export function PassagePanel({ runId, projectId, spanId, prefill, onNext, onClose }: Props) {
  const detail = useSpanDetail(runId, spanId);
  const accept = useAcceptSpan(runId);
  const [status, setStatus] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [success, setSuccess] = useState<Success>(null);
  const succeeded = (from: Action) => setSuccess((s) => ({ from, n: (s?.n ?? 0) + 1 }));
  if (detail.isPending || detail.isError) {
    return (
      <aside className="passage-panel" aria-label="Passage">
        {detail.isError ? <ErrorMessage error={detail.error} /> : <Loading />}
      </aside>
    );
  }
  const d = detail.data;
  const open = d.flags.filter((f) => f.status === "open");

  // Spec §5.4 step 2: using a version or saving an edit also marks the passage checked.
  const changeThenCheck: Change = async (change, from) => {
    setStatus(null);
    setWorking(true);
    try {
      let changed: SpanDetail;
      try {
        changed = await change();
      } catch {
        return false; // the form shows its own error
      }
      accept.reset();
      succeeded(from);
      if (!changed.flags.some((f) => f.status === "open")) setStatus(CHANGED);
      else {
        try {
          await accept.mutateAsync(d.id);
          setStatus(CHANGED);
        } catch {
          setStatus(CHANGED_STILL_OPEN);
        }
      }
      return true;
    } finally {
      setWorking(false);
    }
  };
  const busy = working || accept.isPending;

  return (
    <aside className="passage-panel" aria-label={`Passage ${d.number}`}>
      <div className="row between">
        <h2>
          Passage {d.number}
          {open.length > 0 ? " · worth a look" : ""}
        </h2>
        <button type="button" className="quiet" aria-label="Close the passage" onClick={onClose}>
          ×
        </button>
      </div>
      {open.length > 0 ? (
        <section aria-label="Why it's flagged">
          {open.map((flag) => {
            const { text, note } = flagExplanation(flag);
            return (
              <div key={flag.id} className="why">
                <p>
                  <span aria-hidden="true">⚠ </span>
                  <strong>{flagTitle(flag.code)}</strong>
                </p>
                <p className="muted" dir="auto">
                  {text}
                </p>
                {note ? (
                  <p className="muted" dir="auto">
                    {note}
                  </p>
                ) : null}
              </div>
            );
          })}
        </section>
      ) : (
        <p className="muted">Nothing to check here.</p>
      )}
      <Versions runId={runId} detail={d} busy={busy} success={success} onChange={changeThenCheck} />
      <div className="row">
        <EditText
          runId={runId}
          detail={d}
          busy={busy}
          success={success}
          onChange={changeThenCheck}
        />
        {open.length > 0 ? (
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() =>
              accept.mutate(d.id, {
                onSuccess: () => {
                  succeeded("accept");
                  setStatus(CHECKED);
                  onNext();
                },
              })
            }
          >
            It&apos;s fine
          </button>
        ) : null}
      </div>
      {accept.isError && status === null ? <ErrorMessage error={accept.error} /> : null}
      {status ? (
        <p role="status">
          {status}{" "}
          <button type="button" className="link" onClick={onNext}>
            Next to check →
          </button>
        </p>
      ) : null}
      <AlwaysTranslate runId={runId} projectId={projectId} spanId={d.id} prefill={prefill} />
      <MoreDetails runId={runId} projectId={projectId} detail={d} />
    </aside>
  );
}
