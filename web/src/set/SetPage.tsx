import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useProject } from "../api/projects";
import { useSetDocuments, useTranslateAgain } from "../api/sets";
import type { DocumentSummary, ProjectInfo, RunSummary } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { GuidePanel } from "../guide/GuidePanel";
import type { GuideMode } from "../guide/mode";
import { languageName } from "../lib/languages";
import { useUploads } from "../upload/queue";
import { AddFilesDialog } from "./AddFilesDialog";
import { AddLanguageDialog } from "./AddLanguageDialog";
import { MoveTeamDialog } from "./MoveTeamDialog";
import { FileTabs } from "./FileTabs";
import { LanguageTabs } from "./LanguageTabs";
import { languagesOf, newestIn } from "./languages";
import { RunView } from "./RunView";
import { SetHeader } from "./SetHeader";

/** `run` is null for a file that has no runs yet. */
export type Picked = { doc: DocumentSummary; run: RunSummary | null; latest: boolean };

/** CGL-24: the run in the address decides the language; else `?lang=`; else the set's first. */
export function currentLanguage(
  project: ProjectInfo,
  docs: DocumentSummary[],
  runId: string | undefined,
  asked: string | null,
): string {
  if (runId !== undefined) {
    for (const doc of docs) {
      const run = doc.runs.find((r) => r.id === runId);
      if (run) return run.target_lang;
    }
  }
  return asked ?? languagesOf(project)[0] ?? "";
}

/** The run the address names, else the file it names (`?file=`), else the first file; each
 * file's newest run in `lang` (spec §3.2, CGL-24). A run that is not in the set picks nothing. */
export function pickRun(
  docs: DocumentSummary[],
  runId: string | undefined,
  docId: string | null | undefined,
  lang: string,
): Picked | null {
  if (runId !== undefined) {
    for (const doc of docs) {
      const run = doc.runs.find((r) => r.id === runId);
      if (run) return { doc, run, latest: newestIn(doc, run.target_lang)?.id === run.id };
    }
    return null;
  }
  const doc = docs.find((d) => d.id === docId) ?? docs[0];
  return doc ? { doc, run: newestIn(doc, lang) ?? null, latest: true } : null;
}

/** A file that has runs, but none in the language shown: start one (CGL-24). */
function MissingLanguage({
  projectId,
  doc,
  lang,
}: {
  projectId: string;
  doc: DocumentSummary;
  lang: string;
}) {
  const again = useTranslateAgain(projectId);
  const navigate = useNavigate();
  const name = languageName(lang);
  return (
    <div className="notice">
      <p>This file hasn&apos;t been translated into {name} yet.</p>
      <button
        type="button"
        className="primary"
        disabled={again.isPending}
        onClick={() =>
          again.mutate(
            { documentId: doc.id, targetLang: lang },
            { onSuccess: (run) => navigate(`/t/${projectId}/${run.id}`) },
          )
        }
      >
        Translate into {name}
      </button>
      {again.isError ? <ErrorMessage error={again.error} /> : null}
    </div>
  );
}

export function SetPage() {
  const { projectId = "", runId } = useParams();
  const [search] = useSearchParams();
  const project = useProject(projectId);
  const documents = useSetDocuments(projectId);
  const uploads = useUploads(projectId);
  const [guide, setGuide] = useState<GuideMode | null>(null);
  const [adding, setAdding] = useState(false);
  const [addingLanguage, setAddingLanguage] = useState(false);
  const [moving, setMoving] = useState(false);
  const docs = useMemo(
    () =>
      [...(documents.data ?? [])].sort(
        (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at),
      ),
    [documents.data],
  );
  if (project.isPending) return <Loading />;
  if (project.isError) return <ErrorMessage error={project.error} />;
  const p = project.data;
  const lang = currentLanguage(p, docs, runId, search.get("lang"));
  const picked = pickRun(docs, runId, search.get("file"), lang);
  // A finished upload stays until its run shows up in the files, so the set never flashes empty.
  const listed = new Set(docs.flatMap((d) => d.runs.map((r) => r.id)));
  const pending = uploads.filter(
    (u) => u.status !== "done" || (u.runId !== null && !listed.has(u.runId)),
  );
  const unknown = runId !== undefined && documents.data !== undefined && picked === null;
  return (
    <section className="set">
      <SetHeader
        project={p}
        picked={picked}
        lang={lang}
        onOpenGuide={() => setGuide({ kind: "project" })}
        onAddFiles={() => setAdding(true)}
        onAddLanguage={() => setAddingLanguage(true)}
        onMoveTeam={() => setMoving(true)}
      />
      <LanguageTabs project={p} docs={docs} lang={lang} picked={picked} />
      <FileTabs projectId={p.id} docs={docs} uploads={pending} picked={picked} lang={lang} />
      {documents.isError ? (
        <div className="row">
          <ErrorMessage error={documents.error} />
          <button
            type="button"
            aria-label="Try again: load the files"
            onClick={() => void documents.refetch()}
          >
            Try again
          </button>
        </div>
      ) : null}
      {unknown ? (
        <p className="notice">
          This file isn&apos;t part of this translation.
          {docs.length > 0 ? (
            <>
              {" "}
              <Link to={`/t/${p.id}`}>Open the first file</Link>
            </>
          ) : null}
        </p>
      ) : null}
      {runId !== undefined && documents.isPending ? <Loading /> : null}
      {picked && !picked.run ? (
        picked.doc.runs.length > 0 ? (
          <MissingLanguage projectId={p.id} doc={picked.doc} lang={lang} />
        ) : (
          <p className="notice">This file hasn&apos;t started yet.</p>
        )
      ) : null}
      {picked?.run ? (
        <>
          {!picked.latest ? (
            <p className="notice">
              An earlier version.{" "}
              <Link to={`/t/${p.id}/${newestIn(picked.doc, lang)!.id}`}>See the latest</Link>
            </p>
          ) : null}
          <RunView key={picked.run.id} runId={picked.run.id} onOpenGuide={setGuide} />
        </>
      ) : null}
      {documents.data && docs.length === 0 && pending.length === 0 && !unknown ? (
        <div className="notice">
          <p>No files yet.</p>
          <button type="button" className="primary" onClick={() => setAdding(true)}>
            + Add files
          </button>
        </div>
      ) : null}
      <AddFilesDialog project={p} lang={lang} open={adding} onClose={() => setAdding(false)} />
      <MoveTeamDialog project={p} open={moving} onClose={() => setMoving(false)} />
      <AddLanguageDialog
        project={p}
        files={docs.length}
        open={addingLanguage}
        onClose={() => setAddingLanguage(false)}
      />
      <GuidePanel
        projectId={p.id}
        targetLang={lang}
        mode={guide}
        runId={picked?.run?.id}
        onClose={() => setGuide(null)}
      />
    </section>
  );
}
