import { useEffect, useEffectEvent } from "react";
import { keyBelongsToControl } from "../lib/keys";

type Actions = { next: () => void; previous: () => void; toggle: () => void; close: () => void };

/** Spec §5.3: n / p walk the passages to check, Enter opens or closes, Esc closes. Keys typed
 * into a field, or Enter on a button, belong to that control. */
export function useDocumentKeys(actions: Actions): void {
  // An effect event sees the latest actions without subscribing again on every render.
  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (keyBelongsToControl(event.target, event.key)) return;
    // a dialog that is open (the guide, Add files…) owns the keyboard
    if (event.target instanceof Element && event.target.closest("dialog[open]")) return;
    if (event.key === "n" || event.key === "p") {
      event.preventDefault();
      if (event.key === "n") actions.next();
      else actions.previous();
    } else if (event.key === "Enter") {
      actions.toggle();
    } else if (event.key === "Escape") {
      actions.close();
    }
  });
  useEffect(() => {
    const listener = (event: KeyboardEvent) => onKey(event);
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);
}
