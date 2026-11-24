import json
from datetime import UTC, datetime

import docx
import pytest

from coglossa.config import ConfigError
from coglossa.ingest.base import UNREAD_PREFIX, IngestError
from coglossa.llm.types import OutputTruncatedError, PermanentBackendError
from coglossa.pipeline import GuideCheckpoint, RunOptions, run_document
from coglossa.schema import FlagCode, GlossaryEntry, Guide, PipelineProfile, Report, RunStatus
from coglossa.stages.context import StageError
from tests.conftest import BASE_CONFIG
from tests.ingest.docx_parts import add_chart

TEXT = "Il pleut. Le chat dort.\n\n* * *\n\nDemain, le soleil."
FIXED = datetime(2026, 10, 9, tzinfo=UTC)


async def _no_sleep(_):
    return None


@pytest.fixture
def doc_path(tmp_path):
    path = tmp_path / "story.txt"
    path.write_text(TEXT, encoding="utf-8")
    return path


async def _run(path, cfg, fake_backend, **opts):
    return await run_document(
        path,
        cfg,
        {"fake": fake_backend},
        RunOptions(target_lang="en", **opts),
        sleep=_no_sleep,
        now=lambda: FIXED,
    )


def _tasks(fake_backend):
    return [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["task"]
        for r in fake_backend.requests
    ]


async def test_short_document_end_to_end(doc_path, make_config, fake_backend):
    report = await _run(doc_path, make_config(), fake_backend, source_lang="fr")
    assert isinstance(report, Report)
    assert report.status is RunStatus.COMPLETE
    assert report.created_at == FIXED
    assert report.profile.source_lang == "fr"
    assert report.profile.pipeline_profile is PipelineProfile.SHORT
    assert [s.text for s in report.segments] == ["Il pleut.", "Le chat dort.", "Demain, le soleil."]
    assert [(s.id, s.start, s.text) for s in report.spans] == [
        ("p1", "s1", "[en] Il pleut."),
        ("p2", "s2", "[en] Le chat dort."),
        ("p3", "s3", "[en] Demain, le soleil."),
    ]
    assert all(s.score.meaning is None and s.flags == [] for s in report.spans)
    assert len(report.candidates) == 6
    assert _tasks(fake_backend) == ["profile", "guide", "translate", "translate", "reconcile"]
    assert report.usage.calls == 5
    assert report.config.roles["reviewer"] == "model-a"
    assert report.config.prompt_versions == {
        "profile": 1,
        "guide": 1,
        "guide_merge": 1,
        "reconcile": 3,
        "sweep": 2,
        "literal": 3,
        "contextual": 3,
    }
    assert {c.role for c in report.llm_calls} == {
        "profiler",
        "guide",
        "translator:literal",
        "translator:contextual",
        "reviewer",
    }
    assert Report.model_validate_json(report.model_dump_json()) == report


async def test_what_the_file_could_not_hold_is_in_the_report(tmp_path, make_config, fake_backend):
    """CGL-87: nothing is left out silently; the report names what could not be read."""
    d = docx.Document()
    add_chart(d.add_paragraph("Le chat dort."))
    path = tmp_path / "chart.docx"
    d.save(path)
    report = await _run(path, make_config(), fake_backend, source_lang="fr")
    # a notice, not an error: what could be read is translated, and again would change nothing
    assert report.status is RunStatus.COMPLETE
    assert report.errors == []
    unread = [n for n in report.notices if n.code is FlagCode.UNREAD_CONTENT]
    assert [n.explanation for n in unread] == [UNREAD_PREFIX + "1 chart."]


async def test_every_segment_is_covered_once_by_spans(tmp_path, make_config, fake_backend):
    path = tmp_path / "long.txt"
    path.write_text(
        "\n\n".join(f"Paragraph {i}. It has two sentences." for i in range(400)), encoding="utf-8"
    )
    cfg = make_config(chunking={"target_words": 200, "max_words": 300})
    report = await _run(path, cfg, fake_backend, checkpoint=False)
    positions = {s.id: i for i, s in enumerate(report.segments)}
    covered = [i for s in report.spans for i in range(positions[s.start], positions[s.end] + 1)]
    assert covered == list(range(len(report.segments)))
    assert len(report.chunks) > 1


async def test_long_form_stops_at_checkpoint(doc_path, make_config, fake_backend):
    result = await _run(
        doc_path, make_config(), fake_backend, pipeline_profile=PipelineProfile.LONG_FORM
    )
    assert isinstance(result, GuideCheckpoint)
    assert "translate" not in _tasks(fake_backend)
    assert result.profile.pipeline_profile is PipelineProfile.LONG_FORM


