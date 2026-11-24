import json
import random

import pytest
import yaml

from coglossa.bench.calibrate import (
    MAX_FALSE_POSITIVE_RATE,
    MIN_KIND_SAMPLES,
    MIN_SAMPLES,
    calibrate,
    result_lines,
    run_calibrate,
    suggest_tiers,
)
from coglossa.bench.config import TierCuts
from coglossa.bench.flagtest import SpanRecord
from coglossa.bench.metrics import SummaryRow
from coglossa.cli import main
from coglossa.config import ConfigError, ScoringConfig, ThresholdsConfig
from coglossa.schema import Reliability
from coglossa.scoring.tiers import LanguageTiers
from tests.conftest import PRE_CALIBRATION_THRESHOLDS

CURRENT = ScoringConfig(
    thresholds=PRE_CALIBRATION_THRESHOLDS
)  # the thresholds calibrate starts from


def span(kind, meaning=None, style=None, tier="A", critical=False, critical_style=False):
    return SpanRecord(
        pair="en>es", tier=tier, unit=0, kind=kind, detail="", meaning=meaning, style=style,
        signals={}, critical_accuracy=critical, critical_style=critical_style, flags=[],
    )  # fmt: skip


def uniform(rng, lo, hi, n):
    return [round(rng.uniform(lo, hi), 1) for _ in range(n)]


def flagged_meaning(r, t):
    return r.critical_accuracy or (r.meaning is not None and r.meaning < t)


def rate(records, t, flagged=flagged_meaning):
    return sum(flagged(r, t) for r in records) / len(records)


def gauss(rng, mean, sd, n):
    return [round(min(100.0, max(0.0, rng.gauss(mean, sd))), 1) for _ in range(n)]


def test_threshold_keeps_the_clean_false_positive_rate_within_the_target():
    # a realistic bench: three injected spans per clean one, with overlapping scores
    rng = random.Random(3)
    clean = [span("clean", m) for m in gauss(rng, 80, 10, 60)]
    means = {"omission": 60, "number": 65, "name": 70, "negation": 62}
    bad = [span(k, m) for k, mean in means.items() for m in gauss(rng, mean, 12, 45)]
    result = calibrate(clean + bad, CURRENT)
    t = result.meaning["A"]
    fit = result.scores["tier A"]
    assert fit.threshold == t
    assert rate(clean, t) <= MAX_FALSE_POSITIVE_RATE < rate(clean, t + 1)  # the highest such t
    assert fit.false_positive_rate == pytest.approx(rate(clean, t))
    per_kind = {k: rate([r for r in bad if r.kind == k], t) for k in means}
    assert fit.kinds == pytest.approx(per_kind)
    assert fit.recall == pytest.approx(sum(per_kind.values()) / 4)
    assert fit.counts["omission"] == (
        sum(flagged_meaning(r, t) for r in bad if r.kind == "omission"),
        45,
    )
    # what F1 at this 3:1 mix would have picked flags far more clean text
    f1_best = max(
        range(101),
        key=lambda t: (
            2
            * (tp := sum(flagged_meaning(r, t) for r in bad))
            / (2 * tp + sum(flagged_meaning(r, t) for r in clean) + len(bad) - tp),
            t,
        ),
    )
    assert rate(clean, f1_best) > 2 * MAX_FALSE_POSITIVE_RATE
    assert result.meaning["B"] == 72 and result.meaning["C"] == 75
    assert MAX_FALSE_POSITIVE_RATE == 0.10


def test_recall_weighs_each_kind_the_same():
    clean = [span("clean", 90.0) for _ in range(30)]
    bad = [span("number", 10.0) for _ in range(90)] + [span("negation", 95.0) for _ in range(10)]
    fit = calibrate(clean + bad, CURRENT).scores["tier A"]
    assert fit.threshold == 90  # 91 would flag every clean span
    assert fit.kinds == {"number": 1.0, "negation": 0.0}
    assert fit.counts == {"number": (90, 90), "negation": (0, 10)}
    assert fit.recall == 0.5  # not 90 / 100


