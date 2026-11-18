import { FLAG_TITLES } from "../lib/flags";
import { Dialog } from "./Dialog";

/** Spec §7.4: the shortcuts, statuses and flag titles on one short page. */
export function HelpDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} label="Help" onClose={onClose} className="dialog help">
      <h2>Help</h2>
      <h3>Keyboard shortcuts</h3>
      <dl className="shortcuts">
        <dt>
          <kbd>n</kbd>
        </dt>
        <dd>Next passage to check</dd>
        <dt>
          <kbd>p</kbd>
        </dt>
        <dd>Previous passage to check</dd>
        <dt>
          <kbd>Enter</kbd>
        </dt>
        <dd>Open or close the selected passage</dd>
        <dt>
          <kbd>Esc</kbd>
        </dt>
        <dd>Close the passage, a menu or this window</dd>
        <dt>
          <kbd>?</kbd>
        </dt>
        <dd>Open this help</dd>
      </dl>
      <h3>What the statuses mean</h3>
      <ul>
        <li>✓ ready: translated, nothing left to check.</li>
        <li>
          <span role="group" aria-label="Example">
            ⚠ 3 to check
          </span>
          : passages worth a look before you download. The number is only an example.
        </li>
        <li>… translating: still working. You can leave the page.</li>
        <li>! needs attention: a file couldn&apos;t finish.</li>
      </ul>
      <h3>Why a passage is marked</h3>
      <ul>
        {Object.values(FLAG_TITLES).map((title) => (
          <li key={title}>{title}</li>
        ))}
      </ul>
      <div className="row end">
        <button type="button" className="primary" onClick={onClose}>
          Close
        </button>
      </div>
    </Dialog>
  );
}
