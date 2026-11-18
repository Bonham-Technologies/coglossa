import { readFileSync } from "node:fs";
import type { Candidate, SpanDetail } from "../api/types";
import { spanDetail } from "../test/fixtures";
import { byTranslator } from "./byTranslator";

type Case = {
  name: string;
  candidates: Pick<Candidate, "translator" | "start" | "end" | "text">[];
  choices: { translator: string; text: string; usable: boolean }[];
};

// CGL-44: the same cases the engine checks against its use-candidate action
// (engine/tests/test_review_actions.py), so the panel offers exactly what the API accepts.
const shared = JSON.parse(
  readFileSync("../engine/tests/fixtures/translator_choices.json", "utf8"),
) as { cases: Case[] };

function passage(candidates: Case["candidates"], start = "s2", end = "s3"): SpanDetail {
  return {
    ...spanDetail,
    start,
    end,
    candidates: candidates.map((c) => ({ ...c, model: "m", chunk_id: "c1", notes: "" })),
  };
}

it.each(shared.cases.map((c) => [c.name, c] as const))("joins as the server does: %s", (_, c) => {
  const choices = byTranslator(passage(c.candidates)).map(({ translator, text, usable }) => ({
    translator,
    text,
    usable,
  }));
  expect(choices).toEqual(c.choices);
});

it("orders pieces by segment number, not by spelling", () => {
  const [choice] = byTranslator(
    passage(
      [
        { translator: "t", start: "s10", end: "s10", text: "Second." },
        { translator: "t", start: "s9", end: "s9", text: "First." },
      ],
      "s9",
      "s10",
    ),
  );
  expect(choice).toMatchObject({ text: "First. Second.", usable: true, model: "m" });
});

it("drops lone surrogates but keeps characters outside the basic plane", () => {
  const [choice] = byTranslator(
    passage([
      { translator: "t", start: "s2", end: "s3", text: "Cat \ud83d\ude3a\ud800 \udc00nap" },
    ]),
  );
  expect(choice?.text).toBe("Cat \ud83d\ude3a nap");
});

it("offers nothing to use when a piece has an unknown position", () => {
  const [choice] = byTranslator(
    passage([
      { translator: "t", start: "s2", end: "x2", text: "The cat sleeps." },
      { translator: "t", start: "s3", end: "s3", text: "It rains." },
    ]),
  );
  expect(choice?.usable).toBe(false);
});
