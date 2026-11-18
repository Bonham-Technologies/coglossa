import { render, screen } from "@testing-library/react";
import { Revisions } from "./Revisions";
import { RunLanguages } from "./languages";

it("lists what changed, with the reason and what it replaced", () => {
  render(
    <RunLanguages.Provider value={{ source: "fr", target: "en" }}>
      <Revisions
        revisions={[
          {
            source: "user",
            created_at: "2026-10-01T09:00:00Z",
            reason: "clearer",
            before: "It sleeps.",
            after: "It is asleep.",
          },
          {
            source: "engine",
            created_at: "2026-10-01T08:00:00Z",
            reason: "",
            before: null,
            after: "It sleeps.",
          },
        ]}
      />
    </RunLanguages.Provider>,
  );
  const history = screen.getByRole("list", { name: "History" });
  expect(history).toHaveTextContent("Changed by a reviewer");
  expect(history).toHaveTextContent("Translated");
  expect(history).not.toHaveTextContent("engine");
  expect(history).toHaveTextContent("clearer");
  expect(history.querySelectorAll("del")).toHaveLength(1);
  expect(history.querySelectorAll("ins")).toHaveLength(2);
});
