"""CGL-69: review and guide saves refuse over-long text with a plain-words 422."""

import pytest
from coglossa.schema import RunState
from coglossa.testing.seed import seed_run

PASSAGE = 20_000
SHORT = 2_000

GUIDE = {"summary": "A cat sleeps while it rains.", "style_guide": "Plain."}
TERM = {"term": "Fin", "rendering": "The End", "notes": ""}
NAME = {"name": "Minou", "rendering": "Kitty", "kind": "person", "gender": "female"}


def _refused(response, message: str) -> None:
    assert response.status_code == 422, response.text[:200]
    first = response.json()["detail"][0]
    assert (first["type"], first["msg"]) == ("too_long", message)


async def test_passage_text_and_reason_limits(api, db):
    seeded = await seed_run(db)
    url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[0]}/edit"
    at_limit = await api.client.post(url, json={"text": "a" * PASSAGE, "reason": "r" * SHORT})
    assert at_limit.status_code == 200 and len(at_limit.json()["text"]) == PASSAGE
    _refused(
        await api.client.post(url, json={"text": "b" * (PASSAGE + 1)}),
        "This passage is too long: keep it to 20,000 characters or fewer.",
    )
    _refused(
        await api.client.post(url, json={"text": "c", "reason": "r" * (SHORT + 1)}),
        "The reason is too long: keep it to 2,000 characters or fewer.",
    )
    assert (await api.client.get(url.removesuffix("/edit"))).json()["text"] == "a" * PASSAGE


async def test_flag_reason_limit(api, db):
    seeded = await seed_run(db)
    flag = seeded.flag_ids["p3:terminology_violation"]
    url = f"/api/runs/{seeded.run_id}/flags/{flag}"
    _refused(
        await api.client.post(url, json={"status": "dismissed", "reason": "r" * (SHORT + 1)}),
        "The reason is too long: keep it to 2,000 characters or fewer.",
    )
    dismissed = await api.client.post(url, json={"status": "dismissed", "reason": "r" * SHORT})
    assert dismissed.status_code == 200
    assert dismissed.json()["flags"][0]["dismiss_reason"] == "r" * SHORT


TERM_FIELDS = {
    "term": "A term is too long",
    "rendering": "A term's translation is too long",
    "notes": "The notes on a term are too long",
}
NAME_FIELDS = {
    "name": "A name is too long",
    "rendering": "A name's translation is too long",
    "kind": "A name's kind is too long",
    "gender": "A name's gender is too long",
    "forms_of_address": "A name's forms of address are too long",
    "speech_style": "A name's speech style is too long",
}
KEEP_SHORT = ": keep it to 2,000 characters or fewer."


def _additions():
    for field, words in TERM_FIELDS.items():
        yield "glossary", TERM, field, words
    for field, words in NAME_FIELDS.items():
        yield "entity", NAME, field, words


@pytest.mark.parametrize(("key", "entry", "field", "words"), list(_additions()))
async def test_add_to_guide_limits(api, db, key, entry, field, words):
    seeded = await seed_run(db)
    url = f"/api/runs/{seeded.run_id}/spans/{seeded.span_ids[2]}/guide"
    _refused(
        await api.client.post(url, json={key: {**entry, field: "x" * (SHORT + 1)}}),
        words + KEEP_SHORT,
    )
    added = await api.client.post(url, json={key: {**entry, field: "x" * SHORT}})
    assert added.status_code == 200, added.text[:200]


def _guides():
    for field, words in (("summary", "The summary is"), ("style_guide", "The style guide is")):
        yield (
            {field: "x" * PASSAGE},
            {field: "x" * (PASSAGE + 1)},
            f"{words} too long: keep it to 20,000 characters or fewer.",
        )
    for field, words in TERM_FIELDS.items():
        yield (
            {"glossary": [{**TERM, field: "x" * SHORT}]},
            {"glossary": [{**TERM, field: "x" * (SHORT + 1)}]},
            words + KEEP_SHORT,
        )
    for field, words in NAME_FIELDS.items():
        yield (
            {"entities": [{**NAME, field: "x" * SHORT}]},
            {"entities": [{**NAME, field: "x" * (SHORT + 1)}]},
            words + KEEP_SHORT,
        )
    yield (
        {"cultural_references": ["x" * SHORT]},
        {"cultural_references": ["ok", "x" * (SHORT + 1)]},
        "A cultural reference is too long" + KEEP_SHORT,
    )


@pytest.mark.parametrize(("fits", "too_long", "message"), list(_guides()))
async def test_project_guide_save_limits(api, db, fits, too_long, message):
    seeded = await seed_run(db)
    url = f"/api/projects/{seeded.project_id}/guide"
    _refused(
        await api.client.put(url, json={"guide": {**GUIDE, **too_long}, "base_version": 1}),
        message,
    )
    saved = await api.client.put(url, json={"guide": {**GUIDE, **fits}, "base_version": 1})
    assert saved.status_code == 200, saved.text[:200]


async def test_guide_approval_limits(api, db):
    seeded = await seed_run(db, state=RunState.AWAITING_GUIDE_APPROVAL)
    url = f"/api/runs/{seeded.run_id}/guide/approve"
    too_long = {**GUIDE, "glossary": [{**TERM, "rendering": "x" * (SHORT + 1)}]}
    _refused(
        await api.client.post(url, json={"guide": too_long, "base_version": 1}),
        "A term's translation is too long" + KEEP_SHORT,
    )
    fits = {**GUIDE, "summary": "x" * PASSAGE, "glossary": [{**TERM, "rendering": "x" * SHORT}]}
    approved = await api.client.post(url, json={"guide": fits, "base_version": 1})
    assert approved.status_code == 202, approved.text[:200]


async def test_approving_the_draft_unchanged_keeps_the_engine_as_author(api, db):
    """The editor sends the guide even when nothing changed: the limits' request model must not
    make the unchanged draft count as a person's edit."""
    seeded = await seed_run(db, state=RunState.AWAITING_GUIDE_APPROVAL)
    draft = (await api.client.get(f"/api/runs/{seeded.run_id}/guide")).json()
    approved = await api.client.post(
        f"/api/runs/{seeded.run_id}/guide/approve",
        json={"guide": draft["guide"], "base_version": draft["info"]["version"]},
    )
    assert approved.status_code == 202, approved.text[:200]
    view = (await api.client.get(f"/api/runs/{seeded.run_id}/guide")).json()
    assert (view["info"]["version"], view["info"]["author"]) == (2, "engine")
