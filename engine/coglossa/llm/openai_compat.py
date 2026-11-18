"""Adapter for any OpenAI-compatible server: vLLM, Ollama, TGI, llama.cpp, LM Studio.

Answers are streamed, so that a model that falls into a repetition loop can be stopped as it
happens instead of at its output limit (CGL-71). A server that cannot stream is asked again
without streaming, and that is remembered for the model."""

import asyncio
import json
import time
from typing import Any

import httpx

from coglossa.config import RESERVED_BODY_KEYS, StructuredOutput
from coglossa.llm.loops import LoopContext, Repetition, RepetitionGuard
from coglossa.llm.types import (
    ChatRequest,
    ChatResponse,
    ContextOverflowError,
    OutputTruncatedError,
    PermanentBackendError,
    RepetitionLoopError,
    TransientBackendError,
)

_OVERFLOW_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "context window",
    "too many tokens",
    "prompt is too long",
)
_CHAT = "chat/completions"


class _StreamRefused(Exception):
    """The server rejected a streamed request with a client error; asking without streaming
    tells whether streaming was the problem."""


class OpenAICompatBackend:
    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        api_key: str | None = None,
        max_concurrency: int = 4,
        timeout_s: float = 120.0,
        stream: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=timeout_s,
            transport=transport,
        )
        self._timeout_s = timeout_s
        self._sem = asyncio.Semaphore(max_concurrency)
        self._stream = stream
        self._no_stream: set[str] = set()  # models whose server does not stream
        self._no_usage: set[str] = set()  # models whose server streams without stream_options

    async def chat(self, request: ChatRequest) -> ChatResponse:
        async with self._sem:
            started = time.monotonic()
            if self._stream and request.model not in self._no_stream:
                try:
                    return await self._chat_stream_options(request, started)
                except _StreamRefused:
                    pass
                resp = await self._send("POST", _CHAT, json=_payload(request))
                # the same request without streaming was accepted: streaming was the problem
                self._no_stream.add(request.model)
            else:
                resp = await self._send("POST", _CHAT, json=_payload(request))
            latency_ms = _since(started)
        return _parse_chat(self.name, resp, latency_ms)

    async def list_models(self) -> list[str]:
        resp = await self._send("GET", "models")
        try:
            return [m["id"] for m in resp.json()["data"]]
        except (ValueError, KeyError, TypeError) as e:
            raise PermanentBackendError(f"{self.name}: malformed /models response") from e

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        # httpx's timeout bounds each read, so a server that trickles its answer is never cut
        # off; the total bound covers the whole attempt, body included. Leaving the block
        # cancels the request, and httpx closes the response on the way out.
        try:
            async with asyncio.timeout(self._timeout_s):
                resp = await self._client.request(method, path, **kwargs)
        except (httpx.TimeoutException, TimeoutError) as e:
            raise TransientBackendError(f"{self.name}: timeout calling {path}") from e
        except httpx.TransportError as e:
            raise TransientBackendError(f"{self.name}: {type(e).__name__}: {e}") from e
        _raise_for_status(self.name, resp)
        return resp

    async def _chat_stream_options(self, request: ChatRequest, started: float) -> ChatResponse:
        """A streamed answer with token counts; a server that refuses `stream_options` by name
        is asked to stream without them, and that is remembered for the model."""
        usage = request.model not in self._no_usage
        try:
            return await self._chat_streamed(request, started, usage=usage)
        except _StreamRefused as refused:
            if not usage or "stream_options" not in str(refused):
                raise
        resp = await self._chat_streamed(request, started, usage=False)
        self._no_usage.add(request.model)
        return resp

    async def _chat_streamed(
        self, request: ChatRequest, started: float, *, usage: bool
    ) -> ChatResponse:
        payload: dict[str, Any] = {**_payload(request), "stream": True}
        if usage:
            payload["stream_options"] = {"include_usage": True}
        # The same total bound as _send, over the whole stream. Leaving the block early (a loop,
        # an error, the deadline) closes the connection, which tells the server to stop.
        try:
            async with asyncio.timeout(self._timeout_s):
                async with self._client.stream("POST", _CHAT, json=payload) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                        _raise_for_stream_status(self.name, resp)
                    content_type = resp.headers.get("content-type", "").lower()
                    if not content_type.startswith("text/event-stream"):
                        # it ignored `stream` and sent the whole answer: take it, and do not
                        # ask this model to stream again
                        await resp.aread()
                        self._no_stream.add(request.model)
                        return _parse_chat(self.name, resp, _since(started))
                    content, usage = await self._read_events(resp, request)
        except (httpx.TimeoutException, TimeoutError) as e:
            raise TransientBackendError(f"{self.name}: timeout calling {_CHAT}") from e
        except httpx.TransportError as e:
            raise TransientBackendError(f"{self.name}: {type(e).__name__}: {e}") from e
        return ChatResponse(
            content=content,
            tokens_in=_count(usage.get("prompt_tokens")),
            tokens_out=_count(usage.get("completion_tokens")),
            latency_ms=_since(started),
        )

    async def _read_events(
        self, resp: httpx.Response, request: ChatRequest
    ) -> tuple[str, dict[str, Any]]:
        """The answer's text and usage from a server-sent event stream; a loop ends it early."""
        # the prompt is read once for the repetition it has itself, for both guards
        prompt = LoopContext("\n".join(m.content for m in request.messages))
        answer, thinking = RepetitionGuard(context=prompt), RepetitionGuard(context=prompt)
        parts: list[str] = []
        usage: dict[str, Any] = {}
        finish: str | None = None
        done = False
        has_text = False  # a content field came at all: a reply with none is not an empty one
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue  # blank lines, comments (": ...") and other fields
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break
            chunk = _event(self.name, data)
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            choices = chunk.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else None
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            delta = delta if isinstance(delta, dict) else {}
            text = delta.get("content")
            has_text = has_text or isinstance(text, str)
            if isinstance(text, str) and text:
                parts.append(text)
                _stop_on_loop(self.name, answer.feed(text), "answer")
            # servers name the reasoning differently (vLLM, OpenRouter); a loop there costs too
            thought = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(thought, str) and thought:
                _stop_on_loop(self.name, thinking.feed(thought), "reasoning")
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
        if finish == "length":
            raise OutputTruncatedError(f"{self.name}: output truncated (finish_reason=length)")
        if finish is None and not done:
            raise TransientBackendError(f"{self.name}: the stream ended before the answer did")
        if not has_text:
            # as for a whole answer whose content is null, e.g. one left entirely in reasoning
            raise PermanentBackendError(f"{self.name}: chat completion has no text content")
        return "".join(parts), usage


