import { next, rewrite } from "@vercel/functions";
// Node ESM needs the extension at run time
import { decide, readEnv, ROBOTS_TAG, ROBOTS_TXT, securityHeaders } from "./lib/route.js";

export const config = { runtime: "nodejs" };

export default async function middleware(request: Request): Promise<Response> {
  const d = await decide(request, readEnv(), Date.now());
  // CGL-35: on everything but forwarded /api calls, which carry the API's own. They include
  // X-Robots-Tag (CGL-85), which forwarded calls get on their own below.
  const secured = securityHeaders(request.url);
  switch (d.kind) {
    case "next":
      return next({ headers: secured });
    case "robots":
      return new Response(ROBOTS_TXT, {
        headers: { ...secured, "content-type": "text/plain; charset=utf-8" },
      });
    case "redirect": {
      const headers = new Headers({ ...secured, location: d.location });
      if (d.setCookie) headers.set("set-cookie", d.setCookie);
      return new Response(null, { status: 303, headers });
    }
    case "unauthorized":
      return Response.json(
        { detail: "signed out: sign in again" },
        { status: 401, headers: secured },
      );
    case "misconfigured":
      return new Response("Coglossa preview is not configured.", { status: 503, headers: secured });
    case "forbidden":
      return new Response("Cross-site request refused.", { status: 403, headers: secured });
    case "forward":
      return rewrite(d.url, {
        headers: { "X-Robots-Tag": ROBOTS_TAG },
        request: { headers: d.headers },
      });
  }
}
