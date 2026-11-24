import asyncio
import json
from collections import Counter

import pytest
import yaml

from coglossa.bench.config import BenchConfig
from coglossa.bench.flagtest import SpanRecord, flag_metrics, run_flag_test
from coglossa.bench.flores import FLAGS_MODIFIED, LICENSE_TEXT
from coglossa.config import ConfigError
from coglossa.llm.payload import parse_input
from coglossa.llm.types import ChatResponse, PermanentBackendError
from coglossa.stages.score import SIGNALS
from coglossa.testing.fake_backend import FakeBackend
from coglossa.testing.synthetic import MISTRANSLATED
from tests.bench.test_run import ScorerDouble
from tests.conftest import BASE_CONFIG, PRE_CALIBRATION_THRESHOLDS, build_config

SCORING = {
    **BASE_CONFIG,
    "models": {
        **BASE_CONFIG["models"],
        "model-c": {"backend": "fake", "context_window": 32768},
        "model-d": {"backend": "fake", "context_window": 32768},
    },
    "roles": {
        **BASE_CONFIG["roles"],
        "judge": {"model": "model-c"},
        "roundtrip": {"model": "model-d"},
    },
    "scoring": {"thresholds": PRE_CALIBRATION_THRESHOLDS},
}
TABLE_ROWS = [
    "omission",
    "number",
    "name",
    "negation",
    "register",
    "locked_term",
    "untranslated",
    "clean (false positives)",
]


class SimilarityDouble(ScorerDouble):
    """Task 6's double, with LaBSE similarity: 0.9 for every pair."""

    supports_similarity = True

    def __init__(self):
        super().__init__()
        self.pairs = []
        self.closed = False

    async def similarity(self, pairs):
        self.pairs.extend(pairs)
        return [0.9 for _ in pairs]

    async def aclose(self):
        self.closed = True


class MistranslatingInjector(FakeBackend):
    """The injector model marks what it injects MISTRANSLATED, which the synthetic judge
    reports as a critical accuracy error."""

    async def chat(self, request):
        payload = parse_input([m.model_dump() for m in request.messages])
        if payload["task"] != "inject":
            return await super().chat(request)
        self.requests.append(request)
        text = f"{MISTRANSLATED} {payload['kind']} {payload['text']}"
        return ChatResponse(content=json.dumps({"text": text}), tokens_in=1, tokens_out=1)


class InjectorDown(FakeBackend):
    async def chat(self, request):
        if parse_input([m.model_dump() for m in request.messages])["task"] == "inject":
            raise PermanentBackendError("injector went away")
        return await super().chat(request)


def write_scoring_config(tmp_path, name="coglossa.yaml", **overrides):
    data = {**SCORING, **overrides}
    build_config(**data)  # a valid Coglossa config
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def bench_config(tmp_path, mini_flores, scoring, *, injector=True, **dataset):
    return BenchConfig.model_validate(
        {
            "dataset": {
                "kind": "flores200",
                "path": mini_flores,
                "pairs": ["en>es"],
                "sentences": 8,
                "seed": 7,
                **dataset,
            },
            "systems": [{"name": "pipeline", "kind": "pipeline", "config": scoring}],
            "injector": {"config": scoring, "model": "model-a"} if injector else None,
        }
    )


def factory(backend):
    return lambda cfg: {"fake": backend}


def read_records(out, slug="en-es"):
    lines = (out / "flags" / f"{slug}.jsonl").read_text(encoding="utf-8").splitlines()
    return [SpanRecord.model_validate_json(line) for line in lines]


async def run(tmp_path, mini_flores, *, backend=None, scorer=None, injector=True, logs=None):
    scoring = write_scoring_config(tmp_path)
    cfg = bench_config(tmp_path, mini_flores, scoring, injector=injector)
    backend = backend or MistranslatingInjector()
    out = tmp_path / "out"
    summary = await run_flag_test(
        cfg,
        scoring,
        out,
        backends_factory=factory(backend),
        scorer=scorer if scorer is not None else SimilarityDouble(),
        log=(logs.append if logs is not None else lambda m: None),
    )
    return summary, out, backend


