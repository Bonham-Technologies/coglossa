import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ApiError, isGuideChanged } from "../api/client";
import { useApproveGuide, useProjectGuide, useRunGuide, useSaveProjectGuide } from "../api/guide";
import { useLoadedRun } from "../api/runs";
import type { GlossaryImportResult, Guide, GuideView } from "../api/types";
import { Dialog } from "../components/Dialog";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { useTabs } from "../components/useTabs";
import { EMPTY_GUIDE, guideProblems, sameGuide } from "./entries";
import { NameRows, StyleFields, TermRows } from "./GuideRows";
import { RecheckPrompt } from "./RecheckPrompt";
import type { GuideMode } from "./mode";
import { importSummary, TermImport } from "./TermImport";

type Tab = "names" | "terms" | "style";
const TABS: Tab[] = ["names", "terms", "style"];
const TAB_LABELS: Record<Tab, string> = { names: "Names", terms: "Terms", style: "Style" };

type Shared = {
  projectId: string;
  targetLang: string; // CGL-24: the language whose set guide this is
  runId: string | undefined; // the file on screen, whose guide notes are shown (CGL-80)
  onDirty: (dirty: boolean) => void;
  onDone: () => void;
  onRequestClose: () => void;
};

/** CGL-80: when the open file's guide couldn't be built, or only a shorter one was, say so. */
function GuideNotes({ runId }: { runId: string | undefined }) {
  const run = useLoadedRun(runId);
  const notes = (run.data?.problems ?? []).filter((p) => p.stage === "guide");
  return notes.map((note) => (
    <p key={note.text} className="notice">
      {note.text}
    </p>
  ));
}

function CloseButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      className="quiet"
      aria-label="Close the translation guide"
      onClick={onClick}
    >
      ×
    </button>
  );
}

/** Spec §7.4: focus starts on the safe button, and goes back to what asked the question. */
function DiscardQuestion({
  opener,
  onKeep,
  onDiscard,
}: {
  opener: HTMLElement | null;
  onKeep: () => void;
  onDiscard: () => void;
}) {
  const keep = useRef<HTMLButtonElement>(null);
  const questionId = useId();
  useEffect(() => {
    keep.current?.focus();
    return () => {
      if (opener?.isConnected) opener.focus();
    };
  }, [opener]);
  return (
    <div
      role="alertdialog"
      aria-label="Unsaved changes"
      aria-describedby={questionId}
      className="notice"
    >
      <p id={questionId}>You have unsaved changes. Close without saving them?</p>
      <div className="row end">
        <button ref={keep} type="button" onClick={onKeep}>
          Keep editing
        </button>
        <button type="button" className="danger" onClick={onDiscard}>
          Close without saving
        </button>
      </div>
    </div>
  );
}

/** The guide's loading and error states still offer a way out. */
function GuideWaiting({
  onRequestClose,
  children,
}: {
  onRequestClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="guide-form">
      <div className="row between">
        <h2>Translation guide</h2>
        <CloseButton onClick={onRequestClose} />
      </div>
      {children}
    </div>
  );
}

