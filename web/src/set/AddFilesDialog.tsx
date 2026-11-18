import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useMe, useTiers } from "../api/system";
import type { ProjectInfo } from "../api/types";
import { Dialog } from "../components/Dialog";
import { languageOk } from "../components/LanguageInput";
import { languageName } from "../lib/languages";
import { DropArea, fileProblem } from "../translate/DropArea";
import { LanguagePicker } from "../translate/LanguagePicker";
import { uploadLimit } from "../translate/NewTranslationPage";
import { enqueueUploads } from "../upload/queue";
import { languagesOf, listLanguages } from "./languages";

/** `lang`: the language shown, the one the upload's own run is in. */
type Props = { project: ProjectInfo; lang: string; open: boolean; onClose: () => void };

function AddFilesForm({ project, lang, onClose }: Omit<Props, "open">) {
  const me = useMe();
  const tiers = useTiers();
  const queryClient = useQueryClient();
  const known = lang;
  // CGL-24: a set with several languages translates new files into all of them
  const languages = languagesOf(project);
  const every = languages.length > 1;
  const [files, setFiles] = useState<File[]>([]);
  const [target, setTarget] = useState(known);
  const maxMb = uploadLimit(me.data);
  const usable = files.filter((f) => fileProblem(f, maxMb) === null);
  const ready = usable.length > 0 && languageOk(target, true);
  return (
    <div>
      <h2>Add files</h2>
      <p className="muted">
        New files start from this set&apos;s translation guide, so names stay the same.
      </p>
      <DropArea files={files} onFiles={setFiles} maxMb={maxMb} />
      {every ? (
        <p>Translated into {listLanguages(languages)}.</p>
      ) : known ? (
        <p>Translating into {languageName(known)}.</p>
      ) : (
        <LanguagePicker label="To" value={target} onChange={setTarget} tiers={tiers.data} />
      )}
      <div className="row end">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="primary"
          disabled={!ready}
          onClick={() => {
            enqueueUploads(
              project.id,
              usable,
              {
                target_lang: target,
                source_lang: project.source_lang,
                pipeline_profile: project.pipeline_profile,
              },
              queryClient,
            );
            onClose();
          }}
        >
          Add and translate
        </button>
      </div>
    </div>
  );
}

/** Spec §4.3. */
export function AddFilesDialog({ open, onClose, ...rest }: Props) {
  return (
    <Dialog open={open} label="Add files" onClose={onClose}>
      <AddFilesForm {...rest} onClose={onClose} />
    </Dialog>
  );
}
