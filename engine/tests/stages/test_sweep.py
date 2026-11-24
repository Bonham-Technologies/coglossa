import json

from coglossa.glossary_import import merge_import, parse_glossary, plan_import
from coglossa.llm.types import PermanentBackendError
from coglossa.schema import (
    Agreement,
    DocumentProfile,
    Entity,
    Flag,
    FlagCode,
    FlagStatus,
    Genre,
    GlossaryEntry,
    Guide,
    InlineMark,
    MarkKind,
    PipelineProfile,
    RevisionSource,
    Span,
    SpanFormatting,
    kept_formatting,
)
from coglossa.scoring.rules import guide_check
from coglossa.stages.context import StageContext
from coglossa.stages.score import unscored
from coglossa.stages.sweep import SWEPT, resolve_proposals, run_sweep, span_sources
from tests.conftest import make_segments

PROFILE = DocumentProfile(
    source_lang="fr",
    target_lang="en",
    mixed_language=False,
    genre=Genre.OTHER,
    tone_register="neutral",
    word_count=10,
    pipeline_profile=PipelineProfile.STANDARD,
)
SEGMENTS = make_segments("Le chat dort.", "Ana arrive.", "Il pleut.")
LOCKED = Guide(
    summary="",
    style_guide="",
    glossary=[
        GlossaryEntry(term="chat", rendering="cat", locked=True),
        GlossaryEntry(term="pleut", rendering="pours", locked=False),
    ],
    entities=[Entity(name="Ana", rendering="Anna", kind="person", locked=True)],
)


def _span(sid, seg, text, flags=()):
    return Span(
        id=sid,
        chunk_id="c1",
        start=seg,
        end=seg,
        text=text,
        agreement=Agreement.AGREE,
        chosen_from="literal",
        score=unscored(),
        flags=list(flags),
    )


def _violation_flag():
    return Flag(code=FlagCode.TERMINOLOGY_VIOLATION, explanation="'chat' → 'cat'")


def _ctx(make_runner):
    runner = make_runner()
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


def _sweep_requests(fake_backend):
    payloads = [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1]) for r in fake_backend.requests
    ]
    return [p for p in payloads if p["task"] == "sweep"]


def test_span_sources_join_the_covered_segments():
    span = _span("p1", "s1", "x").model_copy(update={"end": "s2"})
    assert span_sources([span], SEGMENTS) == ["Le chat dort. Ana arrive."]


async def test_nothing_to_enforce_makes_no_calls(make_runner, fake_backend):
    spans = [_span("p1", "s1", "The cat sleeps."), _span("p3", "s3", "It rains.")]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    assert result.spans == spans
    assert result.changes == [] and result.errors == []
    assert fake_backend.requests == []  # unlocked 'pleut' → 'pours' is not enforced


async def test_locked_term_is_enforced_and_its_flag_resolved(make_runner, fake_backend):
    spans = [_span("p1", "s1", "The feline sleeps.", [_violation_flag()])]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    (change,) = result.changes
    assert change.span_id == "p1"
    assert change.before == "The feline sleeps."
    assert "cat" in change.after
    assert change.reason == "Applied locked guide entries: 'chat' → 'cat'."
    assert change.resolved == (FlagCode.TERMINOLOGY_VIOLATION,)
    assert result.spans[0].text == change.after
    assert result.spans[0].flags[0].status is FlagStatus.RESOLVED
    (rev,) = result.revisions
    assert rev.source is RevisionSource.SWEEP and rev.before == "The feline sleeps."
    (payload,) = _sweep_requests(fake_backend)
    assert payload["requirements"] == [{"term": "chat", "rendering": "cat", "kind": "glossary"}]
    assert payload["source"] == "Le chat dort."


async def test_a_rewrite_of_a_formatted_passage_is_flagged(make_runner, fake_backend):
    text = "The feline sleeps."
    formatting = SpanFormatting(text=text, marks=[InlineMark(start=4, end=10, kind=MarkKind.BOLD)])
    span = _span("p1", "s1", text, [_violation_flag()]).model_copy(
        update={"formatting": formatting}
    )
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, [span], SEGMENTS)
    (swept,) = result.spans
    assert kept_formatting(swept) is None  # plain in the Word download
    assert [f.code for f in swept.flags] == [
        FlagCode.TERMINOLOGY_VIOLATION,
        FlagCode.FORMATTING_LOST,
    ]
    # the worker stores the change, not the span: the change carries the flag (CGL-129)
    (change,) = result.changes
    assert change.raised == (swept.flags[1],) and swept.flags[1].evidence == SWEPT


async def test_a_rewrite_of_a_plain_passage_adds_no_formatting_flag(make_runner, fake_backend):
    spans = [_span("p1", "s1", "The feline sleeps.", [_violation_flag()])]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    assert [f.code for f in result.spans[0].flags] == [FlagCode.TERMINOLOGY_VIOLATION]
    assert result.changes[0].raised == ()


