import { screen, within } from "@testing-library/react";
import { documentRun, documentSummary } from "../test/fixtures";
import { renderAt } from "../test/render";
import { dismissUpload, retryUpload, type UploadItem } from "../upload/queue";
import { FileTabs } from "./FileTabs";

vi.mock("../upload/queue", () => ({ retryUpload: vi.fn(), dismissUpload: vi.fn() }));

const docA = {
  ...documentSummary,
  id: "d-a",
  filename: "a.txt",
  runs: [{ ...documentRun, id: "r-a" }],
};
const docB = {
  ...documentSummary,
  id: "d-b",
  filename: "b.txt",
  runs: [{ ...documentRun, id: "r-b", state: "translating" as const }],
};
const upload = (patch: Partial<UploadItem>): UploadItem => ({
  key: "u-1",
  projectId: "p-1",
  name: "c.txt",
  size: 10,
  status: "uploading",
  error: null,
  runId: null,
  ...patch,
});

it("shows nothing for a single file", () => {
  renderAt(
    <FileTabs
      lang="en"
      projectId="p-1"
      docs={[docA]}
      uploads={[]}
      picked={{ doc: docA, run: docA.runs[0]!, latest: true }}
    />,
  );
  expect(screen.queryByRole("navigation", { name: "Files" })).not.toBeInTheDocument();
});

it("shows a tab per file with its status, and uploads still under way", async () => {
  renderAt(
    <FileTabs
      lang="en"
      projectId="p-1"
      docs={[docA, docB]}
      uploads={[
        upload({}),
        upload({
          key: "u-2",
          name: "d.pdf",
          status: "failed",
          error: "Too large for this server.",
        }),
      ]}
      picked={{ doc: docA, run: docA.runs[0]!, latest: true }}
    />,
  );
  const tabs = screen.getByRole("navigation", { name: "Files" });
  const a = within(tabs).getByRole("link", { name: /a\.txt/ });
  expect(a).toHaveAttribute("aria-current", "page");
  expect(a).toHaveTextContent("ready");
  expect(within(tabs).getByRole("link", { name: /b\.txt/ })).toHaveAttribute("href", "/t/p-1/r-b");
  expect(tabs).toHaveTextContent("c.txt uploading…");
  const failed = within(tabs).getByRole("group", { name: "d.pdf" });
  expect(failed).toHaveTextContent("Too large for this server.");
});

it("retries or removes a failed upload", async () => {
  const { user } = renderAt(
    <FileTabs
      lang="en"
      projectId="p-1"
      docs={[]}
      uploads={[upload({ status: "failed", error: "Too large for this server." })]}
      picked={null}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Try again" }));
  expect(retryUpload).toHaveBeenCalledWith("u-1");
  await user.click(screen.getByRole("button", { name: "Remove c.txt" }));
  expect(dismissUpload).toHaveBeenCalledWith("u-1");
});

it("shows a finished file that is being rechecked as rechecking", () => {
  const rechecking = {
    ...docA,
    runs: [
      {
        ...documentRun,
        id: "r-a",
        state: "sweeping" as const,
        finished_at: "2026-10-01T09:05:00Z",
      },
    ],
  };
  renderAt(
    <FileTabs
      lang="en"
      projectId="p-1"
      docs={[rechecking, docB]}
      uploads={[]}
      picked={{ doc: rechecking, run: rechecking.runs[0]!, latest: true }}
    />,
  );
  expect(screen.getByRole("link", { name: /a\.txt/ })).toHaveTextContent("rechecking…");
});

it("shows a finished file that is being retried as retrying", () => {
  const retrying = {
    ...docA,
    runs: [
      {
        ...documentRun,
        id: "r-a",
        state: "translating" as const,
        finished_at: "2026-10-01T09:05:00Z",
      },
    ],
  };
  renderAt(
    <FileTabs
      lang="en"
      projectId="p-1"
      docs={[retrying, docB]}
      uploads={[]}
      picked={{ doc: retrying, run: retrying.runs[0]!, latest: true }}
    />,
  );
  expect(screen.getByRole("link", { name: /a\.txt/ })).toHaveTextContent("retrying…");
});