def test_a_kind_with_few_examples_is_shown_with_counts_but_not_averaged():
    clean = [span("clean", 90.0) for _ in range(30)]
    few = [span("number", 10.0) for _ in range(3)]  # all caught
    omission = [span("omission", 10.0) for _ in range(10)] + [
        span("omission", 95.0) for _ in range(10)
    ]
    name = [span("name", 10.0) for _ in range(10)] + [span("name", 95.0) for _ in range(10)]
    cal = calibrate(clean + few + omission + name, CURRENT)
    fit = cal.scores["tier A"]
    assert MIN_KIND_SAMPLES == 10
    assert fit.recall == 0.5  # not 2 / 3
    assert fit.counts == {"number": (3, 3), "omission": (10, 20), "name": (10, 20)}
    line = result_lines(cal)[0]
    assert "recall averaged over 2 kinds: 50%" in line
    assert "number 100% (3/3, too few to average)" in line
    assert "omission 50% (10/20)" in line


def test_with_too_few_examples_of_every_kind_the_threshold_is_still_fitted():
    clean = [span("clean", 90.0) for _ in range(30)]
    bad = [span(k, 10.0) for k in ("number", "name", "omission", "negation") for _ in range(9)]
    cal = calibrate(clean + bad, CURRENT)
    fit = cal.scores["tier A"]
    assert cal.meaning["A"] == 90 and fit.recall is None
    assert fit.counts["name"] == (9, 9)
    line = result_lines(cal)[0]
    assert "recall: too few examples of each kind to average" in line
    assert "number 100% (9/9, too few to average)" in line


def test_catching_none_is_judged_on_the_counts_not_the_average():
    clean = [span("clean", 90.0) for _ in range(30)]
    bad = [span("omission", 95.0) for _ in range(5)] + [span("number", 95.0) for _ in range(25)]
    cal = calibrate(clean + bad, CURRENT)  # every kind caught 0
    assert "tier A" not in cal.scores and cal.meaning["A"] == 70
    assert any("no injected error is caught" in n for n in cal.notes)


def test_the_target_rate_is_a_parameter():
    clean = [span("clean", float(m)) for m in range(60, 100)]  # 40 spans, one per score
    bad = [span("omission", 50.0) for _ in range(30)]
    assert calibrate(clean + bad, CURRENT).meaning["A"] == 64  # 4 of 40 below 64
    assert calibrate(clean + bad, CURRENT, 0.5).meaning["A"] == 80


def test_critical_errors_alone_above_the_target_keep_the_threshold():
    clean = [span("clean", 95.0, critical=n < 6) for n in range(30)]  # 20% critical
    bad = [span("omission", 50.0) for _ in range(30)]
    result = calibrate(clean + bad, CURRENT)
    assert result.meaning["A"] == 70 and "tier A" not in result.scores
    assert (
        "tier A: critical errors alone flag 20% of clean spans, above the 10% target, "
        "so its threshold stays 70"
    ) in result.notes


def test_nothing_scored_keeps_the_thresholds():
    clean = [span("clean") for _ in range(40)]
    bad = [span("negation") for _ in range(40)] + [span("register") for _ in range(40)]
    result = calibrate(clean + bad, CURRENT)
    assert result.meaning["A"] == 70 and result.style == 60 and not result.scores
    assert "tier A: no injected span has a score, so its threshold stays 70" in result.notes
    assert "style: no injected span has a score, so its threshold stays 60" in result.notes


def test_no_injected_error_caught_keeps_the_threshold():
    clean = [span("clean", 90.0) for _ in range(30)]
    bad = [span("omission", 95.0) for _ in range(30)]
    result = calibrate(clean + bad, CURRENT)
    assert result.meaning["A"] == 70 and "tier A" not in result.scores
    assert (
        "tier A: at threshold 90 (the highest within the 10% target) no injected error is "
        "caught, so its threshold stays 70"
    ) in result.notes


def test_spans_with_a_scoring_error_are_left_out():
    clean = [span("clean", 90.0) for _ in range(30)]
    broken = [span("clean", 20.0) for _ in range(30)]
    for r in broken:
        r.errors = ["judge: judge went away"]
    bad = [span("omission", 50.0) for _ in range(30)]
    result = calibrate(clean + broken + bad, CURRENT)
    assert result.meaning["A"] == 90  # the broken spans would have forced it to 20
    assert result.notes[0] == (
        "30 spans had a scoring error (a failed judge, back-translation or scorer call), "
        "so their flags may be missing; left out"
    )