async def test_every_kind_is_scored_by_the_real_scoring_stage(tmp_path, mini_flores):
    scorer = SimilarityDouble()
    summary, out, backend = await run(tmp_path, mini_flores, scorer=scorer)
    records = read_records(out)
    kinds = Counter(r.kind for r in records)
    # the mini data: every sentence can lose a clause or be left untranslated, 3 have numbers,
    # and 2 have a name (Paris, Lagos) that another reference can stand in for
    assert kinds == {
        "clean": 8,
        "omission": 8,
        "number": 3,
        "name": 2,
        "locked_term": 2,
        "negation": 8,
        "register": 8,
        "untranslated": 8,
    }
    assert {r.tier for r in records} == {"A"}
    assert {r.pair for r in records} == {"en>es"}
    assert sorted(r.unit for r in records if r.kind == "clean") == list(range(8))
    for r in records:
        assert set(r.signals) == set(SIGNALS)
        assert r.meaning is not None and r.style is not None
        assert r.signals["agreement"] is None  # a reference has no translators to agree
    assert all(r.detail for r in records if r.kind != "clean")
    # the judge saw the injected text: the injector's MISTRANSLATED is a critical accuracy error
    for r in records:
        injected = r.kind in ("negation", "register")
        assert r.critical_accuracy is injected and not r.critical_style
        assert ("low_meaning" in r.flags) is injected
    assert not any(r.flags for r in records if r.kind == "clean")
    assert scorer.items and scorer.pairs and not scorer.closed  # the caller's scorer
    assert backend.closed

    tier = next(t for t in summary.tiers if t.tier == "A")
    assert tier.kinds["negation"].recall == 1.0 and tier.kinds["negation"].caught == 8
    assert tier.kinds["register"].recall == 0.0  # only style_drift counts for register
    assert tier.clean_total == 8 and tier.false_positive_rate == 0.0
    assert tier.precision == 1.0
    saved = json.loads((out / "flag-summary.json").read_text(encoding="utf-8"))
    assert saved == json.loads(summary.model_dump_json())
    assert not list(out.rglob("*.tmp"))


async def test_every_judge_call_scores_every_sentence_with_kinds_mixed(tmp_path, mini_flores):
    _, out, backend = await run(tmp_path, mini_flores)
    payloads = [parse_input([m.model_dump() for m in r.messages]) for r in backend.requests]
    judged = [p for p in payloads if p["task"] == "judge"]
    # one chunk and eight slots (clean + seven kinds): eight rounds, each covering all 8 sentences
    assert [len(p["spans"]) for p in judged] == [8] * 8
    for p in judged:
        marked = [MISTRANSLATED in s["text"] for s in p["spans"]]
        assert any(marked) and not all(marked)  # model-injected spans beside other kinds
    assert sum(MISTRANSLATED in s["text"] for p in judged for s in p["spans"]) == 16
    # each sentence carries each of its variants once, and its clean text once
    records = read_records(out)
    assert len({(r.kind, r.unit) for r in records}) == len(records)


async def test_a_locked_term_shared_with_another_sentence_is_left_out(tmp_path):
    (tmp_path / "s.txt").write_text(
        "Yesterday we visited Paris.\nThe flight to Paris was late.\nThen Okafor went to Lagos.\n",
        encoding="utf-8",
    )
    (tmp_path / "r.txt").write_text(
        "Ayer visitamos Paris.\nEl vuelo a Paris llegó tarde.\nLuego Okafor fue a Lagos.\n",
        encoding="utf-8",
    )
    scoring = write_scoring_config(tmp_path)
    cfg = BenchConfig.model_validate(
        {
            "dataset": {
                "kind": "parallel",
                "path": tmp_path,
                "pairs": ["en>es"],
                "sentences": 3,
                "source_file": tmp_path / "s.txt",
                "reference_file": tmp_path / "r.txt",
            },
            "systems": [{"name": "pipeline", "kind": "pipeline", "config": scoring}],
        }
    )
    logs: list[str] = []
    await run_flag_test(
        cfg,
        scoring,
        tmp_path / "out",
        backends_factory=factory(FakeBackend()),
        scorer=SimilarityDouble(),
        log=logs.append,
    )
    records = read_records(tmp_path / "out")
    # Paris is in two sources: a locked Paris entry would also judge the other sentence
    assert [r.unit for r in records if r.kind == "locked_term"] == [2]
    assert sorted(r.unit for r in records if r.kind == "name") == [0, 1, 2]
    assert not any("terminology_violation" in r.flags for r in records if r.kind != "locked_term")
    assert (
        "flags: en>es: 2 locked-term test(s) left out: the term is also in another sentence "
        "of the chunk"
    ) in logs


