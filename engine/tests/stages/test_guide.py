import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coglossa.llm.types import ContextOverflowError, OutputTruncatedError, PermanentBackendError
from coglossa.schema import (
    Chunk,
    DocumentProfile,
    Entity,
    Genre,
    GlossaryEntry,
    Guide,
    PipelineProfile,
)
from coglossa.stages.context import StageContext, StageError
from coglossa.stages.guide import (
    apply_locked,
    carry_over,
    combine_partials,
    guide_slice,
    run_guide,
)
from tests.conftest import make_segments


def _profile(kind: PipelineProfile) -> DocumentProfile:
    return DocumentProfile(
        source_lang="fr",
        target_lang="en",
        mixed_language=False,
        genre=Genre.LITERARY_PROSE,
        tone_register="wry",
        word_count=0,
        pipeline_profile=kind,
    )


def _ctx(runner):
    return StageContext(cfg=runner.cfg, runner=runner, target_lang="en")


def _chunks(n_chunks: int, per_chunk: int = 1):
    segs = make_segments(*[f"Phrase {i} ici." for i in range(n_chunks * per_chunk)])
    chunks = [
        Chunk(id=f"c{c + 1}", segment_ids=[s.id for s in segs[c * per_chunk : (c + 1) * per_chunk]])
        for c in range(n_chunks)
    ]
    return segs, chunks


def _tasks(fake_backend) -> list[str]:
    return [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["task"]
        for r in fake_backend.requests
    ]


@pytest.mark.parametrize(
    ("kind", "depth"), [(PipelineProfile.SHORT, "light"), (PipelineProfile.STANDARD, "full")]
)
async def test_single_pass_depths(make_runner, fake_backend, kind, depth):
    segs, chunks = _chunks(2)
    guide, errors = await run_guide(_ctx(make_runner()), _profile(kind), segs, chunks, prior=None)
    assert errors == []
    assert guide.summary == "Synthetic summary of 6 words."
    payload = json.loads(fake_backend.requests[0].messages[1].content.split("INPUT_JSON:", 1)[1])
    assert (payload["task"], payload["depth"]) == ("guide", depth)


async def test_a_full_guide_cut_off_falls_back_to_a_light_guide(make_runner, fake_backend):
    fake_backend.script("guide", OutputTruncatedError("output truncated (finish_reason=length)"))
    segs, chunks = _chunks(2)
    guide, errors = await run_guide(
        _ctx(make_runner()), _profile(PipelineProfile.STANDARD), segs, chunks, prior=None
    )
    assert guide.summary == "Synthetic summary of 6 words."
    depths = [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["depth"]
        for r in fake_backend.requests
    ]
    assert depths == ["full", "light"]
    assert [(e.stage, e.role) for e in errors] == [("guide", "guide")]
    assert errors[0].message.startswith("the full guide could not be built (")
    assert errors[0].message.endswith("; a light guide was built instead")


async def test_a_light_guide_cut_off_translates_without_a_guide(make_runner, fake_backend):
    # CGL-14: a model that loops runs the light guide to the output limit too
    fake_backend.script("guide", OutputTruncatedError("cut"), OutputTruncatedError("cut"))
    segs, chunks = _chunks(2)
    guide, errors = await run_guide(
        _ctx(make_runner()), _profile(PipelineProfile.STANDARD), segs, chunks, prior=None
    )
    assert guide == Guide(summary="", style_guide="")
    assert [e.stage for e in errors] == ["guide", "guide"]
    assert errors[1].message == (
        "the translation guide could not be built (cut); translating without one"
    )


