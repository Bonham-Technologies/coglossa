import { render, screen } from "@testing-library/react";
import { LANGUAGE_HINT, LanguageInput } from "./LanguageInput";

const setup = (value: string) =>
  render(<LanguageInput label="Language" value={value} onChange={() => {}} />);

it("links the hint to the box when the code is refused", () => {
  setup("not a code!");
  const box = screen.getByRole("textbox", { name: "Language" });
  expect(box).toHaveAttribute("aria-invalid", "true");
  const hint = document.getElementById(box.getAttribute("aria-describedby") ?? "");
  expect(hint).toHaveTextContent(LANGUAGE_HINT);
});

it.each(["", "pt-BR"])("has no invalid state or description for %j", (value) => {
  setup(value);
  const box = screen.getByRole("textbox", { name: "Language" });
  expect(box).not.toHaveAttribute("aria-invalid");
  expect(box).not.toHaveAttribute("aria-describedby");
});
