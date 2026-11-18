import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import middleware from "../middleware";
import { hashPassword, signSession } from "./auth";
import { ROBOTS_TAG, ROBOTS_TXT, SESSION_COOKIE } from "./route";

// CGL-85: the preview is private. Every response the front door makes, whichever way
// middleware.ts builds it, tells search engines not to index it or follow its links.
const SECRET = "s".repeat(32);
const SITE = "https://preview.example";
let users = "";

beforeAll(async () => {
  users = JSON.stringify({ ana: await hashPassword("pw", { iterations: 1000 }) });
});

beforeEach(() => {
  vi.stubEnv("COGLOSSA_USERS", users);
  vi.stubEnv("COGLOSSA_SESSION_SECRET", SECRET);
  vi.stubEnv("COGLOSSA_API_ORIGIN", "https://ws--coglossa-api.modal.run");
  vi.stubEnv("COGLOSSA_PROXY_SECRET", "p".repeat(32));
  vi.stubEnv("COGLOSSA_MODAL_KEY", "wk-test");
  vi.stubEnv("COGLOSSA_MODAL_SECRET", "ws-test");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

async function signedIn(path: string, init: RequestInit = {}): Promise<Request> {
  const cookie = `${SESSION_COOKIE}=${await signSession("ana", Date.now() + 60_000, SECRET)}`;
  const headers = new Headers(init.headers);
  headers.set("cookie", cookie);
  return new Request(`${SITE}${path}`, { ...init, headers });
}

describe("middleware keeps the preview out of search", () => {
  it("serves a robots.txt that disallows everything", async () => {
    const r = await middleware(new Request(`${SITE}/robots.txt`));
    expect(r.status).toBe(200);
    expect(r.headers.get("content-type")).toBe("text/plain; charset=utf-8");
    expect(r.headers.get("x-robots-tag")).toBe("noindex, nofollow");
    expect(await r.text()).toBe("User-agent: *\nDisallow: /\n");
    expect(ROBOTS_TXT).toMatch(/^User-agent: \*$/m);
    expect(ROBOTS_TXT).toMatch(/^Disallow: \/$/m);
  });

  it("serves robots.txt when the front door is misconfigured", async () => {
    vi.stubEnv("COGLOSSA_SESSION_SECRET", "");
    const r = await middleware(new Request(`${SITE}/robots.txt`));
    expect(r.status).toBe(200);
    expect(await r.text()).toBe(ROBOTS_TXT);
  });

  it.each([
    ["the sign-in page", () => new Request(`${SITE}/login`)],
    ["the sign-in file", () => new Request(`${SITE}/login.html`)],
    ["a static file", () => signedIn("/assets/index-1a2b.js")],
    ["the web app", () => signedIn("/projects/1")],
  ])("tags %s, which it lets through", async (_, request) => {
    const r = await middleware(await request());
    expect(r.headers.get("x-middleware-next")).toBe("1");
    expect(r.headers.get("x-robots-tag")).toBe(ROBOTS_TAG);
  });

  it("tags the responses it makes itself", async () => {
    const form = new URLSearchParams({ name: "ana", password: "pw" });
    const cases: [Request, number][] = [
      [new Request(`${SITE}/projects`), 303], // signed out: to /login
      [new Request(`${SITE}/login`, { method: "POST", body: form }), 303], // signed in
      [new Request(`${SITE}/logout`, { method: "POST" }), 303],
      [new Request(`${SITE}/api/me`), 401],
      [
        new Request(`${SITE}/api/projects`, {
          method: "POST",
          headers: { origin: "https://evil.example" },
        }),
        403,
      ],
    ];
    for (const [request, status] of cases) {
      const r = await middleware(request);
      expect(r.status).toBe(status);
      expect(r.headers.get("x-robots-tag")).toBe(ROBOTS_TAG);
    }
    vi.stubEnv("COGLOSSA_SESSION_SECRET", "");
    const r = await middleware(new Request(`${SITE}/projects`));
    expect(r.status).toBe(503);
    expect(r.headers.get("x-robots-tag")).toBe(ROBOTS_TAG);
  });

  it("tags forwarded API responses and leaves the API's own security headers alone", async () => {
    const r = await middleware(await signedIn("/api/me"));
    expect(r.headers.get("x-middleware-rewrite")).toBe("https://ws--coglossa-api.modal.run/api/me");
    expect(r.headers.get("x-robots-tag")).toBe(ROBOTS_TAG);
    expect(r.headers.has("content-security-policy")).toBe(false); // CGL-35: the API sends its own
  });

  it("is backed up by vercel.json for every path", () => {
    const config = JSON.parse(readFileSync(join(__dirname, "../vercel.json"), "utf8"));
    const rule = (config.headers ?? []).find(
      (h: { source: string }) => h.source === "/(.*)",
    ) as { headers: { key: string; value: string }[] } | undefined;
    const tag = rule?.headers.find((h) => h.key.toLowerCase() === "x-robots-tag")?.value;
    expect(tag).toBe(ROBOTS_TAG);
  });
});
