import json

import pytest
import yaml

from coglossa.bench.config import BenchConfig, parse_pair
from coglossa.bench.flores import ATTRIBUTION_TEXT, LICENSE_TEXT
from coglossa.bench.metrics import SystemPairResult, UnitResult, shared_metricx, summarise
from coglossa.bench.run import pick_indexes, run_bench
from coglossa.config import ConfigError
from coglossa.llm.payload import parse_input
from coglossa.llm.types import PermanentBackendError
from coglossa.schema import ScorerHealth
from coglossa.scoring.client import ScorerUnavailableError
from coglossa.testing.fake_backend import FakeBackend
from tests.conftest import BASE_CONFIG


class ScorerDouble:
    """Reference-mode MetricX double: 0 for an exact match, else 10; LaBSE unused."""

    name, supports_qe, supports_similarity = "double", True, False

    def __init__(self, qe_model="model-a"):
        self.items = []
        self.qe_model = qe_model

    async def qe(self, items):
        self.items.extend(items)
        return [0.0 if i.hypothesis == i.reference else 10.0 for i in items]

    async def similarity(self, pairs):
        raise AssertionError("not used")

    async def health(self):
        return ScorerHealth(qe_model=self.qe_model, device="cpu")

    async def aclose(self):
        pass


class YorubaFails(FakeBackend):
    async def chat(self, request):
        if any('"target_lang": "yo"' in m.content for m in request.messages):
            raise PermanentBackendError("no yoruba today")
        return await super().chat(request)


class DeadAfterProfiling(FakeBackend):
    """The model server goes away once the document is profiled."""

    async def chat(self, request):
        if parse_input([m.model_dump() for m in request.messages])["task"] in {"profile", "guide"}:
            return await super().chat(request)
        raise PermanentBackendError("server went away")


def write_system_config(tmp_path, name="coglossa.yaml", **overrides):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({**BASE_CONFIG, **overrides}), encoding="utf-8")
    return path


def bench_config(tmp_path, mini_flores, *, scorer_url="http://127.0.0.1:9", sentences=8, **extra):
    sys_cfg = write_system_config(tmp_path)
    return BenchConfig.model_validate(
        {
            "dataset": {
                "kind": "flores200",
                "path": mini_flores,
                "split": "devtest",
                "pairs": ["en>es", "en>yo"],
                "sentences": sentences,
                "seed": 7,
            },
            "systems": [
                {"name": "pipeline", "kind": "pipeline", "config": sys_cfg},
                {"name": "baseline", "kind": "baseline", "config": sys_cfg, "model": "model-b"},
            ],
            "scorer_url": scorer_url,
            **extra,
        }
    )


def factory(backend):
    return lambda cfg: {"fake": backend}


def read(out, system, slug):
    return SystemPairResult.model_validate_json(
        (out / "runs" / system / f"{slug}.json").read_text(encoding="utf-8")
    )


async def test_run_writes_results_and_a_summary(tmp_path, mini_flores, fake_backend):
    cfg, out, scorer = bench_config(tmp_path, mini_flores), tmp_path / "out", ScorerDouble()
    rows = await run_bench(
        cfg, out, backends_factory=factory(fake_backend), scorer=scorer, log=lambda m: None
    )
    for system, slug in [
        ("pipeline", "en-es"),
        ("pipeline", "en-yo"),
        ("baseline", "en-es"),
        ("baseline", "en-yo"),
    ]:
        assert read(out, system, slug).status == "complete"
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert len(summary) == len(rows) == 4
    for row in summary:
        assert row["status"] == "complete" and row["units"] == 8 and row["scored_units"] == 8
        assert row["metricx_mean"] == 10.0
        assert 0 < row["chrf_corpus"] < 100
        assert row["kind"] == row["system"]  # the fixture names each system after its kind
        assert row["metricx_shared"] == 10.0 and row["shared_units"] == 8
        assert row["long_sources"] == row["long_outputs"] == 0
        # every other sentence has a formatted word; the synthetic models keep its markers
        assert row["markers_total"] == 4 and row["markers_kept"] == 4
    meta = json.loads((out / "bench-meta.json").read_text(encoding="utf-8"))
    assert meta["tier_cuts"] == {"a_max": 5.0, "b_max": 10.0}
    assert meta["dataset"]["seed"] == 7 and meta["dataset"]["pairs"] == ["en>es", "en>yo"]
    assert meta["systems"] == [
        {"name": "pipeline", "kind": "pipeline"},
        {"name": "baseline", "kind": "baseline"},
    ]  # the bench config's order
    md = (out / "summary.md").read_text(encoding="utf-8")
    header = (
        "| system | pair | MetricX ↓ | scored/units | too long src/out | chrF ↑ | markers kept "
        "| tokens in | tokens out | seconds | errors |"
    )
    assert header in md
    lines = md.splitlines()
    assert any(line.startswith("| baseline | en>es | 10.00 | 8/8 | 0/0 | ") for line in lines)
    assert all(" | 4/4 | " in line for line in lines if line.startswith("| baseline "))
    assert '"markers kept" counts the passages with formatting in the source' in md
    assert "devtest" in md and "seed 7" in md and "lower is better" in md
    assert "every system of the pair scored" in md and "weighted by sentence count" in md
    assert "Units every system scored: en>es 8, en>yo 8." in md
    result = read(out, "pipeline", "en-es")
    assert result.fingerprint is not None and not result.metricx_pending
    assert result.metricx_by == "model-a@1500"
    assert (result.fingerprint.dataset, result.fingerprint.sentences) == ("flores200", 8)
    assert result.fingerprint.model is None
    assert read(out, "baseline", "en-es").fingerprint.model == "model-b"
    assert scorer.items and all(i.reference for i in scorer.items)
    assert not list(out.rglob("*.tmp"))


