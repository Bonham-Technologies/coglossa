import { render, screen } from "@testing-library/react";
import { spanDetail } from "../test/fixtures";
import { Signals } from "./Signals";

it("labels every row in plain words", () => {
  render(
    <Signals
      detail={{
        ...spanDetail,
        agreement: "minor",
        score: {
          ...spanDetail.score,
          reliability: "C",
          signals: { qe: 0.5, xsim: null, mystery: 1 },
        },
      }}
    />,
  );
  const table = screen.getByRole("table", { name: "Score details" });
  expect(table).toHaveTextContent("Quality estimate0.50");
  expect(table).toHaveTextContent("Closeness to the original—");
  expect(table).toHaveTextContent("Reliability of these scoresLow");
  expect(table).toHaveTextContent("Translator agreementWording differed");
  expect(table).toHaveTextContent("mystery1.00");
  for (const raw of ["qe", "xsim", "minor"]) {
    expect(screen.queryByText(raw)).not.toBeInTheDocument();
  }
});

it("scopes every header to its row", () => {
  render(<Signals detail={spanDetail} />);
  const headers = screen.getByRole("table").querySelectorAll("th");
  expect(headers.length).toBeGreaterThan(0);
  for (const th of headers) {
    expect(th).toHaveAttribute("scope", "row");
  }
});
