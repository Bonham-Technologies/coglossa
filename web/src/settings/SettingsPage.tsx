import { useDoctor, useDoctorStatus, useMe } from "../api/system";
import type { DoctorReport } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { useTheme, type ThemeChoice } from "../theme/theme";
import { reportFailed, statusSentence } from "./statusSentence";
import { TechnicalDetails } from "./TechnicalDetails";

const THEMES: { value: ThemeChoice; label: string }[] = [
  { value: "system", label: "Match my computer" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

function Appearance() {
  const { choice, setChoice } = useTheme();
  return (
    <fieldset className="appearance">
      <legend>Appearance</legend>
      <p className="muted">Theme</p>
      {THEMES.map((t) => (
        <label key={t.value}>
          <input
            type="radio"
            name="theme"
            value={t.value}
            checked={choice === t.value}
            onChange={() => setChoice(t.value)}
          />{" "}
          {t.label}
        </label>
      ))}
    </fieldset>
  );
}

function Sentence({ report }: { report: DoctorReport }) {
  const failed = reportFailed(report);
  return (
    <p className={failed ? "tone-bad" : "tone-ok"}>
      <span aria-hidden="true">{failed ? "! " : "✓ "}</span>
      {statusSentence(report)}
    </p>
  );
}

function Status({
  quick,
  full,
  report,
}: {
  quick: ReturnType<typeof useDoctorStatus>;
  full: ReturnType<typeof useDoctor>;
  report: DoctorReport | undefined;
}) {
  return (
    <section aria-label="Status">
      <h2>Status</h2>
      {report === undefined && quick.isPending ? <Loading /> : null}
      {report === undefined && quick.isError ? <ErrorMessage error={quick.error} /> : null}
      {report && !full.data ? <Sentence report={report} /> : null}
      {/* Always present, so only a full check's result, which the person asked for, is announced. */}
      <div role="status">{full.data ? <Sentence report={full.data} /> : null}</div>
      <button type="button" disabled={full.isPending} onClick={() => full.mutate(true)}>
        {full.isPending ? "Checking…" : "Run a full check"}
      </button>
      {full.isError ? <ErrorMessage error={full.error} /> : null}
    </section>
  );
}

/** The installation's status and technical details: for admins only (CGL-26), so the checks
 * are never requested for anyone else. */
function AdminSections() {
  const quick = useDoctorStatus();
  const full = useDoctor();
  // The newest check wins, in the sentence and in the table of checks.
  const report = full.data ?? quick.data;
  return (
    <>
      <Status quick={quick} full={full} report={report} />
      <TechnicalDetails report={report} />
    </>
  );
}

/** Spec §6.2. */
export function SettingsPage() {
  const me = useMe();
  return (
    <section className="settings">
      <h1>Settings</h1>
      <Appearance />
      {me.data?.admin === true ? <AdminSections /> : null}
    </section>
  );
}