async def test_locked_term_violations_are_always_caught(tmp_path, mini_flores):
    _, out, _ = await run(tmp_path, mini_flores)
    locked = [r for r in read_records(out) if r.kind == "locked_term"]
    assert len(locked) == 2
    assert all("terminology_violation" in r.flags for r in locked)
    # the same swap without a locked entry is not caught by the rules
    names = [r for r in read_records(out) if r.kind == "name"]
    assert names and not any("terminology_violation" in r.flags for r in names)


async def test_changed_numbers_are_caught_by_the_numbers_check(tmp_path, mini_flores):
    summary, out, _ = await run(tmp_path, mini_flores)
    numbers = [r for r in read_records(out) if r.kind == "number"]
    assert len(numbers) == 3
    assert all("number_mismatch" in r.flags for r in numbers)
    assert not any("number_mismatch" in r.flags for r in read_records(out) if r.kind == "clean")
    [tier] = summary.tiers
    assert tier.kinds["number"].recall == 1.0 and tier.false_positive_rate == 0.0
    assert "number_mismatch" in (out / "flag-summary.md").read_text(encoding="utf-8")


async def test_passages_left_untranslated_are_caught_by_the_untranslated_check(
    tmp_path, mini_flores
):
    summary, out, _ = await run(tmp_path, mini_flores)
    records = read_records(out)
    left = {r.unit: r for r in records if r.kind == "untranslated"}
    assert len(left) == 8 and all(r.detail == "left untranslated" for r in left.values())
    assert all("untranslated" in r.flags for r in left.values())
    assert not any("untranslated" in r.flags for r in records if r.kind != "untranslated")
    [tier] = summary.tiers
    assert tier.kinds["untranslated"].recall == 1.0 and tier.kinds["untranslated"].total == 8
    assert tier.false_positive_rate == 0.0
    md = (out / "flag-summary.md").read_text(encoding="utf-8")
    assert "| untranslated | 8 | 8 | 1.00 |" in md
    assert "number_mismatch or untranslated for meaning errors" in md
    assert "   - injected: Marie arrived in Paris on 14 July 1998." in md


def record(kind, flags=(), tier="A"):
    return SpanRecord(
        pair="en>es",
        tier=tier,
        unit=0,
        kind=kind,
        detail="",
        meaning=None,
        style=None,
        signals={},
        critical_accuracy=False,
        critical_style=False,
        flags=list(flags),
    )


def test_metrics():
    records = [record("clean", ["style_drift"])] + [record("clean")] * 9
    records += [record("negation", ["low_meaning"])] * 3 + [record("negation")]
    records += [record("register", ["style_drift"]), record("register", ["low_meaning"])]
    summary = flag_metrics(records)
    [tier] = summary.tiers
    assert tier.tier == "A"
    assert tier.kinds["negation"].recall == 0.75
    assert (tier.kinds["negation"].caught, tier.kinds["negation"].total) == (3, 4)
    assert tier.kinds["register"].recall == 0.5
    assert tier.false_positive_rate == 0.1 and tier.clean_total == 10
    assert tier.false_positives == 1
    assert tier.precision == pytest.approx(4 / 5)
    assert summary.overall.precision == pytest.approx(4 / 5)
    assert summary.overall.kinds["negation"].recall == 0.75


