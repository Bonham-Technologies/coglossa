import { render, screen } from "@testing-library/react";
import { spanDetail } from "../test/fixtures";
import type { SpanDetail } from "../api/types";
import { Findings } from "./Findings";
import { RunLanguages } from "./languages";

function show(detail: SpanDetail) {
  return render(
    <RunLanguages.Provider value={{ source: "fr", target: "en" }}>
      <Findings detail={detail} />
    </RunLanguages.Provider>,
  );
}

it("shows the rationale, issues, checker findings and back translation", () => {
  const { container } = show(spanDetail);
  expect(screen.getByText("Kept the literal tense.")).toBeInTheDocument();
  expect(screen.getByRole("list", { name: "Issues" })).toHaveTextContent("tense of 'dort'");
  expect(screen.getByText("Why this version was chosen")).toBeInTheDocument();
  expect(screen.getByText("What the checker found")).toBeInTheDocument();
  const findings = screen.getByRole("list", { name: "Checker findings" });
  expect(findings).toHaveTextContent("Major");
  expect(findings).not.toHaveTextContent("major");
  expect(container.querySelector("mark")).toHaveTextContent("sleeps");
  expect(screen.getByText("Le chat dort. Il pleut.")).toHaveAttribute("lang", "fr");
});

it("says so when the checker did not assess the passage", () => {
  show({
    ...spanDetail,
    rationale: "",
    issues: [],
    back_translation: "",
    judge_errors: null,
  });
  expect(screen.getByText("The checker didn't look at this passage.")).toBeInTheDocument();
  expect(screen.queryByText("Back translation")).not.toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Issues" })).not.toBeInTheDocument();
});

it("shows a finding without an excerpt, and nothing when the checker found nothing", () => {
  const { unmount } = show({
    ...spanDetail,
    judge_errors: [
      { category: "style/register", severity: "minor", excerpt: "", explanation: "Stiff." },
    ],
  });
  expect(screen.getByRole("list", { name: "Checker findings" })).not.toHaveTextContent("“");
  expect(screen.getByRole("list", { name: "Checker findings" })).toHaveTextContent("Tone or style");
  unmount();
  show({ ...spanDetail, judge_errors: [] });
  expect(screen.queryByText("The checker didn't look at this passage.")).not.toBeInTheDocument();
  expect(screen.queryByText("What the checker found")).not.toBeInTheDocument();
});
