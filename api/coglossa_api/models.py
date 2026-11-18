"""Request bodies and action results of the REST API. Read models live in the engine
(coglossa.schema.views) because the control plane returns them."""

from typing import Annotated, Any, Literal, Self

from coglossa.people import TEAM_NAME_PATTERN
from coglossa.schema import Entity, GlossaryEntry, Guide, PipelineProfile, RunProgress
from coglossa.schema.views import LanguageCode
from coglossa.text import clean_text
from pydantic import BaseModel, BeforeValidator, Field, model_validator


def _trimmed(value: Any) -> Any:
    return clean_text(value).strip() if isinstance(value, str) else value


# Trimmed before the length checks, as the catalog does: "  name  " at the limit is fine.
ProjectName = Annotated[str, BeforeValidator(_trimmed), Field(min_length=1, max_length=200)]
TeamName = Annotated[str, BeforeValidator(_trimmed), Field(pattern=TEAM_NAME_PATTERN)]


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


class ApproveGuide(BaseModel):
    guide: Guide | None = None  # None: approve the guide as generated
    # with `guide` only: the version the edit started from, the run's draft (None) or the
    # project guide's; a project guide saved since refuses the approval (409)
    base_version: int | None = None


class SaveGuide(BaseModel):
    guide: Guide
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
    text: str
    reason: str | None = None  # default: "edited by a reviewer"
    base: BaseText = None


class UseCandidate(BaseModel):
    translator: str = Field(min_length=1)  # a translator name, e.g. "literal"
    base: BaseText = None


class FlagUpdate(BaseModel):
    status: Literal["accepted", "dismissed", "open"]  # "resolved" is set only by the sweep
    reason: str | None = None  # required to dismiss


class GuideAddition(BaseModel):
    """Exactly one of `glossary` (a term) or `entity` (a name)."""

    glossary: GlossaryEntry | None = None
    entity: Entity | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.glossary is None) == (self.entity is None):
            raise ValueError("send exactly one of glossary or entity")
        return self