async def test_a_failed_pair_is_recorded_and_rerun(tmp_path, mini_flores, fake_backend):
    cfg, out = bench_config(tmp_path, mini_flores), tmp_path / "out"
    logs: list[str] = []
    rows = await run_bench(
        cfg, out, backends_factory=factory(YorubaFails()), scorer=ScorerDouble(), log=logs.append
    )
    failed = read(out, "pipeline", "en-yo")
    assert failed.status == "failed" and failed.units == []
    assert failed.error and "\n" not in failed.error
    # the baseline swallows per-chunk failures, but it translated nothing: failed as well
    failed_pairs = [(r.system, r.pair) for r in rows if r.status == "failed"]
    assert failed_pairs == [("baseline", "en>yo"), ("pipeline", "en>yo")]
    base = read(out, "baseline", "en-yo")
    assert base.status == "failed" and base.units == []
    assert base.error.startswith("8 of 8 sentences untranslated: translate: ")
    assert "\n" not in base.error
    for r in rows:
        if r.status == "failed":  # no invented numbers
            assert r.chrf_corpus is None and r.metricx_mean is None and r.units == 0

    mtimes = {
        p: p.stat().st_mtime_ns for p in (out / "runs").glob("*/*.json") if p.name != "en-yo.json"
    }
    logs.clear()
    rows = await run_bench(
        cfg, out, backends_factory=factory(fake_backend), scorer=ScorerDouble(), log=logs.append
    )
    assert "skipping 2 finished system-pairs" in logs
    assert sum(m.startswith("bench: ") for m in logs) == 2  # exactly the two failed pairs
    assert all(r.status == "complete" for r in rows)
    assert read(out, "pipeline", "en-yo").status == "complete"
    assert read(out, "baseline", "en-yo").status == "complete"
    assert all(p.stat().st_mtime_ns == m for p, m in mtimes.items())

    logs.clear()
    await run_bench(
        cfg,
        out,
        force=True,
        backends_factory=factory(fake_backend),
        scorer=ScorerDouble(),
        log=logs.append,
    )
    assert not any(m.startswith("skipping") for m in logs)
    assert sum(m.startswith("bench: ") for m in logs) == 4


async def test_a_server_dying_after_profiling_fails_both_systems(tmp_path, mini_flores):
    cfg, out = bench_config(tmp_path, mini_flores), tmp_path / "out"
    rows = await run_bench(
        cfg,
        out,
        backends_factory=factory(DeadAfterProfiling()),
        scorer=ScorerDouble(),
        log=lambda m: None,
    )
    assert len(rows) == 4 and all(r.status == "failed" for r in rows)
    assert all(r.chrf_corpus is None and r.metricx_mean is None for r in rows)
    error = read(out, "pipeline", "en-es").error
    assert error and "sentences untranslated" in error and "\n" not in error
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "| pipeline | en>es | n/a | 0/0 | 0/0 | n/a |" in md


async def test_skipping_one_pair_is_singular(tmp_path, mini_flores, fake_backend):
    cfg, out = bench_config(tmp_path, mini_flores), tmp_path / "out"
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=lambda m: None)
    (out / "runs/baseline/en-yo.json").write_text("{not json", encoding="utf-8")  # damaged
    (out / "runs/pipeline/en-yo.json").unlink()
    logs: list[str] = []
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=logs.append)
    assert logs[0] == "skipping 2 finished system-pairs"
    (out / "runs/baseline/en-yo.json").unlink()
    logs.clear()
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=logs.append)
    assert logs[0] == "skipping 3 finished system-pairs"
    logs.clear()
    (out / "runs/baseline/en-yo.json").write_text("{not json", encoding="utf-8")
    for p in [("pipeline", "en-es"), ("pipeline", "en-yo"), ("baseline", "en-es")]:
        (out / "runs" / p[0] / f"{p[1]}.json").unlink()
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=logs.append)
    assert logs == [m for m in logs if m.startswith("bench: ")]  # nothing to skip


