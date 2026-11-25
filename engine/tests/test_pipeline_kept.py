"""CGL-111 end to end: code blocks and text marked translate="no" reach every export unchanged
without a model ever seeing them, and web addresses or placeholders a translation changes are
flagged."""

import io
import json
from datetime import UTC, datetime

import docx

from coglossa.export.docx import bilingual_docx, clean_docx
from coglossa.export.html import render_html
from coglossa.export.rows import translated_blocks
from coglossa.pipeline import RunOptions, run_document
from coglossa.schema import FlagCode, KeepReason, MarkKind, Report

CODE = "  for x in range(3):\n      print(x)  # https://example.com/x"
FENCED = 'curl -X POST "https://api.example.com/v1/{id}" -d \'{"a": 1}\''


async def _no_sleep(_):
    return None


async def _translate(path, cfg, fake_backend) -> Report:
    report = await run_document(
        path,
        cfg,
        {"fake": fake_backend},
        RunOptions(target_lang="en", source_lang="fr"),
        sleep=_no_sleep,
        now=lambda: datetime(2026, 11, 25, tzinfo=UTC),
    )
    assert isinstance(report, Report)
    return report


def _sent(fake_backend) -> str:
    """Everything the models were sent."""
    return "\n".join(m.content for r in fake_backend.requests for m in r.messages)


def _tasks(fake_backend) -> list[str]:
    return [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["task"]
        for r in fake_backend.requests
    ]


async def test_html_code_and_translate_no_cost_no_model_call(tmp_path, make_config, fake_backend):
    path = tmp_path / "page.html"
    path.write_text(
        f"<p>Il pleut.</p><pre>{CODE}</pre><p translate='no'>ACME <b>Turbo</b> 3000</p>"
        "<p>Le chat dort.</p>",
        encoding="utf-8",
    )
    report = await _translate(path, make_config(), fake_backend)
    assert [(s.text, s.kept) for s in report.spans] == [
        ("[en] Il pleut.", None),
        (CODE, KeepReason.CODE),
        ("ACME Turbo 3000", KeepReason.NO_TRANSLATE),
        ("[en] Le chat dort.", None),
    ]
    kept = report.spans[1:3]
    assert all(s.flags == [] and s.score.meaning is None for s in kept)
    assert all(s.chosen_from == "original" for s in kept)
    # no model is sent the kept text, in any stage: profile, guide, translators, reviewer
    sent = _sent(fake_backend)
    assert "print(x)" not in sent and "ACME" not in sent
    assert "Il pleut." in sent and "Le chat dort." in sent
    assert _tasks(fake_backend) == ["profile", "guide", "translate", "translate", "reconcile"]
    # the translated passages are one model round, kept apart by the code between them
    assert {c.start for c in report.candidates} == {"s1", "s4"}

    # every export has the kept text as written; its formatting comes back in the Word file
    assert [b.text for b in translated_blocks(report)] == [
        "[en] Il pleut.",
        CODE,
        "ACME Turbo 3000",
        "[en] Le chat dort.",
    ]
    clean = docx.Document(io.BytesIO(clean_docx(report)))
    assert [p.text for p in clean.paragraphs] == [
        "[en] Il pleut.",
        CODE,
        "ACME Turbo 3000",
        "[en] Le chat dort.",
    ]
    bold = [r.text for r in clean.paragraphs[2].runs if r.bold]
    assert bold == ["Turbo"]
    assert report.spans[2].formatting is not None
    assert [m.kind for m in report.spans[2].formatting.marks] == [MarkKind.BOLD]
    table = docx.Document(io.BytesIO(bilingual_docx(report))).tables[0]
    assert [row.cells[2].text for row in table.rows][2:4] == [CODE, "ACME Turbo 3000"]
    assert "print(x)" in render_html(report)


