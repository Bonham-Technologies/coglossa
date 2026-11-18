"""One agent call: prompt + JSON input -> validated Pydantic output (spec §4.5).

Flow: render prompt -> call backend (transient errors retried with backoff) -> extract JSON ->
normalize (optional, e.g. drop a harmless repeat) -> validate (schema + optional semantic
validator) -> on failure, one repair turn -> else
StructuredOutputError. Every backend attempt is recorded in the CallLog."""

import asyncio
import itertools
import json
import random
import re
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from coglossa.config import CoglossaConfig, StructuredOutput
from coglossa.llm.payload import render_input
from coglossa.llm.retry import RetryPolicy, call_with_retry
from coglossa.llm.types import OVERSIZE_ERRORS, Backend, BackendError, ChatMessage, ChatRequest
from coglossa.prompts.library import PromptLibrary, RenderedPrompt
from coglossa.schema import LLMCallRecord, Usage


class StructuredOutputError(Exception):
    def __init__(self, message: str, *, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


AGENT_FAILURES: tuple[type[Exception], ...] = (StructuredOutputError, BackendError)

# The model answered, but nothing usable came back: invalid, cut off at the output limit (a model
# that loops gets there) or too large for it. Unlike a model that cannot be reached, asking for
# less, or going on without that answer, can still work.
UNUSABLE_OUTPUT: tuple[type[Exception], ...] = (StructuredOutputError, *OVERSIZE_ERRORS)

_WHOLE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*)\n?```$", re.DOTALL | re.IGNORECASE)


def extract_json(content: str) -> str:
    """Pull the JSON object out of replies that wrap it in prose or Markdown fences."""
    text = content.strip()
    try:
        json.loads(text)
    except ValueError:
        pass
    else:
        return text  # already valid: never touch fences that live inside JSON strings
    fence = _WHOLE_FENCE_RE.match(text)
    if fence:
        return fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        return text[start : end + 1]
    return text


def _repair_message(errors: list[str]) -> str:
    bullets = "\n".join(f"- {e}" for e in errors[:20])
    return f"Your previous response was invalid:\n{bullets}\nReturn the corrected JSON object only."


@dataclass
class CallLog:
    store_io: bool = True
    records: list[LLMCallRecord] = field(default_factory=list)

    def add(self, record: LLMCallRecord) -> None:
        self.records.append(record)

    def usage(self) -> Usage:
        return usage_of((r.role, r.tokens_in, r.tokens_out) for r in self.records)


def usage_of(calls: Iterable[tuple[str, int | None, int | None]]) -> Usage:
    """Token usage from (role, tokens_in, tokens_out) per model call."""
    by_role: dict[str, dict[str, int]] = {}
    n = 0
    for role, tokens_in, tokens_out in calls:
        d = by_role.setdefault(role, {"calls": 0, "tokens_in": 0, "tokens_out": 0})
        d["calls"] += 1
        d["tokens_in"] += tokens_in or 0
        d["tokens_out"] += tokens_out or 0
        n += 1
    return Usage(
        calls=n,
        tokens_in=sum(d["tokens_in"] for d in by_role.values()),
        tokens_out=sum(d["tokens_out"] for d in by_role.values()),
        by_role=by_role,
    )


class AgentRunner:
    def __init__(
        self,
        cfg: CoglossaConfig,
        backends: dict[str, Backend],
        prompts: PromptLibrary,
        log: CallLog,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.cfg = cfg
        self.log = log
        self.prompts = prompts
        self._backends = backends
        self._policy = RetryPolicy.from_config(cfg.retry)
        self._sleep = sleep
        self._rng = rng or random.Random()

    async def run[M: BaseModel](
        self,
        *,
        role: str,
        model_key: str,
        prompt_id: str,
        prompt_vars: dict[str, Any],
        payload: dict[str, Any],
        output_type: type[M],
        validate: Callable[[M], list[str]] | None = None,
        normalize: Callable[[M], M] | None = None,
        max_output_tokens: int | None = None,
    ) -> M:
        """max_output_tokens: this call's output limit (a role's, cfg.output_limit); None: the
        model's."""
        model = self.cfg.models[model_key]
        backend = self._backends[model.backend]
        prompt = self.prompts.render(prompt_id, **prompt_vars)
        schema = output_type.model_json_schema()
        system = prompt.text
        if model.structured_output is not StructuredOutput.JSON_SCHEMA:
            system += (
                "\n\nRespond with one JSON object that matches this JSON Schema:\n"
                + json.dumps(schema, ensure_ascii=False)
            )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=render_input(payload)),
        ]
        content = ""
        errors: list[str] = []
        attempts = itertools.count(1)  # numbers every backend call of this run, repair included
        for _ in range(2):  # first attempt + one repair turn
            if errors:
                messages = [
                    *messages,
                    ChatMessage(role="assistant", content=content),
                    ChatMessage(role="user", content=_repair_message(errors)),
                ]
            request = ChatRequest(
                model=model.name or model_key,
                messages=messages,
                schema_name=output_type.__name__,
                json_schema=schema,
                structured_output=model.structured_output,
                temperature=model.temperature,
                max_tokens=max_output_tokens or model.max_output_tokens,
                extra_body=model.extra_body,
            )
            content, record = await self._call(role, backend, request, prompt, attempts)
            result, errors = _parse(content, output_type, validate, normalize)
            if result is not None and not errors:
                return result
            # `ok` means "usable output": the backend call succeeded but its reply was rejected.
            record.ok = False
            record.error = f"invalid output: {'; '.join(errors[:3])}"
        raise StructuredOutputError(
            f"{role}: invalid output after repair: {'; '.join(errors[:5])}", raw=content
        )

    async def _call(
        self,
        role: str,
        backend: Backend,
        request: ChatRequest,
        prompt: RenderedPrompt,
        attempts: Iterator[int],
    ) -> tuple[str, LLMCallRecord]:
        last: LLMCallRecord

        async def once() -> str:
            nonlocal last
            attempt = next(attempts)
            try:
                resp = await backend.chat(request)
            except BackendError as e:
                self._record(role, backend, request, prompt, attempt, ok=False, error=str(e))
                raise
            last = self._record(
                role,
                backend,
                request,
                prompt,
                attempt,
                ok=True,
                latency_ms=resp.latency_ms,
                tokens_in=resp.tokens_in,
                tokens_out=resp.tokens_out,
                response=resp.content,
            )
            return resp.content

        content = await call_with_retry(once, self._policy, sleep=self._sleep, rng=self._rng)
        return content, last

    def _record(
        self,
        role: str,
        backend: Backend,
        request: ChatRequest,
        prompt: RenderedPrompt,
        attempt: int,
        *,
        ok: bool,
        error: str | None = None,
        latency_ms: int = 0,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        response: str | None = None,
    ) -> LLMCallRecord:
        store = self.log.store_io
        record = LLMCallRecord(
            role=role,
            backend=backend.name,
            model=request.model,
            prompt_id=prompt.id,
            prompt_version=prompt.version,
            attempt=attempt,
            ok=ok,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            error=error,
            request={"messages": [m.model_dump() for m in request.messages]} if store else None,
            response=response if store else None,
        )
        self.log.add(record)
        return record


def _parse[M: BaseModel](
    content: str,
    output_type: type[M],
    validate: Callable[[M], list[str]] | None,
    normalize: Callable[[M], M] | None = None,
) -> tuple[M | None, list[str]]:
    try:
        obj = output_type.model_validate_json(extract_json(content))
    except ValidationError as e:
        return None, [
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in e.errors()
        ]
    if normalize is not None:
        obj = normalize(obj)
    return obj, (validate(obj) if validate else [])
