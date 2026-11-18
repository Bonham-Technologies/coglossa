import json

import pytest

from coglossa.config import StructuredOutput
from coglossa.llm.payload import render_input
from coglossa.llm.types import ChatMessage, ChatRequest, TransientBackendError
from coglossa.testing.fake_backend import FakeBackend


def _req(payload: dict) -> ChatRequest:
    return ChatRequest(
        model="fake-model",
        messages=[
            ChatMessage(role="system", content="s"),
            ChatMessage(role="user", content=render_input(payload)),
        ],
        schema_name="X",
        json_schema={},
        structured_output=StructuredOutput.JSON_SCHEMA,
    )


PROFILE = {"task": "profile", "sample": "x", "source_lang_hint": "fr", "target_lang": "en"}


async def test_answers_synthetically_and_records_requests():
    backend = FakeBackend()
    resp = await backend.chat(_req(PROFILE))
    assert json.loads(resp.content)["source_lang"] == "fr"
    assert len(backend.requests) == 1
    assert await backend.list_models() == ["fake-model"]


async def test_scripted_items_are_consumed_in_order_per_task():
    backend = FakeBackend()
    backend.script("profile", "not json", TransientBackendError("busy"))
    assert (await backend.chat(_req(PROFILE))).content == "not json"
    with pytest.raises(TransientBackendError):
        await backend.chat(_req(PROFILE))
    # script exhausted -> synthetic again
    assert json.loads((await backend.chat(_req(PROFILE))).content)["source_lang"] == "fr"


async def test_aclose_marks_closed():
    backend = FakeBackend()
    await backend.aclose()
    assert backend.closed is True