def test_a_perfect_separation_gives_the_highest_threshold():
    clean = [span("clean", 90.0) for _ in range(30)]
    bad = [span("omission", 50.0) for _ in range(30)]
    # every t in 51..90 separates them perfectly
    assert calibrate(clean + bad, CURRENT).meaning["A"] == 90


def test_critical_errors_and_unscored_spans():
    clean = [span("clean", 95.0) for _ in range(30)]
    bad = [span("number", 95.0, critical=True) for _ in range(29)] + [span("number", None)]
    result = calibrate(clean + bad, CURRENT)
    assert result.scores["tier A"].recall == pytest.approx(29 / 30)
    assert any("1 span" in n and "no meaning score" in n for n in result.notes)


@pytest.mark.parametrize("kind", ["locked_term", "untranslated"])
def test_kinds_only_rules_catch_do_not_count(kind):
    clean = [span("clean", 90.0) for _ in range(30)]
    locked = [span(kind, 10.0) for _ in range(30)]
    result = calibrate(clean + locked, CURRENT)
    assert result.meaning["A"] == 70  # unchanged: no meaning positives
    assert any(n.startswith("tier A: 0 injected and 30 clean") for n in result.notes)


def test_style_threshold():
    rng = random.Random(5)
    clean = [span("clean", 90, s, tier=t) for t in "AB" for s in uniform(rng, 60, 100, 20)]
    bad = [span("register", 90, s, tier=t) for t in "AB" for s in uniform(rng, 30, 70, 20)]

    def flagged(r, t):
        return r.style < t or r.critical_style

    result = calibrate(clean + bad, CURRENT)
    t = result.style
    assert rate(clean, t, flagged) <= 0.1 < rate(clean, t + 1, flagged)
    assert result.scores["style"].recall == pytest.approx(rate(bad, t, flagged))
    assert list(result.scores["style"].kinds) == ["register"]


def test_small_tiers_keep_the_current_threshold():
    clean = [span("clean", 80.0, tier="C") for _ in range(8)]
    bad = [span("negation", 20.0, tier="C") for _ in range(5)]
    result = calibrate(clean + bad, CURRENT)
    assert result.meaning["C"] == 75
    assert "tier C" not in result.scores
    assert (
        "tier C: 5 injected and 8 clean spans; at least 30 of each are needed, "
        "so its threshold stays 75"
    ) in result.notes
    assert MIN_SAMPLES == 30
    assert result.style == 60
    assert any(n.startswith("style: 0 injected and 8 clean") for n in result.notes)


def test_result_lines():
    clean = [span("clean", 90.0) for _ in range(30)]
    bad = [span("number", 10.0) for _ in range(30)] + [span("negation", 95.0) for _ in range(30)]
    cal = calibrate(clean + bad, CURRENT)
    assert result_lines(cal)[0] == (
        "tier A: threshold 90 flags 0% of clean spans; recall averaged over 2 kinds: 50% "
        "(number 100% (30/30), negation 0% (0/30))"
    )


def row(pair, mx, system="p", status="complete", kind="pipeline"):
    return SummaryRow(
        system=system, kind=kind, pair=pair, status=status, units=4, scored_units=4,
        metricx_mean=mx,
        chrf_corpus=50.0, tokens_in=0, tokens_out=0, seconds=1.0, errors=0,
    )  # fmt: skip


def test_suggest_tiers():
    rows = [
        row("en>es", 3.1), row("en>hi", 7.5), row("en>yo", 14.0), row("en>fr", 5.0),
        row("en>de", None), row("en>it", 1.0, status="failed"), row("en>ja", 99.0, system="q"),
    ]  # fmt: skip
    assert suggest_tiers(rows, "p", TierCuts()) == {
        "en>es": "A", "en>hi": "B", "en>yo": "C", "en>fr": "A",
    }  # fmt: skip


def populate(tmp_path, with_flags=True):
    out = tmp_path / "bench-out"
    rng = random.Random(1)
    records = [span("clean", m) for m in uniform(rng, 70, 100, 40)] + [
        span("negation", m) for m in uniform(rng, 40, 75, 40)
    ]
    (out / "flags").mkdir(parents=True)
    if with_flags:
        text = "".join(r.model_dump_json() + "\n" for r in records)
        (out / "flags" / "en-es.jsonl").write_text(text, encoding="utf-8")
    rows = [
        row("en>es", 20.0, "a-base", kind="baseline"),  # first, as summary.json sorts it
        row("en>es", 3.1),
        row("en>hi", 7.5),
        row("en>yo", 14.0),
        row("en>es", 1.0, "z"),
    ]
    (out / "summary.json").write_text(json.dumps([r.model_dump() for r in rows]), "utf-8")
    return out


