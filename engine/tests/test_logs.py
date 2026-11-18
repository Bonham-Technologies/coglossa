import io
import json
import logging

from coglossa.logs import JsonFormatter, configure_json_logging, remove_json_logging


def test_json_lines_carry_context_fields():
    stream = io.StringIO()
    configure_json_logging(stream=stream)
    configure_json_logging(stream=stream)  # idempotent: still one handler
    log = logging.getLogger("coglossa.worker")
    log.info("stage complete", extra={"run_id": "r1", "stage": "ingest", "duration_ms": 12})
    try:
        raise ValueError("bad")
    except ValueError:
        log.exception("failed", extra={"chunk_id": "c1"})
    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert first["message"] == "stage complete" and first["level"] == "info"
    assert (first["run_id"], first["stage"], first["duration_ms"]) == ("r1", "ingest", 12)
    assert first["logger"] == "coglossa.worker" and first["ts"].endswith("+00:00")
    assert second["chunk_id"] == "c1" and "ValueError: bad" in second["exception"]
    assert not any(isinstance(h.formatter, JsonFormatter) for h in logging.getLogger().handlers)


def test_remove_json_logging_detaches_the_handler():
    stream = io.StringIO()
    handler = configure_json_logging(stream=stream)
    remove_json_logging(handler)
    logging.getLogger("procrastinate.worker").warning("after removal")
    assert stream.getvalue() == ""
    for name in ("coglossa", "procrastinate"):
        assert handler not in logging.getLogger(name).handlers
