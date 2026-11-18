"""The translation guide: decisions every agent must respect."""

from pydantic import BaseModel, Field


class GlossaryEntry(BaseModel):
    term: str = Field(description="Source-language term exactly as it appears")
    rendering: str = Field(description="Target-language rendering to use")
    notes: str = ""
    locked: bool = False  # set only by humans; locked entries are enforced and never dropped


class Entity(BaseModel):
    name: str
    rendering: str
    kind: str = Field(description="person, place, organization, or other")
    gender: str | None = None
    forms_of_address: str = ""
    speech_style: str = ""
    locked: bool = False  # set only by humans; locked names are enforced like locked terms


class Guide(BaseModel):
    summary: str
    style_guide: str
    glossary: list[GlossaryEntry] = Field(default=[])
    entities: list[Entity] = Field(default=[])
    cultural_references: list[str] = Field(default=[])
