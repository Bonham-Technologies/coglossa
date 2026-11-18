import json

import yaml

from coglossa.cli import main
from coglossa.testing.fake_openai import FakeOpenAIServer
from coglossa.testing.fake_scorer import FakeScorerServer

MODELS = ("model-a", "model-b")


def write_configs(tmp_path, mini_flores, llm_url, scorer_url):
    system = {
        "backends": {"local": {"base_url": llm_url}},
        "models": {m: {"backend": "local", "context_window": 32768} for m in MODELS},
        "roles": {
            "literal_translator": {"model": "model-a"},
            "contextual_translator": {"model": "model-b"},
            "reviewer": {"model": "model-a"},
            "profiler_guide": {"model": "model-b"},
        },
        "retry": {"max_attempts": 1, "base_delay_s": 0, "max_delay_s": 0},
    }
    (tmp_path / "coglossa.yaml").write_text(yaml.safe_dump(system), encoding="utf-8")
    bench = {
        "dataset": {
            "kind": "flores200",
            "path": str(mini_flores),
            "pairs": ["en>es"],
            "sentences": 4,
        },
        "systems": [
            {"name": "pipeline", "kind": "pipeline", "config": "coglossa.yaml"},
            {"name": "baseline", "kind": "baseline", "config": "coglossa.yaml", "model": "model-b"},
        ],
        "scorer_url": scorer_url,
    }
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(bench), encoding="utf-8")
    return path


