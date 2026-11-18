import asyncio
import json

import httpx
import pytest

from coglossa.config import StructuredOutput
from coglossa.llm.openai_compat import OpenAICompatBackend
from coglossa.llm.types import (
    ChatMessage,
    ChatRequest,
    ContextOverflowError,
    OutputTruncatedError,
    PermanentBackendError,
    TransientBackendError,
)


def _req(mode: StructuredOutput = StructuredOutput.JSON_SCHEMA, max_tokens=None) -> ChatRequest:
    return ChatRequest(
        model="m1",
        messages=[ChatMessage(role="user", content="hi")],
        schema_name="Out",
        json_schema={"type": "object"},
        structured_output=mode,
        max_tokens=max_tokens,
    )


def _ok(content="{}", finish="stop", usage=True) -> httpx.Response:
    body = {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}]
    }
    if usage:
        body["usage"] = {"prompt_tokens": 5, "completion_tokens": 7}
    return httpx.Response(200, json=body)


def _backend(handler, **kw) -> OpenAICompatBackend:
    return OpenAICompatBackend("gpu-a", "http://x/v1", transport=httpx.MockTransport(handler), **kw)


async def test_chat_posts_openai_payload_with_json_schema():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _ok('{"a": 1}')

    backend = _backend(handler, api_key="k")
    resp = await backend.chat(_req(max_tokens=64))
    assert seen["url"] == "http://x/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Out", "schema": {"type": "object"}},
    }
    assert seen["body"]["max_tokens"] == 64
    assert resp.content == '{"a": 1}'
    assert (resp.tokens_in, resp.tokens_out) == (5, 7)
    await backend.aclose()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(StructuredOutput.JSON_OBJECT, {"type": "json_object"}), (StructuredOutput.NONE, None)],
)
async def test_chat_response_format_modes(mode, expected):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _ok(usage=False)

    resp = await _backend(handler).chat(_req(mode))
    assert seen["body"].get("response_format") == expected
    assert "max_tokens" not in seen["body"]
    assert resp.tokens_in is None


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_retryable_statuses_are_transient(status):
    backend = _backend(lambda r: httpx.Response(status, text="busy"))
    with pytest.raises(TransientBackendError, match=f"HTTP {status}"):
        await backend.chat(_req())


async def test_context_overflow_is_detected():
    msg = {"error": {"message": "This model's maximum context length is 8192 tokens"}}
    backend = _backend(lambda r: httpx.Response(400, json=msg))
    with pytest.raises(ContextOverflowError):
        await backend.chat(_req())


async def test_other_client_errors_are_permanent():
    backend = _backend(lambda r: httpx.Response(404, text="model not found"))
    with pytest.raises(PermanentBackendError, match="HTTP 404"):
        await backend.chat(_req())


@pytest.mark.parametrize("streamed", [True, False])
async def test_timeout_and_connection_errors_are_transient(streamed):
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TransientBackendError, match="timeout"):
        await _backend(timeout, stream=streamed).chat(_req())
    with pytest.raises(TransientBackendError, match="ConnectError"):
        await _backend(refused, stream=streamed).chat(_req())


class _Trickle(httpx.AsyncByteStream):
    """A response body that sends one byte every `every_s`: each read is quick, the whole is not."""

    def __init__(self, body: bytes, every_s: float) -> None:
        self.body, self.every_s, self.closed = body, every_s, False

    async def __aiter__(self):
        for i in range(len(self.body)):
            await asyncio.sleep(self.every_s)
            yield self.body[i : i + 1]

    async def aclose(self) -> None:
        self.closed = True


def _trickled_ok(every_s: float = 0.02, padding: int = 100) -> tuple[httpx.Response, _Trickle]:
    body = json.dumps({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})
    stream = _Trickle(b" " * padding + body.encode(), every_s)
    return httpx.Response(200, headers={"content-type": "application/json"}, stream=stream), stream


@pytest.mark.parametrize("streamed", [True, False])
async def test_timeout_bounds_the_whole_call_not_each_read(streamed):
    # CGL-55: one OpenRouter call took 1,362 s with timeout_s 300, because httpx's timeout bounds
    # each read. Here every byte comes within 0.02 s, and the whole body would take seconds.
    response, stream = _trickled_ok()
    backend = _backend(lambda request: response, timeout_s=0.3, stream=streamed)
    started = asyncio.get_running_loop().time()
    with pytest.raises(TransientBackendError, match="timeout calling chat/completions"):
        await backend.chat(_req())
    elapsed = asyncio.get_running_loop().time() - started
    # the whole body takes 3.4 s or more; the upper bound leaves room for a loaded CI box
    assert 0.25 <= elapsed < 1.5
    assert stream.closed  # the response is closed, not left open on the connection
    await backend.aclose()


