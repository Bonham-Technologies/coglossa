import asyncio
import json
import time

import httpx
import pytest

from coglossa.config import StructuredOutput
from coglossa.llm.openai_compat import OpenAICompatBackend
from coglossa.llm.payload import render_input
from coglossa.llm.types import (
    ChatMessage,
    ChatRequest,
    ContextOverflowError,
    OutputTruncatedError,
    PermanentBackendError,
)
from coglossa.testing.fake_openai import FakeOpenAIServer


def _req(content: str) -> ChatRequest:
    return ChatRequest(
        model="fake-model",
        messages=[ChatMessage(role="user", content=content)],
        schema_name="X",
        json_schema={},
        structured_output=StructuredOutput.JSON_SCHEMA,
    )


async def test_chat_and_models_over_http():
    with FakeOpenAIServer(models=("m1", "m2")) as server:
        backend = OpenAICompatBackend("fake", server.base_url)
        payload = {
            "task": "translate",
            "target_lang": "de",
            "segments": [{"id": "s1", "text": "Hallo"}],
        }
        resp = await backend.chat(_req(render_input(payload)))
        assert json.loads(resp.content)["spans"][0]["text"] == "[de] Hallo"
        assert await backend.list_models() == ["m1", "m2"]
        assert server.requests[0]["model"] == "fake-model"
        await backend.aclose()


async def test_bad_requests_get_400_and_unknown_paths_404():
    with FakeOpenAIServer() as server:
        backend = OpenAICompatBackend("fake", server.base_url)
        with pytest.raises(PermanentBackendError, match="HTTP 400"):
            await backend.chat(_req("no marker here"))
        await backend.aclose()
        async with httpx.AsyncClient() as client:
            assert (await client.get(server.base_url + "/nope")).status_code == 404
            assert (await client.post(server.base_url + "/nope", json={})).status_code == 404


def test_start_stop_idempotent():
    server = FakeOpenAIServer().start()
    assert server.base_url.startswith("http://127.0.0.1:")
    server.stop()
    server.stop()


async def test_context_window_ping_probe_and_length_finish():
    with FakeOpenAIServer(context_window=100) as server:
        backend = OpenAICompatBackend("fake", server.base_url)
        ping = await backend.chat(_req(render_input({"task": "ping"})))
        assert json.loads(ping.content) == {"ok": True}
        with pytest.raises(ContextOverflowError, match="maximum context length"):
            await backend.chat(_req(render_input({"task": "probe", "filler": "a " * 200})))
        one = _req(render_input({"task": "probe"})).model_copy(update={"max_tokens": 1})
        with pytest.raises(OutputTruncatedError):
            await backend.chat(one)
        await backend.aclose()


def _post(url: str) -> None:
    payload = {"task": "translate", "target_lang": "de", "segments": [{"id": "s1", "text": "Hi"}]}
    httpx.post(
        f"{url}/chat/completions",
        json={
            "model": "fake-model",
            "messages": [{"role": "user", "content": render_input(payload)}],
        },
        timeout=10,
    ).raise_for_status()


def test_max_in_flight_counts_concurrent_requests():
    from concurrent.futures import ThreadPoolExecutor

    with FakeOpenAIServer(delay_s=0.3) as server:
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda _: _post(server.base_url), range(3)))
        assert server.max_in_flight == 3


def test_max_in_flight_is_one_for_sequential_requests():
    with FakeOpenAIServer(delay_s=0.01) as server:
        for _ in range(3):
            _post(server.base_url)
        assert server.max_in_flight == 1


def test_the_fake_openai_server_can_forget_requests():
    server = FakeOpenAIServer(keep_requests=False).start()
    try:
        payload = {
            "task": "translate",
            "target_lang": "de",
            "segments": [{"id": "s1", "text": "Hi"}],
        }
        body = {
            "model": "fake-model",
            "messages": [{"role": "user", "content": render_input(payload)}],
        }
        with httpx.Client() as client:
            assert client.post(f"{server.base_url}/chat/completions", json=body).status_code == 200
        assert server.requests == []
    finally:
        server.stop()


TRANSLATE = {"task": "translate", "target_lang": "de", "segments": [{"id": "s1", "text": "Hallo"}]}


async def test_answers_stream_with_usage():
    with FakeOpenAIServer() as server:
        backend = OpenAICompatBackend("fake", server.base_url)
        resp = await backend.chat(_req(render_input(TRANSLATE)))
        assert json.loads(resp.content)["spans"][0]["text"] == "[de] Hallo"
        assert (resp.tokens_in, resp.tokens_out) == (10, 10)
        assert server.requests[0]["stream"] is True
        await backend.chat(_req(render_input(TRANSLATE)))
        assert server.requests[1]["stream"] is True  # a server that streams is asked again
        await backend.aclose()


@pytest.mark.parametrize("streaming", ["ignore", "reject"])
async def test_a_server_that_cannot_stream_gets_todays_whole_answer_request(streaming):
    with FakeOpenAIServer(streaming=streaming) as server:
        backend = OpenAICompatBackend("fake", server.base_url)
        for _ in range(3):
            resp = await backend.chat(_req(render_input(TRANSLATE)))
            assert json.loads(resp.content)["spans"][0]["text"] == "[de] Hallo"
        asked = [r.get("stream") is True for r in server.requests]
        # once streamed, then (after a refusal, again for the same call) never again
        assert asked == (
            [True, False, False, False] if streaming == "reject" else [True, False, False]
        )
        await backend.aclose()


async def test_a_looping_model_is_stopped_long_before_its_output_limit():
    with FakeOpenAIServer(loop="ውሃ ንጹህ ነው። ") as server:
        backend = OpenAICompatBackend("fake", server.base_url, timeout_s=30)
        request = _req(render_input(TRANSLATE)).model_copy(update={"max_tokens": 16_384})
        started = time.monotonic()
        with pytest.raises(OutputTruncatedError, match="stopped early"):
            await backend.chat(request)
        assert time.monotonic() - started < 5  # 16,384 tokens would take 16 s or more here
        deadline = time.monotonic() + 5
        while server.in_flight and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert server.in_flight == 0  # the server noticed the hang-up and stopped
        assert server.loop_events < 16_384 / 20
        await backend.aclose()


@pytest.mark.parametrize("streaming", ["sse", "ignore"])
async def test_a_short_loop_runs_to_a_low_limit(streaming):
    # 50 tokens of "ha " is too short to be called a loop: the limit ends it, as before
    with FakeOpenAIServer(loop="ha ", streaming=streaming) as server:
        backend = OpenAICompatBackend("fake", server.base_url)
        request = _req(render_input(TRANSLATE)).model_copy(update={"max_tokens": 50})
        with pytest.raises(OutputTruncatedError, match="finish_reason=length"):
            await backend.chat(request)
        await backend.aclose()


def test_a_stream_sends_usage_only_when_asked():
    body = {
        "model": "fake-model",
        "stream": True,
        "messages": [{"role": "user", "content": render_input(TRANSLATE)}],
    }
    with FakeOpenAIServer() as server, httpx.Client() as client:
        plain = client.post(f"{server.base_url}/chat/completions", json=body).text
        asked = {**body, "stream_options": {"include_usage": True}}
        with_usage = client.post(f"{server.base_url}/chat/completions", json=asked).text
    assert plain.endswith("data: [DONE]\n\n") and '"usage"' not in plain
    assert '"usage"' in with_usage