def test_metrics_never_invent_a_rate():
    summary = flag_metrics([record("omission", tier="B"), record("clean", tier="C")])
    b = next(t for t in summary.tiers if t.tier == "B")
    c = next(t for t in summary.tiers if t.tier == "C")
    assert [t.tier for t in summary.tiers] == ["B", "C"]
    assert b.kinds["omission"].recall == 0.0
    assert b.clean_total == 0 and b.false_positive_rate is None
    assert b.precision is None  # nothing was flagged
    assert c.kinds["omission"].total == 0 and c.kinds["omission"].recall is None
    assert c.false_positive_rate == 0.0
    assert summary.overall.kinds["omission"].total == 1


async def test_flag_summary_markdown(tmp_path, mini_flores):
    _, out, _ = await run(tmp_path, mini_flores)
    md = (out / "flag-summary.md").read_text(encoding="utf-8")
    lines = md.splitlines()
    assert lines.count("## Tier A") == 1
    rows = [line.split(" | ")[0].removeprefix("| ") for line in lines if line.startswith("| ")]
    assert rows[1:] == TABLE_ROWS  # after the header row
    assert "| negation | 8 | 8 | 1.00 |" in lines
    assert "| clean (false positives) | 0 | 8 | 0.00 |" in lines
    assert "Clean false-positive rate (any flag): 0.00" in lines
    assert "`bench calibrate` fits each threshold to the rate of its own flag" in md
    assert "Precision (at this bench's mix): 1.00" in lines
    assert "Spans with a scoring error: 0" in lines
    assert "the number that transfers to real use" in md and "mix of injected and clean" in md
    assert "recall is a lower bound" in md
    assert "Agreement.UNKNOWN" in md and "understate" in md
    # three sample injections per kind (fewer when fewer exist), original then injected
    samples = md[md.index("## Sample injections") :]
    for kind, n in [("omission", 3), ("number", 3), ("name", 2), ("negation", 3)]:
        section = samples[samples.index(f"### {kind}") :].split("\n### ")[0]
        assert section.count("   - original: ") == section.count("   - injected: ") == n
    assert "   - original: [spa_Latn] It is not raining in Lagos today." in samples
    assert f"   - injected: {MISTRANSLATED} negation [spa_Latn]" in samples
    saved = json.loads((out / "flag-summary.json").read_text(encoding="utf-8"))
    assert list(saved["samples"]) == [
        "omission", "number", "name", "negation", "register", "locked_term", "untranslated",
    ]  # fmt: skip


def test_markdown_shows_untested_kinds_as_n_a(tmp_path):
    from coglossa.bench.flagtest import summary_markdown

    md = summary_markdown(flag_metrics([record("omission", ["low_meaning"])]))
    assert "| negation | 0 | 0 | n/a |" in md
    assert "| clean (false positives) | 0 | 0 | n/a |" in md


async def test_without_an_injector_model_the_model_kinds_are_skipped(tmp_path, mini_flores):
    logs: list[str] = []
    _, out, _ = await run(tmp_path, mini_flores, injector=False, logs=logs)
    kinds = {r.kind for r in read_records(out)}
    assert "negation" not in kinds and "register" not in kinds
    assert "omission" in kinds and "clean" in kinds
    assert "no injector configured: negation and register are not tested" in logs


async def test_a_failing_injector_skips_its_variants_and_says_so(tmp_path, mini_flores):
    logs: list[str] = []
    _, out, _ = await run(tmp_path, mini_flores, backend=InjectorDown(), logs=logs)
    kinds = {r.kind for r in read_records(out)}
    assert "negation" not in kinds and "register" not in kinds
    failed = [m for m in logs if "injections failed" in m]
    assert len(failed) == 2 and all("\n" not in m for m in failed)
    assert failed[0].startswith("flags: en>es: negation: 8 of 8 injections failed: ")