@pytest.mark.parametrize("streamed", [True, False])
async def test_a_callers_own_timeout_or_cancel_is_not_turned_into_a_retryable_error(streamed):
    # Only the backend's own deadline becomes TransientBackendError; a caller's shorter
    # timeout, or cancelling the task, must reach the caller as it is and never be retried.
    backend = _backend(lambda request: _trickled_ok()[0], timeout_s=5.0, stream=streamed)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await backend.chat(_req())

    task = asyncio.create_task(backend.chat(_req()))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await backend.aclose()


async def test_a_call_that_ran_out_of_time_is_retried():
    from coglossa.llm.retry import RetryPolicy, call_with_retry

    responses = [_trickled_ok()[0], _ok('{"a": 1}')]
    backend = _backend(lambda request: responses.pop(0), timeout_s=0.3)
    resp = await call_with_retry(
        lambda: backend.chat(_req()), RetryPolicy(max_attempts=2, base_delay_s=0.0)
    )
    assert resp.content == '{"a": 1}' and responses == []
    await backend.aclose()


async def test_truncated_output_raises():
    with pytest.raises(OutputTruncatedError):
        await _backend(lambda r: _ok(finish="length")).chat(_req())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    ],
)
async def test_malformed_responses_are_permanent(response):
    with pytest.raises(PermanentBackendError):
        await _backend(lambda r: response).chat(_req())


async def test_list_models():
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})

    assert await _backend(handler).list_models() == ["a", "b"]


async def test_list_models_malformed_and_errors():
    with pytest.raises(PermanentBackendError):
        await _backend(lambda r: httpx.Response(200, json={"nope": 1})).list_models()
    with pytest.raises(TransientBackendError):
        await _backend(lambda r: httpx.Response(502, text="bad gateway")).list_models()


async def test_concurrency_is_limited():
    active = 0
    peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _ok()

    backend = _backend(handler, max_concurrency=2)
    await asyncio.gather(*(backend.chat(_req()) for _ in range(6)))
    assert peak == 2


@pytest.mark.parametrize(
    "usage",
    [
        "oops",
        ["x"],
        {"prompt_tokens": "5", "completion_tokens": None},
        {"prompt_tokens": True, "completion_tokens": 1.5},
    ],
)
async def test_malformed_usage_becomes_none(usage):
    body = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": usage}
    resp = await _backend(lambda r: httpx.Response(200, json=body)).chat(_req())
    assert resp.tokens_in is None and resp.tokens_out is None


def test_payload_merges_extra_body_but_never_overrides_own_keys():
    from coglossa.llm.openai_compat import _payload

    req = _req(max_tokens=64).model_copy(
        update={"extra_body": {"reasoning": {"enabled": False}, "temperature": 9, "stream": True}}
    )
    body = _payload(req)
    assert "stream" not in body
    bare = _payload(
        _req().model_copy(
            update={
                "structured_output": StructuredOutput.NONE,
                "extra_body": {"max_tokens": 5, "response_format": 1},
            }
        )
    )
    assert "max_tokens" not in bare and "response_format" not in bare
    assert body["reasoning"] == {"enabled": False}
    assert body["temperature"] == req.temperature
    assert body["model"] == "m1"


# --- streaming (CGL-71) ------------------------------------------------------------------------


def _event(obj) -> bytes:
    return b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n"


