import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { queryWrapper } from "../test/render";
import { AlwaysTranslate } from "./AlwaysTranslate";
import { RunLanguages } from "./languages";

function Harness({ prefill }: { prefill: string }) {
  return (
    <RunLanguages.Provider value={{ source: "fr", target: "en" }}>
      <AlwaysTranslate runId="r-1" projectId="p-1" spanId="s-2" prefill={prefill} />
    </RunLanguages.Provider>
  );
}

it("fills in text newly selected in the Original column, and keeps typing otherwise", async () => {
  const user = userEvent.setup();
  const { wrapper } = queryWrapper();
  const { rerender } = render(<Harness prefill="Marie" />, { wrapper });
  const field = screen.getByRole("textbox", { name: "Always translate" });
  expect(field).toHaveValue("Marie");
  await user.clear(field);
  await user.type(field, "Marie Curie");
  // The same selection again changes nothing; what was typed stays.
  rerender(<Harness prefill="Marie" />);
  expect(field).toHaveValue("Marie Curie");
  // A new selection replaces it.
  rerender(<Harness prefill="Lyon" />);
  expect(field).toHaveValue("Lyon");
  // Clearing the selection leaves the field as it is.
  await user.type(field, " Part-Dieu");
  rerender(<Harness prefill="" />);
  expect(field).toHaveValue("Lyon Part-Dieu");
});
