import { useEffect, useRef, useState } from "react";
import { Link, NavLink } from "react-router";
import { useSetList } from "../api/sets";
import type { ProjectInfo } from "../api/types";
import { groupByDate } from "../lib/dates";
import { setStatus } from "../lib/status";
import { ErrorMessage } from "./ErrorMessage";
import { Loading } from "./Loading";
import { Menu } from "./Menu";
import { DeleteDialog, RenameDialog } from "./SetDialogs";
import { StatusText } from "./StatusText";

export const SEARCH_FROM = 15;

type RowProps = {
  project: ProjectInfo;
  onNavigate: () => void;
  onRename: (project: ProjectInfo) => void;
  onDelete: (project: ProjectInfo) => void;
};

function SetRow({ project, onNavigate, onRename, onDelete }: RowProps) {
  return (
    <li className="set-row" data-set-id={project.id}>
      <NavLink to={`/t/${project.id}`} className="set-link" onClick={onNavigate}>
        <span className="set-name" dir="auto">
          {project.name}
        </span>
        <StatusText status={setStatus(project)} />
      </NavLink>
      <Menu label={`More actions for ${project.name}`} button="⋯" className="row-menu">
        {(close) => (
          <>
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                close();
                onRename(project);
              }}
            >
              Rename
            </button>
            <button
              role="menuitem"
              type="button"
              className="danger"
              onClick={() => {
                close();
                onDelete(project);
              }}
            >
              Delete…
            </button>
          </>
        )}
      </Menu>
    </li>
  );
}

/** Spec §3.1: + New translation, then the sets, newest activity first. */
export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const sets = useSetList();
  const [search, setSearch] = useState("");
  // One pair of dialogs serves every row; these hold the row each is open for.
  const [renaming, setRenaming] = useState<ProjectInfo | null>(null);
  const [deleting, setDeleting] = useState<ProjectInfo | null>(null);
  const navRef = useRef<HTMLElement>(null);
  const newRef = useRef<HTMLAnchorElement>(null);
  // The row to put focus on (null: New translation). Each request is a new object, so the effect
  // below acts on it once.
  const [refocus, setRefocus] = useState<{ id: string | null } | null>(null);
  const rows = () =>
    Array.from(navRef.current?.querySelectorAll<HTMLElement>("li[data-set-id]") ?? []);
  // A rename can re-sort the list and rebuild the row, so focus is set after the render that
  // shows the settled list.
  useEffect(() => {
    if (refocus === null) return;
    if (refocus.id === null) {
      newRef.current?.focus();
    } else {
      const row = rows().find((li) => li.dataset.setId === refocus.id);
      (row?.querySelector<HTMLElement>("button[aria-haspopup]") ?? navRef.current)?.focus();
    }
  }, [refocus]);
  // The deleted row and its menu button are gone: hand focus to the next row, else the previous.
  const afterDelete = (gone: ProjectInfo) => {
    const ids = rows().map((li) => li.dataset.setId);
    const at = ids.indexOf(gone.id);
    setRefocus({ id: ids[at + 1] ?? ids[at - 1] ?? null });
  };
  const all = [...(sets.data ?? [])].sort(
    (a, b) => Date.parse(b.last_activity_at) - Date.parse(a.last_activity_at),
  );
  const term = search.trim().toLocaleLowerCase();
  const shown = term ? all.filter((p) => p.name.toLocaleLowerCase().includes(term)) : all;
  return (
    <nav
      ref={navRef}
      tabIndex={-1}
      aria-label="Your translations"
      className={open ? "sidebar open" : "sidebar"}
    >
      <Link ref={newRef} to="/" className="button primary new" onClick={onNavigate}>
        + New translation
      </Link>
      {all.length > SEARCH_FROM ? (
        <input
          type="search"
          aria-label="Search your translations"
          placeholder="Search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      ) : null}
      {sets.isPending ? <Loading /> : null}
      {sets.isError ? <ErrorMessage error={sets.error} /> : null}
      {sets.data && all.length === 0 ? (
        <p className="muted">Your translations will appear here.</p>
      ) : null}
      {groupByDate(shown, (p) => p.last_activity_at).map((group) => (
        <section key={group.label} aria-label={group.label}>
          <h2 className="group">{group.label}</h2>
          <ul>
            {group.items.map((p) => (
              <SetRow
                key={p.id}
                project={p}
                onNavigate={onNavigate}
                onRename={setRenaming}
                onDelete={setDeleting}
              />
            ))}
          </ul>
        </section>
      ))}
      <RenameDialog
        project={renaming}
        open={renaming !== null}
        onClose={() => {
          if (renaming) setRefocus({ id: renaming.id });
          setRenaming(null);
        }}
      />
      <DeleteDialog
        project={deleting}
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        onDeleted={afterDelete}
      />
    </nav>
  );
}