async def test_over_long_units_are_not_scored(tmp_path, mini_flores, fake_backend):
    # unit size = source + output + reference; the longest sentence is the only one past 170
    sys_cfg = write_system_config(tmp_path, "short.yaml", scoring={"qe_max_chars": 170})
    cfg = bench_config(tmp_path, mini_flores)
    cfg = cfg.model_copy(
        update={"systems": [s.model_copy(update={"config": sys_cfg}) for s in cfg.systems]}
    )
    scorer = ScorerDouble()
    await run_bench(
        cfg,
        tmp_path / "out",
        backends_factory=factory(fake_backend),
        scorer=scorer,
        log=lambda m: None,
    )
    result = read(tmp_path / "out", "baseline", "en-es")
    over = [u for u in result.units if len(u.source) + len(u.output) + len(u.reference) > 170]
    assert len(over) == 1 and over[0].metricx is None
    row = next(
        r
        for r in json.loads((tmp_path / "out/summary.json").read_text())
        if (r["system"], r["pair"]) == ("baseline", "en>es")
    )
    assert row["scored_units"] == 7
    assert all(len(i.source) + len(i.hypothesis) + len(i.reference) <= 170 for i in scorer.items)


async def test_no_scorer_means_chrf_only(tmp_path, mini_flores, fake_backend):
    cfg = bench_config(tmp_path, mini_flores, scorer_url=None)
    await run_bench(
        cfg, tmp_path / "out", backends_factory=factory(fake_backend), log=lambda m: None
    )
    result = read(tmp_path / "out", "baseline", "en-es")
    assert all(u.metricx is None and u.chrf > 0 for u in result.units)
    assert result.errors == [] and result.metricx_pending and result.metricx_by is None
    summary = json.loads((tmp_path / "out/summary.json").read_text())
    assert all(r["metricx_mean"] is None and r["scored_units"] == 0 for r in summary)
    md = (tmp_path / "out/summary.md").read_text(encoding="utf-8")
    assert "n/a" in md
    assert (
        "MetricX pending for 4 system-pair(s): a run with a working scorer scores them "
        "without translating again." in md
    )


async def test_an_unreachable_scorer_leaves_metricx_null_and_notes_it(
    tmp_path, mini_flores, fake_backend
):
    from coglossa.scoring.client import ScorerUnavailableError

    class Down(ScorerDouble):
        async def qe(self, items):
            raise ScorerUnavailableError("scorer unreachable: refused")

    cfg = bench_config(tmp_path, mini_flores)
    await run_bench(
        cfg,
        tmp_path / "out",
        backends_factory=factory(fake_backend),
        scorer=Down(),
        log=lambda m: None,
    )
    result = read(tmp_path / "out", "baseline", "en-es")
    assert result.status == "complete" and all(u.metricx is None for u in result.units)
    assert "scorer: scorer unreachable: refused" in result.errors


async def test_a_scorer_without_qe_scores_nothing(tmp_path, mini_flores, fake_backend):
    scorer = ScorerDouble(qe_model=None)
    scorer.supports_qe = False
    logs: list[str] = []
    await run_bench(
        bench_config(tmp_path, mini_flores),
        tmp_path / "out",
        backends_factory=factory(fake_backend),
        scorer=scorer,
        log=logs.append,
    )
    assert scorer.items == []
    assert logs.count("scorer has no QE model: MetricX stays pending") == 1
    result = read(tmp_path / "out", "baseline", "en-es")
    assert result.metricx_pending and result.metricx_by is None


def test_seed_and_sentences_pick_the_same_lines():
    first = pick_indexes(1012, 3, 7)
    assert first == pick_indexes(1012, 3, 7) and first == sorted(first) and len(set(first)) == 3
    assert pick_indexes(1012, 3, 8) != first
    assert pick_indexes(5, 99, 1) == [0, 1, 2, 3, 4]


async def test_sampled_sentences_are_the_same_for_every_system(tmp_path, mini_flores, fake_backend):
    cfg = bench_config(tmp_path, mini_flores, scorer_url=None)
    cfg = cfg.model_copy(update={"dataset": cfg.dataset.model_copy(update={"sentences": 3})})
    await run_bench(
        cfg, tmp_path / "out", backends_factory=factory(fake_backend), log=lambda m: None
    )
    a, b = (read(tmp_path / "out", s, "en-es") for s in ("pipeline", "baseline"))
    assert [u.source for u in a.units] == [u.source for u in b.units]
    assert len(a.units) == 3
    # a unit's reference is the matching sentence of the target file
    assert all(u.reference == f"[spa_Latn] {u.source}" for u in a.units)


