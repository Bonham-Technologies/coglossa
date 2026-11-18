import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useNavigate } from "react-router";
import { projectKeys } from "../api/projects";
import { createSet } from "../api/sets";
import { useMe, useTiers } from "../api/system";
import type { CreateProject, Me, PipelineProfile } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { languageOk } from "../components/LanguageInput";
import { sameLanguage } from "../set/languages";
import { useTabs } from "../components/useTabs";
import { languageName, lessPractised, rememberLanguage } from "../lib/languages";
import { pastedFile, setNameFor } from "../lib/names";
import { rememberedTeam, rememberTeam } from "../lib/teams";
import { enqueueUploads } from "../upload/queue";
import { DropArea, fileProblem, MB } from "./DropArea";
import { LanguagePicker } from "./LanguagePicker";

/** Spec §3.3: the length setting in plain words. */
export const LENGTHS: { value: PipelineProfile | ""; label: string }[] = [
  { value: "", label: "Automatic (recommended)" },
  { value: "short", label: "Short" },
  { value: "standard", label: "Standard" },
  { value: "long-form", label: "Long document" },
];

/** api.max_upload_mb, when the server's settings could be read. */
export function uploadLimit(me: Me | undefined): number | null {
  return me?.max_upload_mb ?? null;
}

type Mode = "files" | "paste";
const MODES: Mode[] = ["files", "paste"];
const MODE_LABELS: Record<Mode, string> = { files: "Files", paste: "Paste text" };

/** Spec §4.1: drop files or paste text, pick the languages, Translate. */
export function NewTranslationPage() {
  const tiers = useTiers();
  const me = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { selected: mode, tabProps, panelProps } = useTabs(MODES);
  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState("");
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [length, setLength] = useState<PipelineProfile | "">("");
  const headingId = useId();
  const pasteId = useId();
  const pasteNoteId = useId();
  const lengthId = useId();
  const teamId = useId();
  // CGL-26: a person in two or more teams chooses; one team is used without asking; none: personal
  const teams = me.data?.teams ?? [];
  const [team, setTeam] = useState<string | null>(null); // null: not chosen on this visit
  const chosenTeam = team ?? rememberedTeam(teams);
  const sendTeam = teams.length === 1 ? teams[0]! : teams.length > 1 ? chosenTeam || null : null;
  const create = useMutation({ mutationFn: (body: CreateProject) => createSet(body) });

  const maxMb = uploadLimit(me.data);
  const chosen = mode === "files" ? files : text.trim() ? [pastedFile(text)] : [];
  // Pasted text is sent as a file, so the upload limit is its limit.
  const pasted = mode === "paste" ? chosen[0] : undefined;
  const tooLong = pasted !== undefined && maxMb !== null && pasted.size > maxMb * MB;
  // CGL-96: translating a language into itself would only paraphrase it. Regional variants
  // (en-US to en-GB) are different codes, so they stay allowed; "Detect automatically" is empty.
  const sameLang = source !== "" && target !== "" && sameLanguage(source, target);
  const usable = chosen.filter((file) => fileProblem(file, maxMb) === null);
  // CGL-31: the team to send comes from /api/me, so wait for it.
  const ready =
    me.isSuccess &&
    usable.length > 0 &&
    languageOk(target, true) &&
    languageOk(source) &&
    !sameLang &&
    !create.isPending;
  const sourceOrNull = source || null;
  const options = {
    target_lang: target,
    source_lang: sourceOrNull,
    pipeline_profile: length || null,
  };

  function translate() {
    create.mutate(
      { name: setNameFor(usable), ...options, ...(sendTeam !== null ? { team: sendTeam } : {}) },
      {
        onSuccess: (set) => {
          rememberLanguage(target);
          if (teams.length > 1) rememberTeam(chosenTeam);
          void queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true });
          enqueueUploads(set.id, usable, options, queryClient);
          navigate(`/t/${set.id}`);
        },
      },
    );
  }

  return (
    <section className="new-translation" aria-labelledby={headingId}>
      <h1 id={headingId}>New translation</h1>
      <div role="tablist" aria-label="What to translate" className="tabs">
        {MODES.map((m) => (
          <button key={m} {...tabProps(m)}>
            {MODE_LABELS[m]}
          </button>
        ))}
      </div>
      <div {...panelProps("files")}>
        <DropArea files={files} onFiles={setFiles} maxMb={maxMb} active={mode === "files"} />
      </div>
      <div {...panelProps("paste")}>
        <div className="field">
          <label htmlFor={pasteId}>Text to translate</label>
          <textarea
            id={pasteId}
            aria-describedby={tooLong ? pasteNoteId : undefined}
            dir="auto"
            rows={12}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          {/* Always mounted, so the message is announced when it appears. */}
          <p id={pasteNoteId} role="status" className="error">
            {tooLong
              ? `This text is too long (the limit is ${maxMb} MB). Split it into shorter parts and translate each one.`
              : null}
          </p>
        </div>
      </div>
      <div className="row languages">
        <LanguagePicker
          label="From"
          value={source}
          onChange={setSource}
          tiers={tiers.data}
          allowDetect
        />
        <span aria-hidden="true">→</span>
        <LanguagePicker label="To" value={target} onChange={setTarget} tiers={tiers.data} />
      </div>
      {/* Outside the tab panels, so it is seen and announced in both. Always mounted, so the
          message is announced when it appears. */}
      <p role="status" className="error">
        {sameLang
          ? "From and To are the same language. Choose a different language to translate into."
          : null}
      </p>
      {teams.length > 1 ? (
        <div className="field">
          <label htmlFor={teamId}>Team</label>
          <select id={teamId} value={chosenTeam} onChange={(e) => setTeam(e.target.value)}>
            {teams.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
            <option value="">Only me</option>
          </select>
        </div>
      ) : null}
      {lessPractised(tiers.data, target, sourceOrNull) ? (
        <p className="notice">
          Coglossa has less practice with {languageName(target)}, so check carefully.
        </p>
      ) : null}
      <details className="more-options">
        <summary>More options</summary>
        <div className="field">
          <label htmlFor={lengthId}>Length</label>
          <select
            id={lengthId}
            value={length}
            onChange={(e) => setLength(e.target.value as PipelineProfile | "")}
          >
            {LENGTHS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
      </details>
      {usable.length > 0 && usable.length < chosen.length ? (
        <p className="muted">Files marked in red will be left out.</p>
      ) : null}
      <button type="button" className="primary translate" disabled={!ready} onClick={translate}>
        {create.isPending ? "Starting…" : "Translate"}
      </button>
      {me.isError ? <ErrorMessage error={me.error} /> : null}
      {create.isError ? <ErrorMessage error={create.error} /> : null}
    </section>
  );
}
