"""The translation guide: decisions every agent must respect."""

from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field

from coglossa.text import entry_key


def tidy_forms(forms: list[str]) -> list[str]:
    """Each form trimmed, blanks dropped, and a form repeated (ignoring case) kept once."""
    seen: set[str] = set()
    kept: list[str] = []
    for form in forms:
        form = form.strip()
        if form and entry_key(form) not in seen:
            seen.add(entry_key(form))
            kept.append(form)
    return kept


# CGL-109: in inflected languages the right translation often uses a case or plural form of the
# rendering, and the source text an inflected form of the term. People list those forms; nothing
# infers them. Guides made before have none.
Forms = Annotated[list[str], AfterValidator(tidy_forms)]
_FORMS = "Other accepted forms of the rendering, such as its plural or case forms"
_SOURCE_FORMS = "Other forms of the source words, such as plural or case forms, it also covers"
_FORM_FIELDS = ("forms", "source_forms")


class GlossaryEntry(BaseModel):
    term: str = Field(description="Source-language term exactly as it appears")
    rendering: str = Field(description="Target-language rendering to use")
    notes: str = ""
    locked: bool = False  # set only by humans; locked entries are enforced and never dropped
    forms: Forms = Field(default=[], description=_FORMS)
    source_forms: Forms = Field(default=[], description=_SOURCE_FORMS)

    @property
    def source_words(self) -> list[str]:
        """The term and its source forms: the entry applies where any of them is found."""
        return [self.term, *self.source_forms]

    @property
    def target_words(self) -> list[str]:
        """The rendering and its accepted forms: the entry holds where any of them is found."""
        return [self.rendering, *self.forms]


class Entity(BaseModel):
    name: str
    rendering: str
    kind: str = Field(description="person, place, organization, or other")
    gender: str | None = None
    forms_of_address: str = ""
    speech_style: str = ""
    locked: bool = False  # set only by humans; locked names are enforced like locked terms
    forms: Forms = Field(default=[], description=_FORMS)
    source_forms: Forms = Field(default=[], description=_SOURCE_FORMS)

    @property
    def source_words(self) -> list[str]:
        return [self.name, *self.source_forms]

    @property
    def target_words(self) -> list[str]:
        return [self.rendering, *self.forms]


def prompt_entry(entry: GlossaryEntry | Entity) -> dict[str, Any]:
    """An entry as the agents see it: without empty form lists, so a guide without forms reads
    to them exactly as before."""
    dumped = entry.model_dump()
    for name in _FORM_FIELDS:
        if not dumped[name]:
            del dumped[name]
    return dumped


class Guide(BaseModel):
    summary: str
    style_guide: str
    glossary: list[GlossaryEntry] = Field(default=[])
    entities: list[Entity] = Field(default=[])
    cultural_references: list[str] = Field(default=[])

    def prompt_dump(self) -> dict[str, Any]:
        """The guide as the agents see it (prompt_entry)."""
        dumped = self.model_dump()
        dumped["glossary"] = [prompt_entry(g) for g in self.glossary]
        dumped["entities"] = [prompt_entry(e) for e in self.entities]
        return dumped