async def test_multi_sentence_units_are_scored_against_joined_text(tmp_path):
    from coglossa.bench.metrics import score_units
    from coglossa.bench.units import Unit

    scorer = ScorerDouble()
    units = [Unit(0, 1, "uno dos"), Unit(2, 2, "")]
    results, errors = await score_units(
        units, ["a", "b", "c"], ["A", "B", "C"], scorer, pair=parse_pair("en>es"), qe_max_chars=100
    )
    assert errors == []
    assert [(i.source, i.reference) for i in scorer.items] == [("a b", "A B")]
    assert results[0].metricx == 10.0 and results[1].metricx is None  # empty output: not sent


async def test_parallel_dataset(tmp_path, fake_backend):
    (tmp_path / "s.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "r.txt").write_text("uno\ndos\ntres\n", encoding="utf-8")
    sys_cfg = write_system_config(tmp_path)
    cfg = BenchConfig.model_validate(
        {
            "dataset": {
                "kind": "parallel",
                "path": tmp_path,
                "pairs": ["en>es"],
                "sentences": 10,
                "source_file": tmp_path / "s.txt",
                "reference_file": tmp_path / "r.txt",
            },
            "systems": [{"name": "b", "kind": "baseline", "config": sys_cfg, "model": "model-b"}],
        }
    )
    await run_bench(
        cfg, tmp_path / "out", backends_factory=factory(fake_backend), log=lambda m: None
    )
    assert [u.reference for u in read(tmp_path / "out", "b", "en-es").units] == [
        "uno",
        "dos",
        "tres",
    ]
    assert not (tmp_path / "out" / "ATTRIBUTION.txt").exists()  # no FLORES text, no notice
    (tmp_path / "r.txt").write_text("uno\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="have 3 and 1 lines"):
        await run_bench(
            cfg, tmp_path / "out2", backends_factory=factory(fake_backend), log=lambda m: None
        )
    (tmp_path / "r.txt").unlink()
    with pytest.raises(ConfigError, match="cannot read"):
        await run_bench(
            cfg, tmp_path / "out3", backends_factory=factory(fake_backend), log=lambda m: None
        )


async def test_backends_are_closed(tmp_path, mini_flores, fake_backend):
    await run_bench(
        bench_config(tmp_path, mini_flores),
        tmp_path / "out",
        backends_factory=factory(fake_backend),
        log=lambda m: None,
    )
    assert fake_backend.closed


async def test_flores_results_carry_the_attribution(tmp_path, mini_flores, fake_backend):
    cfg, out = bench_config(tmp_path, mini_flores, sentences=2), tmp_path / "out"
    await run_bench(
        cfg, out, backends_factory=factory(fake_backend), scorer=ScorerDouble(), log=lambda m: None
    )
    assert (out / "LICENSE.txt").read_text(encoding="utf-8") == LICENSE_TEXT
    assert (out / "ATTRIBUTION.txt").read_text(encoding="utf-8") == ATTRIBUTION_TEXT


def quiet(m):
    pass


async def test_changed_settings_are_rerun_not_reused(tmp_path, mini_flores, fake_backend):
    cfg, out = bench_config(tmp_path, mini_flores, sentences=4), tmp_path / "out"
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=quiet)

    async def rerun(new_cfg):
        logs: list[str] = []
        await run_bench(new_cfg, out, backends_factory=factory(fake_backend), log=logs.append)
        return [m for m in logs if m.startswith("re-running ")]

    ds = cfg.dataset
    for change in [{"sentences": 5}, {"seed": 8}, {"split": "dev"}]:
        changed = cfg.model_copy(update={"dataset": ds.model_copy(update=change)})
        assert len(await rerun(changed)) == 4, change
        assert len(read(out, "pipeline", "en-es").units) == changed.dataset.sentences
        assert await rerun(changed) == []  # now finished with these settings
        assert await rerun(cfg) and await rerun(cfg) == []  # back again

    # a system's own config, and a baseline's model
    other = write_system_config(
        tmp_path,
        "other.yaml",
        models={
            **BASE_CONFIG["models"],
            "model-a": {**BASE_CONFIG["models"]["model-a"], "temperature": 0.7},
        },
    )
    systems = [cfg.systems[0].model_copy(update={"config": other}), cfg.systems[1]]
    assert await rerun(cfg.model_copy(update={"systems": systems})) == [
        "re-running pipeline en>es: settings changed",
        "re-running pipeline en>yo: settings changed",
    ]
    systems = [systems[0], cfg.systems[1].model_copy(update={"model": "model-a"})]
    assert await rerun(cfg.model_copy(update={"systems": systems})) == [
        "re-running baseline en>es: settings changed",
        "re-running baseline en>yo: settings changed",
    ]
    assert read(out, "baseline", "en-es").fingerprint.model == "model-a"


async def test_edits_that_do_not_shape_the_text_keep_the_results(
    tmp_path, mini_flores, fake_backend
):
    cfg, out = bench_config(tmp_path, mini_flores, sentences=4), tmp_path / "out"
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=quiet)
    calls = len(fake_backend.requests)
    write_system_config(
        tmp_path,
        scoring={"thresholds": {"style": 0.11}, "qe_max_chars": 900},
        backends={"fake": {**BASE_CONFIG["backends"]["fake"], "timeout_s": 5.0}},
    )
    logs: list[str] = []
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=logs.append)
    assert logs == ["skipping 4 finished system-pairs"]
    assert len(fake_backend.requests) == calls


