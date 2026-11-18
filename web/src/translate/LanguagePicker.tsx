import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { TierTable } from "../api/types";
import { LanguageInput } from "../components/LanguageInput";
import { knownLanguages, languageName, recentLanguages } from "../lib/languages";
import { sameLanguage } from "../set/languages";

const OTHER = "__other__";

type Props = {
  label: string;
  value: string;
  onChange: (code: string) => void;
  tiers: TierTable | undefined;
  allowDetect?: boolean;
  /** Languages that cannot be chosen: listed, disabled (CGL-24: a set's own languages). */
  taken?: string[];
  /** A refusal shown elsewhere (CGL-99): marks the picker invalid and points at its message. */
  refused?: boolean;
  describedBy?: string;
};

/** Spec §4.1: languages by name, recent first, then the install's; Other takes any code. */
export function LanguagePicker({
  label,
  value,
  onChange,
  tiers,
  allowDetect = false,
  taken = [],
  refused = false,
  describedBy,
}: Props) {
  const id = useId();
  const recent = useMemo(() => recentLanguages(), []);
  const known = useMemo(
    () => knownLanguages(tiers).filter((code) => !recent.includes(code)),
    [tiers, recent],
  );
  const listed = value === "" || recent.includes(value) || known.includes(value);
  const [other, setOther] = useState(!listed);
  // The last value seen or sent: an empty value we did not send means the parent cleared it.
  const last = useRef(value);
  useEffect(() => {
    if (value === "" && last.current !== "") setOther(false);
    last.current = value;
  }, [value]);
  const send = (code: string) => {
    last.current = code;
    onChange(code);
  };
  const option = (code: string) => {
    const isTaken = taken.some((t) => sameLanguage(t, code));
    return (
      <option key={code} value={code} disabled={isTaken}>
        {languageName(code)}
        {isTaken ? " — already in this set" : ""}
      </option>
    );
  };
  return (
    <div className="field language-picker">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        aria-invalid={refused || undefined}
        aria-describedby={refused ? describedBy : undefined}
        value={other ? OTHER : value}
        onChange={(e) => {
          const next = e.target.value;
          setOther(next === OTHER);
          send(next === OTHER ? "" : next);
        }}
      >
        {allowDetect ? (
          <option value="">Detect automatically</option>
        ) : (
          <option value="" disabled>
            Choose a language
          </option>
        )}
        {recent.length > 0 ? (
          <optgroup label="Recent">{recent.map((code) => option(code))}</optgroup>
        ) : null}
        {known.length > 0 ? (
          <optgroup label="All languages">{known.map((code) => option(code))}</optgroup>
        ) : null}
        <option value={OTHER}>Other language…</option>
      </select>
      {other ? (
        <LanguageInput
          label={`${label}: language code`}
          value={value}
          onChange={send}
          refused={refused}
          describedBy={describedBy}
        />
      ) : null}
    </div>
  );
}