async def test_markdown_fenced_code_costs_no_model_call(tmp_path, make_config, fake_backend):
    path = tmp_path / "doc.md"
    path.write_text(
        f"# Démarrage\n\nLancez :\n\n```bash\n{FENCED}\n```\n\nC'est tout.\n", encoding="utf-8"
    )
    report = await _translate(path, make_config(), fake_backend)
    assert [(s.text, s.kept) for s in report.spans] == [
        ("[en] Démarrage", None),
        ("[en] Lancez :", None),
        (FENCED, KeepReason.CODE),
        ("[en] C'est tout.", None),
    ]
    assert "curl" not in _sent(fake_backend)
    clean = docx.Document(io.BytesIO(clean_docx(report)))
    assert FENCED in [p.text for p in clean.paragraphs]
    # a URL inside code that is left as written needs no check
    assert all(f.code is not FlagCode.LITERAL_MISMATCH for s in report.spans for f in s.flags)


async def test_a_reply_that_joins_text_across_kept_code_is_refused(
    tmp_path, make_config, fake_backend
):
    path = tmp_path / "doc.md"
    path.write_text("Il pleut.\n\n```\nx = 1\n```\n\nLe chat dort.\n", encoding="utf-8")
    joined = json.dumps({"spans": [{"start": "s1", "end": "s3", "text": "It rains. Cat."}]})
    fake_backend.script("translate", joined, joined)
    report = await _translate(path, make_config(), fake_backend)
    # each translator was told why and answered again with one passage on each side
    assert [(s.start, s.end, s.kept) for s in report.spans] == [
        ("s1", "s1", None),
        ("s2", "s2", KeepReason.CODE),
        ("s3", "s3", None),
    ]
    retries = [
        m.content
        for r in fake_backend.requests
        for m in r.messages
        if "joins text on both sides of a part that is not translated" in m.content
    ]
    assert retries


async def test_a_changed_web_address_is_flagged_and_an_identical_one_is_not(
    tmp_path, make_config, fake_backend
):
    path = tmp_path / "page.html"
    path.write_text(
        "<p>Voir https://example.com/fr/aide.</p><p>Écrivez à aide@example.com.</p>"
        "<p>Lancez <code>make test</code> maintenant.</p>",
        encoding="utf-8",
    )
    reply = json.dumps(
        {
            "spans": [
                {
                    "start": "s1",
                    "end": "s1",
                    "text": "See https://example.com/en/help.",
                    "agreement": "agree",
                    "chosen_from": "contextual",
                },
                {
                    "start": "s2",
                    "end": "s2",
                    "text": "Write to aide@example.com, we answer quickly.",
                    "agreement": "agree",
                    "chosen_from": "contextual",
                },
                {
                    "start": "s3",
                    "end": "s3",
                    "text": "Run make check now.",
                    "agreement": "agree",
                    "chosen_from": "contextual",
                },
            ]
        }
    )
    fake_backend.script("reconcile", reply)
    report = await _translate(path, make_config(), fake_backend)
    flags = [[f for f in s.flags if f.code is FlagCode.LITERAL_MISMATCH] for s in report.spans]
    [url], [], [code] = flags
    assert url.evidence == {
        "changed": [
            {
                "kind": "link",
                "source": "https://example.com/fr/aide",
                "translation": "https://example.com/en/help",
            }
        ],
        "missing": [],
    }
    assert code.evidence == {"changed": [], "missing": [{"kind": "code", "source": "make test"}]}
    assert all(s.score.meaning is None for s in report.spans)


async def test_indented_and_quoted_fences_cost_no_model_call(tmp_path, make_config, fake_backend):
    path = tmp_path / "doc.md"
    path.write_text(
        "1. Installez :\n\n    ```bash\n    npm install\n\n    npm run build\n    ```\n\n"
        "> Exemple :\n>\n> ```\n> print('salut')\n>\n> exit()\n> ```\n\nC'est tout.\n",
        encoding="utf-8",
    )
    report = await _translate(path, make_config(), fake_backend)
    kept = [(s.text, s.kept) for s in report.spans if s.kept is not None]
    assert kept == [
        ("npm install\n\nnpm run build", KeepReason.CODE),
        ("print('salut')\n\nexit()", KeepReason.CODE),
    ]
    sent = _sent(fake_backend)
    assert "npm" not in sent and "print(" not in sent and "exit()" not in sent
    assert all(f.code is not FlagCode.LITERAL_MISMATCH for s in report.spans for f in s.flags)
