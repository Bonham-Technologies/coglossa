import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./server";

// A loaded full run (coverage on, every worker busy) can take longer than a second to render a
// long document after a click; a slow machine is not a failing test (vitest.config testTimeout).
configure({ asyncUtilTimeout: 3000 });

// jsdom has no modal dialogs and no scrolling; the app uses both (spec §7.4).
if (typeof HTMLDialogElement !== "undefined") {
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.open = false;
  };
  Element.prototype.scrollIntoView ??= function () {};
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
afterAll(() => server.close());
