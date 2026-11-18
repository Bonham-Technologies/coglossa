import { act, renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { guideView } from "../test/fixtures";
import { queryWrapper } from "../test/render";
import { server } from "../test/server";
import { fetchProjectGuide, guideKeys, useSaveProjectGuide } from "./guide";

it("names the guide's cache keys under its project and language", () => {
  expect(guideKeys.all("p-1")).toEqual(["projects", "p-1", "guide"]);
  expect(guideKeys.project("p-1", "es")).toEqual(["projects", "p-1", "guide", "es"]);
  expect(guideKeys.versions("p-1", "es")).toEqual(["projects", "p-1", "guide", "es", "versions"]);
});

it("fetches the project guide, optionally at one version", async () => {
  const seen: (string | null)[] = [];
  const languages: (string | null)[] = [];
  server.use(
    http.get("*/api/projects/p-1/guide", ({ request }) => {
      const query = new URL(request.url).searchParams;
      seen.push(query.get("version"));
      languages.push(query.get("target_lang"));
      return HttpResponse.json(guideView);
    }),
  );
  await expect(fetchProjectGuide("p-1", "en")).resolves.toEqual(guideView);
  await fetchProjectGuide("p-1", "es", 2);
  expect(seen).toEqual([null, "2"]);
  expect(languages).toEqual(["en", "es"]);
});

it("asks for no language when the set has none yet, rather than an empty one", async () => {
  // CGL-30: an empty target_lang is refused as invalid (422); left out, the server says 404
  const sent: (string | null)[] = [];
  const record = ({ request }: { request: Request }) => {
    const query = new URL(request.url).searchParams;
    sent.push(query.has("target_lang") ? query.get("target_lang") : "(none)");
    return HttpResponse.json(guideView);
  };
  server.use(
    http.get("*/api/projects/p-1/guide", record),
    http.put("*/api/projects/p-1/guide", record),
  );
  await fetchProjectGuide("p-1", "");
  await fetchProjectGuide("p-1", "", 2);
  const { wrapper } = queryWrapper();
  const { result } = renderHook(() => useSaveProjectGuide("p-1", ""), { wrapper });
  await act(() => result.current.mutateAsync({ guide: guideView.guide, base_version: null }));
  expect(sent).toEqual(["(none)", "(none)", "(none)"]);
});