async def test_an_unknown_injector_model_is_one_line(tmp_path, mini_flores):
    scoring = write_scoring_config(tmp_path)
    cfg = bench_config(tmp_path, mini_flores, scoring)
    cfg = cfg.model_copy(update={"injector": cfg.injector.model_copy(update={"model": "nope"})})
    with pytest.raises(ConfigError, match="^injector model 'nope' is not defined in "):
        await run_flag_test(
            cfg, scoring, tmp_path / "out", backends_factory=factory(FakeBackend()), log=print
        )


async def test_no_judge_and_no_scorer_is_refused(tmp_path, mini_flores):
    roles = {k: v for k, v in SCORING["roles"].items() if k != "judge"}
    scoring = write_scoring_config(tmp_path, roles=roles)
    cfg = bench_config(tmp_path, mini_flores, scoring)
    backend = FakeBackend()
    with pytest.raises(ConfigError) as e:
        await run_flag_test(
            cfg, scoring, tmp_path / "out", backends_factory=factory(backend), log=print
        )
    assert str(e.value) == "the scoring config has no judge and no scorer; nothing can raise a flag"
    assert not backend.requests


async def test_the_scoring_configs_own_scorer_is_built_and_closed(
    tmp_path, mini_flores, monkeypatch
):
    built = SimilarityDouble()
    monkeypatch.setattr("coglossa.bench.flagtest.build_scorer", lambda cfg: built)
    scoring = write_scoring_config(tmp_path)
    cfg = bench_config(tmp_path, mini_flores, scoring, injector=False)
    await run_flag_test(
        cfg, scoring, tmp_path / "out", backends_factory=factory(FakeBackend()), log=print
    )
    assert built.items and built.closed


async def test_scoring_failures_are_logged(tmp_path, mini_flores):
    class JudgeDown(FakeBackend):
        async def chat(self, request):
            if parse_input([m.model_dump() for m in request.messages])["task"] == "judge":
                raise PermanentBackendError("judge went away")
            return await super().chat(request)

    logs: list[str] = []
    summary, out, _ = await run(
        tmp_path, mini_flores, backend=JudgeDown(), injector=False, logs=logs
    )
    records = read_records(out)
    assert all(r.critical_accuracy is False for r in records)
    judge_logs = [m for m in logs if m.startswith("flags: en>es: judge: ")]
    assert judge_logs and all("judge went away" in m for m in judge_logs)
    # every span lost its judge signal, and says so
    assert all(any(e.startswith("judge: ") for e in r.errors) for r in records)
    assert all("\n" not in e for r in records for e in r.errors)
    assert summary.overall.scoring_errors == len(records)
    md = (out / "flag-summary.md").read_text(encoding="utf-8")
    assert f"Spans with a scoring error: {len(records)}" in md

    logs.clear()  # a kept pair says how many of its spans lost a signal
    await run(tmp_path, mini_flores, backend=JudgeDown(), injector=False, logs=logs)
    assert (
        f"flags: en>es: kept from an earlier run ({len(records)} spans; {len(records)} lost a "
        "signal, --force scores them again)"
    ) in logs


def test_old_flag_records_have_no_errors():
    old = record("clean").model_dump()
    del old["errors"]
    assert SpanRecord.model_validate(old).errors == []


