import { screen, within } from "@testing-library/react";
import { documentRun, documentSummary, project, runSummary } from "../test/fixtures";
import { renderAt } from "../test/render";
import { LanguageTabs } from "./LanguageTabs";

const languages = [
  { target_lang: "en", guide_version: 1 },
  { target_lang: "de", guide_version: null },
];
const docs = [{ ...documentSummary, runs: [documentRun] }];

it("links a language no file is in yet to the set in that language", () => {
  const picked = { doc: docs[0]!, run: runSummary, latest: true };
  renderAt(
    <LanguageTabs project={{ ...project, languages }} docs={docs} lang="en" picked={picked} />,
  );
  const tabs = screen.getByRole("navigation", { name: "Languages" });
  const german = within(tabs).getByRole("link", { name: "German" }); // no status: no runs
  expect(german).toHaveAttribute("href", "/t/p-1?lang=de&file=d-1");
  expect(german).not.toHaveAttribute("aria-current");
});

it("without a file picked, links the language alone", () => {
  renderAt(<LanguageTabs project={{ ...project, languages }} docs={[]} lang="en" picked={null} />);
  expect(screen.getByRole("link", { name: "German" })).toHaveAttribute("href", "/t/p-1?lang=de");
});

it("says how many passages are left to check in each language, and nothing when none are", () => {
  // CGL-73
  const german = { ...documentRun, id: "r-de", target_lang: "de", to_check: 3 };
  const both = [{ ...documentSummary, runs: [german, documentRun] }];
  renderAt(
    <LanguageTabs project={{ ...project, languages }} docs={both} lang="en" picked={null} />,
  );
  const tabs = screen.getByRole("navigation", { name: "Languages" });
  const de = within(tabs).getByRole("link", { name: "German 3 to check" });
  expect(de).toHaveAttribute("href", "/t/p-1/r-de");
  const en = within(tabs).getByRole("link", { name: "English ready" }); // to_check: 0
  expect(en).not.toHaveTextContent("to check");
});
