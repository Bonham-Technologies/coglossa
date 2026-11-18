import { act, renderHook } from "@testing-library/react";
import { liveUpdatesAvailable, setEventSourceFactory, useRunEvents } from "./events";
import { FakeEventSource } from "../test/fake-events";
import { ApiError } from "./client";
import { pollInterval, runKeys } from "./runs";
import type { RunDetail } from "./types";
import { progress, runDetail } from "../test/fixtures";
import { queryWrapper } from "../test/render";

afterEach(() => setEventSourceFactory(undefined));

it("uses the browser's EventSource when there is one", () => {
  expect(liveUpdatesAvailable()).toBe(false); // jsdom has none
  vi.stubGlobal("EventSource", FakeEventSource);
  expect(liveUpdatesAvailable()).toBe(true);
  setEventSourceFactory(null);
  expect(liveUpdatesAvailable()).toBe(false);
});

it("polls only without live updates, and only until the run ends", () => {
  expect(pollInterval(runDetail, false)).toBe(3000);
  expect(pollInterval(runDetail, true)).toBe(false);
  expect(pollInterval(undefined, false)).toBe(false);
  const done = { ...runDetail, run: { ...runDetail.run, state: "complete" as const } };
  expect(pollInterval(done, false)).toBe(false);
  expect(pollInterval(runDetail, false, new ApiError(404, "gone"))).toBe(false);
  expect(pollInterval(runDetail, false, new ApiError(500, "boom"))).toBe(3000);
});

describe("useRunEvents", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    setEventSourceFactory((url) => new FakeEventSource(url));
  });

  function follow(
    active = true,
    onStreamFailed?: () => void,
    cached: RunDetail | null = runDetail,
  ) {
    const { queryClient, wrapper } = queryWrapper();
    if (cached) queryClient.setQueryData(runKeys.one("r-1"), cached);
    const refetch = vi.spyOn(queryClient, "invalidateQueries");
    const view = renderHook(() => useRunEvents("r-1", active, onStreamFailed), { wrapper });
    return { queryClient, refetch, view };
  }

  it("opens no stream for a run that is not active", () => {
    follow(false);
    expect(FakeEventSource.instances).toEqual([]);
  });

  it("opens no stream without live updates", () => {
    setEventSourceFactory(null);
    follow();
    expect(FakeEventSource.instances).toEqual([]);
  });

  it("writes progress into the cached run and refetches when the state changes", () => {
    const { queryClient, refetch } = follow();
    const source = FakeEventSource.last();
    expect(source.url).toBe("/api/runs/r-1/events");
    act(() => source.emit("progress", { ...runDetail.progress, chunks_total: 9 }));
    const cached = queryClient.getQueryData<RunDetail>(runKeys.one("r-1"));
    expect(cached?.progress.chunks_total).toBe(9);
    expect(refetch).not.toHaveBeenCalled(); // same state as the cached run
    act(() => source.emit("progress", { ...runDetail.progress, state: "sweeping", error: "slow" }));
    expect(queryClient.getQueryData<RunDetail>(runKeys.one("r-1"))?.run).toMatchObject({
      state: "sweeping",
      error: "slow",
    });
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(source.closed).toBe(false);
  });

  it("closes the stream when the run ends, and clears a missing error", () => {
    const { queryClient } = follow();
    const source = FakeEventSource.last();
    act(() => source.emit("progress", { ...progress, error: undefined }));
    expect(queryClient.getQueryData<RunDetail>(runKeys.one("r-1"))?.run.error).toBeNull();
    expect(source.closed).toBe(true);
  });

  it("does not refetch on the first event when nothing was cached", () => {
    const { queryClient, refetch } = follow(true, undefined, null);
    act(() => FakeEventSource.last().emit("progress", runDetail.progress));
    expect(queryClient.getQueryData(runKeys.one("r-1"))).toBeUndefined();
    expect(refetch).not.toHaveBeenCalled();
  });

  it("closes and refetches when the run is deleted", () => {
    const { refetch } = follow();
    const source = FakeEventSource.last();
    act(() => source.emit("deleted", {}));
    expect(source.closed).toBe(true);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("falls back to polling only when the browser gave up on the stream", () => {
    const failed = vi.fn();
    follow(true, failed);
    const source = FakeEventSource.last();
    act(() => source.emit("error", {})); // still open: the browser reconnects by itself
    expect(source.closed).toBe(false);
    expect(failed).not.toHaveBeenCalled();
    source.readyState = 2; // CLOSED
    act(() => source.emit("error", {}));
    expect(source.closed).toBe(true);
    expect(failed).toHaveBeenCalledTimes(1);
  });

  it("gives up quietly without a fallback, and closes on unmount", () => {
    const { view } = follow(true);
    const source = FakeEventSource.last();
    source.readyState = 2;
    act(() => source.emit("error", {}));
    expect(source.closed).toBe(true);
    const next = follow(true).view;
    next.unmount();
    expect(FakeEventSource.last().closed).toBe(true);
    view.unmount();
  });
});
