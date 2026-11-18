import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { Entity, GlossaryEntry } from "../api/types";
import { emptyEntity, emptyTerm } from "./entries";
import { NameRows, TermRows } from "./GuideRows";

function Names({ initial }: { initial: Entity[] }) {
  const [names, setNames] = useState(initial);
  return <NameRows names={names} onChange={setNames} />;
}

function Terms({ initial }: { initial: GlossaryEntry[] }) {
  const [terms, setTerms] = useState(initial);
  return <TermRows terms={terms} onChange={setTerms} />;
}

it("adds, edits, finishes and removes a name", async () => {
  const user = userEvent.setup();
  render(<Names initial={[]} />);
  expect(screen.getByText("No names yet.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "+ Add a name" }));
  await user.type(screen.getByLabelText("Name"), "Ana");
  await user.type(screen.getByLabelText("Translation"), "Anna");
  await user.click(screen.getByRole("checkbox", { name: "Always use this translation" }));
  await user.selectOptions(screen.getByLabelText("Kind"), "place");
  await user.type(screen.getByLabelText("Gender"), "f");
  await user.type(screen.getByLabelText("Forms of address"), "Dona");
  await user.type(screen.getByLabelText("Speech style"), "formal");
  await user.click(screen.getByRole("button", { name: "Done" }));
  const row = screen.getByRole("button", { name: /^Edit Ana\b/ });
  expect(row).toHaveTextContent("Ana → Anna · always");
  // Label in Name (WCAG 2.5.3): the accessible name contains the visible text.
  expect(row).toHaveAccessibleName("Edit Ana → Anna · always");
  await user.click(row);
  expect(screen.getByLabelText("Gender")).toHaveValue("f");
  await user.clear(screen.getByLabelText("Gender"));
  expect(screen.getByLabelText("Gender")).toHaveValue("");
  await user.click(screen.getByRole("button", { name: "Remove" }));
  expect(screen.getByText("No names yet.")).toBeInTheDocument();
});

it("keeps an unusual kind selectable and names an empty name by its number", async () => {
  render(<Names initial={[{ ...emptyEntity(), kind: "deity" }]} />);
  await userEvent.click(screen.getByRole("button", { name: /^Edit name 1\b/ }));
  expect(screen.getByRole("option", { name: "deity" })).toBeInTheDocument();
});

it("adds, edits, finishes and removes a term", async () => {
  const user = userEvent.setup();
  render(<Terms initial={[]} />);
  expect(screen.getByText("No terms yet.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "+ Add a term" }));
  await user.type(screen.getByLabelText("Term"), "grace");
  await user.type(screen.getByLabelText("Translation"), "gracia");
  await user.click(screen.getByRole("checkbox", { name: "Always use this translation" }));
  await user.type(screen.getByLabelText("Notes"), "doctrinal");
  await user.click(screen.getByRole("button", { name: "Done" }));
  const row = screen.getByRole("button", { name: /^Edit grace\b/ });
  expect(row).toHaveTextContent("grace → gracia · always");
  expect(row).toHaveAccessibleName("Edit grace → gracia · always");
  await user.click(row);
  await user.click(screen.getByRole("button", { name: "Remove" }));
  expect(screen.getByText("No terms yet.")).toBeInTheDocument();
});

it("names an empty term by its number", () => {
  render(<Terms initial={[emptyTerm()]} />);
  expect(screen.getByRole("button", { name: /^Edit term 1\b/ })).toHaveAccessibleName(
    "Edit term 1: … → …",
  );
});

it("removes a name from its collapsed row, keeping an open row open", async () => {
  const user = userEvent.setup();
  const named = (name: string): Entity => ({ ...emptyEntity(), name, rendering: `${name}!` });
  render(<Names initial={[named("Ana"), named("Ben"), named("Cy")]} />);
  await user.click(screen.getByRole("button", { name: /^Edit Cy\b/ }));
  await user.click(screen.getByRole("button", { name: "Remove Ana" }));
  expect(screen.queryByRole("button", { name: /^Edit Ana\b/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Edit Ben\b/ })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Name 2" })).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toHaveValue("Cy");
});

it("removes a term from its collapsed row", async () => {
  const user = userEvent.setup();
  const term = (t: string): GlossaryEntry => ({ ...emptyTerm(), term: t, rendering: `${t}!` });
  render(<Terms initial={[term("cat"), term("dog")]} />);
  await user.click(screen.getByRole("button", { name: "Remove cat" }));
  expect(screen.queryByRole("button", { name: /^Edit cat\b/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Edit dog\b/ })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Remove dog" }));
  expect(screen.getByText("No terms yet.")).toBeInTheDocument();
});

describe("focus after Remove", () => {
  const named = (name: string): Entity => ({ ...emptyEntity(), name, rendering: `${name}!` });
  const term = (t: string): GlossaryEntry => ({ ...emptyTerm(), term: t, rendering: `${t}!` });
  const kinds = [
    {
      kind: "names",
      add: "+ Add a name",
      ui: () => <Names initial={["Ana", "Ben", "Cy"].map(named)} />,
    },
    {
      kind: "terms",
      add: "+ Add a term",
      ui: () => <Terms initial={["Ana", "Ben", "Cy"].map(term)} />,
    },
  ];
  it.each(kinds)("$kind: lands on the next row's Edit button from a middle row", async ({ ui }) => {
    const user = userEvent.setup();
    render(ui());
    await user.click(screen.getByRole("button", { name: "Remove Ben" }));
    expect(screen.getByRole("button", { name: /^Edit Cy\b/ })).toHaveFocus();
  });
  it.each(kinds)("$kind: lands on the previous row when the last one goes", async ({ ui }) => {
    const user = userEvent.setup();
    render(ui());
    await user.click(screen.getByRole("button", { name: "Remove Cy" }));
    expect(screen.getByRole("button", { name: /^Edit Ben\b/ })).toHaveFocus();
  });
  it.each(kinds)("$kind: lands on the next row after removing an open row", async ({ ui }) => {
    const user = userEvent.setup();
    render(ui());
    await user.click(screen.getByRole("button", { name: /^Edit Ben\b/ }));
    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByRole("button", { name: /^Edit Cy\b/ })).toHaveFocus();
  });
  it.each(kinds)("$kind: lands on the Add button when the list empties", async ({ add }) => {
    const user = userEvent.setup();
    render(
      add.includes("name") ? <Names initial={[named("Ana")]} /> : <Terms initial={[term("Ana")]} />,
    );
    await user.click(screen.getByRole("button", { name: "Remove Ana" }));
    expect(screen.getByRole("button", { name: add })).toHaveFocus();
  });
  it("never lands on another Remove, so Enter does not delete the rows in turn", async () => {
    const user = userEvent.setup();
    render(<Names initial={["Ana", "Ben", "Cy"].map(named)} />);
    screen.getByRole("button", { name: "Remove Ana" }).focus();
    await user.keyboard("{Enter}");
    await user.keyboard("{Enter}"); // opens Ben for editing instead of removing it
    expect(screen.getByRole("group", { name: "Name 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Edit Cy\b/ })).toBeInTheDocument();
  });
});
