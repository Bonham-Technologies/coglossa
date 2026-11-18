import { useId, useState } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { useAddLanguage } from "../api/sets";
import { useTiers } from "../api/system";
import type { ProjectInfo } from "../api/types";
import { Dialog } from "../components/Dialog";
import { ErrorMessage } from "../components/ErrorMessage";
import { languageOk } from "../components/LanguageInput";
import { languageName, rememberLanguage } from "../lib/languages";
import { LanguagePicker } from "../translate/LanguagePicker";
import { languagesOf, sameLanguage } from "./languages";

type Props = { project: ProjectInfo; files: number; open: boolean; onClose: () => void };

function AddLanguageForm({ project, files, onClose }: Omit<Props, "open">) {
  const tiers = useTiers();
  const add = useAddLanguage(project.id);
  const navigate = useNavigate();
  const refusalId = useId();
  const [target, setTarget] = useState("");
  const taken = languagesOf(project);
  const valid = languageOk(target, true);
  const already = valid && taken.some((t) => sameLanguage(t, target));
  const exists = add.error instanceof ApiError && add.error.code === "language_exists";
  const name = languageName(target);
  const refused = already || exists;
  const which = files === 1 ? "The file" : `All ${files} files`;
  return (
    <div>
      <h2>Translate into another language</h2>
      <LanguagePicker
        label="Language"
        value={target}
        onChange={(code) => {
          add.reset(); // a refusal is about the language chosen before
          setTarget(code);
        }}
        tiers={tiers.data}
        taken={taken}
        refused={refused}
        describedBy={refusalId}
      />
      {/* Always mounted, so the refusal is announced when it appears. */}
      <p id={refusalId} role="status" className="error">
        {refused ? `This set is already translated into ${name}.` : null}
      </p>
      {!refused && valid ? (
        <p>
          {which} will be translated into {name}. It gets its own translation guide.
        </p>
      ) : null}
      {add.isError && !exists ? <ErrorMessage error={add.error} /> : null}
      <div className="row end">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="primary"
          disabled={!valid || already || add.isPending}
          onClick={() =>
            add.mutate(target, {
              onSuccess: (runs) => {
                rememberLanguage(target);
                onClose();
                if (runs[0]) void navigate(`/t/${project.id}/${runs[0].id}`);
              },
            })
          }
        >
          Translate
        </button>
      </div>
    </div>
  );
}

/** CGL-24: every file of the set in another language, without uploading anything again. */
export function AddLanguageDialog({ open, onClose, ...rest }: Props) {
  return (
    <Dialog open={open} label="Translate into another language" onClose={onClose}>
      <AddLanguageForm {...rest} onClose={onClose} />
    </Dialog>
  );
}
