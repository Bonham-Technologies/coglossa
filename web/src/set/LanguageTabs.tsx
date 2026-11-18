import { Link } from "react-router";
import type { DocumentSummary, ProjectInfo } from "../api/types";
import { StatusText } from "../components/StatusText";
import { languageName } from "../lib/languages";
import { languagesOf, languageStatus, newestIn, sameLanguage } from "./languages";
import type { Picked } from "./SetPage";

type Props = { project: ProjectInfo; docs: DocumentSummary[]; lang: string; picked: Picked | null };

/** CGL-24: one tab per language, shown when the set has two or more. A tab opens the same file
 * in that language, else the first file translated into it. */
export function LanguageTabs({ project, docs, lang, picked }: Props) {
  const languages = languagesOf(project);
  if (languages.length < 2) return null;
  const target = (code: string) => {
    const same = picked ? newestIn(picked.doc, code) : undefined;
    const run = same ?? docs.map((d) => newestIn(d, code)).find((r) => r !== undefined);
    if (run) return `/t/${project.id}/${run.id}`;
    const file = picked ? `&file=${picked.doc.id}` : "";
    return `/t/${project.id}?lang=${encodeURIComponent(code)}${file}`;
  };
  return (
    <nav aria-label="Languages" className="file-tabs language-tabs">
      <ul>
        {languages.map((code) => {
          const current = sameLanguage(code, lang);
          const status = languageStatus(docs, code);
          return (
            <li key={code}>
              <Link
                to={target(code)}
                className={current ? "tab current" : "tab"}
                aria-current={current ? "page" : undefined}
              >
                {/* the space keeps the tab's name "German 3 to check"; the flex gap spaces it */}
                <span>{languageName(code)}</span> {status ? <StatusText status={status} /> : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
