import { useId, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { useDeleteProject } from "../api/projects";
import { useRenameProject } from "../api/sets";
import type { ProjectInfo } from "../api/types";
import { plural } from "../lib/flags";
import { Dialog } from "./Dialog";
import { ErrorMessage, messageOf } from "./ErrorMessage";

export function renameErrorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "Another translation already has this name.";
  }
  return messageOf(error);
}

type FormProps = { project: ProjectInfo; onClose: () => void };
/** The project is null while no one has picked a target (a list shares one dialog). */
type Props = { project: ProjectInfo | null; open: boolean; onClose: () => void };

function RenameForm({ project, onClose }: FormProps) {
  const rename = useRenameProject(project.id);
  const [name, setName] = useState(project.name);
  const id = useId();
  const trimmed = name.trim();
  function submit(event: FormEvent) {
    event.preventDefault();
    if (trimmed && trimmed !== project.name) rename.mutate(trimmed, { onSuccess: onClose });
  }
  return (
    <form onSubmit={submit}>
      <h2>Rename</h2>
      <div className="field">
        <label htmlFor={id}>Name</label>
        <input
          id={id}
          dir="auto"
          maxLength={200}
          value={name}
          autoFocus
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      {rename.isError ? (
        <p role="alert" className="error">
          {renameErrorText(rename.error)}
        </p>
      ) : null}
      <div className="row end">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="submit"
          className="primary"
          disabled={!trimmed || trimmed === project.name || rename.isPending}
        >
          Rename
        </button>
      </div>
    </form>
  );
}

export function RenameDialog({ project, open, onClose }: Props) {
  return (
    <Dialog open={open} label="Rename" onClose={onClose}>
      {project ? <RenameForm project={project} onClose={onClose} /> : null}
    </Dialog>
  );
}

function DeleteForm({
  project,
  onClose,
  onDeleted,
}: FormProps & { onDeleted: (project: ProjectInfo) => void }) {
  const remove = useDeleteProject(project.id);
  const navigate = useNavigate();
  const location = useLocation();
  const leaving =
    location.pathname === `/t/${project.id}` || location.pathname.startsWith(`/t/${project.id}/`);
  return (
    <div>
      <h2>
        Delete “<span dir="auto">{project.name}</span>”?
      </h2>
      <p>
        {project.languages.length > 1
          ? `This deletes the translation in every language, its ${plural(project.documents, "file")}, its translation guides and every review decision.`
          : `This deletes the translation, its ${plural(project.documents, "file")}, its translation guide and every review decision.`}{" "}
        It can&apos;t be undone.
      </p>
      {remove.isError ? <ErrorMessage error={remove.error} /> : null}
      <div className="row end">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="danger"
          disabled={remove.isPending}
          onClick={() =>
            remove.mutate(project.name, {
              onSuccess: () => {
                onClose();
                onDeleted(project);
                if (leaving) navigate("/");
              },
            })
          }
        >
          Delete
        </button>
      </div>
    </div>
  );
}

export function DeleteDialog({
  project,
  open,
  onClose,
  onDeleted,
}: Props & { onDeleted: (project: ProjectInfo) => void }) {
  return (
    <Dialog open={open} label="Delete" onClose={onClose}>
      {project ? <DeleteForm project={project} onClose={onClose} onDeleted={onDeleted} /> : null}
    </Dialog>
  );
}
