import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { DropArea, fileProblem, sizeText } from "./DropArea";

function Harness({ maxMb = 4, active = true }: { maxMb?: number | null; active?: boolean }) {
  const [files, setFiles] = useState<File[]>([]);
  return <DropArea files={files} onFiles={setFiles} maxMb={maxMb} active={active} />;
}

it("collects chosen files as chips that can be removed", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.upload(screen.getByLabelText("Choose files"), [
    new File(["Il pleut."], "Welcome letter.docx"),
    new File(["x"], "notice.pdf"),
  ]);
  const chips = screen.getByRole("list", { name: "Chosen files" });
  expect(within(chips).getAllByRole("listitem")).toHaveLength(2);
  expect(chips).toHaveTextContent("Welcome letter.docx");
  await user.click(screen.getByRole("button", { name: "Remove notice.pdf" }));
  expect(within(chips).getAllByRole("listitem")).toHaveLength(1);
  expect(screen.getByText(/up to 4 MB each/)).toBeInTheDocument();
});

it("takes dropped files and marks the ones it cannot use", () => {
  render(<Harness maxMb={1} />);
  const area = screen.getByText("Drop files here, or click to choose").closest("label")!;
  fireEvent.dragOver(area);
  expect(area).toHaveClass("over");
  fireEvent.drop(area, {
    dataTransfer: {
      files: [
        new File(["x"], "notes.rtf"),
        new File([new Uint8Array(2 * 1024 * 1024)], "big.pdf"),
        new File([], "empty.txt"),
      ],
    },
  });
  expect(area).not.toHaveClass("over");
  expect(screen.getByText(/notes\.rtf isn't a file type Coglossa reads/)).toBeInTheDocument();
  expect(screen.getByText("big.pdf is 2 MB, and the limit is 1 MB.")).toBeInTheDocument();
  expect(screen.getByText("empty.txt is empty.")).toBeInTheDocument();
});

it("words sizes and skips the limit when the server's is unknown", () => {
  expect(sizeText(10)).toBe("1 KB");
  expect(sizeText(300 * 1024)).toBe("300 KB");
  expect(sizeText(1.5 * 1024 * 1024)).toBe("1.5 MB");
  expect(fileProblem(new File([new Uint8Array(10)], "a.txt"), null)).toBeNull();
});

it("skips a file that is already in the list and says so", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const input = screen.getByLabelText("Choose files");
  const letter = () => new File(["Il pleut."], "letter.docx", { lastModified: 1000 });
  await user.upload(input, [letter()]);
  await user.upload(input, [
    letter(),
    new File(["Il pleut."], "letter.docx", { lastModified: 2000 }), // same name, other file
    new File(["x"], "notice.pdf", { lastModified: 1000 }),
    new File(["x"], "notice.pdf", { lastModified: 1000 }), // twice in one drop
  ]);
  const chips = screen.getByRole("list", { name: "Chosen files" });
  expect(
    within(chips)
      .getAllByRole("listitem")
      .map((li) => li.firstChild?.textContent),
  ).toEqual(["letter.docx", "letter.docx", "notice.pdf"]);
  expect(screen.getByRole("status")).toHaveTextContent("Already added: letter.docx, notice.pdf");
  await user.upload(input, [new File(["y"], "other.txt", { lastModified: 1 })]);
  expect(screen.getByRole("status")).toBeEmptyDOMElement();
});

it("announces drop-area problems and notices politely", () => {
  render(<Harness />);
  // Live regions are announced only if they exist before their text changes.
  expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  expect(screen.getByRole("list", { name: "Chosen files" })).toHaveAttribute("aria-live", "polite");
  fireEvent.drop(screen.getByText("Drop files here, or click to choose").closest("label")!, {
    dataTransfer: { files: [new File(["x"], "notes.rtf")] },
  });
  expect(screen.getByRole("list", { name: "Chosen files" })).toHaveTextContent(
    "notes.rtf isn't a file type",
  );
});

it("clears the 'Already added' notice when a file is removed or the area is put away", async () => {
  const user = userEvent.setup();
  const { rerender } = render(<Harness />);
  const input = screen.getByLabelText("Choose files");
  const letter = () => new File(["x"], "letter.docx", { lastModified: 1000 });
  await user.upload(input, [letter()]);
  await user.upload(input, [letter()]);
  expect(screen.getByRole("status")).toHaveTextContent("Already added: letter.docx");
  await user.click(screen.getByRole("button", { name: "Remove letter.docx" }));
  expect(screen.getByRole("status")).toBeEmptyDOMElement();
  await user.upload(input, [letter()]);
  await user.upload(input, [letter()]);
  expect(screen.getByRole("status")).toHaveTextContent("Already added");
  rerender(<Harness active={false} />);
  expect(screen.getByRole("status")).toBeEmptyDOMElement();
  // Back on the tab, the old notice does not come back with it.
  rerender(<Harness active />);
  expect(screen.getByRole("status")).toBeEmptyDOMElement();
});

it("announces only what is added to the chips", () => {
  render(<Harness />);
  expect(screen.getByRole("list", { name: "Chosen files" })).toHaveAttribute(
    "aria-relevant",
    "additions",
  );
});