def write_scoring(tmp_path, tiers_file=None):
    cfg = {
        "backends": {"local": {"base_url": "http://127.0.0.1:9/v1"}},
        "models": {"m": {"backend": "local", "context_window": 32768}},
        "roles": {
            r: {"model": "m"}
            for r in ("literal_translator", "contextual_translator", "reviewer", "profiler_guide")
        },
        "scoring": {
            "thresholds": PRE_CALIBRATION_THRESHOLDS,
            **({"language_tiers_file": str(tiers_file)} if tiers_file else {}),
        },
    }
    path = tmp_path / "coglossa.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def test_files(tmp_path):
    out = populate(tmp_path)
    tiers_in = tmp_path / "tiers.yaml"
    tiers_in.write_text(
        'default: C\nlanguages:\n  A: [en, es]\n  B: ["no"]\npairs:\n  "en>fr": B\n'
    )
    run_calibrate(out, write_scoring(tmp_path, tiers_in), None, log=lambda m: None)
    text = (out / "calibration.yaml").read_text(encoding="utf-8")
    assert text.startswith("#")
    assert "bench-out" in text and "precision" not in text
    assert "# Each threshold is the highest that flags at most 10% of clean spans" in text
    assert "with its own flag (low_meaning for meaning, style_drift for style)" in text
    assert "flag-summary.md" in text and "for all flags together" in text
    assert "averaged over the kinds of error with at least 10 examples" in text
    assert (
        "#   tier A: threshold 70 flags 0% of clean spans; recall averaged over 1 kind: 85% "
        "(negation 85% (34/40))" in text
    )
    assert "#   style: 0 injected and 40 clean spans" in text  # the notes follow
    assert "agreement" in text and "rerun `bench flags` and `bench calibrate`" in text
    scoring = yaml.safe_load(text)["scoring"]
    thresholds = ThresholdsConfig.model_validate(scoring["thresholds"])
    assert thresholds.meaning["B"] == 72
    tiers = LanguageTiers.load(out / "tiers.calibrated.yaml")
    assert tiers.tier("en", "es") == Reliability.A
    assert tiers.tier("en", "hi") == Reliability.B
    assert tiers.tier("en", "yo") == Reliability.C
    assert tiers.tier("en", "fr") == Reliability.B  # kept from the current file
    assert tiers.languages["no"] == Reliability.B


def test_missing_flags_is_a_config_error(tmp_path):
    out = populate(tmp_path, with_flags=False)
    with pytest.raises(ConfigError, match="bench flags"):
        run_calibrate(out, write_scoring(tmp_path), None, log=lambda m: None)


def test_missing_summary_and_unknown_system(tmp_path):
    out = populate(tmp_path)
    scoring = write_scoring(tmp_path)
    with pytest.raises(ConfigError, match="no system 'nope'"):
        run_calibrate(out, scoring, "nope", log=lambda m: None)
    (out / "summary.json").unlink()
    with pytest.raises(ConfigError, match="bench run"):
        run_calibrate(out, scoring, None, log=lambda m: None)