function GuideForm({
  initial,
  base: initialBase,
  mode,
  reload,
  imported,
  onImported,
  projectId,
  targetLang,
  runId,
  onDirty,
  onDone,
  onRequestClose,
}: Shared & {
  initial: Guide;
  base: number | null;
  mode: GuideMode;
  reload: () => Promise<unknown>;
  imported: GlossaryImportResult | null; // CGL-88: the import this form was reloaded after
  onImported: (result: GlossaryImportResult) => void;
}) {
  const [base, setBase] = useState(initialBase);
  const [reloadError, setReloadError] = useState<unknown>(null);
  const [draft, setDraft] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const { selected: tab, tabProps, panelProps } = useTabs(TABS, imported ? "terms" : undefined);
  // How many saves have gone through: the recheck prompt belongs to the latest one (0 = none, or
  // dismissed). An import that added a version counts as one.
  const [saves, setSaves] = useState(imported?.created_version ? 1 : 0);
  const save = useSaveProjectGuide(projectId, targetLang);
  const approve = useApproveGuide(mode.kind === "draft" ? mode.runId : "", projectId);
  const dirty = !sameGuide(draft, baseline);
  useEffect(() => onDirty(dirty), [dirty, onDirty]);
  const problems = guideProblems(draft);
  const pending = save.isPending || approve.isPending;
  const error = save.error ?? approve.error;
  const set = (patch: Partial<Guide>) => setDraft((d) => ({ ...d, ...patch }));

  async function submit() {
    try {
      if (mode.kind === "project") {
        const view = await save.mutateAsync({ guide: draft, base_version: base });
        setBase(view.info.version);
      } else await approve.mutateAsync({ guide: draft, base_version: base ?? 0 });
    } catch {
      return; // shown below
    }
    setReloadError(null); // a failed reload's message is stale once a save has gone through
    setBaseline(draft);
    if (mode.kind === "draft") onDone();
    else setSaves((n) => n + 1);
  }

  return (
    <div className="guide-form">
      <div className="row between">
        <h2>Translation guide</h2>
        <CloseButton onClick={onRequestClose} />
      </div>
      <p className="muted">
        {mode.kind === "draft"
          ? "Check the names, terms and style, then start translating."
          : "The names, terms and style this set's translations follow."}
      </p>
      <GuideNotes runId={runId} />
      <div role="tablist" aria-label="Guide sections" className="tabs">
        {TABS.map((t) => (
          <button key={t} {...tabProps(t)}>
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>
      <div {...panelProps("names")}>
        {tab === "names" ? (
          <NameRows names={draft.entities} onChange={(entities) => set({ entities })} />
        ) : null}
      </div>
      <div {...panelProps("terms")}>
        {tab === "terms" ? (
          <>
            {imported ? (
              <p role="status" className="notice">
                {importSummary(imported)}
              </p>
            ) : null}
            {mode.kind === "project" ? (
              <TermImport
                projectId={projectId}
                targetLang={targetLang}
                blocked={dirty}
                onImported={onImported}
              />
            ) : null}
            <TermRows terms={draft.glossary} onChange={(glossary) => set({ glossary })} />
          </>
        ) : null}
      </div>
      <div {...panelProps("style")}>
        {tab === "style" ? <StyleFields guide={draft} onChange={set} /> : null}
      </div>
      {problems.map((problem) => (
        <p key={problem} className="error">
          {problem}
        </p>
      ))}
      {isGuideChanged(error) ? (
        <div role="alert" className="notice">
          <p>Someone else changed the guide. Reload to see their changes.</p>
          <button
            type="button"
            onClick={() => {
              setReloadError(null);
              void reload().then(undefined, setReloadError);
            }}
          >
            Reload
          </button>
        </div>
      ) : error ? (
        <ErrorMessage error={error} />
      ) : null}
      {reloadError ? <ErrorMessage error={reloadError} /> : null}
      {saves > 0 ? (
        <RecheckPrompt
          key={saves}
          projectId={projectId}
          targetLang={targetLang}
          // With edits under way, dismissing only hides the question.
          onDone={dirty ? () => setSaves(0) : onDone}
        />
      ) : null}
      {saves > 0 && !dirty ? null : (
        <div className="row end">
          <button type="button" onClick={onRequestClose}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            disabled={(mode.kind === "project" && !dirty) || problems.length > 0 || pending}
            onClick={() => void submit()}
          >
            {mode.kind === "draft" ? "Save and translate" : "Save"}
          </button>
        </div>
      )}
    </div>
  );
}

/** Reload (after a conflict) remounts the form with the server's guide, dropping the edits. */
function Loaded({
  query,
  mode,
  allowMissing,
  ...shared
}: Shared & {
  query: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    data: GuideView | undefined;
    refetch: () => Promise<{ data: GuideView | undefined; isError: boolean; error: unknown }>;
  };
  mode: GuideMode;
  allowMissing: boolean;
}) {
  const [generation, setGeneration] = useState(0);
  // Seeds only the remount after Reload or an import (the hook's own data can lag one render
  // behind it); the form keeps its own base version afterwards.
  const [reloaded, setReloaded] = useState<GuideView | undefined>(undefined);
  const [imported, setImported] = useState<GlossaryImportResult | null>(null);
  if (query.isPending) {
    return (
      <GuideWaiting onRequestClose={shared.onRequestClose}>
        <Loading />
      </GuideWaiting>
    );
  }
  const missing = allowMissing && query.error instanceof ApiError && query.error.status === 404;
  if (query.isError && !missing && !query.data) {
    return (
      <GuideWaiting onRequestClose={shared.onRequestClose}>
        <ErrorMessage error={query.error} />
      </GuideWaiting>
    );
  }
  const view = reloaded ?? query.data;
  return (
    <GuideForm
      key={generation}
      initial={view?.guide ?? EMPTY_GUIDE}
      base={view?.info.version ?? null}
      mode={mode}
      reload={async () => {
        const result = await query.refetch();
        if (result.isError) throw result.error;
        setReloaded(result.data);
        setImported(null);
        setGeneration((g) => g + 1);
      }}
      imported={imported}
      onImported={(result) => {
        setReloaded(result.view);
        setImported(result);
        setGeneration((g) => g + 1);
      }}
      {...shared}
    />
  );
}

function ProjectGuide(shared: Shared) {
  const query = useProjectGuide(shared.projectId, shared.targetLang);
  return <Loaded query={query} mode={{ kind: "project" }} allowMissing {...shared} />;
}

function DraftGuide({ draftRunId, ...shared }: Shared & { draftRunId: string }) {
  const query = useRunGuide(draftRunId);
  return (
    <Loaded
      query={query}
      mode={{ kind: "draft", runId: draftRunId }}
      allowMissing={false}
      {...shared}
    />
  );
}

/** Spec §5.5. */
export function GuidePanel({
  projectId,
  targetLang,
  mode,
  runId,
  onClose,
}: {
  projectId: string;
  targetLang: string;
  mode: GuideMode | null;
  runId?: string; // the file on screen, if any
  onClose: () => void;
}) {
  const dirty = useRef(false);
  const [confirming, setConfirming] = useState(false);
  const [opener, setOpener] = useState<HTMLElement | null>(null);
  const onDirty = useCallback((value: boolean) => {
    dirty.current = value;
  }, []);
  const close = () => {
    dirty.current = false;
    setConfirming(false);
    onClose();
  };
  const requestClose = () => {
    // Esc (or Close) with the question already open means Keep editing; the question's own
    // cleanup gives focus back to whatever first asked it.
    if (confirming) return setConfirming(false);
    if (!dirty.current) return close();
    setOpener(document.activeElement instanceof HTMLElement ? document.activeElement : null);
    setConfirming(true);
  };
  const shared = {
    projectId,
    targetLang,
    runId: mode?.kind === "draft" ? mode.runId : runId,
    onDirty,
    onDone: close,
    onRequestClose: requestClose,
  };
  return (
    <Dialog
      open={mode !== null}
      label="Translation guide"
      onClose={requestClose}
      className="dialog guide-panel"
    >
      {/* The form waits, unreachable, while the question is open. */}
      <div inert={confirming}>
        {mode?.kind === "project" ? <ProjectGuide {...shared} /> : null}
        {mode?.kind === "draft" ? <DraftGuide draftRunId={mode.runId} {...shared} /> : null}
      </div>
      {confirming ? (
        <DiscardQuestion opener={opener} onKeep={() => setConfirming(false)} onDiscard={close} />
      ) : null}
    </Dialog>
  );
}
