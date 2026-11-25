"""Standalone HTML export (spec §7.5): the summary, the document notices and a side-by-side
table of source and translation with scores and open flags. One self-contained file: no
scripts and no external fonts, images or stylesheets, so it opens offline. Every text cell
has dir="auto" so right-to-left scripts display correctly (spec §8)."""

from html import escape

from coglossa.export.rows import export_rows, source_name
from coglossa.schema import Report

_STYLE = """
body { font-family: "Noto Sans", "Segoe UI", system-ui, sans-serif; margin: 2rem; color: #1b1b1f;
  background: #fff; }
h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
.meta { color: #55555c; margin-top: 0; }
.notice { background: #fff4d6; border-left: 4px solid #c98a00; padding: 0.5rem 0.75rem; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; }
th, td { border: 1px solid #d0d0d6; padding: 0.4rem 0.5rem; vertical-align: top;
  text-align: start; overflow-wrap: anywhere; }
th { background: #f2f2f5; }
th:nth-child(1), th:nth-child(4), th:nth-child(5) { width: 3.5rem; }
th:nth-child(6) { width: 22%; }
tr.flagged td:first-child { border-left: 4px solid #c0392b; }
td ul { margin: 0; padding-inline-start: 1.1rem; }
td.kept { white-space: pre-wrap; }
""".strip()


def _score(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}"


def _text(value: str) -> str:
    return escape(value).replace("\n", "<br>")


def render_html(report: Report) -> str:
    rows = export_rows(report)
    src = escape(report.profile.source_lang)
    tgt = escape(report.profile.target_lang)
    name = escape(source_name(report).name)
    open_count = sum(len(r.open_flags) for r in rows)
    flag_word = "flag" if open_count == 1 else "flags"
    parts = [
        "<!doctype html>",
        f'<html lang="{tgt}">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{name} ({src} → {tgt})</title>",
        f"<style>\n{_STYLE}\n</style>",
        "</head>",
        "<body>",
        f"<h1>{name}</h1>",
        f'<p class="meta">{src} → {tgt} · {escape(report.status.value)} · {len(rows)} passages · '
        f"{open_count} open {flag_word}</p>",
    ]
    for notice in report.notices:
        parts.append(f'<p class="notice" role="note">{escape(notice.explanation)}</p>')
    if report.guide.summary.strip():
        parts.append(
            f'<section><h2>Summary</h2><p dir="auto">{_text(report.guide.summary)}</p></section>'
        )
    parts.append(
        "<table><thead><tr><th>#</th><th>Source</th><th>Translation</th><th>Meaning</th>"
        "<th>Style</th><th>Flags</th></tr></thead><tbody>"
    )
    for row in rows:
        flagged = ' class="flagged"' if row.open_flags else ""
        items = "".join(
            f"<li><b>{escape(f.code.value)}</b>: {escape(f.explanation)}</li>"
            for f in row.open_flags
        )
        flags = f"<ul>{items}</ul>" if items else ""
        score = row.span.score
        # text left as written keeps its spacing: code is indented (CGL-111)
        kept = ' class="kept"' if row.span.kept is not None else ""
        parts.append(
            f'<tr id="p{row.number}"{flagged}><td>{row.number}</td>'
            f'<td dir="auto" lang="{src}"{kept}>{_text(row.source)}</td>'
            f'<td dir="auto" lang="{tgt}"{kept}>{_text(row.span.text)}</td>'
            f"<td>{_score(score.meaning)}</td><td>{_score(score.style)}</td><td>{flags}</td></tr>"
        )
    parts += ["</tbody></table>", "</body>", "</html>"]
    return "\n".join(parts) + "\n"
