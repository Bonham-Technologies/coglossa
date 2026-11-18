"""Problems with a whole file rather than one passage, in the plain words the app shows (CGL-80).

A run's errors are written for operators: model output, config hashes. The app gets one sentence
per stage instead; the errors themselves stay in the log and the report. Errors of the passage
stages that name a chunk are shown on their passages (an engine_error flag), so they are left out
here, except where nothing on a passage says so: the checks (the judge, the back translation, the
scorer) that failed on some passages get one sentence for them all (CGL-95)."""

from coglossa.schema import RunError
from coglossa.schema.views import RunProblem
from coglossa.stages.guide import LIGHT_INSTEAD, NOT_BUILT, NOT_MERGED, WITH_EARLIER

GUIDE_NOT_BUILT = (
    "The translation guide for this file couldn't be built, so names and terms may vary."
)
GUIDE_EARLIER = (
    "The translation guide couldn't be updated for this file, so the earlier one was used and "
    "new names and terms may vary."
)
GUIDE_LIGHT = (
    "Only a shorter translation guide could be built for this file, so some names and terms "
    "may vary."
)
GUIDE_PART = (
    "Part of the translation guide for this file couldn't be built, so some names and terms "
    "may vary."
)
CONFIG_CHANGED = "The settings changed while this file waited; the translation used the new ones."
SWEEP_UNFINISHED = "Some names and terms couldn't be brought in line with the translation guide."
GUIDE_CHANGED_AGAIN = (
    "The translation guide changed again while this file was being checked, so its latest change "
    "isn't applied yet. Recheck the file to apply it."
)  # also the run's own note when the sweep stops re-checking a guide that keeps changing (CGL-81)
SOMETHING_UNFINISHED = "Part of the work on this file didn't finish, so check it with care."
CHECK_UNFINISHED = "Some passages weren't fully checked, so mistakes in them may not be marked."

PASSAGE_STAGES = frozenset({"translate", "reconcile", "judge", "roundtrip", "score"})
# A passage's checks. Their errors name a role; a chunk that failed outright has a score error
# without one, and its passages carry an engine_error flag instead.
CHECK_STAGES = frozenset({"judge", "roundtrip", "score"})


def run_problems(errors: list[RunError]) -> list[RunProblem]:
    """One plain sentence for each stage with problems, in the order the stages ran. The checks
    that failed on some passages are one problem, under the chunk's last stage ("score")."""
    by_stage: dict[str, list[RunError]] = {}
    for e in errors:
        if e.stage in PASSAGE_STAGES and e.chunk_id is not None:
            if e.stage in CHECK_STAGES and e.role is not None:
                by_stage.setdefault("score", []).append(e)
            continue
        by_stage.setdefault(e.stage, []).append(e)
    return [
        RunProblem(stage=stage, text=_sentence(stage, found)) for stage, found in by_stage.items()
    ]


def _sentence(stage: str, errors: list[RunError]) -> str:
    if stage == "guide":
        return _guide_sentence(errors)
    if stage == "config":
        return CONFIG_CHANGED
    if stage == "score" and all(e.chunk_id is not None for e in errors):
        return CHECK_UNFINISHED
    if stage == "sweep":
        if all(e.message == GUIDE_CHANGED_AGAIN for e in errors):
            return GUIDE_CHANGED_AGAIN
        return SWEEP_UNFINISHED
    return SOMETHING_UNFINISHED


def _guide_sentence(errors: list[RunError]) -> str:
    """The worst of the guide's errors: none built, then a light one, then a part missing."""
    messages = [e.message for e in errors]
    if any(m.startswith(NOT_BUILT) for m in messages):
        earlier = all(WITH_EARLIER in m for m in messages if m.startswith(NOT_BUILT))
        return GUIDE_EARLIER if earlier else GUIDE_NOT_BUILT
    # a run-level note that is none of the known ones is the stage failing outright
    known = (LIGHT_INSTEAD, NOT_MERGED)
    if any(e.chunk_id is None and not any(k in e.message for k in known) for e in errors):
        return GUIDE_NOT_BUILT
    if any(LIGHT_INSTEAD in m for m in messages):
        return GUIDE_LIGHT
    return GUIDE_PART
