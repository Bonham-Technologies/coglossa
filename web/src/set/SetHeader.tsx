import { useId, useState } from "react";
import { Link, useNavigate } from "react-router";
import { exportUrl, reportUrl } from "../api/client";
import { useProjectGuide } from "../api/guide";
import { useRun } from "../api/runs";
import { useSetBusy, useTranslateAgain } from "../api/sets";
import { useMe } from "../api/system";
import type { Me, ProjectInfo } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { Menu } from "../components/Menu";
import { RenameDialog } from "../components/SetDialogs";
import { whenText } from "../lib/dates";
import { languageName } from "../lib/languages";
import { runsIn } from "./languages";
import type { Picked } from "./SetPage";

/** Spec §5.1: shown once the file has a finished translation. */
export function DownloadMenu({ runId }: { runId: string }) {
  const run = useRun(runId);
  const formatsId = useId();
  if (!run.data?.report_available || run.data.run.state === "sweeping") return null;
  return (
    <Menu
      label="Download"
      button={
        <>
          Download <span aria-hidden="true">▾</span>
        </>
      }
      className="download"
    >
      {(close) => (
        <>
          <a role="menuitem" href={exportUrl(runId, "docx")} download onClick={close}>
            Word document
          </a>
          <a role="menuitem" href={exportUrl(runId, "bilingual-docx")} download onClick={close}>
            Side-by-side Word file with reviewer notes
          </a>
          <a role="menuitem" href={exportUrl(runId, "html")} download onClick={close}>
            Web page
          </a>
          <div role="group" aria-labelledby={formatsId} className="menu-group">
            <p id={formatsId} role="none" className="menu-heading">
              Other formats
            </p>
            <a role="menuitem" href={exportUrl(runId, "json")} download onClick={close}>
              Data file (JSON)
            </a>
            <a role="menuitem" href={exportUrl(runId, "tmx")} download onClick={close}>
              Translation memory (TMX)
            </a>
            <a
              role="menuitem"
              href={exportUrl(runId, "tmx", { reviewedOnly: true })}
              download
              onClick={close}
            >
              Translation memory, only passages a person checked (TMX)
            </a>
            <a role="menuitem" href={reportUrl(runId)} download onClick={close}>
              Quality report
            </a>
          </div>
        </>
      )}
    </Menu>
  );
}

function MoreMenu({
  project,
  picked,
  lang,
  admin,
  onAddFiles,
  onAddLanguage,
  onMoveTeam,
}: {
  project: ProjectInfo;
  picked: Picked | null;
  lang: string;
  admin: boolean;
  onAddFiles: () => void;
  onAddLanguage: () => void;
  onMoveTeam: () => void;
}) {
  const again = useTranslateAgain(project.id);
  const { busy, reason } = useSetBusy(project.id);
  const navigate = useNavigate();
  const earlierId = useId();
  const earlier = picked ? runsIn(picked.doc, lang).slice(1) : [];
  return (
    <>
      <Menu label="More actions" button="⋯">
        {(close) => (
          <>
            {picked ? (
              <button
                role="menuitem"
                type="button"
                disabled={again.isPending}
                aria-disabled={busy || undefined}
                aria-describedby={busy ? "again-reason" : undefined}
                onClick={() => {
                  if (busy) return; // stays focusable so its reason is read
                  close();
                  again.mutate(
                    { documentId: picked.doc.id, targetLang: lang },
                    {
                      onSuccess: (run) => navigate(`/t/${project.id}/${run.id}`),
                    },
                  );
                }}
              >
                Translate again
              </button>
            ) : null}
            {picked && busy ? (
              <p id="again-reason" role="none" className="menu-note">
                {reason}
              </p>
            ) : null}
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                close();
                onAddFiles();
              }}
            >
              + Add files
            </button>
            {/* a picked file means the set has files; the set's own count lags a new upload */}
            {picked ? (
              <button
                role="menuitem"
                type="button"
                onClick={() => {
                  close();
                  onAddLanguage();
                }}
              >
                Translate into another language…
              </button>
            ) : null}
            {admin ? (
              <button
                role="menuitem"
                type="button"
                onClick={() => {
                  close();
                  onMoveTeam();
                }}
              >
                Move to team…
              </button>
            ) : null}
            {earlier.length > 0 ? (
              <div role="group" aria-labelledby={earlierId} className="menu-group">
                <p id={earlierId} role="none" className="menu-heading">
                  Earlier versions
                </p>
                {earlier.map((run) => (
                  <Link
                    key={run.id}
                    role="menuitem"
                    to={`/t/${project.id}/${run.id}`}
                    onClick={close}
                  >
                    Started {whenText(run.created_at)}
                  </Link>
                ))}
              </div>
            ) : null}
          </>
        )}
      </Menu>
      {again.isError ? <ErrorMessage error={again.error} /> : null}
    </>
  );
}

type Props = {
  project: ProjectInfo;
  picked: Picked | null;
  lang: string; // CGL-24: the language shown
  onOpenGuide: () => void;
  onAddFiles: () => void;
  onAddLanguage: () => void;
  onMoveTeam: () => void;
};

/** CGL-26: whose set this is, for people who see more than one team's sets. */
export function teamLabel(project: ProjectInfo, me: Me): string | null {
  if (me.user === null) return null; // no sign-in: there are no teams, everyone sees everything
  if (!me.admin && me.teams.length < 2) return null;
  if (project.team) return project.team;
  if (project.created_by === null) return "No team";
  // names compare case-insensitively, as the server compares them
  const own = project.created_by.trim().toLowerCase() === me.user?.trim().toLowerCase();
  return own ? "Only you" : `Personal to ${project.created_by}`;
}

export function SetHeader({
  project,
  picked,
  lang,
  onOpenGuide,
  onAddFiles,
  onAddLanguage,
  onMoveTeam,
}: Props) {
  const [renaming, setRenaming] = useState(false);
  const me = useMe();
  const label = me.data ? teamLabel(project, me.data) : null;
  const run = picked?.run ?? null;
  const source = run?.source_lang ?? project.source_lang;
  const target = run?.target_lang ?? (lang || project.target_lang);
  const guide = useProjectGuide(project.id, lang);
  const count = guide.data
    ? guide.data.guide.entities.length + guide.data.guide.glossary.length
    : 0;
  return (
    <header className="set-header">
      <div>
        <h1 aria-label={project.name}>
          <button
            type="button"
            className="title-button"
            aria-label={`Rename ${project.name}`}
            onClick={() => setRenaming(true)}
          >
            <span dir="auto">{project.name}</span>
          </button>
        </h1>
        <p className="muted">
          {label ? `${label} · ` : null}
          {source ? languageName(source) : "Detected language"} →{" "}
          {target ? languageName(target) : "—"}
          {run?.finished_at ? ` · finished ${whenText(run.finished_at)}` : ""}
        </p>
      </div>
      <div className="row">
        <button type="button" onClick={onOpenGuide}>
          Translation guide ({count})
        </button>
        {run ? <DownloadMenu runId={run.id} /> : null}
        <MoreMenu
          project={project}
          picked={picked}
          lang={lang}
          admin={me.data?.admin === true}
          onAddFiles={onAddFiles}
          onAddLanguage={onAddLanguage}
          onMoveTeam={onMoveTeam}
        />
      </div>
      <RenameDialog project={project} open={renaming} onClose={() => setRenaming(false)} />
    </header>
  );
}
