import { useId } from "react";
import { isLanguageCode } from "../lib/format";

export const LANGUAGE_HINT = "Use a language code such as en, pt-BR or zh-Hant";

type Props = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  placeholder?: string;
  /** The caller's own refusal: marks the field invalid and points at the message (CGL-99). */
  refused?: boolean;
  describedBy?: string;
};

export function LanguageInput({
  label,
  value,
  onChange,
  required,
  placeholder,
  refused,
  describedBy,
}: Props) {
  const id = useId();
  const hintId = useId();
  const invalid = value !== "" && !isLanguageCode(value);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        value={value}
        required={required}
        placeholder={placeholder}
        aria-invalid={invalid || refused || undefined}
        aria-describedby={
          [invalid ? hintId : undefined, refused ? describedBy : undefined]
            .filter(Boolean)
            .join(" ") || undefined
        }
        onChange={(e) => onChange(e.target.value.trim())}
        size={12}
      />
      {invalid ? (
        <span id={hintId} className="error">
          {" "}
          {LANGUAGE_HINT}
        </span>
      ) : null}
    </div>
  );
}

/** True when the field is acceptable: a valid code, or empty when optional. */
export function languageOk(value: string, required = false): boolean {
  return value === "" ? !required : isLanguageCode(value);
}