def _delta(text=None, finish=None, **delta) -> bytes:
    if text is not None:
        delta["content"] = text
    return _event({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


def _sse_body(pieces, *, finish="stop", usage=True, done=True) -> bytes:
    body = _delta(role="assistant", text="") + b"".join(_delta(p) for p in pieces)
    body += _delta(finish=finish)
    if usage:
        body += _event({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 9}})
    if done:
        body += b"data: [DONE]\n\n"
    return body


def _sse(body: bytes | httpx.AsyncByteStream) -> httpx.Response:
    headers = {"content-type": "text/event-stream"}
    if isinstance(body, bytes):
        return httpx.Response(200, headers=headers, content=body)
    return httpx.Response(200, headers=headers, stream=body)


class _Endless(httpx.AsyncByteStream):
    """A server stuck in a loop: the same phrase, one event per token, up to the output limit."""

    def __init__(self, phrase: str, limit: int = 16_384, field: str = "content") -> None:
        self.phrase, self.limit, self.field = phrase, limit, field
        self.sent, self.closed = 0, False

    async def __aiter__(self):
        yield _delta(role="assistant", text="")
        while self.sent < self.limit:
            self.sent += 1
            yield _delta(**{self.field: self.phrase})
        yield _delta(finish="length")
        yield b"data: [DONE]\n\n"

    async def aclose(self) -> None:
        self.closed = True


async def test_chat_streams_and_asks_for_usage():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return _sse(_sse_body(['{"a"', ": 1", "}"]))

    backend = _backend(handler)
    resp = await backend.chat(_req(max_tokens=64))
    assert resp.content == '{"a": 1}'
    assert (resp.tokens_in, resp.tokens_out) == (5, 9)
    [body] = seen
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 64
    await backend.aclose()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (
            StructuredOutput.JSON_SCHEMA,
            {"type": "json_schema", "json_schema": {"name": "Out", "schema": {"type": "object"}}},
        ),
        (StructuredOutput.JSON_OBJECT, {"type": "json_object"}),
    ],
)
async def test_constrained_decoding_is_kept_when_streaming(mode, expected):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return _sse(_sse_body(["{}"]))

    resp = await _backend(handler).chat(_req(mode))
    assert resp.content == "{}"
    assert seen[0]["response_format"] == expected and seen[0]["stream"] is True


async def test_a_stream_without_usage_has_no_token_counts():
    resp = await _backend(lambda r: _sse(_sse_body(["{}"], usage=False))).chat(_req())
    assert resp.content == "{}"
    assert resp.tokens_in is None and resp.tokens_out is None


async def test_a_stream_ignores_comments_reasoning_and_other_fields():
    body = (
        b": OPENROUTER PROCESSING\n\n"
        + b"event: message\n"
        + _delta(reasoning_content="Let me think. ")
        + _delta(reasoning="Still thinking. ")
        + _delta("{}")
        + _delta(finish="stop")
        + _event({"choices": [], "usage": "oops"})
        + b"data: [DONE]\n\n"
    )
    resp = await _backend(lambda r: _sse(body)).chat(_req())
    assert resp.content == "{}" and resp.tokens_in is None


@pytest.mark.parametrize(
    "body",
    [
        # the whole answer left in the reasoning, as a whole reply with "content": null
        _delta(role="assistant") + _delta(reasoning_content="{}") + _delta(finish="stop"),
        _event({"choices": []}) + b"data: [DONE]\n\n",
    ],
)
async def test_a_stream_without_any_content_is_permanent_like_a_whole_reply(body):
    with pytest.raises(PermanentBackendError, match="no text content"):
        await _backend(lambda r: _sse(body)).chat(_req())


async def test_a_streamed_answer_cut_off_at_the_limit_raises_truncated():
    with pytest.raises(OutputTruncatedError, match="finish_reason=length"):
        await _backend(lambda r: _sse(_sse_body(['{"a": '], finish="length"))).chat(_req())


async def test_a_stream_that_ends_before_the_answer_finished_is_transient():
    body = _delta(role="assistant", text="") + _delta('{"a": ')
    with pytest.raises(TransientBackendError, match="ended before"):
        await _backend(lambda r: _sse(body)).chat(_req())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"message": "engine crashed", "code": 500}, TransientBackendError),
        ({"message": "bad grammar"}, PermanentBackendError),
        ({"message": "maximum context length is 8192 tokens", "code": 400}, ContextOverflowError),
    ],
)
async def test_an_error_inside_the_stream(error, expected):
    body = _delta(role="assistant", text="") + _event({"error": error})
    with pytest.raises(expected):
        await _backend(lambda r: _sse(body)).chat(_req())


@pytest.mark.parametrize("data", [b"data: not json\n\n", b"data: [1, 2]\n\n"])
async def test_a_malformed_stream_is_permanent(data):
    with pytest.raises(PermanentBackendError, match="malformed"):
        await _backend(lambda r: _sse(_delta(role="assistant") + data)).chat(_req())