def _since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _event(name: str, data: str) -> dict[str, Any]:
    try:
        chunk = json.loads(data)
    except ValueError as e:
        raise PermanentBackendError(f"{name}: malformed chat completion stream") from e
    if not isinstance(chunk, dict):
        raise PermanentBackendError(f"{name}: malformed chat completion stream")
    error = chunk.get("error")
    if error is not None:
        # an error after the stream started: servers send it as an event, with its own code
        message = error.get("message", error) if isinstance(error, dict) else error
        code = error.get("code") if isinstance(error, dict) else None
        status = code if isinstance(code, int) and not isinstance(code, bool) else 400
        _raise_for_error(name, status, str(message)[:500])
    return chunk


def _stop_on_loop(name: str, loop: Repetition | None, where: str) -> None:
    if loop is None:
        return
    raise RepetitionLoopError(
        f"{name}: stopped early: the {where} repeated a phrase of {loop.period} characters "
        f"{loop.repeats} times in a row, {loop.at_chars} characters in (a repetition loop)"
    )


def _payload(request: ChatRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        # reserved keys never come from extra_body, even for a request built outside config
        **{k: v for k, v in request.extra_body.items() if k not in RESERVED_BODY_KEYS},
        "model": request.model,
        "messages": [m.model_dump() for m in request.messages],
        "temperature": request.temperature,
    }
    if request.max_tokens is not None:
        payload["max_tokens"] = request.max_tokens
    if request.structured_output is StructuredOutput.JSON_SCHEMA:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "schema": request.json_schema},
        }
    elif request.structured_output is StructuredOutput.JSON_OBJECT:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _raise_for_status(name: str, resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    _raise_for_error(name, resp.status_code, resp.text[:500])


def _raise_for_stream_status(name: str, resp: httpx.Response) -> None:
    """Like _raise_for_status, but a client error that may be about streaming is refused."""
    try:
        _raise_for_status(name, resp)
    except ContextOverflowError:
        raise  # the prompt is too long, streamed or not
    except TransientBackendError:
        raise
    except PermanentBackendError as e:
        raise _StreamRefused(str(e)) from e


def _raise_for_error(name: str, status: int, body: str) -> None:
    message = f"{name}: HTTP {status}: {body}"
    if status == 429 or status >= 500:
        raise TransientBackendError(message)
    if any(marker in body.lower() for marker in _OVERFLOW_MARKERS):
        raise ContextOverflowError(message)
    raise PermanentBackendError(message)


def _parse_chat(name: str, resp: httpx.Response, latency_ms: int) -> ChatResponse:
    try:
        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise PermanentBackendError(f"{name}: malformed chat completion response") from e
    if choice.get("finish_reason") == "length":
        raise OutputTruncatedError(f"{name}: output truncated (finish_reason=length)")
    if not isinstance(content, str):
        raise PermanentBackendError(f"{name}: chat completion has no text content")
    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return ChatResponse(
        content=content,
        tokens_in=_count(usage.get("prompt_tokens")),
        tokens_out=_count(usage.get("completion_tokens")),
        latency_ms=latency_ms,
    )


def _count(value: object) -> int | None:
    """A token count, or None when a server reports something that is not an integer."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None
