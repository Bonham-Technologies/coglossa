import json

import pytest
import yaml

from coglossa import __version__
from coglossa.cli import _write_report, main
from coglossa.schema import Flag, FlagCode, FlagStatus, Report, Revision, RevisionSource
from coglossa.testing.fake_openai import FakeOpenAIServer

TEXT = "Il pleut. Le chat dort.\n\nDemain, le soleil."


@pytest.fixture
def server():
    with FakeOpenAIServer(models=("model-a", "model-b")) as s:
        yield s


def _config(tmp_path, base_url, **extra):
    data = {
        "backends": {"local": {"base_url": base_url, "max_concurrency": 4}},
        "models": {
            "model-a": {"backend": "local", "context_window": 32768},
            "model-b": {"backend": "local", "context_window": 32768},
        },
        "roles": {
            "literal_translator": {"model": "model-a"},
            "contextual_translator": {"model": "model-b"},
            "reviewer": {"model": "model-a"},
            "profiler_guide": {"model": "model-b"},
        },
        "retry": {"max_attempts": 1, "base_delay_s": 0, "max_delay_s": 0},
        **extra,
    }
    path = tmp_path / "coglossa.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "story.txt"
    path.write_text(TEXT, encoding="utf-8")
    return path


def _tasks(server):
    return [
        json.loads(r["messages"][1]["content"].split("INPUT_JSON:", 1)[1])["task"]
        for r in server.requests
    ]


def test_run_end_to_end_over_http(tmp_path, server, doc, capsys):
    out = tmp_path / "out"
    code = main(
        [
            "run",
            str(doc),
            "--config",
            str(_config(tmp_path, server.base_url)),
            "--target-lang",
            "fr",
            "--source-lang",
            "fr",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    report = Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))
    assert [s.text for s in report.spans] == [
        "[fr] Il pleut.",
        "[fr] Le chat dort.",
        "[fr] Demain, le soleil.",
    ]
    captured = capsys.readouterr()
    printed = captured.out
    assert "complete: 3 spans, 0 flags, 0 errors" in printed
    assert "tokens (unscored)\n" in printed and "(tier " not in printed
    assert "notice: No scores were computed" in captured.err
    assert str(out / "report.json") in printed


def test_default_out_dir(tmp_path, server, doc, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "run",
                str(doc),
                "--config",
                str(_config(tmp_path, server.base_url)),
                "--target-lang",
                "en",
            ]
        )
        == 0
    )
    assert (tmp_path / "coglossa-out" / "story" / "report.json").exists()