@pytest.mark.parametrize(
    ("phrase", "field"),
    [
        ("'Bottled water' is 'omi ti a paapaa' is not right. ", "content"),
        ("ውሃ ንጹህ ነው። ", "content"),
        ("瓶装水不对。", "content"),
        ("Wait, let me check the term again. ", "reasoning_content"),
    ],
)
async def test_a_loop_is_stopped_long_before_the_output_limit(phrase, field):
    from coglossa.llm.types import RepetitionLoopError

    stream = _Endless(phrase, field=field)
    backend = _backend(lambda request: _sse(stream))
    with pytest.raises(OutputTruncatedError) as caught:  # what a cut-off answer raises
        await backend.chat(_req(max_tokens=16_384))
    assert isinstance(caught.value, RepetitionLoopError)
    assert "repeat" in str(caught.value) and str(caught.value).startswith("gpu-a: ")
    assert stream.sent < 16_384 / 20  # a few hundred tokens instead of 16,384
    assert stream.closed  # the request is cancelled: the server stops generating
    await backend.aclose()


class _TrickledSSE(httpx.AsyncByteStream):
    def __init__(self, every_s: float) -> None:
        self.every_s, self.closed = every_s, False

    async def __aiter__(self):
        for piece in ["{", '"a"', ": ", "1", "}"] * 40:
            await asyncio.sleep(self.every_s)
            yield _delta(piece)
        yield _delta(finish="stop")

    async def aclose(self) -> None:
        self.closed = True


async def test_the_total_time_limit_bounds_a_streamed_call():
    stream = _TrickledSSE(every_s=0.02)  # 200 events: 4 s in all, each one quick
    backend = _backend(lambda request: _sse(stream), timeout_s=0.3)
    started = asyncio.get_running_loop().time()
    with pytest.raises(TransientBackendError, match="timeout calling chat/completions"):
        await backend.chat(_req())
    assert asyncio.get_running_loop().time() - started < 1.5
    assert stream.closed
    await backend.aclose()


async def test_a_server_that_answers_a_stream_request_with_json_is_not_asked_to_stream_again():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return _ok('{"a": 1}')

    backend = _backend(handler)
    assert (await backend.chat(_req())).content == '{"a": 1}'
    assert (await backend.chat(_req())).content == '{"a": 1}'
    assert [b.get("stream") for b in seen] == [True, None]
    other = _req().model_copy(update={"model": "m2"})  # remembered per model
    await backend.chat(other)
    assert seen[-1]["stream"] is True


async def test_a_server_that_rejects_streaming_is_asked_again_without_it_and_remembered():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if body.get("stream"):
            return httpx.Response(400, json={"error": {"message": "stream is not supported"}})
        return _ok('{"a": 1}')

    backend = _backend(handler)
    assert (await backend.chat(_req())).content == '{"a": 1}'
    assert (await backend.chat(_req())).content == '{"a": 1}'
    assert [b.get("stream") for b in seen] == [True, None, None]
    assert "stream_options" not in seen[1]


async def test_a_server_that_rejects_stream_options_streams_without_them_and_remembers():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "stream_options" in body:
            message = "stream_options: Extra inputs are not permitted"
            return httpx.Response(400, json={"error": {"message": message}})
        return _sse(_sse_body(["{}"], usage=False))

    backend = _backend(handler)
    for _ in range(2):
        resp = await backend.chat(_req())
        assert resp.content == "{}" and resp.tokens_out is None
    assert [(b.get("stream"), "stream_options" in b) for b in seen] == [
        (True, True),
        (True, False),
        (True, False),
    ]


async def test_a_server_that_rejects_any_stream_after_stream_options_is_asked_whole():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if body.get("stream"):
            return httpx.Response(400, json={"error": {"message": "stream_options unsupported"}})
        return _ok('{"a": 1}')

    backend = _backend(handler)
    assert (await backend.chat(_req())).content == '{"a": 1}'
    assert [(b.get("stream"), "stream_options" in b) for b in seen] == [
        (True, True),
        (True, False),
        (None, False),
    ]


async def test_a_rejection_that_is_not_about_streaming_is_raised_and_not_remembered():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content).get("stream"))
        return httpx.Response(404, text="model not found")

    backend = _backend(handler)
    for _ in range(2):
        with pytest.raises(PermanentBackendError, match="HTTP 404"):
            await backend.chat(_req())
    assert seen == [True, None, True, None]


async def test_a_context_overflow_on_a_stream_request_is_not_asked_again():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(400, json={"error": {"message": "maximum context length is 8192"}})

    with pytest.raises(ContextOverflowError):
        await _backend(handler).chat(_req())
    assert len(seen) == 1


async def test_streaming_can_be_turned_off():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return _ok()

    await _backend(handler, stream=False).chat(_req())
    assert "stream" not in seen[0] and "stream_options" not in seen[0]
