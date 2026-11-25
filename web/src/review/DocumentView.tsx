import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { findFlagged, useDocumentPages } from "../api/review";
import type { ReviewSpan, RunDetail } from "../api/types";
import { ErrorMessage, messageOf } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { languageName } from "../lib/languages";
import { SummaryBanner } from "../set/SummaryBanner";
import { scoreFilterFrom } from "./filters";
import { RunLanguages, runLanguages, type RunLanguagesValue } from "./languages";
import { PassagePanel } from "./PassagePanel";
import { useDocumentKeys } from "./useDocumentKeys";

type RowProps = {
  span: ReviewSpan;
  languages: RunLanguagesValue;
  selected: boolean;
  onOpen: () => void;
  onFocus: () => void;
  onPointerDown: () => void;
};

function PassageRow({ span, languages, selected, onOpen, onFocus, onPointerDown }: RowProps) {
  const flagged = span.flags.some((f) => f.status === "open");
  const classes = [flagged ? "flagged" : "", selected ? "selected" : "", span.kept ? "kept" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <tr
      id={`passage-${span.id}`}
      className={classes || undefined}
      aria-current={selected ? "true" : undefined}
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        // Enter on the row itself opens it; it must not reach the window's open/close toggle
        if (e.key !== "Enter" || e.target !== e.currentTarget) return;
        e.preventDefault();
        e.stopPropagation();
        onOpen();
      }}
      onFocus={onFocus}
      onPointerDown={onPointerDown}
    >
      <td className="source" dir="auto" lang={languages.source}>
        {flagged ? (
          <>
            <span aria-hidden="true">⚠ </span>
            <span className="visually-hidden">Worth a look: </span>
          </>
        ) : null}
        {span.source}
      </td>
      <td className="target">
        <span dir="auto" lang={languages.target}>
          {span.text}
        </span>
        {span.kept ? <span className="tag">left as written</span> : null}
        {span.edited ? <span className="tag">edited</span> : null}
      </td>
    </tr>
  );
}

