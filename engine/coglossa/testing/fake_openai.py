"""A tiny OpenAI-compatible HTTP server (stdlib only) answering with the synthetic responder.
Used by integration tests, by `coglossa fake-openai`, and by the demo stack (compose.demo.yaml),
which keeps no request log.

It streams an answer when asked to (`stream: true`), like vLLM or Ollama. `streaming="ignore"`
answers every request whole, and `streaming="reject"` refuses streamed requests with HTTP 400:
the two ways a server that cannot stream behaves. `loop="phrase"` plays a model stuck in a
repetition loop: every answer is that phrase, one event per token, until the output limit."""

import json
import threading
import time
from collections.abc import Iterable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal

from coglossa.llm.payload import parse_input
from coglossa.testing.synthetic import chat_completion, synthetic_response

Streaming = Literal["sse", "ignore", "reject"]
_DEFAULT_LIMIT = 16_384  # tokens a looping answer runs to when the request sets no max_tokens


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    models: list[str]
    context_window: int | None
    requests: list[dict[str, Any]]
    keep_requests: bool
    delay_s: float
    streaming: Streaming
    loop: str | None
    loop_events: int
    lock: threading.Lock
    in_flight: int
    max_in_flight: int


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            data = [{"id": m, "object": "model"} for m in self.server.models]
            self._send(200, {"object": "list", "data": data})
        else:
            self._send(404, {"error": {"message": f"no route {self.path}"}})

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": f"no route {self.path}"}})
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with self.server.lock:
            if self.server.keep_requests:
                self.server.requests.append(body)
            self.server.in_flight += 1
            self.server.max_in_flight = max(self.server.max_in_flight, self.server.in_flight)
        try:
            if self.server.delay_s:
                time.sleep(self.server.delay_s)
            self._reply(body)
        finally:
            with self.server.lock:
                self.server.in_flight -= 1

    def _reply(self, body: dict[str, Any]) -> None:
        window = self.server.context_window
        chars = sum(len(m.get("content") or "") for m in body.get("messages", []))
        if window is not None and chars / 2 > window:
            self._send(
                400,
                {"error": {"message": f"This model's maximum context length is {window} tokens"}},
            )
            return
        wants_stream = bool(body.get("stream"))
        if wants_stream and self.server.streaming == "reject":
            self._send(400, {"error": {"message": "'stream' is not supported"}})
            return
        stream = wants_stream and self.server.streaming == "sse"
        options = body.get("stream_options")
        usage = isinstance(options, dict) and options.get("include_usage") is True
        if self.server.loop is not None:
            limit = body.get("max_tokens") or _DEFAULT_LIMIT
            if stream:
                pieces = self._looping(self.server.loop, limit)
                self._stream(pieces, "length", usage, pace_s=0.001)
            else:
                self._send(200, chat_completion(self.server.loop * limit, finish_reason="length"))
            return
        try:
            payload = parse_input(body["messages"])
            content = json.dumps(synthetic_response(payload), ensure_ascii=False)
        except (ValueError, KeyError) as e:
            self._send(400, {"error": {"message": str(e)}})
            return
        reason = "length" if body.get("max_tokens") == 1 else "stop"
        if stream:
            pieces = [content[i : i + 8] for i in range(0, len(content), 8)]
            self._stream(pieces, reason, usage)
        else:
            self._send(200, chat_completion(content, finish_reason=reason))

    def _stream(
        self, pieces: Iterable[str], reason: str, usage: bool, *, pace_s: float = 0.0
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()  # no Content-Length: the stream ends when the connection closes
        try:
            self._event(_delta({"role": "assistant", "content": ""}))
            for piece in pieces:
                self._event(_delta({"content": piece}))
                time.sleep(pace_s)  # a model takes time per token
            self._event({"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]})
            if usage:
                usage_counts = {"prompt_tokens": 10, "completion_tokens": 10}
                self._event({"choices": [], "usage": usage_counts})
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            return  # the client hung up, as it does when it stops a loop

    def _looping(self, phrase: str, limit: int) -> Iterator[str]:
        for _ in range(limit):
            with self.server.lock:
                self.server.loop_events += 1
            yield phrase

    def _event(self, obj: dict[str, Any]) -> None:
        self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n")

    def _send(self, status: int, obj: dict[str, Any]) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _delta(delta: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}


class FakeOpenAIServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        models: tuple[str, ...] = ("fake-model",),
        context_window: int | None = None,
        delay_s: float = 0.0,
        keep_requests: bool = True,
        streaming: Streaming = "sse",
        loop: str | None = None,
    ) -> None:
        self._server = _Server((host, port), _Handler)
        self._server.models = list(models)
        self._server.context_window = context_window
        self._server.requests = []
        self._server.keep_requests = keep_requests
        self._server.delay_s = delay_s
        self._server.streaming = streaming
        self._server.loop = loop
        self._server.loop_events = 0
        self._server.lock = threading.Lock()
        self._server.in_flight = 0
        self._server.max_in_flight = 0
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self._server.requests

    @property
    def max_in_flight(self) -> int:
        """The most chat requests the server has handled at the same time."""
        return self._server.max_in_flight

    @property
    def in_flight(self) -> int:
        """Chat requests the server is handling now."""
        return self._server.in_flight

    @property
    def loop_events(self) -> int:
        """Tokens of looping answers sent so far, with `loop` set."""
        return self._server.loop_events

    def start(self) -> "FakeOpenAIServer":
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        """Blocking variant for the CLI."""
        self._server.serve_forever()

    def stop(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join()
            self._thread = None
        self._server.server_close()  # also releases the socket when never started (CLI path)

    def __enter__(self) -> "FakeOpenAIServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
