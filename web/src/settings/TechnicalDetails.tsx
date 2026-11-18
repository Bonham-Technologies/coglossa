import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { useSetList } from "../api/sets";
import { useConfig, useTiers } from "../api/system";
import type { DoctorReport, TierTable } from "../api/types";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { languageName } from "../lib/languages";
import { score, SCORE_KEYS } from "../review/filters";

const BOUND_LABELS: Record<(typeof SCORE_KEYS)[number], string> = {
  min_meaning: "Meaning from",
  max_meaning: "Meaning to",
  min_style: "Style from",
  max_style: "Style to",
};

/** Spec §6.1: the numeric filters, as a form that opens a translation filtered by score. */
function ScoreSearch() {
  const sets = useSetList();
  const navigate = useNavigate();
  const id = useId();
  const [setId, setSetId] = useState("");
  const [bounds, setBounds] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  function submit(event: FormEvent) {
    event.preventDefault();
    const params = new URLSearchParams();
    for (const key of SCORE_KEYS) {
      const value = score(bounds[key] ?? null);
      if (value !== null) params.set(key, String(value));
    }
    const backwards = (["meaning", "style"] as const).some((what) => {
      const low = params.get(`min_${what}`);
      const high = params.get(`max_${what}`);
      return low !== null && high !== null && Number(low) > Number(high);
    });
    if (backwards) {
      setProblem("The lowest score must not be higher than the highest.");
      return;
    }
    setProblem(null);
    const query = params.toString();
    navigate(`/t/${encodeURIComponent(setId)}${query ? `?${query}` : ""}`);
  }
  return (
    <form aria-label="Find passages by score" onSubmit={submit}>
      <h3>Find passages by score</h3>
      <div className="field">
        <label htmlFor={`${id}-set`}>Translation</label>
        <select id={`${id}-set`} value={setId} onChange={(e) => setSetId(e.target.value)}>
          <option value="" disabled>
            Choose a translation
          </option>
          {(sets.data ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div className="row">
        {SCORE_KEYS.map((key) => (
          <div key={key} className="field">
            <label htmlFor={`${id}-${key}`}>{BOUND_LABELS[key]}</label>
            <input
              id={`${id}-${key}`}
              type="number"
              min={0}
              max={100}
              value={bounds[key] ?? ""}
              onChange={(e) => {
                setBounds({ ...bounds, [key]: e.target.value });
                setProblem(null);
              }}
            />
          </div>
        ))}
      </div>
      {problem ? (
        <p role="alert" className="error">
          {problem}
        </p>
      ) : null}
      <button type="submit" disabled={setId === ""}>
        Show passages
      </button>
    </form>
  );
}

function Checks({ report }: { report: DoctorReport | undefined }) {
  if (report === undefined) return null;
  return (
    <table aria-label="Checks">
      <thead>
        <tr>
          <th>Check</th>
          <th>Result</th>
          <th>Details</th>
          <th>What to do</th>
        </tr>
      </thead>
      <tbody>
        {report.checks.map((check, i) => (
          <tr key={`${check.name}-${i}`}>
            <td>{check.name}</td>
            <td>{check.status}</td>
            <td>{check.message}</td>
            <td>{check.fix}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Config() {
  const config = useConfig();
  if (config.isPending) return <Loading />;
  if (config.isError) return <ErrorMessage error={config.error} />;
  return (
    <>
      <p>
        <code>{config.data.path}</code> <span className="muted">(hash {config.data.hash})</span>
      </p>
      <pre className="config" dir="auto">
        {JSON.stringify(config.data.config, null, 2)}
      </pre>
    </>
  );
}

function TierRows({ entries, named }: { entries: [string, string][]; named?: boolean }) {
  return (
    <tbody>
      {entries.map(([key, tier]) => (
        <tr key={key}>
          <td>
            {named ? (
              <>
                {languageName(key)} <span className="muted">({key})</span>
              </>
            ) : (
              key
            )}
          </td>
          <td>{tier}</td>
        </tr>
      ))}
    </tbody>
  );
}

function Tiers() {
  const tiers = useTiers();
  if (tiers.isPending) return <Loading />;
  if (tiers.isError) return <ErrorMessage error={tiers.error} />;
  const t: TierTable = tiers.data;
  return (
    <>
      <p>Pairs not listed here are tier {t.default}.</p>
      <div className="two-columns">
        <table aria-label="Languages">
          <thead>
            <tr>
              <th>Language</th>
              <th>Tier</th>
            </tr>
          </thead>
          <TierRows entries={Object.entries(t.languages)} named />
        </table>
        <table aria-label="Language pairs">
          <thead>
            <tr>
              <th>Pair</th>
              <th>Tier</th>
            </tr>
          </thead>
          <TierRows entries={Object.entries(t.pairs)} />
        </table>
      </div>
    </>
  );
}

/** Spec §6.2: today's System page, for administrators, folded away. The settings and tiers are
 * fetched when it is first unfolded; `report` is the newest installation check (quick or full). */
export function TechnicalDetails({ report }: { report: DoctorReport | undefined }) {
  const [opened, setOpened] = useState(false);
  return (
    <details
      className="technical"
      onToggle={(event) => {
        if (event.currentTarget.open) setOpened(true);
      }}
    >
      <summary>Technical details</summary>
      <ScoreSearch />
      <h3>Installation checks</h3>
      <Checks report={report} />
      <h3>Active configuration</h3>
      <p className="muted">
        Read-only: change coglossa.yaml on the server and restart the services.
      </p>
      {opened ? <Config /> : null}
      <h3>Language reliability tiers</h3>
      {opened ? <Tiers /> : null}
    </details>
  );
}