async def test_an_imported_locked_term_is_enforced_like_a_hand_locked_one(
    make_runner, fake_backend
):
    """CGL-88: a term from a glossary file, locked in one step, is checked and enforced."""
    parsed = parse_glossary(
        b"term,translation\nchat,cat\n", "terms.csv", target_lang="en", source_lang="fr"
    )
    base = Guide(summary="", style_guide="")
    guide, _ = merge_import(
        base, plan_import(base, parsed.terms), lock_all=True, overwrite_conflicts=False
    )
    assert guide.glossary == [GlossaryEntry(term="chat", rendering="cat", locked=True)]
    _, violations = guide_check("Le chat dort.", "The feline sleeps.", guide)
    assert [(v.term, v.expected, v.locked) for v in violations] == [("chat", "cat", True)]

    spans = [_span("p1", "s1", "The feline sleeps.", [_violation_flag()])]
    result = await run_sweep(_ctx(make_runner), PROFILE, guide, spans, SEGMENTS)
    (change,) = result.changes
    assert change.reason == "Applied locked guide entries: 'chat' → 'cat'."
    assert "cat" in result.spans[0].text
    assert result.spans[0].flags[0].status is FlagStatus.RESOLVED
    (payload,) = _sweep_requests(fake_backend)
    assert payload["requirements"] == [{"term": "chat", "rendering": "cat", "kind": "glossary"}]


async def test_locked_entity_is_enforced_with_entity_context(make_runner, fake_backend):
    spans = [_span("p2", "s2", "Ana arrives.")]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    assert "Anna" in result.spans[0].text
    (payload,) = _sweep_requests(fake_backend)
    assert payload["requirements"][0]["kind"] == "entity"
    assert payload["entities"][0]["name"] == "Ana"


async def test_a_fix_that_never_validates_is_an_error(make_runner, fake_backend):
    bad = '{"text": "The feline sleeps."}'
    fake_backend.script("sweep", bad, bad)
    spans = [_span("p1", "s1", "The feline sleeps.", [_violation_flag()])]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    assert result.changes == []
    assert result.spans[0].flags[0].status is FlagStatus.OPEN
    (error,) = result.errors
    assert error.stage == "sweep" and error.role == "sweep" and error.chunk_id == "c1"
    assert error.message.startswith("p1: ")
    assert "must contain 'cat'" in error.message


async def test_a_fix_cut_off_is_repaired(make_runner, fake_backend):
    fake_backend.script("sweep", '{"text": "The cat "}', '{"text": "The cat sleeps."}')
    spans = [_span("p1", "s1", "The feline sleeps.", [_violation_flag()])]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    assert result.spans[0].text == "The cat sleeps."
    assert "cut off" in fake_backend.requests[1].messages[3].content


async def test_backend_failure_is_an_error_not_a_crash(make_runner, fake_backend):
    fake_backend.script("sweep", PermanentBackendError("HTTP 400"))
    spans = [_span("p1", "s1", "The feline sleeps.")]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, SEGMENTS)
    assert result.changes == [] and len(result.errors) == 1


async def test_empty_spans_are_skipped(make_runner, fake_backend):
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, [_span("p1", "s1", "")], SEGMENTS)
    assert result.changes == [] and fake_backend.requests == []


def test_resolve_proposals():
    guide = Guide(
        summary="", style_guide="", glossary=[GlossaryEntry(term="Ville", rendering="City")]
    )
    proposals = [
        GlossaryEntry(term="mer", rendering="sea", notes="n1"),
        GlossaryEntry(term="Mer", rendering="ocean"),
        GlossaryEntry(term="mer ", rendering=" ocean "),
        GlossaryEntry(term="ville", rendering="town"),  # already in the guide
        GlossaryEntry(term="ciel", rendering="sky", locked=True),
        GlossaryEntry(term="  ", rendering="x"),
    ]
    resolved = resolve_proposals(guide, proposals)
    assert [(g.term, g.rendering, g.locked) for g in resolved] == [
        ("Mer", "ocean", False),
        ("ciel", "sky", False),
    ]
    assert resolved[0].notes == "Reviewers proposed different renderings: 'sea' (1), 'ocean' (2)."


def test_resolve_proposals_tie_keeps_the_first_seen():
    guide = Guide(summary="", style_guide="")
    resolved = resolve_proposals(
        guide,
        [GlossaryEntry(term="mer", rendering="sea"), GlossaryEntry(term="mer", rendering="ocean")],
    )
    assert resolved[0].rendering == "sea"


async def test_a_fix_that_breaks_another_locked_entry_is_rejected(make_runner, fake_backend):
    source_segments = make_segments("Le chat dort avec Ana.")
    bad = '{"text": "The cat sleeps with Ann."}'
    fake_backend.script("sweep", bad, bad)
    spans = [_span("p1", "s1", "The feline sleeps with Anna.", [_violation_flag()])]
    result = await run_sweep(_ctx(make_runner), PROFILE, LOCKED, spans, source_segments)
    assert result.changes == []
    assert result.spans[0].text == "The feline sleeps with Anna."
    assert result.spans[0].flags[0].status is FlagStatus.OPEN
    (error,) = result.errors
    assert "must contain 'Anna'" in error.message