def test_checkpoint_then_approved_guide(tmp_path, server, doc, capsys):
    cfg = str(_config(tmp_path, server.base_url))
    out = tmp_path / "out"
    assert (
        main(
            [
                "run",
                str(doc),
                "--config",
                cfg,
                "--target-lang",
                "en",
                "--profile",
                "long-form",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    guide_path = out / "guide.json"
    assert guide_path.exists() and (out / "profile.json").exists()
    printed = capsys.readouterr().out
    assert "--approved-guide" in printed
    assert "not yet checked automatically" not in printed
    assert "the sweep enforces them" in printed
    guide = json.loads(guide_path.read_text(encoding="utf-8"))
    guide["glossary"] = [{"term": "chat", "rendering": "cat", "locked": True}]
    guide_path.write_text(json.dumps(guide), encoding="utf-8")
    server.requests.clear()
    assert (
        main(
            [
                "run",
                str(doc),
                "--config",
                cfg,
                "--target-lang",
                "en",
                "--profile",
                "long-form",
                "--guide",
                str(guide_path),
                "--approved-guide",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert "guide" not in _tasks(server)
    report = Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))
    assert report.guide.glossary[0].locked is True


def test_no_checkpoint_flag(tmp_path, server, doc):
    out = tmp_path / "out"
    assert (
        main(
            [
                "run",
                str(doc),
                "--config",
                str(_config(tmp_path, server.base_url)),
                "--target-lang",
                "en",
                "--profile",
                "long-form",
                "--no-checkpoint",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert (out / "report.json").exists()


def test_complete_with_errors_exit_code(tmp_path, server, doc):
    cfg = _config(
        tmp_path,
        server.base_url,
        backends={
            "local": {"base_url": server.base_url},
            "dead": {"base_url": "http://127.0.0.1:9/v1", "timeout_s": 2},
        },
        models={
            "model-a": {"backend": "local", "context_window": 32768},
            "model-b": {"backend": "local", "context_window": 32768},
            "gone": {"backend": "dead", "context_window": 32768},
        },
        roles={
            "literal_translator": {"model": "model-a"},
            "contextual_translator": {"model": "model-b"},
            "reviewer": {"model": "gone"},
            "profiler_guide": {"model": "model-b"},
        },
    )
    out = tmp_path / "out"
    assert (
        main(["run", str(doc), "--config", str(cfg), "--target-lang", "en", "--out", str(out)]) == 3
    )
    report = Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))
    assert report.status.value == "complete_with_errors"


def test_warnings_are_printed(tmp_path, server, doc, capsys):
    cfg = _config(
        tmp_path,
        server.base_url,
        models={"m": {"backend": "local", "context_window": 32768}},
        roles={
            k: {"model": "m"}
            for k in (
                "literal_translator",
                "contextual_translator",
                "reviewer",
                "profiler_guide",
                "judge",
            )
        },
    )
    assert (
        main(
            [
                "run",
                str(doc),
                "--config",
                str(cfg),
                "--target-lang",
                "en",
                "--out",
                str(tmp_path / "o"),
            ]
        )
        == 0
    )
    assert "warning: judge must use a different model" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--config", "missing.yaml"], "config file not found"),
        (["--guide", "nope.json"], "guide file not found"),
    ],
)
def test_failures_exit_1(tmp_path, server, doc, capsys, args, message, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _config(tmp_path, server.base_url)
    assert main(["run", str(doc), "--target-lang", "en", *args]) == 1
    assert message in capsys.readouterr().err


def test_invalid_guide_and_unsupported_input(tmp_path, server, doc, capsys):
    cfg = str(_config(tmp_path, server.base_url))
    bad = tmp_path / "bad.json"
    bad.write_text('{"summary": 1}', encoding="utf-8")
    assert main(["run", str(doc), "--config", cfg, "--target-lang", "en", "--guide", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "not a valid guide" in err
    assert len(err.strip().splitlines()) == 1
    pdf = tmp_path / "scan.odt"
    pdf.write_bytes(b"%PDF")
    assert main(["run", str(pdf), "--config", cfg, "--target-lang", "en"]) == 1
    assert "unsupported file type" in capsys.readouterr().err


def test_usage_errors_exit_2(doc):
    with pytest.raises(SystemExit) as exc:
        main(["run", str(doc), "--target-lang", "en", "--approved-guide"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_fake_openai_command(monkeypatch, capsys):
    def interrupt(self):
        raise KeyboardInterrupt

    kwargs = {}
    real_init = FakeOpenAIServer.__init__

    def spy(self, *args, **kw):
        kwargs.update(kw)
        real_init(self, *args, **kw)

    monkeypatch.setattr(FakeOpenAIServer, "__init__", spy)
    monkeypatch.setattr(FakeOpenAIServer, "serve_forever", interrupt)
    assert main(["fake-openai", "--port", "0", "--model", "m1"]) == 0
    assert kwargs["keep_requests"] is False
    assert "fake OpenAI-compatible server at http://127.0.0.1:" in capsys.readouterr().out


def test_bad_prompt_id_is_a_clean_error(tmp_path, server, doc, capsys):
    cfg = _config(
        tmp_path,
        server.base_url,
        roles={
            "literal_translator": {"model": "model-a"},
            "contextual_translator": {"model": "model-b"},
            "extra_translators": [{"name": "x", "prompt": "nope", "model": "model-a"}],
            "reviewer": {"model": "model-a"},
            "profiler_guide": {"model": "model-b"},
        },
    )
    args = ["run", str(doc), "--config", str(cfg), "--target-lang", "en"]
    assert main([*args, "--out", str(tmp_path / "o")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "nope" in err
    assert server.requests == []


def test_resume_command_round_trips(tmp_path, server, capsys):
    import shlex

    workdir = tmp_path / "my dir"
    workdir.mkdir()
    doc = workdir / "my story.txt"
    doc.write_text(TEXT, encoding="utf-8")
    cfg = _config(workdir, server.base_url)
    out = workdir / "out put"
    assert (
        main(
            [
                "run",
                str(doc),
                "--config",
                str(cfg),
                "--target-lang",
                "en",
                "--source-lang",
                "fr",
                "--profile",
                "long-form",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    printed = capsys.readouterr().out
    line = next(ln for ln in printed.splitlines() if ln.strip().startswith("coglossa run"))
    argv = shlex.split(line)
    assert argv[0] == "coglossa" and "--profile-file" in argv and "--out" in argv
    server.requests.clear()
    assert main(argv[1:]) == 0
    assert set(_tasks(server)) == {"translate", "reconcile"}
    report = Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))
    assert report.profile.source_lang == "fr"


def test_profile_file_is_used_verbatim(tmp_path, server, doc, capsys):
    from coglossa.schema import DocumentProfile

    profile = DocumentProfile(
        source_lang="fr",
        target_lang="en",
        mixed_language=False,
        genre="poetry",
        tone_register="hand-edited register",
        word_count=7,
        pipeline_profile="short",
    )
    pfile = tmp_path / "profile.json"
    pfile.write_text(profile.model_dump_json(), encoding="utf-8")
    out = tmp_path / "out"
    base = ["run", str(doc), "--config", str(_config(tmp_path, server.base_url))]
    args = [*base, "--target-lang", "en", "--out", str(out), "--profile-file", str(pfile)]
    assert main(args) == 0
    assert "profile" not in _tasks(server)
    report = Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))
    assert report.profile == profile
    # target-language mismatch is an error
    server.requests.clear()
    args = [*base, "--target-lang", "de", "--out", str(out), "--profile-file", str(pfile)]
    assert main(args) == 1
    assert "target" in capsys.readouterr().err and server.requests == []


@pytest.mark.parametrize("content", [None, "not json", '{"source_lang": 1}'])
def test_bad_profile_file(tmp_path, server, doc, capsys, content):
    pfile = tmp_path / "p.json"
    if content is not None:
        pfile.write_text(content, encoding="utf-8")
    args = ["run", str(doc), "--config", str(_config(tmp_path, server.base_url)), "--target-lang"]
    assert main([*args, "en", "--profile-file", str(pfile)]) == 1
    err = capsys.readouterr().err
    assert "profile file" in err
    assert len(err.strip().splitlines()) == 1


def test_checkpoint_with_failed_excerpt_saves_and_warns(tmp_path, server, doc, capsys, monkeypatch):
    from coglossa.pipeline import GuideCheckpoint
    from coglossa.schema import DocumentProfile, Guide, RunError

    profile = DocumentProfile(
        source_lang="fr",
        target_lang="en",
        mixed_language=False,
        genre="other",
        tone_register="x",
        word_count=1,
        pipeline_profile="long-form",
    )
    errors = [RunError(stage="guide", chunk_id="c2", role="guide", message="boom")]

    async def fake_run(*a, **k):
        return GuideCheckpoint(
            profile=profile, guide=Guide(summary="s", style_guide="g"), errors=errors
        )

    monkeypatch.setattr("coglossa.cli.run_document", fake_run)
    out = tmp_path / "out"
    cfg = str(_config(tmp_path, server.base_url))
    assert main(["run", str(doc), "--config", cfg, "--target-lang", "en", "--out", str(out)]) == 0
    saved = json.loads((out / "guide_errors.json").read_text(encoding="utf-8"))
    assert saved[0]["message"] == "boom"
    err = capsys.readouterr().err
    assert "warning: 1 excerpt(s) failed while building the guide" in err
    assert str(out / "guide_errors.json") in err


def test_unwritable_out_dir_fails_before_any_model_call(tmp_path, server, doc, capsys):
    blocker = tmp_path / "taken"
    blocker.write_text("i am a file", encoding="utf-8")
    cfg = str(_config(tmp_path, server.base_url))
    for out in (blocker, blocker / "sub"):
        argv = ["run", str(doc), "--config", cfg, "--target-lang", "en", "--out", str(out)]
        assert main(argv) == 1
        assert "error: cannot write output directory" in capsys.readouterr().err
    assert server.requests == []


def test_report_write_failure_is_a_clean_error(tmp_path, server, doc, capsys):
    out = tmp_path / "out"
    (out / "report.json").mkdir(parents=True)  # a directory where the file should go
    cfg = str(_config(tmp_path, server.base_url))
    argv = ["run", str(doc), "--config", cfg, "--target-lang", "en", "--out", str(out)]
    assert main(argv) == 1
    assert "error: cannot write" in capsys.readouterr().err


def test_progress_goes_to_stderr(tmp_path, server, doc, capsys):
    argv = ["run", str(doc), "--config", str(_config(tmp_path, server.base_url))]
    assert main([*argv, "--target-lang", "en", "--out", str(tmp_path / "o")]) == 0
    err = [ln for ln in capsys.readouterr().err.splitlines() if not ln.startswith("notice: ")]
    assert err[0].startswith("coglossa: profile: ")
    assert err[-2] == "coglossa: report: assembled"
    assert err[-1] == "coglossa: scoring: unscored (no scorer or judge produced scores)"
    assert "coglossa: chunk 1/1 done" in err


def test_internal_errors_and_interrupts(tmp_path, server, doc, capsys, monkeypatch):
    argv = ["run", str(doc), "--config", str(_config(tmp_path, server.base_url))]
    argv += ["--target-lang", "en", "--out", str(tmp_path / "o")]

    async def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr("coglossa.cli.run_document", boom)
    assert main(argv) == 1
    assert "error: internal error: RuntimeError: kaput" in capsys.readouterr().err

    async def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr("coglossa.cli.run_document", interrupted)
    assert main(argv) == 130
    assert "interrupted" in capsys.readouterr().err


def test_summary_counts_open_flags_and_sweep_revisions(tmp_path, server, doc, capsys):
    out = tmp_path / "out"
    args = ["run", str(doc), "--config", str(_config(tmp_path, server.base_url))]
    assert main([*args, "--target-lang", "fr", "--source-lang", "fr", "--out", str(out)]) == 0
    base = Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))
    span = base.spans[0]

    def flag(status):
        return Flag(code=FlagCode.TERMINOLOGY_VIOLATION, explanation="x", status=status)

    flagged = span.model_copy(update={"flags": [flag(FlagStatus.RESOLVED), flag(FlagStatus.OPEN)]})
    revision = Revision(
        span_id=span.id, source=RevisionSource.SWEEP, before="a", after="b", reason="r"
    )
    report = base.model_copy(update={"spans": [flagged], "revisions": [revision]})
    capsys.readouterr()
    _write_report(report, out)
    printed = capsys.readouterr().out
    assert "1 spans, 1 flags," in printed
    assert "(unscored), 1 revised by the sweep\n" in printed