async def test_a_sentence_split_across_chunks_is_scored_whole(tmp_path):
    # chunking flushes inside the second paragraph (90 + 60 + 60 words > max_words 200),
    # so its second sentence would land in a chunk of its own
    first = " ".join(["alpha"] * 89) + "."
    second = " ".join(["bravo"] * 59) + ". " + " ".join(["charlie"] * 59) + "."
    (tmp_path / "s.txt").write_text(f"{first}\n{second}\n", encoding="utf-8")
    (tmp_path / "r.txt").write_text("uno, dos y tres.\ncuatro, cinco y seis.\n", encoding="utf-8")
    scoring = write_scoring_config(
        tmp_path, chunking={"target_words": 100, "max_words": 200, "reserve_tokens": 3000}
    )
    cfg = BenchConfig.model_validate(
        {
            "dataset": {
                "kind": "parallel",
                "path": tmp_path,
                "pairs": ["en>es"],
                "sentences": 2,
                "source_file": tmp_path / "s.txt",
                "reference_file": tmp_path / "r.txt",
            },
            "systems": [{"name": "pipeline", "kind": "pipeline", "config": scoring}],
        }
    )
    backend = FakeBackend()
    await run_flag_test(
        cfg,
        scoring,
        tmp_path / "out",
        backends_factory=factory(backend),
        scorer=SimilarityDouble(),
        log=lambda m: None,
    )
    records = read_records(tmp_path / "out")
    assert sorted(r.unit for r in records if r.kind == "clean") == [0, 1]
    assert not (tmp_path / "out" / "ATTRIBUTION.txt").exists()  # no FLORES text, no notice
    assert all(r.meaning is not None for r in records)


async def test_a_round_with_nothing_to_record_is_not_scored(tmp_path):
    first, second = " ".join(["alpha"] * 150) + ".", " ".join(["bravo"] * 150) + "."
    (tmp_path / "s.txt").write_text(f"{first}\n{second}\n", encoding="utf-8")
    # the second reference is too short to lose anything: it can only be left untranslated
    (tmp_path / "r.txt").write_text("uno, dos y tres.\nseis.\n", encoding="utf-8")
    scoring = write_scoring_config(
        tmp_path, chunking={"target_words": 100, "max_words": 200, "reserve_tokens": 3000}
    )
    cfg = BenchConfig.model_validate(
        {
            "dataset": {
                "kind": "parallel",
                "path": tmp_path,
                "pairs": ["en>es"],
                "sentences": 2,
                "source_file": tmp_path / "s.txt",
                "reference_file": tmp_path / "r.txt",
            },
            "systems": [{"name": "pipeline", "kind": "pipeline", "config": scoring}],
        }
    )
    backend = FakeBackend()
    await run_flag_test(
        cfg,
        scoring,
        tmp_path / "out",
        backends_factory=factory(backend),
        scorer=SimilarityDouble(),
        log=lambda m: None,
    )
    records = read_records(tmp_path / "out")
    assert [(r.kind, r.unit) for r in records] == [
        ("clean", 0),
        ("clean", 1),
        ("omission", 0),
        ("untranslated", 0),
        ("untranslated", 1),
    ]
    # three rounds (clean, omission, untranslated) in each of the two chunks, but the second
    # sentence has no omission to carry, so its chunk is judged twice
    judged = [
        p
        for p in (parse_input([m.model_dump() for m in r.messages]) for r in backend.requests)
        if p["task"] == "judge"
    ]
    assert [len(p["spans"]) for p in judged] == [1] * 5


async def test_an_empty_sentence_is_one_error_line(tmp_path):
    (tmp_path / "s.txt").write_text("one two three\n\nfour five six\n", encoding="utf-8")
    (tmp_path / "r.txt").write_text("uno dos tres\n\ncuatro cinco seis\n", encoding="utf-8")
    scoring = write_scoring_config(tmp_path)
    cfg = BenchConfig.model_validate(
        {
            "dataset": {
                "kind": "parallel",
                "path": tmp_path,
                "pairs": ["en>es"],
                "source_file": tmp_path / "s.txt",
                "reference_file": tmp_path / "r.txt",
            },
            "systems": [{"name": "pipeline", "kind": "pipeline", "config": scoring}],
        }
    )
    backend = FakeBackend()
    with pytest.raises(
        ConfigError, match=r"s\.txt: line 2 is blank; every line must hold a sentence$"
    ):
        await run_flag_test(
            cfg,
            scoring,
            tmp_path / "out",
            backends_factory=factory(backend),
            scorer=SimilarityDouble(),
            log=lambda m: None,
        )
    assert backend.closed


