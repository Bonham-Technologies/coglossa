import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { Entity, GlossaryEntry } from "../api/types";
import { emptyEntity, emptyTerm } from "./entries";
import { NameRows, PAGE_SIZE, TermRows } from "./GuideRows";

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

it("lists other forms of a term and a name, one per line (CGL-109)", async () => {
  const user = userEvent.setup();
  const seen: GlossaryEntry[][] = [];
  function Watched() {
    const [terms, setTerms] = useState([
      { ...emptyTerm(), term: "contract", rendering: "Vertrag" },
    ]);
    seen.push(terms);
    return <TermRows terms={terms} onChange={setTerms} />;
  }
  const { unmount } = render(<Watched />);
  await user.click(screen.getByRole("button", { name: /^Edit contract\b/ }));
  const forms = screen.getByLabelText("Other correct forms of the translation, one per line");
  await user.type(forms, "Vertrags{Enter}Verträge");
  await user.type(
    screen.getByLabelText("Other forms of the term in the original, one per line"),
    "contracts",
  );
  expect(forms).toHaveValue("Vertrags\nVerträge");
  expect(seen.at(-1)![0]).toMatchObject({
    forms: ["Vertrags", "Verträge"],
    source_forms: ["contracts"],
  });

  unmount();
  render(<Names initial={[{ ...emptyEntity(), name: "Moskva", forms: ["Moskau"] }]} />);
  await user.click(screen.getByRole("button", { name: /^Edit Moskva\b/ }));
  expect(screen.getByLabelText("Other correct forms of the translation, one per line")).toHaveValue(
    "Moskau",
  );
  expect(
    screen.getByLabelText("Other forms of the name in the original, one per line"),
  ).toHaveValue("");
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

describe("a long list (CGL-116)", () => {
  const many = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      ...emptyTerm(),
      term: `term ${i + 1}`,
      rendering: `Begriff ${i + 1}`,
    }));
  const rows = () => within(screen.getByRole("list", { name: "Terms" })).getAllByRole("listitem");

  it("shows a hundred rows at a time, with the tools above the list", async () => {
    const user = userEvent.setup();
    render(<Terms initial={many(5000)} />);
    expect(rows()).toHaveLength(PAGE_SIZE);
    expect(screen.getByText("Showing the first 100 of 5,000 terms.")).toBeInTheDocument();
    // the search box and Add come before the list, so neither needs a scroll past it
    const list = screen.getByRole("list", { name: "Terms" });
    for (const control of [
      screen.getByLabelText("Find a term"),
      screen.getByRole("button", { name: "+ Add a term" }),
    ]) {
      expect(control.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    await user.click(screen.getByRole("button", { name: "Show 100 more terms" }));
    expect(rows()).toHaveLength(2 * PAGE_SIZE);
    expect(rows()[PAGE_SIZE]).toHaveTextContent("term 101 → Begriff 101");
  });

  it("moves focus to the first new row after showing more", async () => {
    const user = userEvent.setup();
    render(<Terms initial={many(150)} />);
    await user.click(screen.getByRole("button", { name: "Show 50 more terms" }));
    expect(rows()).toHaveLength(150);
    expect(screen.getByRole("button", { name: /^Edit term 101\b/ })).toHaveFocus();
    expect(screen.queryByRole("button", { name: /more terms/ })).toBeNull();
  });

  it("clears the search to add a term, so the new term stays in view", async () => {
    const user = userEvent.setup();
    render(<Terms initial={many(20)} />);
    await user.type(screen.getByLabelText("Find a term"), "term 7");
    await waitFor(() => expect(rows()).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "+ Add a term" }));
    expect(screen.getByLabelText("Find a term")).toHaveValue("");
    await user.type(screen.getByLabelText("Term"), "fenêtre");
    await user.type(screen.getByLabelText("Translation"), "Fenster");
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.getByRole("button", { name: /^Edit fenêtre\b/ })).toBeInTheDocument();
  });

  it("finds a term by its words or its translation, in any case", async () => {
    const user = userEvent.setup();
    render(<Terms initial={many(5000)} />);
    await user.type(screen.getByLabelText("Find a term"), "TERM 4321");
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(rows()[0]).toHaveTextContent("term 4321 → Begriff 4321");
    expect(screen.getByText("1 term matches.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Find a term"));
    await user.type(screen.getByLabelText("Find a term"), "begriff 49");
    // 49, 490-499 and 4900-4999
    await waitFor(() => expect(rows()).toHaveLength(PAGE_SIZE));
    expect(
      screen.getByText("111 terms match. Showing the first 100 of 111 terms."),
    ).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Find a term"));
    await user.type(screen.getByLabelText("Find a term"), "zebra");
    expect(await screen.findByText("No terms match “zebra”.")).toBeInTheDocument();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("finds a name by its words or its translation", async () => {
    const user = userEvent.setup();
    render(
      <Names
        initial={["Ana", "Ben", "Cy"].map((name) => ({
          ...emptyEntity(),
          name,
          rendering: `${name}!`,
        }))}
      />,
    );
    await user.type(screen.getByLabelText("Find a name"), "ben");
    await waitFor(() =>
      expect(
        within(screen.getByRole("list", { name: "Names" })).getAllByRole("listitem"),
      ).toHaveLength(1),
    );
    expect(screen.getByRole("button", { name: /^Edit Ben\b/ })).toBeInTheDocument();
  });

  it("shows a new term at the top while the list is longer than a page", async () => {
    const user = userEvent.setup();
    render(<Terms initial={many(250)} />);
    await user.click(screen.getByRole("button", { name: "+ Add a term" }));
    expect(rows()[0]).toContainElement(screen.getByRole("group", { name: "Term 251" }));
    await user.type(screen.getByLabelText("Term"), "fenêtre");
    await user.type(screen.getByLabelText("Translation"), "Fenster");
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(rows()).toHaveLength(PAGE_SIZE); // back at the end of the list, past the page
  });

  it("removing a found row moves focus to the next found row", async () => {
    const user = userEvent.setup();
    render(<Terms initial={many(300)} />);
    await user.type(screen.getByLabelText("Find a term"), "term 29");
    await waitFor(() => expect(rows()).toHaveLength(11)); // 29, 290-299
    await user.click(screen.getByRole("button", { name: "Remove term 290" }));
    expect(screen.getByRole("button", { name: /^Edit term 291\b/ })).toHaveFocus();
    expect(rows()).toHaveLength(10);
  });

  it("opens and finds in a 20,000-term guide rendering one page", async () => {
    const user = userEvent.setup();
    render(<Terms initial={many(20_000)} />);
    expect(rows()).toHaveLength(PAGE_SIZE);
    await user.type(screen.getByLabelText("Find a term"), "19999");
    await waitFor(() => expect(rows()).toHaveLength(1));
  });
});
