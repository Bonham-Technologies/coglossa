import pytest

from coglossa.schema import Entity, GlossaryEntry, Guide
from coglossa.text import clean_guide, clean_text


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("plain text", "plain text"),
        ("tab\tline\nreturn\r", "tab\tline\nreturn\r"),
        ("page\x0cbreak", "page\nbreak"),
        ("line\x0bbreak", "line\nbreak"),
        ("n\x00u\x01l\x08s", "nuls"),
        ("c0\x0e\x1f end", "c0 end"),
        ("non￾char￿s", "nonchars"),
        ("lone\ud800 \udfffsurrogates", "lone surrogates"),
        ("keeps é, رواية, 😀 and \x7f\x85", "keeps é, رواية, 😀 and \x7f\x85"),
    ],
)
def test_clean_text(raw, clean):
    assert clean_text(raw) == clean


def test_clean_guide_cleans_every_text_field():
    dirty = Guide(
        summary="A\x00 cat\x0csleeps.",
        style_guide="Plain\x01.",
        glossary=[
            GlossaryEntry(
                term="ch\x00at",
                rendering="cat\x0b",
                notes="n\x02",
                locked=True,
                forms=["ca\x00ts"],
                source_forms=["chats\x01"],
            )
        ],
        entities=[Entity(name="Ma\x00rie", kind="person", rendering="Marie\x0c", gender=None)],
        cultural_references=["Paris\x00"],
    )
    clean = clean_guide(dirty)
    assert clean.summary == "A cat\nsleeps."
    assert clean.style_guide == "Plain."
    assert clean.glossary[0].model_dump() == {
        "term": "chat",
        "rendering": "cat\n",
        "notes": "n",
        "locked": True,
        "forms": ["cats"],
        "source_forms": ["chats"],
    }
    assert (clean.entities[0].name, clean.entities[0].rendering) == ("Marie", "Marie\n")
    assert clean.entities[0].gender is None
    assert clean.cultural_references == ["Paris"]
    assert clean_guide(clean) == clean
