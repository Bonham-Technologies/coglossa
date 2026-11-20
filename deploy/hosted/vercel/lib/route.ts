/** Routing for the front door (spec §3, §5). Pure: middleware.ts turns a Decision into a
 * response. */
import {
  type Account,
  ITERATIONS,
  parseUsers,
  readCookie,
  readSession,
  signSession,
  verifyPassword,
} from "./auth.js";

export const SESSION_COOKIE = "cg_session";
const WEEK_S = 7 * 24 * 3600;
const COOKIE_ATTRS = "Path=/; HttpOnly; Secure; SameSite=Lax";
const OPEN_PATHS = new Set(["/login", "/login.html"]);
// Identity and Modal proxy-auth headers are replaced; the session cookie must not reach the
// API; Host must stay the API origin's (Modal routes by Host).
const STRIPPED_HEADERS = [
  "x-remote-user",
  "x-remote-groups",
  "x-coglossa-proxy-secret",
  "modal-key",
  "modal-secret",
  "cookie",
  "host",
];
const SAFE_METHODS = new Set(["GET", "HEAD"]);
// CGL-85: the preview is private and stops at a sign-in page, so search engines are told to
// keep out: a robots.txt that disallows everything, which the front door answers itself
// (no sign-in), and X-Robots-Tag on every response.
export const ROBOTS_TXT = "User-agent: *\nDisallow: /\n";
export const ROBOTS_TAG = "noindex, nofollow";
// Verified for names that do not exist, so known and unknown names take the same time.
// Made with hashPassword at the production iteration count; it matches no real password.
const DUMMY_HASH = "pbkdf2_sha256$600000$ISUExc42f4gJFEKFX+3rlA==$3KfiTf3JiTZWRBa6KSkcWL2Ylojqoo6tkRYDZzbtINo=";

// CGL-35: the API's security headers (api/coglossa_api/headers.py), on what the front door
// answers itself; forwarded /api responses carry the API's own. The tests check both policies.
const APP_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "font-src 'self' data:",
  "img-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");
