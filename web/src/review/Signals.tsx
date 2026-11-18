import type { SpanDetail } from "../api/types";
import { formatScore } from "../lib/format";
import { agreementLabel, reliabilityLabel, signalLabel } from "./labels";

const value = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : v.toFixed(2);

export function Signals({ detail }: { detail: SpanDetail }) {
  const { score } = detail;
  return (
    <table aria-label="Score details" className="signals">
      <tbody>
        <tr>
          <th scope="row">Meaning</th>
          <td>{formatScore(score.meaning)}</td>
        </tr>
        <tr>
          <th scope="row">Style</th>
          <td>{formatScore(score.style)}</td>
        </tr>
        <tr>
          <th scope="row">Reliability of these scores</th>
          <td>{reliabilityLabel(score.reliability)}</td>
        </tr>
        <tr>
          <th scope="row">Translator agreement</th>
          <td>{agreementLabel(detail.agreement)}</td>
        </tr>
        {Object.entries(score.signals).map(([name, v]) => (
          <tr key={name}>
            <th scope="row">{signalLabel(name)}</th>
            <td>{value(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