async def test_a_guide_that_cannot_be_built_keeps_the_earlier_one(make_runner, fake_backend):
    prior = Guide(
        summary="earlier",
        style_guide="",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    fake_backend.script("guide", "garbage", "garbage")
    segs, chunks = _chunks(1)
    guide, errors = await run_guide(
        _ctx(make_runner()), _profile(PipelineProfile.SHORT), segs, chunks, prior=prior
    )
    assert guide == prior
    (error,) = errors
    assert error.message.startswith("the translation guide could not be built (guide: invalid")
    assert error.message.endswith("; translating with the earlier guide")


async def test_a_backend_that_cannot_be_reached_still_fails_the_guide(make_runner, fake_backend):
    fake_backend.script("guide", PermanentBackendError("401 unauthorized"))
    segs, chunks = _chunks(1)
    with pytest.raises(StageError, match="guide failed: 401 unauthorized"):
        await run_guide(
            _ctx(make_runner()), _profile(PipelineProfile.SHORT), segs, chunks, prior=None
        )


async def test_standard_too_large_for_profiler_falls_back_to_map_reduce(make_runner, fake_backend):
    segs = make_segments(*["mot " * 5000 for _ in range(4)])  # ~100 KB of text
    chunks = [Chunk(id=f"c{i + 1}", segment_ids=[s.id]) for i, s in enumerate(segs)]
    await run_guide(
        _ctx(make_runner()), _profile(PipelineProfile.STANDARD), segs, chunks, prior=None
    )
    assert _tasks(fake_backend) == ["guide"] * 4 + ["guide_merge"]


async def test_long_form_map_reduce(make_runner, fake_backend):
    segs, chunks = _chunks(3)
    runner = make_runner()
    guide, _ = await run_guide(
        _ctx(runner), _profile(PipelineProfile.LONG_FORM), segs, chunks, prior=None
    )
    assert _tasks(fake_backend) == ["guide", "guide", "guide", "guide_merge"]
    assert guide.summary.count("Synthetic summary") == 3
    assert {r.role for r in runner.log.records} == {"guide"}


async def test_merge_is_hierarchical_when_partials_do_not_fit(
    make_config, make_runner, fake_backend
):
    cfg = make_config(chunking={"reserve_tokens": 32000})  # profiler budget: 768 tokens
    segs, chunks = _chunks(8)
    guide, _ = await run_guide(
        _ctx(make_runner(cfg)), _profile(PipelineProfile.LONG_FORM), segs, chunks, prior=None
    )
    assert _tasks(fake_backend).count("guide_merge") >= 3
    assert guide.summary.count("Synthetic summary") == 8


async def test_failed_partials_are_recorded_and_skipped(make_runner, fake_backend):
    fake_backend.script("guide", PermanentBackendError("boom"))
    segs, chunks = _chunks(3)
    guide, errors = await run_guide(
        _ctx(make_runner()), _profile(PipelineProfile.LONG_FORM), segs, chunks, prior=None
    )
    assert len(errors) == 1 and errors[0].stage == "guide" and "boom" in errors[0].message
    assert guide.summary.count("Synthetic summary") == 2


async def test_all_partials_failing_is_stage_error(make_runner, fake_backend):
    fake_backend.script("guide", *[PermanentBackendError("boom")] * 2)
    segs, chunks = _chunks(2)
    with pytest.raises(StageError, match="guide failed.*boom"):
        await run_guide(
            _ctx(make_runner()), _profile(PipelineProfile.LONG_FORM), segs, chunks, prior=None
        )


async def test_all_partials_cut_off_translates_without_a_guide(make_runner, fake_backend):
    fake_backend.script("guide", *[OutputTruncatedError("cut")] * 2)
    segs, chunks = _chunks(2)
    guide, errors = await run_guide(
        _ctx(make_runner()), _profile(PipelineProfile.LONG_FORM), segs, chunks, prior=None
    )
    assert guide == Guide(summary="", style_guide="")
    assert [e.chunk_id for e in errors] == ["c1", "c2", None]
    assert errors[2].message.endswith("; translating without one")


async def test_a_failed_merge_combines_the_partial_guides_as_they_are(make_runner, fake_backend):
    fake_backend.script("guide_merge", "garbage", "garbage")
    segs, chunks = _chunks(2)
    guide, errors = await run_guide(
        _ctx(make_runner()), _profile(PipelineProfile.LONG_FORM), segs, chunks, prior=None
    )
    assert guide.summary.count("Synthetic summary") == 2
    (error,) = errors
    assert error.message.startswith("the partial guides could not be merged (guide: invalid")
    assert error.message.endswith("; their entries were combined as they are")


def test_combine_partials_trims_keys():
    """ "Chat " and "chat" are one term, as everywhere entries are merged (CGL-28)."""
    first = Guide(
        summary="One.",
        style_guide="Terse.",
        glossary=[GlossaryEntry(term="Chat ", rendering="cat")],
        entities=[Entity(name=" Ana", rendering="Anna", kind="person")],
    )
    second = Guide(
        summary="Two.",
        style_guide="Wordy.",
        glossary=[GlossaryEntry(term="chat", rendering="kitty")],
        entities=[Entity(name="ana ", rendering="Annie", kind="person")],
    )
    combined = combine_partials([first, second])
    assert combined.glossary == first.glossary and combined.entities == first.entities


def test_combine_partials_keeps_the_first_of_each_term_and_name():
    first = Guide(
        summary="One.",
        style_guide="Terse.",
        glossary=[GlossaryEntry(term="Chat", rendering="cat")],
        entities=[Entity(name="Ana", rendering="Anna", kind="person")],
        cultural_references=["a saying"],
    )
    second = Guide(
        summary="Two.",
        style_guide="Wordy.",
        glossary=[
            GlossaryEntry(term="chat", rendering="kitty"),
            GlossaryEntry(term="pluie", rendering="rain"),
        ],
        entities=[Entity(name="ana", rendering="Annie", kind="person")],
        cultural_references=["a saying", "a song"],
    )
    combined = combine_partials([first, second])
    assert combined.summary == "One. Two." and combined.style_guide == "Terse."
    assert [(g.term, g.rendering) for g in combined.glossary] == [
        ("Chat", "cat"),
        ("pluie", "rain"),
    ]
    assert [e.rendering for e in combined.entities] == ["Anna"]
    assert combined.cultural_references == ["a saying", "a song"]


async def test_a_full_guide_that_is_invalid_falls_back_to_a_light_guide(make_runner, fake_backend):
    fake_backend.script("guide", "garbage", "garbage")
    segs, chunks = _chunks(2)
    guide, errors = await run_guide(
        _ctx(make_runner()), _profile(PipelineProfile.STANDARD), segs, chunks, prior=None
    )
    assert guide.summary == "Synthetic summary of 6 words."
    (error,) = errors
    assert error.message.startswith("the full guide could not be built (guide: invalid")


async def test_a_guide_too_large_for_the_model_translates_without_one(make_runner, fake_backend):
    fake_backend.script("guide", ContextOverflowError("context length exceeded"))
    segs, chunks = _chunks(1)
    guide, errors = await run_guide(
        _ctx(make_runner()), _profile(PipelineProfile.SHORT), segs, chunks, prior=None
    )
    assert guide == Guide(summary="", style_guide="") and "context length" in errors[0].message


async def test_partials_failing_partly_unreachable_is_stage_error(make_runner, fake_backend):
    fake_backend.script("guide", OutputTruncatedError("cut"), PermanentBackendError("401"))
    segs, chunks = _chunks(2)
    with pytest.raises(StageError, match="every excerpt failed"):
        await run_guide(
            _ctx(make_runner()), _profile(PipelineProfile.LONG_FORM), segs, chunks, prior=None
        )


async def test_an_empty_earlier_guide_is_not_called_an_earlier_guide(make_runner, fake_backend):
    fake_backend.script("guide", "garbage", "garbage")
    segs, chunks = _chunks(1)
    guide, errors = await run_guide(
        _ctx(make_runner()),
        _profile(PipelineProfile.SHORT),
        segs,
        chunks,
        prior=Guide(summary="", style_guide=""),
    )
    assert errors[0].message.endswith("; translating without one")


async def test_locked_prior_terms_win_and_engine_cannot_lock(make_runner, fake_backend):
    engine_guide = {
        "summary": "s",
        "style_guide": "g",
        "cultural_references": [],
        "entities": [],
        "glossary": [
            {"term": "GESTALT", "rendering": "shape", "locked": True},
            {"term": "Zeitgeist", "rendering": "spirit", "locked": True},
        ],
    }
    fake_backend.script("guide", json.dumps(engine_guide))
    prior = Guide(
        summary="old",
        style_guide="old",
        glossary=[
            GlossaryEntry(term="Gestalt", rendering="gestalt", locked=True),
            GlossaryEntry(term="Angst", rendering="dread"),
        ],
    )
    segs, chunks = _chunks(1)
    guide, _ = await run_guide(
        _ctx(make_runner()), _profile(PipelineProfile.SHORT), segs, chunks, prior=prior
    )
    assert [(g.term, g.rendering, g.locked) for g in guide.glossary] == [
        ("Gestalt", "gestalt", True),
        ("Zeitgeist", "spirit", False),
    ]
    payload = json.loads(fake_backend.requests[0].messages[1].content.split("INPUT_JSON:", 1)[1])
    assert payload["prior_guide"]["summary"] == "old"


def test_guide_slice_keeps_only_relevant_entries():
    guide = Guide(
        summary="s",
        style_guide="g",
        cultural_references=["c"],
        glossary=[
            GlossaryEntry(term="Gestalt", rendering="g"),
            GlossaryEntry(term="Angst", rendering="a"),
        ],
        entities=[
            Entity(name="Anna", rendering="Anna", kind="person"),
            Entity(name="Berlin", rendering="Berlin", kind="place"),
        ],
    )
    sliced = guide_slice(guide, "Anna felt a strange gestalt.")
    assert [g.term for g in sliced.glossary] == ["Gestalt"]
    assert [e.name for e in sliced.entities] == ["Anna"]
    assert (sliced.summary, sliced.style_guide, sliced.cultural_references) == ("s", "g", ["c"])


terms = st.text(alphabet="abcABC", min_size=1, max_size=3)


@given(st.lists(st.tuples(terms, st.booleans()), max_size=8), st.lists(terms, max_size=8))
def test_apply_locked_never_drops_or_duplicates_locked_terms(prior_terms, engine_terms):
    prior = Guide(
        summary="",
        style_guide="",
        glossary=[GlossaryEntry(term=t, rendering="p", locked=locked) for t, locked in prior_terms],
    )
    engine = Guide(
        summary="",
        style_guide="",
        glossary=[GlossaryEntry(term=t, rendering="e") for t in engine_terms],
    )
    merged = apply_locked(prior, engine)
    locked = [g for g in prior.glossary if g.locked]
    for entry in locked:
        assert entry in merged.glossary
    locked_keys = {g.term.casefold() for g in locked}
    for g in merged.glossary:
        if g.term.casefold() in locked_keys:
            assert g.locked
    assert apply_locked(None, engine) == engine


def test_carry_over_keeps_the_set_guides_entries_and_adds_new_ones():
    base = Guide(
        summary="first file",
        style_guide="first style",
        glossary=[GlossaryEntry(term="Chat", rendering="cat", notes="set", locked=True)],
        entities=[Entity(name="Anna", rendering="Anna", kind="person", gender="f")],
        cultural_references=["the cafe"],
    )
    draft = Guide(
        summary="second file",
        style_guide="second style",
        glossary=[
            GlossaryEntry(term="chat", rendering="kitty"),
            GlossaryEntry(term="chien", rendering="dog"),
        ],
        entities=[
            Entity(name="ANNA", rendering="Anne", kind="person"),
            Entity(name="Paris", rendering="Paris", kind="place"),
        ],
        cultural_references=["the cafe", "the metro"],
    )
    merged = carry_over(base, draft)
    assert merged.glossary == [base.glossary[0], draft.glossary[1]]
    assert merged.entities == [base.entities[0], draft.entities[1]]
    assert merged.cultural_references == ["the cafe", "the metro"]
    assert (merged.summary, merged.style_guide) == ("second file", "second style")


def test_carry_over_checks_keys_across_terms_and_names():
    """One file lists "Assembly" as a term, another as a name: the set guide's entry wins
    whichever list either is in, so the set keeps one rendering (CGL-28)."""
    base = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="Assembly", rendering="Versammlung")],
        entities=[Entity(name="Anna", rendering="Anna", kind="person")],
    )
    draft = Guide(
        summary="s",
        style_guide="g",
        glossary=[
            GlossaryEntry(term="anna", rendering="Anne"),
            GlossaryEntry(term="chien", rendering="dog"),
        ],
        entities=[
            Entity(name="ASSEMBLY", rendering="Assemblée", kind="organization"),
            Entity(name="Paris", rendering="Paris", kind="place"),
        ],
    )
    merged = carry_over(base, draft)
    assert merged.glossary == [base.glossary[0], draft.glossary[1]]
    assert merged.entities == [base.entities[0], draft.entities[1]]


