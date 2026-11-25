"""Request bodies and action results of the REST API. Read models live in the engine
(coglossa.schema.views) because the control plane returns them."""

from typing import Annotated, Any, Literal, Self

from coglossa.people import TEAM_NAME_PATTERN
from coglossa.schema import Entity, GlossaryEntry, Guide, PipelineProfile, RunProgress
from coglossa.schema.views import LanguageCode
from coglossa.text import clean_text
from pydantic import BaseModel, BeforeValidator, Field, model_validator
from pydantic_core import PydanticCustomError


def _trimmed(value: Any) -> Any:
    return clean_text(value).strip() if isinstance(value, str) else value


# Trimmed before the length checks, as the catalog does: "  name  " at the limit is fine.
ProjectName = Annotated[str, BeforeValidator(_trimmed), Field(min_length=1, max_length=200)]
TeamName = Annotated[str, BeforeValidator(_trimmed), Field(pattern=TEAM_NAME_PATTERN)]

# CGL-69: how much one review or guide save may store. Longer text is refused (422) with the
# error type "too_long" and a message the web app shows as it is.
PASSAGE_MAX = 20_000  # a passage's text; a guide's summary or style guide
SHORT_MAX = 2_000  # a reason; each field of a guide entry; a cultural reference


def _limited(limit: int, subject: str) -> Any:
    """A string of at most `limit` characters. A longer one is refused with "<subject> too long:
    …" (the subject brings its verb: "A term is"), checked before Field's max_length so that the
    plain message, not pydantic's, is the one sent."""
    message = f"{subject} too long: keep it to {limit:,} characters or fewer."

    def check(value: Any) -> Any:
        if isinstance(value, str) and len(value) > limit:
            raise PydanticCustomError("too_long", message, {"max_length": limit})
        return value

    return Annotated[str, BeforeValidator(check), Field(max_length=limit)]


Reason = _limited(SHORT_MAX, "The reason is") | None


class ErrorBody(BaseModel):
    """What an error answers (coglossa_api.errors): one line, plus a code for some conflicts."""

    detail: str
    code: str | None = None
    current_text: str | None = None  # code "passage_changed": what the passage says now


class CreateProject(BaseModel):
    name: ProjectName
    target_lang: LanguageCode
    source_lang: LanguageCode | None = None  # None: detected per document
    pipeline_profile: PipelineProfile | None = None  # None: chosen per document
    team: TeamName | None = None  # CGL-26: None: personal to the person who makes it


class UpdateProject(BaseModel):
    """Rename a set and/or (admins) move it to another team; at least one field."""

    name: ProjectName | None = None
    team: TeamName | None = None  # None, sent explicitly: personal to its creator

    @model_validator(mode="after")
    def _something(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("send a name or a team")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("a project needs a name: leave name out to keep it")
        return self


class RunRequest(BaseModel):
    """A new run of a document; the fields left out take the project's defaults."""

    target_lang: LanguageCode | None = None
    source_lang: LanguageCode | None = None
    pipeline_profile: PipelineProfile | None = None
    checkpoint: bool | None = None  # None: the profile's default
    use_project_guide: bool = False


class AddLanguage(BaseModel):
    """Translate every file of a set into another language (CGL-24)."""

    target_lang: LanguageCode


class ActionResult(BaseModel):
    message: str
    progress: RunProgress


class GlossaryEntryEdit(GlossaryEntry):
    """A term as a reviewer saves it: each field at most 2,000 characters."""

    term: _limited(SHORT_MAX, "A term is") = Field(
        description=GlossaryEntry.model_fields["term"].description
    )
    rendering: _limited(SHORT_MAX, "A term's translation is") = Field(
        description=GlossaryEntry.model_fields["rendering"].description
    )
    notes: _limited(SHORT_MAX, "The notes on a term are") = ""
    forms: list[_limited(SHORT_MAX, "A form of a term's translation is")] = Field(
        default=[], description=GlossaryEntry.model_fields["forms"].description
    )
    source_forms: list[_limited(SHORT_MAX, "A form of a term is")] = Field(
        default=[], description=GlossaryEntry.model_fields["source_forms"].description
    )


class EntityEdit(Entity):
    """A name as a reviewer saves it: each field at most 2,000 characters."""

    name: _limited(SHORT_MAX, "A name is")
    rendering: _limited(SHORT_MAX, "A name's translation is")
    kind: _limited(SHORT_MAX, "A name's kind is") = Field(
        description=Entity.model_fields["kind"].description
    )
    gender: _limited(SHORT_MAX, "A name's gender is") | None = None
    forms_of_address: _limited(SHORT_MAX, "A name's forms of address are") = ""
    speech_style: _limited(SHORT_MAX, "A name's speech style is") = ""
    forms: list[_limited(SHORT_MAX, "A form of a name's translation is")] = Field(
        default=[], description=Entity.model_fields["forms"].description
    )
    source_forms: list[_limited(SHORT_MAX, "A form of a name is")] = Field(
        default=[], description=Entity.model_fields["source_forms"].description
    )


class GuideEdit(Guide):
    """A guide as a reviewer saves or approves it: the summary and the style guide at most
    20,000 characters each (a generated guide's can run to several paragraphs), each cultural
    reference at most 2,000."""

    summary: _limited(PASSAGE_MAX, "The summary is")
    style_guide: _limited(PASSAGE_MAX, "The style guide is")
    glossary: list[GlossaryEntryEdit] = Field(default=[])
    entities: list[EntityEdit] = Field(default=[])
    cultural_references: list[_limited(SHORT_MAX, "A cultural reference is")] = Field(default=[])

    def as_guide(self) -> Guide:
        """The engine's Guide, for the engine: pydantic models of different classes never compare
        equal, and approval compares the guide sent with the draft to tell an edit from none."""
        return Guide.model_validate(self.model_dump())


class ApproveGuide(BaseModel):
    guide: GuideEdit | None = None  # None: approve the guide as generated
    # with `guide` only: the version the edit started from, the run's draft (None) or the
    # project guide's; a project guide saved since refuses the approval (409)
    base_version: int | None = None


class SaveGuide(BaseModel):
    guide: GuideEdit
    base_version: int | None  # the version the edit started from; None for a first guide


# CGL-36: the passage text the reviewer started from; omitted, the change is not checked.
BaseText = Annotated[
    str | None,
    Field(
        description="The passage text the change started from; if it is no longer current, "
        "the change is refused with 409 `passage_changed`."
    ),
]


class EditSpan(BaseModel):
    text: _limited(PASSAGE_MAX, "This passage is")
    reason: Reason = None  # default: "edited by a reviewer"
    base: BaseText = None


class UseCandidate(BaseModel):
    translator: str = Field(min_length=1)  # a translator name, e.g. "literal"
    base: BaseText = None


class FlagUpdate(BaseModel):
    status: Literal["accepted", "dismissed", "open"]  # "resolved" is set only by the sweep
    reason: Reason = None  # required to dismiss


class AcceptForm(BaseModel):
    """CGL-109 "This form is fine": the passage uses `form` of the translation of `term`, one of
    the terms or names its terminology flag names."""

    term: _limited(SHORT_MAX, "A term is") = Field(min_length=1)
    form: _limited(SHORT_MAX, "A form is") = Field(min_length=1)


class GuideAddition(BaseModel):
    """Exactly one of `glossary` (a term) or `entity` (a name)."""

    glossary: GlossaryEntryEdit | None = None
    entity: EntityEdit | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.glossary is None) == (self.entity is None):
            raise ValueError("send exactly one of glossary or entity")
        return self
