import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { rememberLanguage, resetRecentForTests } from "../lib/languages";
import { tiers } from "../test/fixtures";
import { LanguagePicker } from "./LanguagePicker";

function Harness({ initial = "", detect = false }: { initial?: string; detect?: boolean }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <LanguagePicker
        label="To"
        value={value}
        onChange={setValue}
        tiers={tiers}
        allowDetect={detect}
      />
      <output>{value}</output>
      <button type="button" onClick={() => setValue("")}>
        Clear
      </button>
    </>
  );
}

beforeEach(() => {
  localStorage.clear();
  resetRecentForTests();
});

it("offers recent languages, then the install's by name, then Other", async () => {
  rememberLanguage("es");
  const user = userEvent.setup();
  render(<Harness />);
  const select = screen.getByLabelText("To");
  const [recent, all] = within(select).getAllByRole("group");
  expect(recent).toHaveAttribute("label", "Recent");
  expect(
    within(recent!)
      .getAllByRole("option")
      .map((o) => o.textContent),
  ).toEqual(["Spanish"]);
  expect(
    within(all!)
      .getAllByRole("option")
      .map((o) => o.textContent),
  ).toEqual(["Amharic", "Arabic", "English", "French"]);
  expect(within(select).getByRole("option", { name: "Choose a language" })).toBeDisabled();
  await user.selectOptions(select, "fr");
  expect(screen.getByRole("status")).toHaveTextContent("fr");
});

it("takes any code under Other language", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.selectOptions(screen.getByLabelText("To"), "Other language…");
  const code = screen.getByLabelText("To: language code");
  await user.type(code, "to");
  expect(screen.getByRole("status")).toHaveTextContent("to");
  await user.clear(code);
  await user.type(code, "english!");
  expect(screen.getByText(/Use a language code/)).toBeInTheDocument();
});

it("shows a code it does not list under Other", () => {
  render(<Harness initial="haw" />);
  expect(screen.getByLabelText("To")).toHaveValue("__other__");
  expect(screen.getByLabelText("To: language code")).toHaveValue("haw");
});

it("can offer automatic detection", () => {
  render(<Harness detect />);
  expect(screen.getByLabelText("To")).toHaveDisplayValue("Detect automatically");
});

it("drops the Other box when the parent clears the value", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.selectOptions(screen.getByLabelText("To"), "Other language…");
  await user.type(screen.getByLabelText("To: language code"), "to");
  await user.clear(screen.getByLabelText("To: language code"));
  expect(screen.getByLabelText("To: language code")).toBeInTheDocument(); // the user's own clear
  await user.type(screen.getByLabelText("To: language code"), "to");
  await user.click(screen.getByRole("button", { name: "Clear" }));
  expect(screen.queryByLabelText("To: language code")).not.toBeInTheDocument();
  expect(screen.getByLabelText("To")).toHaveDisplayValue("Choose a language");
});

it("shows languages the set already has, disabled, recent ones too", () => {
  rememberLanguage("ar");
  render(
    <LanguagePicker label="To" value="" onChange={() => {}} tiers={tiers} taken={["FR", "ar"]} />,
  );
  expect(screen.getByRole("option", { name: "French — already in this set" })).toBeDisabled();
  expect(screen.getByRole("option", { name: "Arabic — already in this set" })).toBeDisabled();
  expect(screen.getByRole("option", { name: "English" })).toBeEnabled();
});
