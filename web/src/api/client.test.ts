import { http, HttpResponse } from "msw";
import { server } from "../test/server";
import {
  ApiError,
  client,
  errorCode,
  errorText,
  eventsUrl,
  exportUrl,
  isGuideChanged,
  NETWORK_ERROR,
  unwrap,
} from "./client";

const BUSY = "The server is starting up or busy. Try again in a minute.";
const SIGNED_OUT = "You're signed out. Reload the page to sign in again.";

describe("unwrap", () => {
  it("returns the data of a 2xx answer", async () => {
    server.use(http.get("*/api/me", () => HttpResponse.json({ user: "ana" })));
    expect(await unwrap(client.GET("/api/me"))).toEqual({ user: "ana" });
  });

  it("throws the server's one-line detail", async () => {
    server.use(
      http.get("*/api/me", () => HttpResponse.json({ detail: "Already exists." }, { status: 409 })),
    );
    const error = await unwrap(client.GET("/api/me")).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, message: "Already exists." });
  });

  it("says a missing sign-in in plain words", async () => {
    server.use(
      http.get("*/api/me", () =>
        HttpResponse.json({ detail: "missing X-Remote-User header" }, { status: 401 }),
      ),
    );
    const error = await unwrap(client.GET("/api/me")).catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 401, message: SIGNED_OUT });
  });

  it("keeps the server's conflict code", async () => {
    server.use(
      http.get("*/api/projects", () =>
        HttpResponse.json({ detail: "the guide changed", code: "guide_changed" }, { status: 409 }),
      ),
    );
    const error = await unwrap(client.GET("/api/projects")).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("guide_changed");
    expect(isGuideChanged(error)).toBe(true);
  });

  it("has no code when the server sends none, or not a string", () => {
    expect(errorCode({ detail: "x" })).toBeNull();
    expect(errorCode({ code: 7 })).toBeNull();
    expect(errorCode(null)).toBeNull();
    expect(isGuideChanged(new ApiError(409, "busy"))).toBe(false);
    expect(isGuideChanged(new Error("x"))).toBe(false);
  });

  it("throws a network error when the server cannot be reached", async () => {
    server.use(http.get("*/api/me", () => HttpResponse.error()));
    await expect(unwrap(client.GET("/api/me"))).rejects.toMatchObject({
      status: 0,
      message: NETWORK_ERROR,
    });
  });
});

describe("errorText", () => {
  it("reads FastAPI validation errors", () => {
    const body = { detail: [{ loc: ["body", "target_lang"], msg: "String should match pattern" }] };
    expect(errorText(422, body)).toBe("target_lang: String should match pattern");
    expect(errorText(422, { detail: [{ loc: ["body"], msg: "Field required" }] })).toBe(
      "Field required",
    );
    expect(errorText(422, { detail: [{ msg: "x" }] })).toBe("x");
  });

  it("says a 413 without a detail means the file is too large", () => {
    expect(errorText(413, undefined)).toBe("The file is too large to upload here.");
    expect(errorText(413, { detail: "File too big (limit 4 MB)." })).toBe(
      "File too big (limit 4 MB).",
    );
  });

  it("says a proxy's 502, 503 or 504 means the server is starting up or busy", () => {
    for (const status of [502, 503, 504]) {
      expect(errorText(status, undefined)).toBe(BUSY);
      expect(errorText(status, null)).toBe(BUSY);
      expect(errorText(status, "<html>Bad Gateway</html>")).toBe(BUSY);
      expect(errorText(status, { detail: "" })).toBe(BUSY);
    }
  });

  it("keeps the API's own detail on a 502, 503 or 504", () => {
    expect(errorText(503, { detail: "The scorer is still loading." })).toBe(
      "The scorer is still loading.",
    );
  });

  it("says a 401 in plain words, whatever its detail", () => {
    const detail = "missing X-Remote-User header: sign in through the proxy in front of Coglossa";
    expect(errorText(401, { detail })).toBe(SIGNED_OUT);
    expect(errorText(401, undefined)).toBe(SIGNED_OUT);
  });

  it("explains an answer without a detail", () => {
    expect(errorText(409, { detail: "" })).toBe("The server answered 409 without an explanation.");
    expect(errorText(404, { detail: [] })).toBe("The server answered 404 without an explanation.");
  });

  it("says what to do when the server fails (500) without saying why", () => {
    const plain =
      "Something went wrong on the server. Try again; if it keeps happening, tell your administrator.";
    expect(errorText(500, { detail: "internal error" })).toBe(plain);
    expect(errorText(500, undefined)).toBe(plain);
    expect(errorText(500, "<html>Internal Server Error</html>")).toBe(plain);
    expect(errorText(500, { detail: "" })).toBe(plain);
    expect(errorText(500, { detail: [] })).toBe(plain);
  });

  it("keeps a 500's own explanation (api/coglossa_api/errors.py)", () => {
    const setup =
      "The server isn't set up correctly. Ask an administrator to check the server log.";
    expect(errorText(500, { detail: setup })).toBe(setup);
  });

  it("keeps the detail of a 503 and of other statuses", () => {
    expect(errorText(503, { detail: "The scorer is not ready yet." })).toBe(
      "The scorer is not ready yet.",
    );
    expect(errorText(409, { detail: "Already exists." })).toBe("Already exists.");
  });
});

describe("urls", () => {
  it("builds the URLs a browser loads directly", () => {
    expect(eventsUrl("r 1")).toBe("/api/runs/r%201/events");
    expect(exportUrl("r1", "html")).toBe("/api/runs/r1/exports/html");
    expect(exportUrl("r1", "bilingual-docx", { comments: false })).toBe(
      "/api/runs/r1/exports/bilingual-docx?comments=false",
    );
    expect(exportUrl("r1", "bilingual-docx", { comments: true })).toBe(
      "/api/runs/r1/exports/bilingual-docx",
    );
    expect(exportUrl("r1", "tmx")).toBe("/api/runs/r1/exports/tmx");
    expect(exportUrl("r1", "tmx", { reviewedOnly: false })).toBe("/api/runs/r1/exports/tmx");
    expect(exportUrl("r1", "tmx", { reviewedOnly: true })).toBe(
      "/api/runs/r1/exports/tmx?reviewed_only=true",
    );
  });
});
