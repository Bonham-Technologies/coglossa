import { useId, useRef, useState, type KeyboardEvent } from "react";

/** ARIA tabs: one selected tab in the tab order, arrows (wrapping), Home and End move to a tab
 * and select it. Spread tabProps(id) on each tab button and panelProps(id) on each panel.
 * `initial`: the tab selected first (default: the first). */
export function useTabs<T extends string>(ids: readonly T[], initial?: T) {
  const [selected, setSelected] = useState<T>(initial ?? ids[0]!);
  const base = useId();
  const refs = useRef<Partial<Record<T, HTMLButtonElement | null>>>({});

  function move(event: KeyboardEvent, from: number) {
    const last = ids.length - 1;
    const to =
      event.key === "ArrowRight"
        ? (from + 1) % ids.length
        : event.key === "ArrowLeft"
          ? (from + last) % ids.length
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? last
              : null;
    if (to === null) return;
    event.preventDefault();
    const next = ids[to]!;
    setSelected(next);
    refs.current[next]?.focus();
  }

  return {
    selected,
    setSelected,
    tabProps: (id: T) => ({
      ref: (el: HTMLButtonElement | null) => {
        refs.current[id] = el;
      },
      role: "tab" as const,
      type: "button" as const,
      id: `${base}-tab-${id}`,
      "aria-selected": selected === id,
      "aria-controls": `${base}-panel-${id}`,
      tabIndex: selected === id ? 0 : -1,
      onClick: () => setSelected(id),
      onKeyDown: (event: KeyboardEvent) => move(event, ids.indexOf(id)),
    }),
    panelProps: (id: T) => ({
      role: "tabpanel" as const,
      id: `${base}-panel-${id}`,
      "aria-labelledby": `${base}-tab-${id}`,
      hidden: selected !== id,
    }),
  };
}