async def test_a_prompt_text_edit_is_rerun(tmp_path, mini_flores, fake_backend):
    prompts = tmp_path / "prompts"
    (prompts / "contextual").mkdir(parents=True)
    template = prompts / "contextual" / "v1.j2"
    template.write_text("Translate with context.", encoding="utf-8")
    cfg, out = bench_config(tmp_path, mini_flores, sentences=2), tmp_path / "out"
    write_system_config(tmp_path, prompts_dir=str(prompts))
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=quiet)
    template.write_text("Translate, and keep the tone.", encoding="utf-8")
    logs: list[str] = []
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=logs.append)
    assert "re-running pipeline en>es: settings changed" in logs
    assert "re-running baseline en>es: settings changed" in logs


async def test_a_result_with_an_old_fingerprint_is_redone(tmp_path, mini_flores, fake_backend):
    cfg, out = bench_config(tmp_path, mini_flores, sentences=2), tmp_path / "out"
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=quiet)
    path = out / "runs" / "pipeline" / "en-es.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["fingerprint"] = {"dataset": "flores200", "config": "abc"}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert read(out, "pipeline", "en-es").fingerprint is None
    logs: list[str] = []
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=logs.append)
    assert "re-running pipeline en>es: written by an older version" in logs
    assert read(out, "pipeline", "en-es").fingerprint is not None


async def test_changed_parallel_data_is_rerun(tmp_path, fake_backend):
    (tmp_path / "s.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "r.txt").write_text("uno\ndos\n", encoding="utf-8")
    cfg = BenchConfig.model_validate(
        {
            "dataset": {
                "kind": "parallel",
                "path": tmp_path,
                "pairs": ["en>es"],
                "source_file": tmp_path / "s.txt",
                "reference_file": tmp_path / "r.txt",
            },
            "systems": [
                {"name": "b", "kind": "baseline", "config": write_system_config(tmp_path),
                 "model": "model-b"},
            ],
        }
    )  # fmt: skip
    out = tmp_path / "out"
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=quiet)
    (tmp_path / "r.txt").write_text("uno\nDOS\n", encoding="utf-8")
    logs: list[str] = []
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=logs.append)
    assert "re-running b en>es: settings changed" in logs
    assert [u.reference for u in read(out, "b", "en-es").units] == ["uno", "DOS"]


async def test_results_of_systems_no_longer_benched_are_left_out(
    tmp_path, mini_flores, fake_backend
):
    cfg, out = bench_config(tmp_path, mini_flores, sentences=2), tmp_path / "out"
    await run_bench(cfg, out, backends_factory=factory(fake_backend), log=quiet)
    renamed = [cfg.systems[0].model_copy(update={"name": "coglossa"}), cfg.systems[1]]
    pairs = cfg.dataset.model_copy(update={"pairs": ["en>es"]})
    logs: list[str] = []
    rows = await run_bench(
        cfg.model_copy(update={"systems": renamed, "dataset": pairs}),
        out,
        backends_factory=factory(fake_backend),
        log=logs.append,
    )
    assert [(r.system, r.pair) for r in rows] == [("baseline", "en>es"), ("coglossa", "en>es")]
    assert (out / "runs/pipeline/en-es.json").is_file()  # left on disk
    noted = [m for m in logs if m.startswith("summary: ")]
    assert noted == [
        "summary: left out 3 result file(s) of systems or pairs that are not in the bench config"
    ]
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert {r["system"] for r in summary} == {"baseline", "coglossa"}


