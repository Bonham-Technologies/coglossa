import { useDeferredValue, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Entity, GlossaryEntry, Guide } from "../api/types";
import { emptyEntity, emptyTerm, ENTITY_KINDS, KIND_WORDS } from "./entries";

// The summary is the row's edit button. Its accessible name is built from the
// visible text plus a hidden "Edit" prefix, so the name contains what sighted
// users see (WCAG 2.5.3 Label in Name) and voice users can say it.
function Summary({
  from,
  to,
  always,
  fallback,
}: {
  from: string;
  to: string;
  always: boolean;
  fallback: string;
}) {
  return (
    <>
      <span className="visually-hidden">{from ? "Edit" : `Edit ${fallback}:`}</span>{" "}
      <span dir="auto">{from || "…"}</span> → <span dir="auto">{to || "…"}</span>
      {always ? (
        <>
          {" "}
          <span className="muted">· always</span>
        </>
      ) : null}
    </>
  );
}

function NameForm({
  entity,
  n,
  onChange,
  onRemove,
  onDone,
}: {
  entity: Entity;
  n: number;
  onChange: (patch: Partial<Entity>) => void;
  onRemove: () => void;
  onDone: () => void;
}) {
  const id = useId();
  return (
    <fieldset className="guide-row-form">
      <legend>Name {n}</legend>
      <div className="field">
        <label htmlFor={`${id}-name`}>Name</label>
        <input
          id={`${id}-name`}
          dir="auto"
          value={entity.name}
          onChange={(e) => onChange({ name: e.target.value })}
        />
      </div>
      <div className="field">
        <label htmlFor={`${id}-rendering`}>Translation</label>
        <input
          id={`${id}-rendering`}
          dir="auto"
          value={entity.rendering}
          onChange={(e) => onChange({ rendering: e.target.value })}
        />
      </div>
      <label>
        <input
          type="checkbox"
          checked={entity.locked}
          onChange={(e) => onChange({ locked: e.target.checked })}
        />{" "}
        Always use this translation
      </label>
      <div className="field">
        <label htmlFor={`${id}-kind`}>Kind</label>
        <select
          id={`${id}-kind`}
          value={entity.kind}
          onChange={(e) => onChange({ kind: e.target.value })}
        >
          {[...new Set([...ENTITY_KINDS, entity.kind])].map((kind) => (
            <option key={kind} value={kind}>
              {KIND_WORDS[kind] ?? kind}
            </option>
          ))}
        </select>
      </div>
      <details>
        <summary>More</summary>
        <div className="field">
          <label htmlFor={`${id}-gender`}>Gender</label>
          <input
            id={`${id}-gender`}
            dir="auto"
            value={entity.gender ?? ""}
            onChange={(e) => onChange({ gender: e.target.value || null })}
          />
        </div>
        <div className="field">
          <label htmlFor={`${id}-address`}>Forms of address</label>
          <input
            id={`${id}-address`}
            dir="auto"
            value={entity.forms_of_address}
            onChange={(e) => onChange({ forms_of_address: e.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor={`${id}-speech`}>Speech style</label>
          <input
            id={`${id}-speech`}
            dir="auto"
            value={entity.speech_style}
            onChange={(e) => onChange({ speech_style: e.target.value })}
          />
        </div>
      </details>
      <div className="row between">
        <button type="button" className="danger" onClick={onRemove}>
          Remove
        </button>
        <button type="button" onClick={onDone}>
          Done
        </button>
      </div>
    </fieldset>
  );
}

/** Removing a row above the open one moves the open one up with it. */
function openAfterRemoving(open: number | null, removed: number): number | null {
  if (open === null || open === removed) return null;
  return open > removed ? open - 1 : open;
}

/** After a removal the focused button is gone: move focus to the row now in its place (or the one
 * before, if it was last), else the Add button. Never to another Remove, which Enter would then
 * press in turn. Call removed(position) with the row's position in the list as shown, just before
 * changing the list. */
function useFocusAfterRemove(items: unknown[]) {
  const listRef = useRef<HTMLUListElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const target = useRef<number | null>(null);
  useLayoutEffect(() => {
    const at = target.current;
    if (at === null) return;
    target.current = null;
    const rows = listRef.current?.children;
    if (!rows || rows.length === 0) addRef.current?.focus();
    else {
      const row = rows[Math.min(at, rows.length - 1)];
      row?.querySelector<HTMLElement>(".row-summary, input")?.focus();
    }
  }, [items]);
  return {
    listRef,
    addRef,
    removed: (position: number) => {
      target.current = position;
    },
  };
}

/** Rows shown at once: a termbase of thousands stays quick to open, find in and tab through. */
export const PAGE_SIZE = 100;

function fold(text: string): string {
  return text.normalize("NFKC").toLowerCase(); // the same in every browser language
}

/** The rows a search shows, a page at a time (CGL-116). `texts` gives the words a row is found by.
 * The open row is always shown, first if the search or the page would hide it. */
function useFound<T>(items: T[], texts: (item: T) => string[], open: number | null) {
  const [query, setQuery] = useState("");
  const [pages, setPages] = useState(1);
  const deferred = useDeferredValue(query);
  const folded = useMemo(() => items.map((item) => texts(item).map(fold)), [items, texts]);
  const matching = useMemo(() => {
    const q = fold(deferred.trim());
    const all = items.map((_, i) => i);
    return q ? all.filter((i) => folded[i]!.some((t) => t.includes(q))) : all;
  }, [items, folded, deferred]);
  const page = matching.slice(0, pages * PAGE_SIZE);
  const shown =
    open !== null && open < items.length && !page.includes(open) ? [open, ...page] : page;
  return {
    query,
    search: (value: string) => {
      setQuery(value);
      setPages(1);
    },
    shown,
    matching: matching.length,
    hidden: matching.length - page.length,
    showMore: () => setPages((p) => p + 1),
    searching: deferred.trim() !== "",
  };
}

function count(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/** The search box, the Add button, and what the list shows, above the list. */
function ListTools({
  label,
  one,
  many,
  total,
  found,
  addRef,
  addLabel,
  onAdd,
}: {
  label: string;
  one: string;
  many: string;
  total: number;
  found: ReturnType<typeof useFound>;
  addRef: React.RefObject<HTMLButtonElement | null>;
  addLabel: string;
  onAdd: () => void;
}) {
  const id = useId();
  const shown = found.matching - found.hidden;
  let status = "";
  if (found.searching) {
    status =
      found.matching === 0
        ? `No ${many} match “${found.query.trim()}”.`
        : `${count(found.matching, one, many)} ${found.matching === 1 ? "matches" : "match"}.`;
  }
  if (found.hidden > 0) {
    status += `${status ? " " : ""}Showing the first ${shown.toLocaleString("en-US")} of ${count(
      found.matching,
      one,
      many,
    )}.`;
  }
  return (
    <div className="list-tools">
      <div className="row">
        {total > 0 ? (
          <div className="field list-search">
            <label htmlFor={`${id}-find`}>{label}</label>
            <input
              id={`${id}-find`}
              type="search"
              dir="auto"
              value={found.query}
              onChange={(e) => found.search(e.target.value)}
            />
          </div>
        ) : null}
        <button ref={addRef} type="button" onClick={onAdd}>
          {addLabel}
        </button>
      </div>
      <p aria-live="polite" className="muted list-status">
        {status}
      </p>
    </div>
  );
}

function ShowMore({
  found,
  many,
  listRef,
}: {
  found: ReturnType<typeof useFound>;
  many: string;
  listRef: React.RefObject<HTMLUListElement | null>;
}) {
  const reveal = useRef<number | null>(null);
  const shownCount = found.shown.length;
  useLayoutEffect(() => {
    const at = reveal.current;
    if (at === null) return;
    reveal.current = null;
    listRef.current?.children[at]?.querySelector<HTMLElement>(".row-summary, input")?.focus();
  }, [shownCount, listRef]);
  if (found.hidden <= 0) return null;
  return (
    <button
      type="button"
      onClick={() => {
        reveal.current = shownCount;
        found.showMore();
      }}
    >
      Show {Math.min(PAGE_SIZE, found.hidden).toLocaleString("en-US")} more {many}
    </button>
  );
}

const nameTexts = (e: Entity) => [e.name, e.rendering];
const termTexts = (t: GlossaryEntry) => [t.term, t.rendering];

export function NameRows({
  names,
  onChange,
}: {
  names: Entity[];
  onChange: (names: Entity[]) => void;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const update = (i: number, patch: Partial<Entity>) =>
    onChange(names.map((e, k) => (k === i ? { ...e, ...patch } : e)));
  const { listRef, addRef, removed } = useFocusAfterRemove(names);
  const found = useFound(names, nameTexts, open);
  const remove = (i: number) => {
    removed(found.shown.indexOf(i));
    onChange(names.filter((_, k) => k !== i));
    setOpen(openAfterRemoving(open, i));
  };
  return (
    <div className="guide-rows">
      <ListTools
        label="Find a name"
        one="name"
        many="names"
        total={names.length}
        found={found}
        addRef={addRef}
        addLabel="+ Add a name"
        onAdd={() => {
          found.search(""); // so the new row stays in view once it is done
          onChange([...names, emptyEntity()]);
          setOpen(names.length);
        }}
      />
      {names.length === 0 ? <p className="muted">No names yet.</p> : null}
      <ul ref={listRef} aria-label="Names">
        {found.shown
          .map((i) => [i, names[i]!] as const)
          .map(([i, entity]) => (
            <li key={i} className="guide-row">
              {open === i ? (
                <NameForm
                  entity={entity}
                  n={i + 1}
                  onChange={(patch) => update(i, patch)}
                  onRemove={() => remove(i)}
                  onDone={() => setOpen(null)}
                />
              ) : (
                <>
                  <button type="button" className="row-summary" onClick={() => setOpen(i)}>
                    <Summary
                      from={entity.name}
                      to={entity.rendering}
                      always={entity.locked}
                      fallback={`name ${i + 1}`}
                    />
                  </button>
                  <button
                    type="button"
                    className="danger"
                    aria-label={`Remove ${entity.name || `name ${i + 1}`}`}
                    onClick={() => remove(i)}
                  >
                    Remove
                  </button>
                </>
              )}
            </li>
          ))}
      </ul>
      <ShowMore found={found} many="names" listRef={listRef} />
    </div>
  );
}

function TermForm({
  term,
  n,
  onChange,
  onRemove,
  onDone,
}: {
  term: GlossaryEntry;
  n: number;
  onChange: (patch: Partial<GlossaryEntry>) => void;
  onRemove: () => void;
  onDone: () => void;
}) {
  const id = useId();
  return (
    <fieldset className="guide-row-form">
      <legend>Term {n}</legend>
      <div className="field">
        <label htmlFor={`${id}-term`}>Term</label>
        <input
          id={`${id}-term`}
          dir="auto"
          value={term.term}
          onChange={(e) => onChange({ term: e.target.value })}
        />
      </div>
      <div className="field">
        <label htmlFor={`${id}-rendering`}>Translation</label>
        <input
          id={`${id}-rendering`}
          dir="auto"
          value={term.rendering}
          onChange={(e) => onChange({ rendering: e.target.value })}
        />
      </div>
      <label>
        <input
          type="checkbox"
          checked={term.locked}
          onChange={(e) => onChange({ locked: e.target.checked })}
        />{" "}
        Always use this translation
      </label>
      <details>
        <summary>More</summary>
        <div className="field">
          <label htmlFor={`${id}-notes`}>Notes</label>
          <input
            id={`${id}-notes`}
            dir="auto"
            value={term.notes}
            onChange={(e) => onChange({ notes: e.target.value })}
          />
        </div>
      </details>
      <div className="row between">
        <button type="button" className="danger" onClick={onRemove}>
          Remove
        </button>
        <button type="button" onClick={onDone}>
          Done
        </button>
      </div>
    </fieldset>
  );
}

export function TermRows({
  terms,
  onChange,
}: {
  terms: GlossaryEntry[];
  onChange: (terms: GlossaryEntry[]) => void;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const update = (i: number, patch: Partial<GlossaryEntry>) =>
    onChange(terms.map((t, k) => (k === i ? { ...t, ...patch } : t)));
  const { listRef, addRef, removed } = useFocusAfterRemove(terms);
  const found = useFound(terms, termTexts, open);
  const remove = (i: number) => {
    removed(found.shown.indexOf(i));
    onChange(terms.filter((_, k) => k !== i));
    setOpen(openAfterRemoving(open, i));
  };
  return (
    <div className="guide-rows">
      <ListTools
        label="Find a term"
        one="term"
        many="terms"
        total={terms.length}
        found={found}
        addRef={addRef}
        addLabel="+ Add a term"
        onAdd={() => {
          found.search(""); // so the new row stays in view once it is done
          onChange([...terms, emptyTerm()]);
          setOpen(terms.length);
        }}
      />
      {terms.length === 0 ? <p className="muted">No terms yet.</p> : null}
      <ul ref={listRef} aria-label="Terms">
        {found.shown
          .map((i) => [i, terms[i]!] as const)
          .map(([i, term]) => (
            <li key={i} className="guide-row">
              {open === i ? (
                <TermForm
                  term={term}
                  n={i + 1}
                  onChange={(patch) => update(i, patch)}
                  onRemove={() => remove(i)}
                  onDone={() => setOpen(null)}
                />
              ) : (
                <>
                  <button type="button" className="row-summary" onClick={() => setOpen(i)}>
                    <Summary
                      from={term.term}
                      to={term.rendering}
                      always={term.locked}
                      fallback={`term ${i + 1}`}
                    />
                  </button>
                  <button
                    type="button"
                    className="danger"
                    aria-label={`Remove ${term.term || `term ${i + 1}`}`}
                    onClick={() => remove(i)}
                  >
                    Remove
                  </button>
                </>
              )}
            </li>
          ))}
      </ul>
      <ShowMore found={found} many="terms" listRef={listRef} />
    </div>
  );
}

export function StyleFields({
  guide,
  onChange,
}: {
  guide: Guide;
  onChange: (patch: Partial<Guide>) => void;
}) {
  const id = useId();
  const [culture, setCulture] = useState(guide.cultural_references.join("\n"));
  return (
    <div className="style-fields">
      <div className="field">
        <label htmlFor={`${id}-summary`}>Summary</label>
        <textarea
          id={`${id}-summary`}
          dir="auto"
          rows={3}
          value={guide.summary}
          onChange={(e) => onChange({ summary: e.target.value })}
        />
      </div>
      <div className="field">
        <label htmlFor={`${id}-style`}>Style guide</label>
        <textarea
          id={`${id}-style`}
          dir="auto"
          rows={4}
          value={guide.style_guide}
          onChange={(e) => onChange({ style_guide: e.target.value })}
        />
      </div>
      <div className="field">
        <label htmlFor={`${id}-culture`}>Cultural references (one per line)</label>
        <textarea
          id={`${id}-culture`}
          dir="auto"
          rows={4}
          value={culture}
          onChange={(e) => {
            setCulture(e.target.value);
            onChange({
              cultural_references: e.target.value
                .split("\n")
                .map((line) => line.trim())
                .filter(Boolean),
            });
          }}
        />
      </div>
    </div>
  );
}