/** Spec §5.3: the original and the translation side by side, the passage panel beside them. */
export function DocumentView({ detail }: { detail: RunDetail }) {
  const runId = detail.run.id;
  const [params, setParams] = useSearchParams();
  const scores = useMemo(() => scoreFilterFrom(params), [params]);
  const byScore = Object.keys(scores).length > 0;
  const [show, setShow] = useState<"all" | "check">("all");
  const pages = useDocumentPages(runId, show === "check", scores);
  const spans = useMemo(() => pages.data?.pages.flatMap((p) => p.spans) ?? [], [pages.data]);
  const total = pages.data?.pages[0]?.total ?? 0;
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [prefill, setPrefill] = useState<{ spanId: string; text: string } | null>(null);
  const [lastNumber, setLastNumber] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const showId = useId();
  const languages = runLanguages(detail);
  const selectedSpan = spans.find((s) => s.id === selected);
  if (selectedSpan && selectedSpan.number !== lastNumber) setLastNumber(selectedSpan.number);
  const queryClient = useQueryClient();
  // Only the latest n / p press may move the document: an earlier, slower walk gives up.
  const walks = useRef(0);
  // The passage the walk in flight is heading for: a quicker press continues from there.
  const heading = useRef<Promise<ReviewSpan | null> | null>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = pages;

  // A passage the reader pressed is where they are: it must not move. A tap focuses the row on
  // the way down, and scrolling then would move the page under the finger, so the tap would miss
  // the row (on a phone, nearly always). Only passages the app moves to (Next, n, p) scroll.
  const pressed = useRef(false);
  useEffect(() => {
    if (pressed.current) {
      pressed.current = false;
      return;
    }
    if (selected !== null) {
      document.getElementById(`passage-${selected}`)?.scrollIntoView({ block: "center" });
    }
  }, [selected]);

  useEffect(() => {
    const element = sentinel.current;
    if (!element || !hasNextPage || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && !isFetchingNextPage) void fetchNextPage();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  async function reveal(target: ReviewSpan, current: () => boolean) {
    let data = pages.data;
    let more = pages.hasNextPage;
    const loaded = () => data?.pages.some((p) => p.spans.some((s) => s.id === target.id)) ?? false;
    while (!loaded() && more) {
      const result = await fetchNextPage({ throwOnError: true });
      data = result.data;
      more = result.hasNextPage;
      if (!current()) return;
    }
    if (!current()) return;
    setSelected(target.id);
    setOpen(true);
  }

  async function goToFlag(forward: boolean) {
    const walk = ++walks.current;
    const current = () => walk === walks.current;
    setNote(null);
    const start = selectedSpan?.number ?? lastNumber ?? (forward ? 0 : Number.MAX_SAFE_INTEGER);
    const ahead = heading.current;
    const find = (async () => {
      const base = ahead ? await ahead.catch(() => null) : null;
      return findFlagged(queryClient, runId, base?.number ?? start, forward, scores);
    })();
    heading.current = find;
    try {
      const target = await find;
      if (!current()) return;
      if (target) await reveal(target, current);
      else setNote(forward ? "No later passage to check." : "No earlier passage to check.");
    } catch (error) {
      if (current()) setNote(messageOf(error));
    } finally {
      if (heading.current === find) heading.current = null;
    }
  }

  // Closing the panel hands focus back to its row, so the keyboard keeps its place, unless
  // focus was somewhere else on the page (a refocus there would scroll the document).
  function closePanel() {
    const active = document.activeElement;
    const inPanel = active === document.body || active?.closest(".passage-panel") != null;
    if (open && inPanel && selected !== null) {
      document.getElementById(`passage-${selected}`)?.focus();
    }
    setOpen(false);
  }

  useDocumentKeys({
    next: () => void goToFlag(true),
    previous: () => void goToFlag(false),
    toggle: () => {
      if (selected !== null) setOpen(!open);
    },
    close: closePanel,
  });

  function onMouseUp() {
    const selection = document.getSelection();
    const text = selection?.toString().trim() ?? "";
    const cell = selection?.anchorNode?.parentElement?.closest("td.source");
    const spanId = cell?.closest("tr")?.id.replace(/^passage-/, "");
    if (text && text.length <= 200 && spanId) setPrefill({ spanId, text });
  }

  const panelOpen = open && selectedSpan !== undefined;
  return (
    <RunLanguages.Provider value={languages}>
      <SummaryBanner detail={detail} onNext={() => void goToFlag(true)} />
      <div className="row between doc-tools">
        <div className="row">
          <label htmlFor={showId}>Show</label>
          <select
            id={showId}
            value={show}
            onChange={(e) => {
              setShow(e.target.value as "all" | "check");
              setOpen(false);
            }}
          >
            <option value="all">Everything</option>
            <option value="check">To check</option>
          </select>
        </div>
        {byScore ? (
          <p className="notice">
            Showing passages by score.{" "}
            <button type="button" className="link" onClick={() => setParams({})}>
              Show everything
            </button>
          </p>
        ) : null}
        <p role="status" className="muted">
          {note}
        </p>
      </div>
      <div className={panelOpen ? "review with-panel" : "review"}>
        <div className="document-wrap">
          {pages.isPending ? <Loading /> : null}
          {pages.isError ? <ErrorMessage error={pages.error} /> : null}
          {pages.data && spans.length === 0 ? (
            <p className="muted">
              {show === "check" ? "Nothing left to check." : "No passages to show."}
            </p>
          ) : null}
          {spans.length > 0 ? (
            <table className="document" aria-label="Document" onMouseUp={onMouseUp}>
              <thead>
                <tr>
                  <th scope="col">Original</th>
                  <th scope="col">{languageName(languages.target)}</th>
                </tr>
              </thead>
              <tbody>
                {spans.map((span) => (
                  <PassageRow
                    key={span.id}
                    span={span}
                    languages={languages}
                    selected={span.id === selected}
                    onOpen={() => {
                      setSelected(span.id);
                      setOpen(true);
                    }}
                    onFocus={() => setSelected(span.id)}
                    onPointerDown={() => {
                      pressed.current = span.id !== selected; // a re-press changes nothing
                    }}
                  />
                ))}
              </tbody>
            </table>
          ) : null}
          {hasNextPage ? (
            <div ref={sentinel} className="load-more">
              <button
                type="button"
                disabled={isFetchingNextPage}
                onClick={() => void fetchNextPage()}
              >
                Load more passages
              </button>
            </div>
          ) : null}
          {spans.length > 0 ? (
            <p className="muted">
              {spans.length} of {total} passages shown
            </p>
          ) : null}
        </div>
        {panelOpen ? (
          <PassagePanel
            key={selectedSpan.id}
            runId={runId}
            projectId={detail.run.project_id}
            spanId={selectedSpan.id}
            prefill={prefill?.spanId === selectedSpan.id ? prefill.text : ""}
            onNext={() => void goToFlag(true)}
            onClose={closePanel}
          />
        ) : null}
      </div>
    </RunLanguages.Provider>
  );
}
