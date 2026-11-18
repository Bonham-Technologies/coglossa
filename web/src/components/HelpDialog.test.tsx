import { render, screen, within } from "@testing-library/react";
import { HelpDialog } from "./HelpDialog";

it("shows the 'to check' status as a labelled example, not as live data", () => {
  render(<HelpDialog open onClose={() => {}} />);
  const help = screen.getByRole("dialog", { name: "Help" });
  const example = within(help).getByRole("group", { name: "Example" });
  expect(example).toHaveTextContent("⚠ 3 to check");
  expect(help).not.toHaveTextContent("2 to check");
});
