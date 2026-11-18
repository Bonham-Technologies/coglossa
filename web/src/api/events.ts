import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { isTerminal } from "../lib/format";
import { eventsUrl } from "./client";
import { runKeys } from "./runs";
import type { RunDetail, RunProgress, RunState } from "./types";

export type EventSourceLike = {
  readonly readyState: number;
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void;
  close(): void;
};
export type EventSourceFactory = (url: string) => EventSourceLike;

const EVENT_SOURCE_CLOSED = 2;

let override: EventSourceFactory | null | undefined;

/** Tests replace the browser's EventSource; `undefined` restores it, `null` turns it off. */
export function setEventSourceFactory(factory?: EventSourceFactory | null): void {
  override = factory;
}

function factory(): EventSourceFactory | null {
  if (override !== undefined) return override;
  if (typeof EventSource === "undefined") return null;
  return (url) => new EventSource(url);
}

export function liveUpdatesAvailable(): boolean {
  return factory() !== null;
}

/** Spec §7.1: follow a run's progress over server-sent events while it runs. The stream ends
 * after a terminal state; the client closes it then, or a browser would reconnect forever. */
export function useRunEvents(runId: string, active: boolean, onStreamFailed?: () => void): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    const open = factory();
    if (!active || open === null) return;
    const source = open(eventsUrl(runId));
    let last: RunState | null =
      queryClient.getQueryData<RunDetail>(runKeys.one(runId))?.run.state ?? null;
    const refetch = () => queryClient.invalidateQueries({ queryKey: runKeys.one(runId) });
    source.addEventListener("progress", (event) => {
      const progress = JSON.parse(event.data) as RunProgress;
      queryClient.setQueryData<RunDetail>(runKeys.one(runId), (old) =>
        old === undefined
          ? old
          : {
              ...old,
              progress,
              run: { ...old.run, state: progress.state, error: progress.error ?? null },
            },
      );
      if (last !== null && last !== progress.state) void refetch();
      last = progress.state;
      if (isTerminal(progress.state)) source.close();
    });
    source.addEventListener("deleted", () => {
      source.close();
      void refetch();
    });
    // The browser reconnects by itself unless it gave up (readyState CLOSED, e.g. a 502):
    // then the page falls back to polling.
    source.addEventListener("error", () => {
      if (source.readyState !== EVENT_SOURCE_CLOSED) return;
      source.close();
      onStreamFailed?.();
    });
    return () => source.close();
  }, [runId, active, queryClient, onStreamFailed]);
}
