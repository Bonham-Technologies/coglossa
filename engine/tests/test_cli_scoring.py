import pytest
import yaml

from coglossa.cli import _check_scorer, main
from coglossa.schema import Report
from coglossa.testing.fake_openai import FakeOpenAIServer
from coglossa.testing.fake_scorer import FakeScorer, FakeScorerServer

TEXT = "Il pleut. Le chat dort.\n\nDemain, le soleil."


@pytest.fixture
def llm():
    with FakeOpenAIServer(models=("model-a", "model-b", "model-c", "model-d")) as s:
        yield s


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "story.txt"
    path.write_text(TEXT, encoding="utf-8")
    return path


def _config(tmp_path, llm_url, scorer_url):
    data = {
        "backends": {"local": {"base_url": llm_url}},
        "models": {
            m: {"backend": "local", "context_window": 32768}
            for m in ("model-a", "model-b", "model-c", "model-d")
        },
        "roles": {
            "literal_translator": {"model": "model-a"},
            "contextual_translator": {"model": "model-b"},
            "reviewer": {"model": "model-a"},
            "profiler_guide": {"model": "model-b"},
            "judge": {"model": "model-d"},
            "roundtrip": {"model": "model-c"},
        },
        "scorer": {"url": scorer_url, "timeout_s": 5},
        "retry": {"max_attempts": 1, "base_delay_s": 0, "max_delay_s": 0},
    }
    path = tmp_path / "coglossa.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_scored_run_end_to_end(tmp_path, llm, doc, capsys):
    with FakeScorerServer() as scorer:
        out = tmp_path / "out"
        code = main(
            [
                "run",
                str(doc),
                "--config",
                str(_config(tmp_path, llm.base_url, scorer.base_url)),
                "--target-lang",
                "en",
                "--source-lang",
                "fr",
                "--out",
                str(out),
            ]
        )
        paths = [r["path"] for r in scorer.requests]
    assert code == 0
    report = Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))
    assert all(s.score.meaning is not None for s in report.spans)
    assert report.language_tier.value == "A"
    assert all(s.score.reliability.value == "A" for s in report.spans)
    assert report.config.scorer is not None
    assert (report.config.scorer.qe_model, report.config.scorer.embed_model) == (
        "fake-metricx",
        "fake-labse",
    )
    assert paths[0] == "/health" and "/v1/qe" in paths and "/v1/similarity" in paths
    assert "(tier A)" in capsys.readouterr().out


def test_low_resource_notice_printed(tmp_path, llm, doc, capsys):
    with FakeScorerServer() as scorer:
        code = main(
            [
                "run",
                str(doc),
                "--config",
                str(_config(tmp_path, llm.base_url, scorer.base_url)),
                "--target-lang",
                "en",
                "--source-lang",
                "yo",
                "--out",
                str(tmp_path / "o"),
            ]
        )
    assert code == 0
    assert "notice: Scores for yo→en are less reliable" in capsys.readouterr().err


def test_unreachable_scorer_warns_and_completes_with_errors(tmp_path, llm, doc, capsys):
    cfg = _config(tmp_path, llm.base_url, "http://127.0.0.1:9")
    code = main(
        [
            "run",
            str(doc),
            "--config",
            str(cfg),
            "--target-lang",
            "en",
            "--source-lang",
            "fr",
            "--out",
            str(tmp_path / "o"),
        ]
    )
    assert code == 3
    err = capsys.readouterr().err
    assert "warning: scorer unavailable at http://127.0.0.1:9" in err
    report = Report.model_validate_json((tmp_path / "o" / "report.json").read_text("utf-8"))
    assert {e.stage for e in report.errors} == {"score"}
    assert all(s.score.signals["qe"] is None for s in report.spans)
    assert len([e for e in report.errors if e.stage == "score"]) == len(report.chunks)
    assert all(s.score.reliability.value == "B" for s in report.spans)
    assert all(s.score.meaning is not None for s in report.spans)
    assert report.config.scorer is None  # health check failed: no provenance to record


def test_fake_scorer_command(monkeypatch, capsys):
    def interrupt(self):
        raise KeyboardInterrupt

    kwargs = {}
    real_init = FakeScorerServer.__init__

    def spy(self, *args, **kw):
        kwargs.update(kw)
        real_init(self, *args, **kw)

    monkeypatch.setattr(FakeScorerServer, "__init__", spy)
    monkeypatch.setattr(FakeScorerServer, "serve_forever", interrupt)
    assert main(["fake-scorer", "--port", "0"]) == 0
    assert kwargs["keep_requests"] is False
    assert "fake scorer at http://127.0.0.1:" in capsys.readouterr().out


async def test_check_scorer_prints_each_warning(make_config, capsys):
    cfg = make_config(scorer={"url": "http://127.0.0.1:1"})
    scorer = FakeScorer(supports_qe=False, supports_similarity=False)
    health = await _check_scorer(scorer, cfg)
    assert capsys.readouterr().err.splitlines() == [
        "warning: scorer has no quality-estimation model loaded; qe signals will be empty",
        "warning: scorer has no embedding model loaded; xsim signals will be empty",
    ]
    assert health == await scorer.health()