// The sign-in page is one self-contained file: its inline script and style are allowed by
// hash. Editing static/login.html changes the hashes; route.test.ts says which to use.
const LOGIN_CSP = [
  "default-src 'none'",
  "script-src 'sha256-eN1g2wXEd/fWymF2rKtfxCr179jcfYuFcIEtTijkOIs=' 'sha256-BXMgL0ttVIm4OEvjAYjk69pvcK7bNXUUxpcuGrOhmnQ='",
  "style-src 'sha256-x0p+06g8+5fnxY5jhTP23cc3McDhnfY3YX3eYt0Ojo8='",
  "font-src 'self' data:",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

// CGL-70: the API's PERMISSIONS_POLICY, features the app never uses; the tests check it matches.
const PERMISSIONS_POLICY = [
  "accelerometer=()",
  "browsing-topics=()",
  "camera=()",
  "display-capture=()",
  "geolocation=()",
  "gyroscope=()",
  "hid=()",
  "magnetometer=()",
  "microphone=()",
  "midi=()",
  "payment=()",
  "serial=()",
  "usb=()",
  "xr-spatial-tracking=()",
].join(", ");

export function securityHeaders(path: string): Record<string, string> {
  const pathname = new URL(path, "https://front.door").pathname;
  return {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": PERMISSIONS_POLICY,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Robots-Tag": ROBOTS_TAG,
    "Content-Security-Policy": OPEN_PATHS.has(pathname) ? LOGIN_CSP : APP_CSP,
  };
}

export interface Env {
  users: Map<string, Account>;
  sessionSecret: string;
  apiOrigin: string;
  proxySecret: string;
  modalKey: string; // Modal proxy auth: the API endpoint refuses requests without them
  modalSecret: string;
  failDelayMs: number;
}

export type Decision =
  | { kind: "next" }
  | { kind: "robots" }
  | { kind: "redirect"; location: string; setCookie: string | null }
  | { kind: "unauthorized" }
  | { kind: "misconfigured" }
  | { kind: "forbidden" }
  | { kind: "forward"; url: string; headers: Headers };

export function readEnv(env: Record<string, string | undefined> = process.env): Env {
  return {
    users: parseUsers(env.COGLOSSA_USERS),
    sessionSecret: env.COGLOSSA_SESSION_SECRET ?? "",
    apiOrigin: (env.COGLOSSA_API_ORIGIN ?? "").replace(/\/+$/, ""),
    proxySecret: env.COGLOSSA_PROXY_SECRET ?? "",
    modalKey: env.COGLOSSA_MODAL_KEY ?? "",
    modalSecret: env.COGLOSSA_MODAL_SECRET ?? "",
    failDelayMs: 1000,
  };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function signIn(request: Request, env: Env, now: number): Promise<Decision> {
  const form = await request.formData().catch(() => new FormData());
  const name = String(form.get("name") ?? "").trim();
  const encoded = env.users.get(name)?.hash;
  const ok = await verifyPassword(String(form.get("password") ?? ""), encoded ?? DUMMY_HASH);
  if (!encoded || !ok) {
    await sleep(env.failDelayMs); // slows password guessing; no lockout at demo scale
    return { kind: "redirect", location: "/login?failed=1", setCookie: null };
  }
  const value = await signSession(name, now + WEEK_S * 1000, env.sessionSecret);
  return {
    kind: "redirect",
    location: "/",
    setCookie: `${SESSION_COOKIE}=${value}; ${COOKIE_ATTRS}; Max-Age=${WEEK_S}`,
  };
}

export async function decide(request: Request, env: Env, now: number): Promise<Decision> {
  const url = new URL(request.url);
  const isRobots = url.pathname === "/robots.txt" && SAFE_METHODS.has(request.method);
  if (isRobots) return { kind: "robots" }; // before the config check: always keep crawlers out
  if (OPEN_PATHS.has(url.pathname) && request.method !== "POST") return { kind: "next" };
  if (
    env.sessionSecret.length < 32 ||
    !env.apiOrigin.startsWith("https://") || // the proxy secret must never travel in cleartext
    !env.proxySecret ||
    !env.modalKey ||
    !env.modalSecret ||
    env.users.size === 0
  ) {
    return { kind: "misconfigured" };
  }
  const origin = request.headers.get("origin");
  const isApi = url.pathname === "/api" || url.pathname.startsWith("/api/");
  const isPost = request.method === "POST";
  const changes =
    (isPost && (url.pathname === "/login" || url.pathname === "/logout")) ||
    (isApi && !SAFE_METHODS.has(request.method));
  if (changes && origin !== null && origin !== url.origin) return { kind: "forbidden" }; // CSRF
  if (url.pathname === "/login") return signIn(request, env, now);
  if (url.pathname === "/logout" && request.method === "POST") {
    return {
      kind: "redirect",
      location: "/login",
      setCookie: `${SESSION_COOKIE}=; ${COOKIE_ATTRS}; Max-Age=0`,
    };
  }
  const cookie = readCookie(request.headers.get("cookie"), SESSION_COOKIE);
  const name = cookie ? await readSession(cookie, env.sessionSecret, now) : null;
  const user = name !== null && env.users.has(name) ? name : null; // removed users are out
  if (user === null) {
    return isApi
      ? { kind: "unauthorized" }
      : { kind: "redirect", location: "/login", setCookie: null };
  }
  if (!isApi) return { kind: "next" };
  return forward(request, url, { name: user, groups: env.users.get(user)!.groups }, env);
}

/** The /api rewrite. Spike S1 (plan Task 8) decides whether this stays a header-adding
 * rewrite; its fallback is a streaming proxy function, swapped in here. */
export function forward(
  request: Request,
  url: URL,
  user: { name: string; groups: string[] },
  env: Env,
): Decision {
  const headers = new Headers(request.headers);
  for (const h of STRIPPED_HEADERS) headers.delete(h);
  const names: string[] = [];
  headers.forEach((_, name) => names.push(name));
  for (const name of names) if (name.startsWith("x-middleware-")) headers.delete(name);
  headers.set("x-remote-user", user.name);
  headers.set("x-remote-groups", user.groups.join(",")); // always set: never the client's
  headers.set("x-coglossa-proxy-secret", env.proxySecret);
  headers.set("modal-key", env.modalKey);
  headers.set("modal-secret", env.modalSecret);
  return { kind: "forward", url: `${env.apiOrigin}${url.pathname}${url.search}`, headers };
}
