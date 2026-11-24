import pytest
from jinja2 import UndefinedError

from coglossa.prompts.library import PromptLibrary, PromptNotFoundError

ALL_VARS = {
    "profile": {"target_lang": "en"},
    "guide": {"source_lang": "fr", "target_lang": "en", "depth": "full"},
    "guide_merge": {"source_lang": "fr", "target_lang": "en"},
    "literal": {"source_lang": "fr", "target_lang": "en", "genre": "poetry", "register": "x"},
    "contextual": {"source_lang": "fr", "target_lang": "en", "genre": "poetry", "register": "x"},
    "reconcile": {"source_lang": "fr", "target_lang": "en", "translators": ["literal", "ctx"]},
}


@pytest.mark.parametrize("prompt_id", sorted(ALL_VARS))
def test_builtin_templates_render(prompt_id):
    rendered = PromptLibrary().render(prompt_id, **ALL_VARS[prompt_id])
    assert rendered.id == prompt_id
    assert rendered.version == MARKER_PROMPTS.get(prompt_id, 1)
    assert "JSON" in rendered.text
    assert "{{" not in rendered.text and "{%" not in rendered.text


# CGL-108: the prompts that see formatting markers are at version 2 and ask for them back.
MARKER_PROMPTS = {"literal": 2, "contextual": 2, "reconcile": 2}


@pytest.mark.parametrize("prompt_id", sorted(MARKER_PROMPTS))
def test_translator_and_reviewer_prompts_ask_for_the_formatting_markers_back(prompt_id):
    text = PromptLibrary().render(prompt_id, **ALL_VARS[prompt_id]).text
    assert "<1>…</1>" in text
    assert "exactly once" in text and "never drop, add or renumber them" in text


# A model that types a plain " inside a JSON string under constrained decoding ends the string
# there: the translation is cut off (the M6 bench, s92 and s99). Every prompt whose output holds
# translated or quoted text asks for typographic quotation marks.
TEXT_PROMPTS = [
    ("literal", ALL_VARS["literal"]),
    ("contextual", ALL_VARS["contextual"]),
    ("reconcile", ALL_VARS["reconcile"]),
    ("sweep", {"source_lang": "fr", "target_lang": "en"}),
    ("back_translate", {"from_lang": "fr", "to_lang": "en"}),
    ("judge", {"source_lang": "fr", "target_lang": "en"}),
    ("guide", ALL_VARS["guide"]),
    ("guide_merge", ALL_VARS["guide_merge"]),
]


@pytest.mark.parametrize(("prompt_id", "variables"), TEXT_PROMPTS)
def test_text_prompts_ask_for_typographic_quotation_marks(prompt_id, variables):
    text = PromptLibrary().render(prompt_id, **variables).text
    assert 'never as the plain " character' in text


@pytest.mark.parametrize("depth", ["light", "full", "partial"])
def test_guide_depth_variants_differ(depth):
    text = PromptLibrary().render("guide", source_lang="fr", target_lang="en", depth=depth).text
    marker = {"light": "short document", "full": "full document", "partial": "one excerpt"}[depth]
    assert marker in text


def test_reconcile_lists_translators():
    text = PromptLibrary().render("reconcile", **ALL_VARS["reconcile"]).text
    assert "literal, ctx" in text


def test_missing_variable_fails_loudly():
    with pytest.raises(UndefinedError):
        PromptLibrary().render("literal", source_lang="fr")


def test_override_dir_wins_and_picks_highest_version(tmp_path):
    d = tmp_path / "literal"
    d.mkdir()
    (d / "v1.j2").write_text("old {{ target_lang }}", encoding="utf-8")
    (d / "v7.j2").write_text("custom into {{ target_lang }} JSON", encoding="utf-8")
    (d / "notes.txt").write_text("ignored", encoding="utf-8")
    lib = PromptLibrary(tmp_path)
    rendered = lib.render("literal", target_lang="de")
    assert (rendered.version, rendered.text) == (7, "custom into German (de) JSON")
    # prompts not overridden still come from the built-in templates
    assert lib.resolve("contextual")[0] == 2
    assert lib.versions(["literal", "profile"]) == {"literal": 7, "profile": 1}


@pytest.mark.parametrize("bad", ["nope", "../etc", "Literal", ""])
def test_unknown_or_unsafe_ids_raise(bad):
    with pytest.raises(PromptNotFoundError):
        PromptLibrary().resolve(bad)


@pytest.mark.parametrize("prompt_id", ["literal", "contextual", "reconcile"])
def test_templates_state_locked_entries_are_mandatory(prompt_id):
    text = PromptLibrary().render(prompt_id, **ALL_VARS[prompt_id]).text
    assert '"locked": true are mandatory renderings' in text


def test_judge_prompt_says_omissions_have_no_excerpt():
    text = PromptLibrary().render("judge", source_lang="fr", target_lang="en").text
    assert "For omissions, leave excerpt empty" in text
    assert "only for omissions" not in text


def test_language_variables_render_with_their_names():
    text = PromptLibrary().render("literal", **{**ALL_VARS["literal"], "target_lang": "ha"}).text
    assert "from French (fr) into Hausa (ha)" in text


def test_back_translate_languages_render_with_their_names():
    text = PromptLibrary().render("back_translate", from_lang="yo", to_lang="en").text
    assert "from Yoruba (yo) back into English (en)" in text


def test_other_variables_are_not_relabelled():
    text = PromptLibrary().render("literal", **{**ALL_VARS["literal"], "genre": "es"}).text
    assert "Spanish" not in text