async def test_a_pending_metricx_is_scored_on_resume_without_translating(
    tmp_path, mini_flores, fake_backend
):
    from coglossa.scoring.client import ScorerUnavailableError

    class Down(ScorerDouble):
        async def qe(self, items):
            raise ScorerUnavailableError("scorer unreachable: refused")

    cfg, out = bench_config(tmp_path, mini_flores, sentences=3), tmp_path / "out"
    await run_bench(cfg, out, backends_factory=factory(fake_backend), scorer=Down(), log=quiet)
    before = read(out, "baseline", "en-es")
    assert before.metricx_pending and before.status == "complete"
    assert "scorer: scorer unreachable: refused" in before.errors

    # still down: still pending; no scorer: nothing to score with, so it is left as it is
    logs: list[str] = []
    await run_bench(
        cfg, out, backends_factory=factory(fake_backend), scorer=Down(), log=logs.append
    )
    assert sum(m.startswith("re-scoring ") for m in logs) == 4
    assert read(out, "baseline", "en-es").metricx_pending
    assert read(out, "baseline", "en-es").errors.count("scorer: scorer unreachable: refused") == 1
    logs.clear()
    rows = await run_bench(cfg, out, backends_factory=factory(fake_backend), log=logs.append)
    assert logs[0] == "skipping 4 finished system-pairs"
    assert all(r.metricx_pending and r.metricx_mean is None for r in rows)

    calls = len(fake_backend.requests)
    logs.clear()
    scorer = ScorerDouble()
    rows = await run_bench(
        cfg, out, backends_factory=factory(fake_backend), scorer=scorer, log=logs.append
    )
    assert len(fake_backend.requests) == calls  # nothing translated again
    assert "re-scoring baseline en>es: MetricX pending" in logs
    after = read(out, "baseline", "en-es")
    assert not after.metricx_pending and all(u.metricx == 10.0 for u in after.units)
    assert [u.output for u in after.units] == [u.output for u in before.units]
    assert after.seconds == before.seconds and after.tokens_in == before.tokens_in
    assert not any(e.startswith("scorer: ") for e in after.errors)
    assert all(r.metricx_mean == 10.0 and not r.metricx_pending for r in rows)
    assert len(scorer.items) == 12  # 4 system-pairs of 3 units, scored from the stored outputs

    logs.clear()
    await run_bench(cfg, out, force=True, backends_factory=factory(fake_backend), log=logs.append)
    assert len(fake_backend.requests) > calls  # --force translates again


async def test_systems_are_checked_before_anything_is_translated(
    tmp_path, mini_flores, fake_backend
):
    cfg = bench_config(tmp_path, mini_flores)
    systems = [cfg.systems[0], cfg.systems[1].model_copy(update={"model": "nope"})]
    with pytest.raises(ConfigError) as e:
        await run_bench(
            cfg.model_copy(update={"systems": systems}),
            tmp_path / "out",
            backends_factory=factory(fake_backend),
            log=quiet,
        )
    assert str(e.value) == (
        f"system 'baseline': model 'nope' is not defined in {cfg.systems[1].config}"
    )
    systems = [cfg.systems[0], cfg.systems[1].model_copy(update={"config": tmp_path / "x.yaml"})]
    with pytest.raises(ConfigError, match="^system 'baseline': config file not found") as e:
        await run_bench(
            cfg.model_copy(update={"systems": systems}),
            tmp_path / "out",
            backends_factory=factory(fake_backend),
            log=quiet,
        )
    assert "\n" not in str(e.value)
    assert fake_backend.requests == [] and not (tmp_path / "out").exists()


