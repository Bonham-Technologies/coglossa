"""The review summary (CGL-112): how much post-editing a run, or one language of a set, took,
read from what the review view already stores (revisions, the feedback log and the flags).
Read-only: nothing here changes a threshold or teaches the engine anything."""

from pydantic import BaseModel, Field


class DismissReason(BaseModel):
    reason: str = Field(description="The reason a reviewer gave, as typed.")
    count: int = Field(description="How many flags of this code were dismissed with it.")


class FlagReviewFigures(BaseModel):
    """What became of one kind of flag."""

    code: str = Field(description="The flag's code, e.g. `terminology_violation`.")
    raised: int = Field(description="Flags of this code on the passages, whatever became of them.")
    passages: int = Field(description="Passages with at least one flag of this code.")
    open: int = Field(description="Flags still waiting for a decision.")
    accepted: int = Field(description="Flags a reviewer accepted (the passage is fine as it is).")
    dismissed: int = Field(description="Flags a reviewer dismissed, with a reason.")
    resolved: int = Field(description="Flags the consistency recheck fixed.")
    edited_after: int = Field(
        description="Passages with a flag of this code that a person edited after the flag "
        "was raised."
    )
    edited_after_share: float = Field(
        description="`edited_after / passages`, from 0 to 1: how often a flag of this code "
        "pointed at a passage a person then changed (its precision in real use). 0 when "
        "`passages` is 0."
    )
    dismiss_reasons: list[DismissReason] = Field(
        description="The reasons given for dismissing flags of this code, most frequent first "
        "(at most 20)."
    )


class ReviewFigures(BaseModel):
    """Post-editing effort over a set of passages. Every figure is a count or a share computed
    from counts: a passage nobody reviewed counts as 0, never as missing."""

    passages: int = Field(description="Passages in the translation.")
    checked: int = Field(
        description="Passages a person approved: accepted, edited, or chose another "
        "translator's version for, and has not reopened a flag on since."
    )
    checked_share: float = Field(description="`checked / passages`, from 0 to 1; 0 with none.")
    edited: int = Field(description="Passages a person changed the text of at least once.")
    edited_share: float = Field(description="`edited / passages`, from 0 to 1; 0 with none.")
    edit_distance_total: int = Field(
        description="The sum of the edited passages' edit distances. A passage's edit distance "
        "is the Levenshtein distance, in Unicode code points, between the engine's text (its "
        "last text before the first person's edit) and the text the person approved (or, for "
        "an edited passage reopened since, its current text): the fewest characters inserted, "
        "deleted or replaced to turn one into the other."
    )
    mean_edit_distance: float = Field(
        description="`edit_distance_total / edited`: characters changed per edited passage; 0 "
        "when none was edited."
    )
    mean_edit_ratio: float = Field(
        description="The mean, over the edited passages, of each passage's edit distance "
        "divided by the length of the longer of its two texts, from 0 (unchanged) to 1 "
        "(rewritten); 0 when none was edited."
    )
    estimated_distances: int = Field(
        description="Edited passages too long to compare character by character in time; "
        "their distance is an upper bound (the length of the longer differing middle part)."
    )
    flags: list[FlagReviewFigures] = Field(
        description="One entry per flag code raised, the most raised first."
    )


class RunReviewSummary(ReviewFigures):
    run_id: str
    filename: str
    target_lang: str


class LanguageReviewSummary(ReviewFigures):
    """One language of a set: the newest run of each file in that language, as the set page
    shows them."""

    target_lang: str
    run_ids: list[str] = Field(description="The runs counted: each file's newest in the language.")


class SetReviewSummary(BaseModel):
    project_id: str
    languages: list[LanguageReviewSummary] = Field(
        description="One entry per language of the set, in the set's order."
    )
