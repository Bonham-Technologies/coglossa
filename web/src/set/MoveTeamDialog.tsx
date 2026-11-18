import { useId, useState } from "react";
import { useMoveProject } from "../api/projects";
import { useTeams } from "../api/system";
import type { ProjectInfo } from "../api/types";
import { Dialog } from "../components/Dialog";
import { ErrorMessage } from "../components/ErrorMessage";

type Props = { project: ProjectInfo; open: boolean; onClose: () => void };

function MoveTeamForm({ project, onClose }: Omit<Props, "open">) {
  const teams = useTeams(true);
  const move = useMoveProject(project.id);
  const [team, setTeam] = useState(project.team ?? "");
  const inputId = useId();
  const listId = useId();
  return (
    <div>
      <h2>Move to team</h2>
      <p className="muted">
        People in the team see this translation. Leave it empty to keep it for the person who made
        it.
      </p>
      <div className="field">
        <label htmlFor={inputId}>Team</label>
        <input id={inputId} list={listId} value={team} onChange={(e) => setTeam(e.target.value)} />
        <datalist id={listId}>
          {(teams.data ?? []).map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
      </div>
      {move.isError ? <ErrorMessage error={move.error} /> : null}
      <div className="row end">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="primary"
          disabled={move.isPending}
          onClick={() => move.mutate(team.trim() || null, { onSuccess: onClose })}
        >
          Move
        </button>
      </div>
    </div>
  );
}

/** CGL-26: an admin moves a set into a team. */
export function MoveTeamDialog({ open, onClose, ...rest }: Props) {
  return (
    <Dialog open={open} label="Move to team" onClose={onClose}>
      <MoveTeamForm {...rest} onClose={onClose} />
    </Dialog>
  );
}