def test_cli(tmp_path, capsys):
    out = populate(tmp_path)
    argv = [
        "bench",
        "calibrate",
        "--out",
        str(out),
        "--scoring-config",
        str(write_scoring(tmp_path)),
    ]
    assert main([*argv, "--tier-system", "p"]) == 0
    printed = capsys.readouterr().out
    assert str(out / "calibration.yaml") in printed and "tiers from system 'p'" in printed
    assert printed.startswith("tier A: threshold ")  # the results, as in calibration.yaml
    assert "style: 0 injected and 40 clean spans" in printed
    assert main(argv) == 0  # a baseline sorts first; the default is the first pipeline
    assert "tiers from system 'p'" in capsys.readouterr().out
    assert yaml.safe_load((out / "tiers.calibrated.yaml").read_text())["pairs"]["en>es"] == "A"
    low = yaml.safe_load((out / "calibration.yaml").read_text())["scoring"]["thresholds"]
    assert main([*argv, "--max-false-positive-rate", "0.5"]) == 0
    assert "at most 50% of clean spans" in (out / "calibration.yaml").read_text()
    high = yaml.safe_load((out / "calibration.yaml").read_text())["scoring"]["thresholds"]
    assert high["meaning"]["A"] > low["meaning"]["A"]
    capsys.readouterr()
    assert main([*argv, "--max-false-positive-rate", "1.5"]) == 1
    assert capsys.readouterr().err == (
        "error: --max-false-positive-rate must be between 0 and 1, not 1.5\n"
    )
    assert main([*argv, "--tier-system", "nope"]) == 1
    assert capsys.readouterr().err.splitlines()[-1].startswith("error: ")


def test_the_tier_system_defaults_to_the_first_pipeline_of_the_bench_config(tmp_path):
    out = populate(tmp_path)
    systems = [
        {"name": "a-base", "kind": "baseline"},
        {"name": "z", "kind": "pipeline"},
        {"name": "p", "kind": "pipeline"},
    ]
    meta = {"tier_cuts": {"a_max": 5.0, "b_max": 10.0}, "systems": systems}
    (out / "bench-meta.json").write_text(json.dumps(meta), "utf-8")
    cal = run_calibrate(out, write_scoring(tmp_path), None, log=lambda m: None)
    assert cal.tier_system == "z"  # not "p", the first pipeline of summary.json
    pairs = yaml.safe_load((out / "tiers.calibrated.yaml").read_text())["pairs"]
    assert pairs == {"en>es": "A"}
    meta["systems"] = [{"name": "z"}]
    (out / "bench-meta.json").write_text(json.dumps(meta), "utf-8")
    with pytest.raises(ConfigError, match="^.*bench-meta.json: not a bench meta file: systems"):
        run_calibrate(out, write_scoring(tmp_path), None, log=lambda m: None)


def test_no_pipeline_system(tmp_path):
    out = populate(tmp_path)
    rows = [row("en>es", 3.1, "b", kind="baseline")]
    (out / "summary.json").write_text(json.dumps([r.model_dump() for r in rows]), "utf-8")
    with pytest.raises(ConfigError, match="pass --tier-system"):
        run_calibrate(out, write_scoring(tmp_path), None, log=lambda m: None)


def test_old_summary_rows_default_to_pipeline():
    old = row("en>es", 1.0).model_dump()
    del old["kind"]
    assert SummaryRow.model_validate(old).kind == "pipeline"


def test_cuts_come_from_bench_meta(tmp_path):
    out = populate(tmp_path)
    scoring = write_scoring(tmp_path)
    notes = []
    run_calibrate(out, scoring, None, log=notes.append)
    assert len(notes) == 1 and "bench-meta.json" in notes[0]  # defaults: 3.1 is A
    meta = {"tier_cuts": {"a_max": 2.0, "b_max": 5.0}}
    (out / "bench-meta.json").write_text(json.dumps(meta), "utf-8")
    run_calibrate(out, scoring, None, log=notes.append)
    pairs = yaml.safe_load((out / "tiers.calibrated.yaml").read_text())["pairs"]
    assert pairs == {"en>es": "B", "en>hi": "C", "en>yo": "C"}
    assert len(notes) == 1
    (out / "bench-meta.json").write_text("{}", "utf-8")
    with pytest.raises(ConfigError, match="bench meta"):
        run_calibrate(out, scoring, None, log=notes.append)


def test_damaged_inputs_are_one_line_errors(tmp_path):
    out = populate(tmp_path)
    scoring = write_scoring(tmp_path)
    (out / "flags" / "en-es.jsonl").write_text('{"pair": "en>es"}\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="not a flag record file"):
        run_calibrate(out, scoring, None, log=lambda m: None)
    (out / "summary.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ConfigError, match="not a bench summary") as e:
        run_calibrate(out, scoring, None, log=lambda m: None)
    assert "\n" not in str(e.value)
    (out / "summary.json").write_text("[", encoding="utf-8")
    with pytest.raises(ConfigError, match="not a bench summary: Expecting value"):
        run_calibrate(out, scoring, None, log=lambda m: None)