async def test_flags_declare_the_injected_errors_beside_the_flores_text(tmp_path, mini_flores):
    _, out, _ = await run(tmp_path, mini_flores)
    assert (out / "LICENSE.txt").read_text(encoding="utf-8") == LICENSE_TEXT
    text = (out / "ATTRIBUTION.txt").read_text(encoding="utf-8")
    assert "NLLB Team" in text and "Unmodified" not in text
    assert f"- {FLAGS_MODIFIED}\n" in text
    assert "flags/*.jsonl" in FLAGS_MODIFIED and "injected errors" in FLAGS_MODIFIED


async def test_a_failed_judge_batch_marks_only_its_own_spans(tmp_path, mini_flores, monkeypatch):
    monkeypatch.setattr("coglossa.stages.judge.JUDGE_BATCH_SPANS", 1)

    class FirstSpanJudgeDown(FakeBackend):
        async def chat(self, request):
            payload = parse_input([m.model_dump() for m in request.messages])
            if payload["task"] == "judge" and payload["spans"][0]["start"] == "s1":
                raise PermanentBackendError("judge went away")
            return await super().chat(request)

    _, out, _ = await run(tmp_path, mini_flores, backend=FirstSpanJudgeDown(), injector=False)
    records = read_records(out)
    unjudged = [r for r in records if r.signals["judge"] is None]
    judged = [r for r in records if r.signals["judge"] is not None]
    assert unjudged and judged
    assert all(any(e.startswith("judge: ") for e in r.errors) for r in unjudged)
    assert all(not any(e.startswith("judge: ") for e in r.errors) for r in judged)


async def run_pairs(tmp_path, mini_flores, *, scoring=None, logs=None, **kwargs):
    """Two pairs into tmp_path/out; a fresh backend each call, so its requests count only the
    pairs this call scored."""
    tmp_path.mkdir(exist_ok=True)
    scoring = scoring or write_scoring_config(tmp_path)
    cfg = bench_config(tmp_path, mini_flores, scoring, pairs=["en>es", "en>yo"])
    backend = MistranslatingInjector()
    summary = await run_flag_test(
        cfg,
        scoring,
        tmp_path / "out",
        backends_factory=factory(backend),
        scorer=SimilarityDouble(),
        log=(logs.append if logs is not None else lambda m: None),
        **kwargs,
    )
    return summary, backend


async def test_finished_pairs_are_kept_and_the_summary_rebuilt(tmp_path, mini_flores):
    first, _ = await run_pairs(tmp_path, mini_flores)
    out = tmp_path / "out"
    saved = {slug: read_records(out, slug) for slug in ("en-es", "en-yo")}
    logs = []
    again, backend = await run_pairs(tmp_path, mini_flores, logs=logs)
    assert backend.requests == []  # nothing was translated, injected or judged again
    assert again == first  # the summary, samples included, comes from the saved records
    assert {slug: read_records(out, slug) for slug in saved} == saved
    for slug, label in (("en-es", "en>es"), ("en-yo", "en>yo")):
        assert f"flags: {label}: kept from an earlier run ({len(saved[slug])} spans)" in logs


async def test_force_scores_every_pair_again(tmp_path, mini_flores):
    await run_pairs(tmp_path, mini_flores)
    _, backend = await run_pairs(tmp_path, mini_flores, force=True)
    assert backend.requests


async def test_a_pair_without_its_record_is_scored_again(tmp_path, mini_flores):
    """flags/<pair>.jsonl from before resume existed, or a crash between the two writes."""
    await run_pairs(tmp_path, mini_flores)
    (tmp_path / "out" / "flags" / "en-yo.meta.json").unlink()
    logs = []
    _, backend = await run_pairs(tmp_path, mini_flores, logs=logs)
    assert any(line.startswith("flags: en>es: kept from an earlier run") for line in logs)
    assert "flags: en>yo: scoring again: its settings were not recorded" in logs
    assert backend.requests
    assert (tmp_path / "out" / "flags" / "en-yo.meta.json").is_file()


