import type { Status } from "../lib/status";

/** A status in words, with its symbol, coloured by tone (never colour alone). */
export function StatusText({ status }: { status: Status }) {
  return (
    <span className={`status tone-${status.tone}`}>
      {status.symbol ? <span aria-hidden="true">{status.symbol} </span> : null}
      {status.text}
    </span>
  );
}