def test_bench_run_end_to_end(tmp_path, mini_flores, capsys):
    out = tmp_path / "out"
    with FakeOpenAIServer(models=MODELS) as llm, FakeScorerServer() as scorer:
        bench = write_configs(tmp_path, mini_flores, llm.base_url, scorer.base_url)
        assert main(["bench", "run", str(bench), "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert f"wrote {out / 'summary.md'}" in captured.out
    assert "bench: pipeline en>es: 4 units, MetricX 2.0" in captured.err
    rows = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert [r["system"] for r in rows] == ["baseline", "pipeline"]
    assert all(r["status"] == "complete" and r["metricx_mean"] == 2.0 for r in rows)


def test_bench_run_exits_1_when_a_pair_fails(tmp_path, mini_flores, capsys):
    out = tmp_path / "out"
    bench = write_configs(tmp_path, mini_flores, "http://127.0.0.1:9/v1", None)
    assert main(["bench", "run", str(bench), "--out", str(out)]) == 1
    errors = [x for x in capsys.readouterr().err.splitlines() if x.startswith("error:")]
    assert len(errors) == 2  # the pipeline raised; the baseline translated nothing
    assert errors[0].startswith("error: baseline en>es: 4 of 4 sentences untranslated: ")
    assert errors[1].startswith("error: pipeline en>es: ")
    assert all("Traceback" not in e for e in errors)


def test_missing_bench_file_is_one_error_line(tmp_path, capsys):
    assert main(["bench", "run", str(tmp_path / "nope.yaml"), "--out", str(tmp_path / "o")]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert err == [f"error: bench config file not found: {tmp_path / 'nope.yaml'}"]


def test_missing_flores_data_is_one_error_line(tmp_path, capsys):
    bench = write_configs(tmp_path, tmp_path / "nowhere", "http://127.0.0.1:9/v1", None)
    assert main(["bench", "run", str(bench), "--out", str(tmp_path / "o")]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("error: no FLORES-200 data at")


def test_fetch_flores_prints_where_the_data_is(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_fetch(dest, *, log=print):
        calls.append(dest)
        log("downloading")
        return dest

    monkeypatch.setattr("coglossa.bench.cli.fetch_flores", fake_fetch)
    assert main(["bench", "fetch-flores", "--out", str(tmp_path / "f")]) == 0
    out = capsys.readouterr()
    d = tmp_path / "f"
    assert calls == [d]
    assert out.out.strip() == f"FLORES-200 is in {d} (CC BY-SA 4.0; see {d}/ATTRIBUTION.txt)"
    assert "downloading" in out.err


def test_fetch_failure_is_one_error_line(tmp_path, monkeypatch, capsys):
    from coglossa.config import ConfigError

    def broken(dest, *, log=print):
        raise ConfigError("cannot download FLORES-200: offline")

    monkeypatch.setattr("coglossa.bench.cli.fetch_flores", broken)
    assert main(["bench", "fetch-flores", "--out", str(tmp_path / "f")]) == 1
    assert capsys.readouterr().err.strip() == "error: cannot download FLORES-200: offline"


def test_unwritable_out_is_one_error_line(tmp_path, mini_flores, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    bench = write_configs(tmp_path, mini_flores, "http://127.0.0.1:9/v1", None)
    assert main(["bench", "run", str(bench), "--out", str(blocker / "out")]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) >= 1 and err[-1].startswith("error: ")


def test_ctrl_c_exits_130(tmp_path, monkeypatch, capsys):
    def interrupted(dest, *, log=print):
        raise KeyboardInterrupt

    monkeypatch.setattr("coglossa.bench.cli.fetch_flores", interrupted)
    assert main(["bench", "fetch-flores", "--out", str(tmp_path)]) == 130
    assert capsys.readouterr().err.strip() == "interrupted"


def write_flag_configs(tmp_path, mini_flores, llm_url, scorer_url, *, judge=True):
    roles = {
        "literal_translator": {"model": "model-a"},
        "contextual_translator": {"model": "model-b"},
        "reviewer": {"model": "model-a"},
        "profiler_guide": {"model": "model-b"},
        "roundtrip": {"model": "model-c"},
    }
    if judge:
        roles["judge"] = {"model": "model-c"}
    scoring = {
        "backends": {"local": {"base_url": llm_url}},
        "models": {m: {"backend": "local", "context_window": 32768} for m in (*MODELS, "model-c")},
        "roles": roles,
        "retry": {"max_attempts": 1, "base_delay_s": 0, "max_delay_s": 0},
    }
    if scorer_url:
        scoring["scorer"] = {"url": scorer_url}
    (tmp_path / "scoring.yaml").write_text(yaml.safe_dump(scoring), encoding="utf-8")
    bench = {
        "dataset": {"kind": "flores200", "path": str(mini_flores), "pairs": ["en>es"]},
        "systems": [{"name": "pipeline", "kind": "pipeline", "config": "scoring.yaml"}],
    }
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(bench), encoding="utf-8")
    return path, tmp_path / "scoring.yaml"


def test_bench_flags_end_to_end(tmp_path, mini_flores, capsys):
    out = tmp_path / "out"
    with FakeOpenAIServer(models=(*MODELS, "model-c")) as llm, FakeScorerServer() as scorer:
        bench, scoring = write_flag_configs(tmp_path, mini_flores, llm.base_url, scorer.base_url)
        argv = ["bench", "flags", str(bench), "--scoring-config", str(scoring), "--out", str(out)]
        assert main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"wrote {out / 'flag-summary.md'}"
    assert "no injector configured: negation and register are not tested" in captured.err
    assert (out / "flags" / "en-es.jsonl").is_file()
    assert "## Tier A" in (out / "flag-summary.md").read_text(encoding="utf-8")


def test_bench_flags_keeps_finished_pairs_unless_forced(tmp_path, mini_flores, capsys):
    out = tmp_path / "out"
    with FakeOpenAIServer(models=(*MODELS, "model-c")) as llm, FakeScorerServer() as scorer:
        bench, scoring = write_flag_configs(tmp_path, mini_flores, llm.base_url, scorer.base_url)
        argv = ["bench", "flags", str(bench), "--scoring-config", str(scoring), "--out", str(out)]
        assert main(argv) == 0
        capsys.readouterr()
        assert main([*argv, "--pairs", "2"]) == 0
        assert "flags: en>es: kept from an earlier run" in capsys.readouterr().err
        assert main([*argv, "--force"]) == 0
        err = capsys.readouterr().err
    assert "kept from an earlier run" not in err
    assert "flags: en>es: " in err and "spans scored" in err


def test_bench_flags_pairs_below_one_is_one_error_line(tmp_path, mini_flores, capsys):
    bench, scoring = write_flag_configs(tmp_path, mini_flores, "http://127.0.0.1:9/v1", None)
    argv = ["bench", "flags", str(bench), "--scoring-config", str(scoring), "--out", str(tmp_path)]
    assert main([*argv, "--pairs", "0"]) == 1
    assert capsys.readouterr().err.strip().splitlines() == ["error: --pairs must be at least 1"]


def test_bench_flags_without_judge_or_scorer_is_one_error_line(tmp_path, mini_flores, capsys):
    bench, scoring = write_flag_configs(
        tmp_path, mini_flores, "http://127.0.0.1:9/v1", None, judge=False
    )
    argv = ["bench", "flags", str(bench), "--scoring-config", str(scoring), "--out", str(tmp_path)]
    assert main(argv) == 1
    assert capsys.readouterr().err.strip().splitlines() == [
        "error: the scoring config has no judge and no scorer; nothing can raise a flag"
    ]