def test_carry_over_trims_keys():
    """ "Anna " and "Anna" are one name, as the guide editor treats them (review._key)."""
    base = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat ", rendering="cat")],
        entities=[Entity(name="Anna", rendering="Anna", kind="person")],
    )
    draft = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term=" Chat", rendering="kitty")],
        entities=[Entity(name="Anna ", rendering="Anne", kind="person")],
    )
    merged = carry_over(base, draft)
    assert merged.glossary == base.glossary
    assert merged.entities == base.entities


def test_carry_over_never_brings_back_a_lock_the_set_guide_lacks():
    """Only people lock entries, and every lock lives on in the set guide: a locked entry the set
    guide lacks was removed by a person while the draft was built (final review)."""
    base = Guide(summary="s", style_guide="g")
    draft = Guide(
        summary="s",
        style_guide="g",
        glossary=[
            GlossaryEntry(term="chat", rendering="cat", locked=True),
            GlossaryEntry(term="chien", rendering="dog"),
        ],
        entities=[
            Entity(name="Anna", rendering="Anna", kind="person", locked=True),
            Entity(name="Paris", rendering="Paris", kind="place"),
        ],
    )
    merged = carry_over(base, draft)
    assert merged.glossary == [draft.glossary[1]]
    assert merged.entities == [draft.entities[1]]


