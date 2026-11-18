import json

import pytest

from coglossa.llm.agent import StructuredOutputError, extract_json
from coglossa.llm.types import PermanentBackendError, TransientBackendError
from coglossa.schema import ProfileAssessment
from tests.conftest import BASE_CONFIG

PAYLOAD = {"task": "profile", "sample": "Bonjour", "source_lang_hint": "fr", "target_lang": "en"}
GOOD = json.dumps(
    {"source_lang": "fr", "mixed_language": False, "genre": "news", "tone_register": "plain"}
)


async def _run(runner, **kw):
    args = dict(
        role="profiler",
        model_key="model-b",
        prompt_id="profile",
        prompt_vars={"target_lang": "en"},
        payload=PAYLOAD,
        output_type=ProfileAssessment,
    )
    args.update(kw)
    return await runner.run(**args)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('Sure!\n```json\n{"a": 1}\n```\nDone.', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('Here you go: {"a": {"b": 2}} hope it helps', '{"a": {"b": 2}}'),
        ("no json at all", "no json at all"),
        ('{"t": "run:\\n```bash\\nls\\n```"}', '{"t": "run:\\n```bash\\nls\\n```"}'),
        ('```json\n{"t": "```x```"}\n```', '{"t": "```x```"}'),
    ],
)
def test_extract_json(content, expected):
    assert extract_json(content) == expected


async def test_success_records_call(make_runner, fake_backend):
    runner = make_runner()
    out = await _run(runner)
    assert out.source_lang == "fr"
    [rec] = runner.log.records
    assert (rec.role, rec.backend, rec.model, rec.prompt_id, rec.prompt_version) == (
        "profiler",
        "fake",
        "model-b",
        "profile",
        1,
    )
    assert rec.ok and rec.attempt == 1 and rec.tokens_in == 10
    assert rec.request["messages"][1]["content"].startswith("INPUT_JSON:")
    assert rec.response
    req = fake_backend.requests[0]
    assert req.schema_name == "ProfileAssessment"
    assert "JSON Schema" not in req.messages[0].content  # json_schema mode: server enforces


async def test_wrapped_json_from_local_models_is_accepted(make_runner, fake_backend):
    fake_backend.script("profile", f"Of course!\n```json\n{GOOD}\n```")
    out = await _run(make_runner())
    assert out.genre.value == "news"


async def test_invalid_output_is_repaired_once(make_runner, fake_backend):
    fake_backend.script("profile", "garbage")
    runner = make_runner()
    out = await _run(runner)
    assert out.source_lang == "fr"
    assert len(runner.log.records) == 2
    repair = fake_backend.requests[1].messages
    assert [m.role for m in repair] == ["system", "user", "assistant", "user"]
    assert repair[2].content == "garbage"
    assert "invalid" in repair[3].content


async def test_fails_after_repair(make_runner, fake_backend):
    fake_backend.script("profile", "garbage", '{"source_lang": "fr"}')
    with pytest.raises(StructuredOutputError, match="profiler: invalid output") as exc:
        await _run(make_runner())
    assert exc.value.raw == '{"source_lang": "fr"}'


async def test_custom_validator_triggers_repair(make_runner, fake_backend):
    seen = []

    def validate(obj):
        seen.append(obj)
        return ["source_lang must not be fr"] if len(seen) == 1 else []

    out = await _run(make_runner(), validate=validate)
    assert len(seen) == 2 and out.source_lang == "fr"
    assert "source_lang must not be fr" in fake_backend.requests[1].messages[3].content


async def test_normalize_runs_before_the_validator_and_its_result_is_returned(make_runner):
    seen = []

    def validate(obj):
        seen.append(obj.genre)
        return [] if obj.genre == "poetry" else ["genre must be poetry"]

    out = await _run(
        make_runner(),
        normalize=lambda o: o.model_copy(update={"genre": "poetry"}),
        validate=validate,
    )
    assert seen == ["poetry"] and out.genre == "poetry"


async def test_schema_in_prompt_when_server_cannot_enforce(make_config, make_runner, fake_backend):
    models = {
        **BASE_CONFIG["models"],
        "model-b": {
            "backend": "fake",
            "context_window": 8192,
            "structured_output": "json_object",
            "name": "served-name",
            "max_output_tokens": 512,
            "temperature": 0.0,
        },
    }
    await _run(make_runner(make_config(models=models)))
    req = fake_backend.requests[0]
    assert "JSON Schema" in req.messages[0].content
    assert (req.model, req.max_tokens, req.temperature) == ("served-name", 512, 0.0)


async def test_a_calls_own_output_limit_replaces_the_models(make_config, make_runner, fake_backend):
    models = {"model-b": {"backend": "fake", "context_window": 8192, "max_output_tokens": 512}}
    runner = make_runner(make_config(models={**BASE_CONFIG["models"], **models}))
    fake_backend.script("profile", "not json")  # the repair turn keeps the call's limit too
    await _run(runner, max_output_tokens=128)
    await _run(runner)
    assert [r.max_tokens for r in fake_backend.requests] == [128, 128, 512]


async def test_transient_errors_are_retried_and_logged(make_runner, fake_backend):
    fake_backend.script("profile", TransientBackendError("busy"))
    runner = make_runner()
    await _run(runner)
    assert [(r.attempt, r.ok) for r in runner.log.records] == [(1, False), (2, True)]
    assert runner.log.records[0].error == "busy"


