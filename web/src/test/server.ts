import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { me } from "./fixtures";

// Every page renders the frame: it asks who is signed in and lists the sets.
export const server = setupServer(
  http.get("*/api/me", () => HttpResponse.json(me)),
  http.get("*/api/projects", () => HttpResponse.json([])),
);
