import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useTabs } from "./useTabs";

function Tabs() {
  const tabs = useTabs(["one", "two", "three"] as const);
  return (
    <>
      <div role="tablist">
        {(["one", "two", "three"] as const).map((id) => (
          <button key={id} {...tabs.tabProps(id)}>
            {id}
          </button>
        ))}
      </div>
      <div {...tabs.panelProps(tabs.selected)}>{tabs.selected} panel</div>
    </>
  );
}

it("moves between tabs with the arrows (wrapping), Home and End, and ignores other keys", async () => {
  const user = userEvent.setup();
  render(<Tabs />);
  const tab = (name: string) => screen.getByRole("tab", { name });
  await user.click(tab("one"));
  await user.keyboard("{ArrowLeft}");
  expect(tab("three")).toHaveFocus();
  expect(tab("three")).toHaveAttribute("aria-selected", "true");
  await user.keyboard("{ArrowRight}");
  expect(tab("one")).toHaveFocus();
  await user.keyboard("{End}");
  expect(tab("three")).toHaveFocus();
  await user.keyboard("{Home}");
  expect(tab("one")).toHaveFocus();
  await user.keyboard("{ArrowDown}");
  expect(tab("one")).toHaveFocus();
  expect(screen.getByRole("tabpanel")).toHaveTextContent("one panel");
  expect(tab("two")).toHaveAttribute("tabindex", "-1");
});
