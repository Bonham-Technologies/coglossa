import type { Environment } from "vitest/runtime";
import { builtinEnvironments } from "vitest/runtime";
import { pointStorageAtJsdom } from "./jsdom-storage";

// Vitest's jsdom environment replaces some of Node's classes with jsdom's, but requests in tests
// go through Node's fetch (undici), which cannot use them:
// - AbortController: react-router builds a Node Request with its signal, which rejects jsdom's,
//   so navigations fail silently (AbortSignal stays Node's too).
// - Blob and File: since jsdom 30, Node's fetch fails reading a jsdom File in an upload's body
//   ("reading '_buffer'"), so uploads failed as unreachable. The app never reads a file itself
//   (no FileReader), so nothing needs jsdom's.
// FormData stays jsdom's: react-router's <Form> builds one from a form element, which Node's
// cannot read. Tests that read an upload's fields stub Node's FormData (test/node-web.ts).
const KEEP_NODE = ["AbortController", "Blob", "File"] as const;

const env: Environment = {
  ...builtinEnvironments.jsdom,
  name: "jsdom-node-fetch",
  async setup(global, options) {
    const node = Object.fromEntries(KEEP_NODE.map((name) => [name, global[name]]));
    const result = await builtinEnvironments.jsdom.setup(global, options);
    Object.assign(global, node);
    pointStorageAtJsdom(global);
    return result;
  },
};
export default env;
