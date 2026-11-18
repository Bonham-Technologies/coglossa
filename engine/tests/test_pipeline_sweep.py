from datetime import UTC, datetime

from coglossa.pipeline import RunOptions, run_document
from coglossa.schema import (
    Agreement,
    FlagCode,
    FlagStatus,
    GlossaryEntry,
    Guide,
    PipelineProfile,
    RevisionSource,
    RunStatus,
    Span,
    SpanScore,
)
from coglossa.scoring.aggregate import any_scored

TEXT = "Le chat dort. Il pleut."
GUIDE = Guide(
    summary="s",
    style_guide="g",
    glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
)


async def _no_sleep(_):
    return None


async def _run(tmp_path, cfg, fake_backend, **opts):
    path = tmp_path / "doc.txt"
    path.write_text(TEXT, encoding="utf-8")
    lines = []
    report = await run_document(
        path,
        cfg,
        {"fake": fake_backend},
        RunOptions(target_lang="en", source_lang="fr", guide=GUIDE, guide_approved=True, **opts),
        sleep=_no_sleep,
        now=lambda: datetime(2026, 10, 10, tzinfo=UTC),
        progress=lines.append,
    )
    return report, lines


async def test_sweep_enforces_locked_terms(tmp_path, make_config, fake_backend):
    cfg = make_config(profiles={"sweep": {"short": True}})
    report, lines = await _run(tmp_path, cfg, fake_backend)
    first = report.spans[0]
    assert "cat" in first.text
    (rev,) = report.revisions
    assert rev.span_id == "p1" and rev.source is RevisionSource.SWEEP
    assert rev.before == "[en] Le chat dort."
    assert rev.after == first.text
    violation = [f for f in first.flags if f.code is FlagCode.TERMINOLOGY_VIOLATION]
    assert [f.status for f in violation] == [FlagStatus.RESOLVED]
    assert "sweep: 1 passage(s) revised" in lines
    assert report.status is RunStatus.COMPLETE


async def test_sweep_is_off_for_short_documents_by_default(tmp_path, make_config, fake_backend):
    report, lines = await _run(tmp_path, make_config(), fake_backend)
    assert report.revisions == []
    assert "sweep: off for the short profile" in lines
    violation = [f for f in report.spans[0].flags if f.code is FlagCode.TERMINOLOGY_VIOLATION]
    assert [f.status for f in violation] == [FlagStatus.OPEN]


async def test_sweep_errors_make_the_run_complete_with_errors(tmp_path, make_config, fake_backend):
    fake_backend.script("sweep", '{"text": "nope"}', '{"text": "nope"}')
    cfg = make_config(profiles={"sweep": {"short": True}})
    report, _ = await _run(tmp_path, cfg, fake_backend)
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    assert [e.stage for e in report.errors] == ["sweep"]


async def test_standard_profile_sweeps(tmp_path, make_config, fake_backend):
    report, _ = await _run(
        tmp_path, make_config(), fake_backend, pipeline_profile=PipelineProfile.STANDARD
    )
    assert len(report.revisions) == 1


def _span(score: SpanScore) -> Span:
    return Span(
        id="p1",
        chunk_id="c1",
        start="s1",
        end="s1",
        text="t",
        agreement=Agreement.AGREE,
        chosen_from="literal",
        score=score,
    )


def test_any_scored():
    assert any_scored([]) is False
    assert any_scored([_span(SpanScore(signals={}))]) is False
    assert any_scored([_span(SpanScore(signals={}, meaning=80.0))]) is True
    assert any_scored([_span(SpanScore(signals={}, style=55.0))]) is True