async def test_checkpoint_config_and_override(doc_path, make_config, fake_backend):
    cfg = make_config(profiles={"checkpoint": {"short": True}})
    assert isinstance(await _run(doc_path, cfg, fake_backend), GuideCheckpoint)
    report = await _run(doc_path, cfg, fake_backend, checkpoint=False)
    assert isinstance(report, Report)


async def test_approved_guide_skips_generation_and_is_used(doc_path, make_config, fake_backend):
    approved = Guide(
        summary="approved",
        style_guide="terse",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    report = await _run(
        doc_path,
        make_config(),
        fake_backend,
        guide=approved,
        guide_approved=True,
        pipeline_profile=PipelineProfile.LONG_FORM,
    )
    assert isinstance(report, Report)
    assert "guide" not in _tasks(fake_backend)
    assert report.guide == approved
    contextual = [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1]) for r in fake_backend.requests
    ]
    assert any(
        # a guide without forms reads to the agents as before (CGL-109)
        p.get("guide", {}).get("glossary")
        == [{"term": "chat", "rendering": "cat", "notes": "", "locked": True}]
        for p in contextual
        if p["task"] == "translate"
    )


async def test_prior_guide_locked_terms_survive_regeneration(doc_path, make_config, fake_backend):
    prior = Guide(
        summary="old",
        style_guide="old",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    report = await _run(doc_path, make_config(), fake_backend, guide=prior)
    assert report.guide.glossary == prior.glossary
    assert report.guide.summary.startswith("Synthetic summary")


async def test_reviewer_failure_marks_spans_and_status(doc_path, make_config, fake_backend):
    fake_backend.script("reconcile", PermanentBackendError("reviewer down"))
    report = await _run(doc_path, make_config(), fake_backend)
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    assert all(s.flags[0].code is FlagCode.ENGINE_ERROR for s in report.spans)
    assert report.spans[0].flags[0].evidence["candidates"]  # alternatives shown to the user
    assert [e.stage for e in report.errors] == ["reconcile"]


async def test_a_guide_that_cannot_be_built_still_translates(doc_path, make_config, fake_backend):
    # CGL-14: a model looping to the output limit used to fail the whole document here
    fake_backend.script("guide", OutputTruncatedError("cut"), OutputTruncatedError("cut"))
    report = await _run(doc_path, make_config(), fake_backend)
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    assert report.guide == Guide(summary="", style_guide="")
    assert [e.stage for e in report.errors] == ["guide"]
    assert report.errors[0].message.endswith("; translating without one")
    assert all(s.text for s in report.spans)


async def test_a_guide_that_cannot_be_built_does_not_stop_for_review(
    doc_path, make_config, fake_backend
):
    # long-form documents stop for guide review; with no guide there is nothing to review
    fake_backend.script("guide", *[OutputTruncatedError("cut")] * 3)
    report = await _run(
        doc_path, make_config(), fake_backend, pipeline_profile=PipelineProfile.LONG_FORM
    )
    assert isinstance(report, Report)
    assert report.errors[-1].message.endswith("; translating without one")


async def test_translator_failure_is_reported(doc_path, make_config, fake_backend):
    fake_backend.script("translate", PermanentBackendError("literal down"))
    report = await _run(doc_path, make_config(), fake_backend)
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    assert [e.role for e in report.errors] == ["translator:literal"]
    assert all(s.flags == [] for s in report.spans)


async def test_disagreement_and_proposals(doc_path, make_config, fake_backend):
    spans = [
        {
            "start": "s1",
            "end": "s2",
            "text": "It rains; the cat sleeps.",
            "agreement": "major",
            "chosen_from": "merged",
            "rationale": "literal misreads 'dort'",
            "cultural_or_wordplay": False,
            "proposed_terms": [
                {"term": "Pluie", "rendering": "rain", "locked": True},
                {"term": "pluie", "rendering": "rain"},
            ],
        },
        {
            "start": "s3",
            "end": "s3",
            "text": "Tomorrow, sun.",
            "agreement": "agree",
            "chosen_from": "literal",
        },
    ]
    fake_backend.script("reconcile", json.dumps({"spans": spans}))
    report = await _run(doc_path, make_config(), fake_backend)
    assert [s.flags[0].code for s in report.spans if s.flags] == [FlagCode.AGENT_DISAGREEMENT]
    assert len(report.spans[0].flags[0].evidence["candidates"]) == 4  # 2 translators x s1,s2
    assert [(g.term, g.locked) for g in report.guide_proposals] == [("Pluie", False)]


async def test_failures_before_translation_propagate(tmp_path, doc_path, make_config, fake_backend):
    fake_backend.script("profile", "garbage", "garbage")
    with pytest.raises(StageError):
        await _run(doc_path, make_config(), fake_backend)
    empty = tmp_path / "empty.txt"
    empty.write_text("* * *", encoding="utf-8")
    with pytest.raises(IngestError):
        await _run(empty, make_config(), fake_backend)
    small = {**BASE_CONFIG["models"], "model-a": {"backend": "fake", "context_window": 4096}}
    with pytest.raises(ConfigError):
        await _run(doc_path, make_config(models=small), fake_backend)


async def test_markdown_with_code_fence_survives_translation(tmp_path, make_config, fake_backend):
    path = tmp_path / "doc.md"
    path.write_text("Voici la commande :\n\n```bash\nls -la\n```\n\nFin.", encoding="utf-8")
    fenced = "Run it:\n```bash\nls -la\n```"
    translate = json.dumps(
        {"spans": [{"start": f"s{i}", "end": f"s{i}", "text": fenced} for i in (1, 2, 3)]}
    )
    reconcile = json.dumps(
        {
            "spans": [
                {
                    "start": f"s{i}",
                    "end": f"s{i}",
                    "text": fenced,
                    "agreement": "agree",
                    "chosen_from": "literal",
                }
                for i in (1, 2, 3)
            ]
        }
    )
    fake_backend.script("translate", translate, translate)
    fake_backend.script("reconcile", reconcile)
    report = await _run(path, make_config(), fake_backend, source_lang="fr")
    assert isinstance(report, Report)
    assert report.status is RunStatus.COMPLETE
    assert [s.text for s in report.spans] == [fenced] * 3


async def test_bad_prompts_fail_before_any_model_call(doc_path, make_config, fake_backend):
    roles = {
        **make_config().roles.model_dump(mode="json"),
        "extra_translators": [{"name": "x", "prompt": "nope", "model": "model-b"}],
    }
    with pytest.raises(ConfigError, match="nope"):
        await _run(doc_path, make_config(roles=roles), fake_backend)
    assert fake_backend.requests == []


async def test_supplied_profile_skips_profiling(doc_path, make_config, fake_backend):
    from coglossa.schema import DocumentProfile

    profile = DocumentProfile(
        source_lang="fr",
        target_lang="en",
        mixed_language=False,
        genre="poetry",
        tone_register="edited",
        word_count=7,
        pipeline_profile=PipelineProfile.SHORT,
    )
    report = await _run(doc_path, make_config(), fake_backend, profile=profile)
    assert isinstance(report, Report) and report.profile == profile
    assert "profile" not in _tasks(fake_backend)
    wrong = profile.model_copy(update={"target_lang": "de"})
    with pytest.raises(ConfigError, match="target"):
        await _run(doc_path, make_config(), fake_backend, profile=wrong)


async def test_progress_lines_in_order(tmp_path, make_config, fake_backend):
    path = tmp_path / "long.txt"
    path.write_text(
        "\n\n".join(f"Paragraph {i}. It has two sentences." for i in range(400)), encoding="utf-8"
    )
    lines: list[str] = []
    cfg = make_config(chunking={"target_words": 200, "max_words": 300})
    report = await run_document(
        path,
        cfg,
        {"fake": fake_backend},
        RunOptions(target_lang="en", checkpoint=False),
        sleep=_no_sleep,
        now=lambda: FIXED,
        progress=lines.append,
    )
    n = len(report.chunks)
    assert n > 1
    assert lines[0].startswith("profile: ") and "words" in lines[0]
    assert lines[1] == "guide: ready"
    assert lines[2:-3] == [f"chunk {k}/{n} done" for k in range(1, n + 1)]
    assert lines[-3] == "sweep: 0 passage(s) revised"
    assert lines[-2] == "report: assembled"
    assert lines[-1] == "scoring: unscored (no scorer or judge produced scores)"


async def test_progress_reports_checkpoint(doc_path, make_config, fake_backend):
    lines: list[str] = []
    result = await run_document(
        doc_path,
        make_config(),
        {"fake": fake_backend},
        RunOptions(target_lang="en", pipeline_profile=PipelineProfile.LONG_FORM),
        sleep=_no_sleep,
        progress=lines.append,
    )
    assert isinstance(result, GuideCheckpoint)
    assert lines[-1] == "guide: ready for review"
