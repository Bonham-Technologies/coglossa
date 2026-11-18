"""Document profile: language, genre, register and pipeline profile."""

from enum import StrEnum

from pydantic import BaseModel, Field


class Genre(StrEnum):
    LITERARY_PROSE = "literary_prose"
    POETRY = "poetry"
    TECHNICAL = "technical"
    ACADEMIC = "academic"
    LEGAL = "legal"
    NEWS = "news"
    CORRESPONDENCE = "correspondence"
    SOCIAL_MEDIA = "social_media"
    TRANSCRIPT = "transcript"
    OTHER = "other"


class PipelineProfile(StrEnum):
    SHORT = "short"
    STANDARD = "standard"
    LONG_FORM = "long-form"


class ProfileAssessment(BaseModel):
    """What the profiler agent returns."""

    source_lang: str = Field(description="BCP-47 code of the dominant source language")
    mixed_language: bool = Field(description="True if substantial text is in another language")
    genre: Genre
    tone_register: str = Field(description="Short description of tone and register")


class DocumentProfile(BaseModel):
    source_lang: str
    target_lang: str
    mixed_language: bool
    genre: Genre
    tone_register: str
    word_count: int
    pipeline_profile: PipelineProfile
