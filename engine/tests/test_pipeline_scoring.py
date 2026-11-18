import json

import pytest

from coglossa.config import ConfigError
from coglossa.pipeline import RunOptions, run_document
from coglossa.schema import FlagCode, Reliability, RunStatus
from coglossa.testing.fake_scorer import FAKE_HEALTH, FakeScorer
from tests.conftest import BASE_CONFIG

TEXT = "Il pleut. Le chat dort.\n\nDemain, le soleil."


async def _no_sleep(_):
    return None


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "story.txt"
    path.write_text(TEXT, encoding="utf-8")
    return path


@pytest.fixture
def scored_config(make_config):
    models = {
        **BASE_CONFIG["models"],
        "model-c": {"backend": "fake", "context_window": 32768},
        "model-d": {"backend": "fake", "context_window": 32768},
    }
    return make_config(
        models=models,
        roles={
            **BASE_CONFIG["roles"],
            "judge": {"model": "model-d"},  # independent of translators and reviewer
            "roundtrip": {"model": "model-c"},
        },
    )


async def _run(doc, cfg, fake_backend, scorer, scorer_health=None, **opts):
    lines = []
    report = await run_document(
        doc,
        cfg,
        {"fake": fake_backend},
        RunOptions(target_lang="en", **opts),
        sleep=_no_sleep,
        scorer=scorer,
        scorer_health=scorer_health,
        progress=lines.append,
    )
    return report, lines


def _tasks(fake_backend):
    return [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])["task"]
        for r in fake_backend.requests
    ]


async def test_scored_run(doc, scored_config, fake_backend):
    report, lines = await _run(doc, scored_config, fake_backend, FakeScorer(), source_lang="fr")
    assert report.status is RunStatus.COMPLETE
    assert report.language_tier is Reliability.A and report.notices == []
    assert all(s.score.meaning is not None for s in report.spans)
    assert all(s.score.reliability is Reliability.A for s in report.spans)
    assert all(s.back_translation.startswith("[fr] ") for s in report.spans)
    assert "judge" in _tasks(fake_backend) and "back_translate" in _tasks(fake_backend)
    assert report.config.prompt_versions["judge"] == 1
    assert report.config.prompt_versions["back_translate"] == 1
    assert lines[-1] == "scoring: tier A, 0 of 3 spans flagged"


async def test_report_records_scorer_provenance(doc, scored_config, fake_backend):
    report, _ = await _run(
        doc, scored_config, fake_backend, FakeScorer(), FAKE_HEALTH, source_lang="fr"
    )
    assert report.config.scorer == FAKE_HEALTH
    unscored, _ = await _run(doc, scored_config, fake_backend, None, source_lang="fr")
    assert unscored.config.scorer is None


async def test_low_resource_pair_gets_notice(doc, scored_config, fake_backend):
    report, _ = await _run(doc, scored_config, fake_backend, FakeScorer(), source_lang="yo")
    assert report.language_tier is Reliability.C
    assert [n.code for n in report.notices] == [FlagCode.LOW_RELIABILITY_LANGUAGE]


async def test_scorer_down_completes_with_errors(doc, scored_config, fake_backend):
    report, _ = await _run(
        doc, scored_config, fake_backend, FakeScorer(fail=True), source_lang="fr"
    )
    assert report.status is RunStatus.COMPLETE_WITH_ERRORS
    assert {(e.stage, e.role) for e in report.errors} == {("score", "scorer")}
    assert all(s.score.signals["qe"] is None and s.score.meaning is not None for s in report.spans)


async def test_unscored_run_keeps_m1_behaviour(doc, make_config, fake_backend):
    report, lines = await _run(doc, make_config(), fake_backend, None, source_lang="fr")
    assert report.status is RunStatus.COMPLETE
    assert all(s.score.meaning is None and s.flags == [] for s in report.spans)
    assert all(s.score.reliability is Reliability.UNSCORED for s in report.spans)
    assert all(s.judge_errors is None and s.back_translation is None for s in report.spans)
    assert "judge" not in report.config.prompt_versions
    assert [n.code for n in report.notices] == [FlagCode.UNSCORED]
    assert lines[-1] == "scoring: unscored (no scorer or judge produced scores)"


async def test_unscored_low_resource_run_has_only_the_unscored_notice(
    doc, make_config, fake_backend
):
    report, _ = await _run(
        doc, make_config(), fake_backend, FakeScorer(fail=True), source_lang="yo"
    )
    assert report.language_tier is Reliability.C
    assert [n.code for n in report.notices] == [FlagCode.UNSCORED]


async def test_bad_tiers_file_fails_before_model_calls(doc, make_config, fake_backend, tmp_path):
    bad = tmp_path / "tiers.yaml"
    bad.write_text("default: Z\n", encoding="utf-8")
    cfg = make_config(scoring={"language_tiers_file": str(bad)})
    with pytest.raises(ConfigError, match="tier must be A, B or C"):
        await _run(doc, cfg, fake_backend, None)
    assert fake_backend.requests == []


def _limited(make_config, **role_limits):
    """The scored roster, every model limited to 16,384 output tokens; role_limits sets a
    role's own max_output_tokens."""
    models = {
        k: {"backend": "fake", "context_window": 32768, "max_output_tokens": 16384}
        for k in ("model-a", "model-b", "model-c", "model-d")
    }
    roles = {
        **BASE_CONFIG["roles"],
        "judge": {"model": "model-d"},
        "roundtrip": {"model": "model-c"},
    }
    for role, limit in role_limits.items():
        roles[role] = {**roles[role], "max_output_tokens": limit}
    return make_config(models=models, roles=roles)


def _limits(fake_backend):
    """Each task, with the output limits its calls asked for."""
    limits: dict[str, set] = {}
    for task, request in zip(_tasks(fake_backend), fake_backend.requests, strict=True):
        limits.setdefault(task, set()).add(request.max_tokens)
    return limits


async def test_the_judge_is_capped_and_the_other_roles_keep_the_models_limit(
    doc, make_config, fake_backend
):
    await _run(doc, _limited(make_config), fake_backend, FakeScorer(), source_lang="fr")
    assert _limits(fake_backend) == {
        "profile": {16384},
        "guide": {16384},
        "translate": {16384},
        "reconcile": {16384},
        "judge": {8192},
        "back_translate": {16384},
    }


async def test_a_roles_own_limit_applies_to_that_roles_calls(doc, make_config, fake_backend):
    cfg = _limited(
        make_config,
        judge=12000,
        reviewer=4000,
        literal_translator=2000,
        profiler_guide=10000,
        roundtrip=9000,
    )
    await _run(doc, cfg, fake_backend, FakeScorer(), source_lang="fr")
    assert _limits(fake_backend) == {
        "profile": {10000},
        "guide": {10000},
        "translate": {2000, 16384},  # the literal translator's own, the contextual the model's
        "reconcile": {4000},
        "judge": {12000},
        "back_translate": {9000},
    }