async def test_a_forced_pair_is_not_kept_until_its_new_record_is_written(
    tmp_path, mini_flores, monkeypatch
):
    """A crash after --force writes the new records but before their meta: the old meta must
    not vouch for them."""
    from coglossa.bench import flagtest

    await run_pairs(tmp_path, mini_flores)
    real = flagtest.write_atomic

    def crash_on_meta(path, text):
        if path.name.endswith(".meta.json"):
            raise OSError("disk went away")
        real(path, text)

    monkeypatch.setattr(flagtest, "write_atomic", crash_on_meta)
    with pytest.raises(OSError, match="disk went away"):
        await run_pairs(tmp_path, mini_flores, force=True)
    monkeypatch.setattr(flagtest, "write_atomic", real)
    logs = []
    await run_pairs(tmp_path, mini_flores, logs=logs)
    assert "flags: en>es: scoring again: its settings were not recorded" in logs


async def test_a_changed_setting_scores_the_pairs_again(tmp_path, mini_flores):
    await run_pairs(tmp_path, mini_flores)
    thresholds = {**PRE_CALIBRATION_THRESHOLDS, "style": 50.0}
    scoring = write_scoring_config(tmp_path, scoring={"thresholds": thresholds})
    logs = []
    _, backend = await run_pairs(tmp_path, mini_flores, scoring=scoring, logs=logs)
    assert "flags: en>es: scoring again: settings changed" in logs
    assert backend.requests


async def test_timeouts_and_concurrency_do_not_score_a_pair_again(tmp_path, mini_flores):
    """Raising a timeout so that a shared CPU scorer keeps up must not cost the finished pairs."""
    scorer = {"url": "http://scorer:8000"}
    await run_pairs(tmp_path, mini_flores, scoring=write_scoring_config(tmp_path, scorer=scorer))
    backends = {
        k: {**b, "timeout_s": 900, "max_concurrency": 1} for k, b in SCORING["backends"].items()
    }
    scoring = write_scoring_config(
        tmp_path,
        backends=backends,
        scorer={**scorer, "timeout_s": 1200, "max_concurrency": 1},
        retry={"max_attempts": 8},
    )
    logs = []
    _, backend = await run_pairs(tmp_path, mini_flores, scoring=scoring, logs=logs)
    assert backend.requests == [], logs


async def test_pairs_are_scored_at_the_same_time_up_to_the_limit(
    tmp_path, mini_flores, monkeypatch
):
    from coglossa.bench import flagtest

    real = flagtest._score_pair
    running, peak = 0, 0

    async def tracked(*args, **kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            await asyncio.sleep(0.05)
            return await real(*args, **kwargs)
        finally:
            running -= 1

    monkeypatch.setattr(flagtest, "_score_pair", tracked)
    one, _ = await run_pairs(tmp_path / "one", mini_flores)
    assert peak == 1  # the default: one pair at a time
    peak = 0
    two, _ = await run_pairs(tmp_path / "two", mini_flores, pairs=2)
    assert peak == 2
    assert two == one  # the same summary and samples, in the bench config's pair order


async def test_a_failed_pair_keeps_the_pairs_that_finished(tmp_path, mini_flores, monkeypatch):
    from coglossa.bench import flagtest

    real = flagtest._score_pair

    async def yoruba_fails(ctx, scorer, pair, *args, **kwargs):
        if pair.tgt == "yo":
            raise ConfigError("yoruba went wrong")
        return await real(ctx, scorer, pair, *args, **kwargs)

    monkeypatch.setattr(flagtest, "_score_pair", yoruba_fails)
    with pytest.raises(ConfigError, match="yoruba went wrong"):
        await run_pairs(tmp_path, mini_flores)
    monkeypatch.setattr(flagtest, "_score_pair", real)
    logs = []
    await run_pairs(tmp_path, mini_flores, logs=logs)
    assert any(line.startswith("flags: en>es: kept from an earlier run") for line in logs)
    assert not any(line.startswith("flags: en>yo: kept") for line in logs)


def test_pairs_must_be_at_least_one():
    with pytest.raises(ConfigError, match="--pairs must be at least 1"):
        asyncio.run(run_flag_test(None, None, None, pairs=0))