async def test_permanent_errors_propagate(make_runner, fake_backend):
    fake_backend.script("profile", PermanentBackendError("no such model"))
    runner = make_runner()
    with pytest.raises(PermanentBackendError):
        await _run(runner)
    assert len(runner.log.records) == 1


async def test_store_io_false_omits_payloads(make_runner):
    runner = make_runner(store_io=False)
    await _run(runner)
    assert runner.log.records[0].request is None
    assert runner.log.records[0].response is None


async def test_usage_aggregates_by_role(make_runner, fake_backend):
    runner = make_runner()
    await _run(runner)
    await _run(runner, role="other")
    usage = runner.log.usage()
    assert (usage.calls, usage.tokens_in, usage.tokens_out) == (2, 20, 20)
    assert usage.by_role["other"] == {"calls": 1, "tokens_in": 10, "tokens_out": 10}


FENCED_TEXT = json.dumps(
    {
        "source_lang": "fr",
        "mixed_language": False,
        "genre": "news",
        "tone_register": "see:\n```bash\nls\n```\nok",
    }
)


@pytest.mark.parametrize("mode", ["json_schema", "json_object", "none"])
async def test_valid_json_containing_code_fence_is_not_mangled(
    mode, make_config, make_runner, fake_backend
):
    models = {
        **BASE_CONFIG["models"],
        "model-b": {"backend": "fake", "context_window": 8192, "structured_output": mode},
    }
    fake_backend.script("profile", FENCED_TEXT)
    runner = make_runner(make_config(models=models))
    out = await _run(runner)
    assert out.tone_register == "see:\n```bash\nls\n```\nok"
    assert len(runner.log.records) == 1


async def test_whole_reply_fenced_and_prose_wrapped_still_parse(make_runner, fake_backend):
    fake_backend.script("profile", f"```json\n{FENCED_TEXT}\n```")
    assert (await _run(make_runner())).tone_register.startswith("see:")
    fake_backend.script("profile", f"Here it is: {FENCED_TEXT} enjoy")
    assert (await _run(make_runner())).tone_register.startswith("see:")


async def test_invalid_attempt_is_recorded_as_not_ok(make_runner, fake_backend):
    fake_backend.script("profile", '{"source_lang": "fr"}')
    runner = make_runner()
    await _run(runner)
    bad, good = runner.log.records
    assert (bad.attempt, bad.ok) == (1, False)
    assert bad.error.startswith("invalid output: ") and "genre" in bad.error
    assert bad.response == '{"source_lang": "fr"}'  # the backend call itself did succeed
    assert (good.attempt, good.ok, good.error) == (2, True, None)


async def test_attempts_are_numbered_across_retries_and_repair(make_runner, fake_backend):
    fake_backend.script("profile", TransientBackendError("busy"), "garbage")
    runner = make_runner()
    await _run(runner)
    assert [(r.attempt, r.ok) for r in runner.log.records] == [
        (1, False),
        (2, False),
        (3, True),
    ]
    assert runner.log.records[0].error == "busy"
    assert runner.log.records[1].error.startswith("invalid output: ")


async def test_failed_run_logs_both_invalid_attempts(make_runner, fake_backend):
    fake_backend.script("profile", "garbage", "more garbage")
    runner = make_runner()
    with pytest.raises(StructuredOutputError):
        await _run(runner)
    assert [(r.attempt, r.ok) for r in runner.log.records] == [(1, False), (2, False)]


async def test_model_extra_body_is_sent_on_the_request(make_runner, make_config, fake_backend):
    body = {"chat_template_kwargs": {"enable_thinking": False}}
    cfg = make_config(
        models={
            "model-a": {"backend": "fake", "context_window": 32768},
            "model-b": {"backend": "fake", "context_window": 32768, "extra_body": body},
        }
    )
    await _run(make_runner(cfg))
    assert fake_backend.requests[0].extra_body == body


async def test_a_loop_stopped_early_ends_like_a_cut_off_answer_and_is_logged(make_config):
    # CGL-71: the call ends with the cut-off error every role already recovers from, and the call
    # log says it was a loop that Coglossa stopped, not the model reaching its limit
    from coglossa.llm.agent import AgentRunner, CallLog
    from coglossa.llm.openai_compat import OpenAICompatBackend
    from coglossa.llm.types import OVERSIZE_ERRORS, OutputTruncatedError
    from coglossa.prompts.library import PromptLibrary
    from coglossa.testing.fake_openai import FakeOpenAIServer

    with FakeOpenAIServer(models=("model-b",), loop="Le français est ") as server:
        cfg = make_config(
            backends={"fake": {"base_url": server.base_url}},
            models={
                "model-a": {"backend": "fake", "context_window": 32768},
                "model-b": {"backend": "fake", "context_window": 32768, "max_output_tokens": 16384},
            },
        )
        backend = OpenAICompatBackend("fake", server.base_url)
        runner = AgentRunner(cfg, {"fake": backend}, PromptLibrary(None), CallLog())
        with pytest.raises(OutputTruncatedError) as caught:
            await _run(runner)
        assert isinstance(caught.value, OVERSIZE_ERRORS)
        [record] = runner.log.records  # not retried: a loop is not a transient failure
        assert not record.ok
        assert record.error.startswith(
            "fake: stopped early: the answer repeated a phrase of 16 characters"
        )
        await backend.aclose()