def test_carry_over_keeps_the_set_guides_summary_and_style_when_the_draft_has_none():
    """A guide that could not be built leaves an empty draft (CGL-14): it must not blank the
    set guide's summary and style guide."""
    base = Guide(summary="the set", style_guide="British spelling")
    assert carry_over(base, Guide(summary=" ", style_guide="")) == base


def test_carry_over_without_a_set_guide_is_the_draft():
    draft = Guide(summary="s", style_guide="g", glossary=[GlossaryEntry(term="a", rendering="b")])
    assert carry_over(None, draft) == draft


@given(
    st.lists(st.tuples(terms, st.booleans()), max_size=8),
    st.lists(terms, max_size=8),
)
def test_carry_over_never_changes_or_drops_a_set_entry(base_terms, draft_terms):
    base = Guide(
        summary="",
        style_guide="",
        glossary=[GlossaryEntry(term=t, rendering="p", locked=locked) for t, locked in base_terms],
    )
    draft = Guide(
        summary="",
        style_guide="",
        glossary=[GlossaryEntry(term=t, rendering="e") for t in draft_terms],
    )
    merged = carry_over(base, draft)
    assert merged.glossary[: len(base.glossary)] == base.glossary
    known = {g.term.casefold() for g in base.glossary}
    added = merged.glossary[len(base.glossary) :]
    assert added == [g for g in draft.glossary if g.term.casefold() not in known]


async def test_no_excerpts_is_stage_error(make_runner, fake_backend):
    segs = make_segments("x")
    with pytest.raises(StageError, match="no excerpts"):
        await run_guide(
            _ctx(make_runner()), _profile(PipelineProfile.LONG_FORM), segs, [], prior=None
        )


def test_apply_locked_keeps_locked_entities_from_the_prior_guide():
    prior = Guide(
        summary="",
        style_guide="",
        entities=[
            Entity(name="Ana", rendering="Anna", kind="person", locked=True),
            Entity(name="Rio", rendering="Rio", kind="place"),
        ],
    )
    engine = Guide(
        summary="s",
        style_guide="g",
        entities=[
            Entity(name="ana", rendering="Ana", kind="person"),
            Entity(name="Lisboa", rendering="Lisbon", kind="place"),
        ],
    )
    merged = apply_locked(prior, engine)
    assert [(e.name, e.rendering, e.locked) for e in merged.entities] == [
        ("Ana", "Anna", True),
        ("Lisboa", "Lisbon", False),
    ]