async def test_a_blank_line_is_one_error_naming_its_file_and_line(tmp_path, mini_flores):
    (tmp_path / "s.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "r.txt").write_text("uno\n  ", encoding="utf-8")  # no newline at the end
    cfg = BenchConfig.model_validate(
        {
            "dataset": {
                "kind": "parallel",
                "path": tmp_path,
                "pairs": ["en>es"],
                "source_file": tmp_path / "s.txt",
                "reference_file": tmp_path / "r.txt",
            },
            "systems": [
                {"name": "b", "kind": "baseline", "config": write_system_config(tmp_path),
                 "model": "model-b"},
            ],
        }
    )  # fmt: skip
    with pytest.raises(ConfigError) as e:
        await run_bench(cfg, tmp_path / "out", log=quiet)
    assert str(e.value) == f"{tmp_path / 'r.txt'}: line 2 is blank; every line must hold a sentence"
    spa = mini_flores / "flores200_dataset/devtest/spa_Latn.devtest"
    spa.write_text(
        spa.read_text(encoding="utf-8").replace("[spa_Latn] Please speak slowly.", " "), "utf-8"
    )
    with pytest.raises(ConfigError) as e:
        await run_bench(bench_config(tmp_path, mini_flores), tmp_path / "out", log=quiet)
    assert str(e.value) == f"{spa}: line 8 is blank; the data is damaged"


async def test_too_long_units_say_whether_the_source_or_the_output_is_to_blame():
    from coglossa.bench.metrics import score_units
    from coglossa.bench.units import Unit

    scorer = ScorerDouble()
    units = [Unit(0, 0, "x" * 10), Unit(1, 1, "x" * 100), Unit(2, 2, "x" * 10)]
    sources, references = ["a" * 10, "b" * 10, "c" * 40], ["A" * 10, "B" * 10, "C" * 10]
    results, _ = await score_units(
        units, sources, references, scorer, pair=parse_pair("en>es"), qe_max_chars=50
    )
    assert [(r.too_long, r.metricx) for r in results] == [
        (None, 10.0),
        ("output", None),  # a runaway output: it would fit with an output like its reference
        ("source", None),  # 40 + 10 + 10: too long whatever the output
    ]
    row = summarise(SystemPairResult(system="s", pair="en>es", status="complete", units=results))
    assert (row.long_sources, row.long_outputs, row.scored_units) == (1, 1, 1)
    asked = len(scorer.items)
    results, errors = await score_units(
        units[1:], sources, references, scorer, pair=parse_pair("en>es"), qe_max_chars=50
    )
    assert len(scorer.items) == asked and errors == []  # nothing fits: the scorer is not called
    assert [r.too_long for r in results] == ["output", "source"]


async def test_sentences_of_a_spaceless_language_join_without_a_space():
    from coglossa.bench.metrics import score_units
    from coglossa.bench.units import Unit

    scorer = ScorerDouble()
    units = [Unit(0, 1, "你好。再见。")]
    results, _ = await score_units(
        units, ["Hi.", "Bye."], ["你好。", "再见。"], scorer, pair=parse_pair("en>zh-Hant"),
        qe_max_chars=100,
    )  # fmt: skip
    assert (results[0].source, results[0].reference) == ("Hi. Bye.", "你好。再见。")
    assert results[0].metricx == 0.0  # the double: 0 for an exact match
    results, _ = await score_units(
        units, ["こんにちは。", "さようなら。"], ["Hi.", "Bye."], None, pair=parse_pair("ja>en"),
        qe_max_chars=100,
    )  # fmt: skip
    assert (results[0].source, results[0].reference) == ("こんにちは。さようなら。", "Hi. Bye.")


def unit(first, last, metricx):
    return UnitResult(
        first=first, last=last, source="s", reference="r", output="o", chrf=1.0, metricx=metricx
    )


def test_shared_metricx_is_over_the_units_every_system_scored():
    a = SystemPairResult(
        system="a", pair="en>es", status="complete",
        units=[unit(0, 0, 1.0), unit(1, 2, 4.0), unit(3, 3, 20.0)],
    )  # fmt: skip
    b = SystemPairResult(
        system="b", pair="en>es", status="complete",
        units=[unit(0, 0, 3.0), unit(1, 2, 6.0), unit(3, 3, None)],
    )  # fmt: skip
    c = SystemPairResult(system="c", pair="en>es", status="failed")  # takes no part
    shared = shared_metricx([a, b, c])
    # units (0,0) and (1,2), the latter two sentences long: (1 + 2*4) / 3 and (3 + 2*6) / 3
    assert shared == {"a": (3.0, 2), "b": (5.0, 2)}
    assert summarise(a).metricx_mean == pytest.approx(25 / 3)  # its own mean, kept as well
    merged = a.model_copy(update={"units": [unit(0, 1, 2.0), unit(2, 3, 2.0)]})
    assert shared_metricx([merged, b]) == {"a": (None, 0), "b": (None, 0)}
    assert shared_metricx([c]) == {}


async def _first_run(tmp_path, mini_flores, fake_backend, scorer):
    cfg, out = bench_config(tmp_path, mini_flores, sentences=3), tmp_path / "out"
    await run_bench(cfg, out, backends_factory=factory(fake_backend), scorer=scorer, log=quiet)
    return cfg, out


async def _again(cfg, out, fake_backend, scorer):
    logs: list[str] = []
    await run_bench(
        cfg, out, backends_factory=factory(fake_backend), scorer=scorer, log=logs.append
    )
    return logs


async def test_a_scorer_without_qe_is_scored_later_without_translating(
    tmp_path, mini_flores, fake_backend
):
    cfg, out = await _first_run(tmp_path, mini_flores, fake_backend, ScorerDouble(qe_model=None))
    assert all(
        read(out, s, p).metricx_pending
        for s in ("pipeline", "baseline")
        for p in ("en-es", "en-yo")
    )
    calls, scorer = len(fake_backend.requests), ScorerDouble()
    logs = await _again(cfg, out, fake_backend, scorer)
    assert len(fake_backend.requests) == calls
    assert "re-scoring baseline en>es: MetricX pending" in logs
    after = read(out, "baseline", "en-es")
    assert not after.metricx_pending and after.metricx_by == "model-a@1500"
    rows = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert all(r["metricx_mean"] == 10.0 and not r["metricx_pending"] for r in rows)


async def test_another_qe_model_scores_again(tmp_path, mini_flores, fake_backend):
    cfg, out = await _first_run(tmp_path, mini_flores, fake_backend, ScorerDouble("model-a"))
    calls, scorer = len(fake_backend.requests), ScorerDouble("model-b")
    logs = await _again(cfg, out, fake_backend, scorer)
    assert len(fake_backend.requests) == calls and len(scorer.items) == 12
    assert "re-scoring baseline en>es: scorer changed" in logs
    assert read(out, "baseline", "en-es").metricx_by == "model-b@1500"


async def test_the_same_qe_scorer_leaves_results_finished(tmp_path, mini_flores, fake_backend):
    cfg, out = await _first_run(tmp_path, mini_flores, fake_backend, ScorerDouble())
    scorer = ScorerDouble()
    assert await _again(cfg, out, fake_backend, scorer) == ["skipping 4 finished system-pairs"]
    assert scorer.items == []


async def test_a_changed_qe_max_chars_scores_again_without_translating(
    tmp_path, mini_flores, fake_backend
):
    cfg, out = await _first_run(tmp_path, mini_flores, fake_backend, ScorerDouble())
    calls, scorer = len(fake_backend.requests), ScorerDouble()
    write_system_config(tmp_path, scoring={"qe_max_chars": 900})
    logs = await _again(cfg, out, fake_backend, scorer)
    assert len(fake_backend.requests) == calls
    assert "re-scoring baseline en>es: scorer changed" in logs
    assert not any(m.startswith("re-running") for m in logs)
    assert read(out, "baseline", "en-es").metricx_by == "model-a@900"


async def test_an_unreachable_scorer_health_leaves_metricx_pending(
    tmp_path, mini_flores, fake_backend
):
    class Unreachable(ScorerDouble):
        async def health(self):
            raise ScorerUnavailableError("scorer unreachable: refused")

    scorer, logs = Unreachable(), []
    cfg, out = bench_config(tmp_path, mini_flores, sentences=3), tmp_path / "out"
    rows = await run_bench(
        cfg, out, backends_factory=factory(fake_backend), scorer=scorer, log=logs.append
    )
    assert (
        logs.count(
            "scorer unavailable (scorer unreachable: refused): MetricX stays pending; "
            "the next run with a scorer scores it"
        )
        == 1
    )
    assert scorer.items == [] and all(r.status == "complete" and r.metricx_pending for r in rows)
    assert read(out, "baseline", "en-es").metricx_by is None


async def test_a_result_from_before_metricx_identity_is_scored_once(
    tmp_path, mini_flores, fake_backend
):
    cfg, out = await _first_run(tmp_path, mini_flores, fake_backend, ScorerDouble())
    path = out / "runs" / "baseline" / "en-es.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["metricx_by"]  # written by an older version: scored, not pending, no identity
    assert data["metricx_pending"] is False and data["units"][0]["metricx"] == 10.0
    path.write_text(json.dumps(data), encoding="utf-8")
    assert read(out, "baseline", "en-es").metricx_by is None
    logs = await _again(cfg, out, fake_backend, ScorerDouble())
    assert "re-scoring baseline en>es: MetricX pending" in logs
    assert logs[0] == "skipping 3 finished system-pairs"
    assert read(out, "baseline", "en-es").metricx_by == "model-a@1500"
    assert await _again(cfg, out, fake_backend, ScorerDouble()) == [
        "skipping 4 finished system-pairs"
    ]
    # and without a scorer it is left as it is
    data["metricx_by"] = None
    path.write_text(json.dumps(data), encoding="utf-8")
    logs = await _again(cfg, out, fake_backend, None)
    assert logs == ["skipping 4 finished system-pairs"]


async def test_nothing_to_score_counts_as_scored(tmp_path):
    from coglossa.bench.run import _rescore, _Sample

    sample = _Sample(["a"], ["A"], "x")
    stored = SystemPairResult(system="s", pair="en>es", status="complete", units=[])
    scorer = ScorerDouble()
    result = await _rescore(stored, parse_pair("en>es"), sample, scorer, 100, "model-a@100")
    assert result.metricx_by == "model-a@100" and not result.metricx_pending
    assert scorer.items == []
    # no units and no scorer: nothing is waiting for a score
    result = await _rescore(stored, parse_pair("en>es"), sample, None, 100, None)
    assert result.metricx_by is None and not result.metricx_pending


def test_an_old_result_with_units_loads_as_pending():
    old = {
        "system": "s", "pair": "en>es", "status": "complete", "metricx_pending": False,
        "units": [unit(0, 0, 3.0).model_dump()],
    }  # fmt: skip
    result = SystemPairResult.model_validate(old)
    assert result.metricx_by is None and result.metricx_pending
    assert summarise(result).metricx_pending
    empty = SystemPairResult.model_validate({**old, "units": []})
    assert not empty.metricx_pending
