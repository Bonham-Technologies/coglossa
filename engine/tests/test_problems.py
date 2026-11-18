"""CGL-80: problems with a whole file, in plain words, never the raw error text."""

from coglossa.problems import (
    CHECK_UNFINISHED,
    CONFIG_CHANGED,
    GUIDE_CHANGED_AGAIN,
    GUIDE_EARLIER,
    GUIDE_LIGHT,
    GUIDE_NOT_BUILT,
    GUIDE_PART,
    SOMETHING_UNFINISHED,
    SWEEP_UNFINISHED,
    run_problems,
)
from coglossa.schema import RunError
from coglossa.service import stage_errors
from coglossa.stages.chunk import ChunkOutcome
from coglossa.stages.guide import NOT_BUILT
from coglossa.store import runs
from coglossa.store.models import StageResult


def _guide(message: str, chunk_id: str | None = None) -> RunError:
    return RunError(stage="guide", role="guide", chunk_id=chunk_id, message=message)


def _texts(errors: list[RunError]) -> list[tuple[str, str]]:
    return [(p.stage, p.text) for p in run_problems(errors)]


def test_a_guide_that_was_not_built():
    not_built = _guide(f"{NOT_BUILT} (model output was cut off); translating without one")
    assert _texts([not_built]) == [("guide", GUIDE_NOT_BUILT)]
    assert GUIDE_NOT_BUILT == (
        "The translation guide for this file couldn't be built, so names and terms may vary."
    )
    earlier = _guide(f"{NOT_BUILT} (bad json); translating with the earlier guide")
    assert _texts([earlier]) == [("guide", GUIDE_EARLIER)]
    # a stage that failed outright, whatever its words
    assert _texts([_guide("guide failed: every excerpt failed (timeout)")]) == [
        ("guide", GUIDE_NOT_BUILT)
    ]


def test_a_light_or_partial_guide():
    light = _guide("the full guide could not be built (too long); a light guide was built instead")
    assert _texts([light]) == [("guide", GUIDE_LIGHT)]
    excerpt = _guide("model timed out after 3 attempts", chunk_id="c2")
    merge = _guide("the partial guides could not be merged (x); their entries were combined")
    assert _texts([excerpt]) == [("guide", GUIDE_PART)]
    assert _texts([merge]) == [("guide", GUIDE_PART)]
    # the worst of a stage's errors names it, once
    not_built = _guide(f"{NOT_BUILT} (bad json); translating without one")
    assert _texts([excerpt, not_built, merge]) == [("guide", GUIDE_NOT_BUILT)]


def test_the_settings_changed_and_hashes_stay_out():
    message = (
        "the worker's configuration (abc123) differs from the one this run was submitted "
        "with (def456); results reflect the worker's configuration"
    )
    problems = run_problems([RunError(stage="config", message=message)])
    assert [(p.stage, p.text) for p in problems] == [("config", CONFIG_CHANGED)]
    assert CONFIG_CHANGED == (
        "The settings changed while this file waited; the translation used the new ones."
    )
    assert "abc123" not in problems[0].model_dump_json()


def test_passage_errors_are_left_to_their_passages():
    errors = [
        RunError(stage="config", message="hashes"),
        RunError(stage="translate", chunk_id="c1", role="translator", message="timeout"),
        RunError(stage="reconcile", chunk_id="c1", role="reviewer", message="timeout"),
        _guide(f"{NOT_BUILT} (x); translating without one"),
        RunError(stage="sweep", chunk_id="c1", role="sweep", message="c1.2: bad json"),
        RunError(stage="sweep", chunk_id="c3", role="sweep", message="c3.1: bad json"),
        RunError(stage="mystery", message="Traceback: KeyError"),
    ]
    assert _texts(errors) == [
        ("config", CONFIG_CHANGED),
        ("guide", GUIDE_NOT_BUILT),
        ("sweep", SWEEP_UNFINISHED),
        ("mystery", SOMETHING_UNFINISHED),
    ]
    assert run_problems([]) == []


def test_passages_the_checks_missed_are_named_once():
    """CGL-95: a judge, back-translation or scorer failure on a passage leaves no flag on it, so
    the file says some passages weren't fully checked, once for all of them."""
    judge = RunError(stage="judge", chunk_id="c1", role="judge", message="c1.1..c1.3: bad json")
    roundtrip = RunError(stage="roundtrip", chunk_id="c2", role="roundtrip", message="timeout")
    scorer = RunError(stage="score", chunk_id="c3", role="scorer", message="scorer unavailable")
    assert _texts([judge]) == [("score", CHECK_UNFINISHED)]
    assert _texts([roundtrip]) == [("score", CHECK_UNFINISHED)]
    assert _texts([scorer]) == [("score", CHECK_UNFINISHED)]
    config = RunError(stage="config", message="hashes")
    assert _texts([config, judge, roundtrip, scorer]) == [
        ("config", CONFIG_CHANGED),
        ("score", CHECK_UNFINISHED),
    ]
    assert CHECK_UNFINISHED == (
        "Some passages weren't fully checked, so mistakes in them may not be marked."
    )


def test_a_failed_passage_is_left_to_its_flag():
    """A chunk that failed outright became engine_error passages: its flag says so, and once
    the flag is dealt with the file reads as finished (the score row's own error has no role)."""
    failed = RunError(stage="score", chunk_id="c1", message="internal error: KeyError")
    reconcile = RunError(stage="reconcile", chunk_id="c2", role="reviewer", message="timeout")
    translator = RunError(stage="translate", chunk_id="c3", role="translator:a", message="x")
    assert _texts([failed, reconcile, translator]) == []


def test_a_chunks_stored_errors_reach_the_problems():
    """The errors as the app reads them back from the chunks' score rows (service.stage_errors)."""
    scorer = RunError(stage="score", chunk_id="c1", role="scorer", message="scorer unavailable")
    checked = ChunkOutcome(chunk_id="c1", errors=[scorer]).model_dump(mode="json")
    finished = StageResult(stage="score", chunk_id="c1", status=runs.COMPLETE, output=checked)
    failed = StageResult(stage="score", chunk_id="c2", status=runs.FAILED, error="KeyError")
    assert _texts(stage_errors([finished, failed])) == [("score", CHECK_UNFINISHED)]
    assert _texts(stage_errors([failed])) == []


def test_the_sentences_use_no_engine_words():
    words = ("chunk", "span", "run ", "stage", "config", "hash", "sweep", "profile", "tier")
    for text in (
        GUIDE_NOT_BUILT,
        GUIDE_EARLIER,
        GUIDE_LIGHT,
        GUIDE_PART,
        CONFIG_CHANGED,
        SWEEP_UNFINISHED,
        GUIDE_CHANGED_AGAIN,
        SOMETHING_UNFINISHED,
        CHECK_UNFINISHED,
    ):
        assert not any(w in text.lower() for w in words), text


def test_a_guide_that_kept_changing_says_a_recheck_applies_it():
    """CGL-81: the sweep's own note is already plain; a sweep that also failed says so instead."""
    changed = RunError(stage="sweep", role="sweep", message=GUIDE_CHANGED_AGAIN)
    assert _texts([changed]) == [("sweep", GUIDE_CHANGED_AGAIN)]
    failed = RunError(stage="sweep", message="Traceback: KeyError")
    assert _texts([failed, changed]) == [("sweep", SWEEP_UNFINISHED)]
