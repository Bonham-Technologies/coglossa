import csv
import json

import pytest

from coglossa.bench.absheet import export_ab
from coglossa.bench.flores import (
    ATTRIBUTION_TEXT,
    FLAGS_MODIFIED,
    LICENSE_TEXT,
    write_attribution,
)
from coglossa.bench.metrics import SystemPairResult, UnitResult, summarise
from coglossa.cli import main
from coglossa.config import ConfigError


def unit(first, last, output, source="src"):
    return UnitResult(
        first=first, last=last, source=source, reference="ref", output=output, chrf=1.0
    )


def write(tmp_path, system, units, *, pair="en>es", status="complete", in_summary=True):
    """A result file, and its row in summary.json (as `bench run` leaves them)."""
    result = SystemPairResult(system=system, pair=pair, status=status, units=units)
    if in_summary:
        summary = tmp_path / "summary.json"
        rows = json.loads(summary.read_text("utf-8")) if summary.exists() else []
        rows.append(summarise(result).model_dump())
        summary.write_text(json.dumps(rows), "utf-8")
    folder = tmp_path / "runs" / system
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{pair.replace('>', '-')}.json").write_text(result.model_dump_json(), "utf-8")


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


@pytest.fixture
def two(tmp_path):
    write(
        tmp_path,
        "x",
        [
            unit(0, 0, "x0, uno"),
            unit(1, 1, "same"),
            unit(2, 2, "x2\nlínea"),
            unit(3, 3, "x3 日本語"),
        ],
    )
    write(tmp_path, "y", [unit(0, 0, "y0"), unit(1, 1, "same"), unit(2, 2, "y2"), unit(3, 3, "y3")])
    return tmp_path


def test_blind_sheet_and_key(two):
    sheet, key = export_ab(two, "x", "y")
    rows = read(sheet)
    assert rows[0] == [
        "item", "pair", "source", "translation_1", "translation_2",
        "preferred (1, 2 or same)", "comment",
    ]  # fmt: skip
    assert len(rows) == 4  # the identical unit is left out
    assert all(r[1] == "en>es" and r[2] == "src" for r in rows[1:])
    keys = read(key)
    assert keys[0] == ["item", "translation_1", "translation_2"]
    assert [k[0] for k in keys[1:]] == ["1", "2", "3"]
    assert any(k[2] == "x" for k in keys[1:])
    outputs = {"x": {"x0, uno", "x2\nlínea", "x3 日本語"}, "y": {"y0", "y2", "y3"}}
    for k, r in zip(keys[1:], rows[1:], strict=True):
        assert {k[1], k[2]} == {"x", "y"}
        assert r[3] in outputs[k[1]] and r[4] in outputs[k[2]]


def test_sheet_never_names_systems_and_is_bom_csv(tmp_path):
    write(tmp_path, "alpha", [unit(0, 0, "uno, dos\ntres 日本語")])
    write(tmp_path, "beta", [unit(0, 0, "otro")])
    sheet, _ = export_ab(tmp_path, "alpha", "beta")
    raw = sheet.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf"
    text = raw.decode("utf-8-sig")
    assert "alpha" not in text and "beta" not in text
    assert '"uno, dos\ntres 日本語"' in text.replace("\r\n", "\n")  # quoted by csv


def test_same_seed_same_files(two):
    s1, k1 = export_ab(two, "x", "y", seed=7)
    a, b = s1.read_bytes(), k1.read_bytes()
    s2, k2 = export_ab(two, "x", "y", seed=7)
    assert (s2.read_bytes(), k2.read_bytes()) == (a, b)


def test_mismatched_failed_and_missing(tmp_path):
    write(tmp_path, "x", [unit(0, 1, "merged"), unit(2, 2, "a")])
    write(tmp_path, "y", [unit(0, 0, "p"), unit(1, 1, "q"), unit(2, 2, "b")])
    write(tmp_path, "x", [], pair="en>fr", status="failed")
    write(tmp_path, "y", [unit(0, 0, "z")], pair="en>fr")
    logs = []
    sheet, _ = export_ab(tmp_path, "x", "y", pairs=["en>es", "en>fr", "en>de"], log=logs.append)
    assert len(read(sheet)) == 2
    assert logs == [
        "ab: 1 items; left out 0 identical, 3 unmatched unit(s), 1 failed pair(s), "
        "1 missing pair(s)"
    ]


def test_missing_system(two):
    with pytest.raises(ConfigError, match="no results for system 'z'"):
        export_ab(two, "x", "z")


def test_bad_pair(two):
    with pytest.raises(ConfigError, match="src>tgt"):
        export_ab(two, "x", "y", pairs=["enes"])


def test_cli(two, capsys):
    assert main(["bench", "ab", "--out", str(two), "--a", "x", "--b", "y", "--pair", "en>es"]) == 0
    captured = capsys.readouterr()
    assert "ab-sheet.csv" in captured.out and "ab-key.csv" in captured.out
    assert "1 identical" in captured.err


def test_cli_missing_system(two, capsys):
    assert main(["bench", "ab", "--out", str(two), "--a", "x", "--b", "z"]) == 1
    assert capsys.readouterr().err.strip() == (
        f"error: no results for system 'z' in {two / 'summary.json'} (run coglossa bench run first)"
    )


def meta(folder, kind):
    (folder / "bench-meta.json").write_text(json.dumps({"dataset": {"kind": kind}}), "utf-8")


@pytest.mark.parametrize("bench_meta", [None, "flores200", "not json"])
def test_the_sheet_travels_with_the_flores_attribution(two, bench_meta):
    """Without a readable bench-meta.json the attribution is written anyway."""
    if bench_meta == "not json":
        (two / "bench-meta.json").write_text("{", "utf-8")
    elif bench_meta:
        meta(two, bench_meta)
    export_ab(two, "x", "y")
    assert (two / "LICENSE.txt").read_text(encoding="utf-8") == LICENSE_TEXT
    assert (two / "ATTRIBUTION.txt").read_text(encoding="utf-8") == ATTRIBUTION_TEXT


def test_a_parallel_dataset_sheet_has_no_flores_notice(two):
    meta(two, "parallel")
    export_ab(two, "x", "y")
    assert not (two / "LICENSE.txt").exists() and not (two / "ATTRIBUTION.txt").exists()


def test_the_sheet_keeps_the_flags_notice(two):
    meta(two, "flores200")
    write_attribution(two, modified=FLAGS_MODIFIED)
    export_ab(two, "x", "y")
    assert FLAGS_MODIFIED in (two / "ATTRIBUTION.txt").read_text(encoding="utf-8")


def test_a_result_the_summary_left_out_is_not_read(two):
    write(two, "x", [unit(0, 0, "xa")], pair="en>fr", in_summary=False)
    write(two, "y", [unit(0, 0, "ya")], pair="en>fr", in_summary=False)
    (two / "runs" / "y" / "en-de.json").write_text("{not json", "utf-8")  # never opened
    logs = []
    sheet, _ = export_ab(two, "x", "y", log=logs.append)
    assert {r[1] for r in read(sheet)[1:]} == {"en>es"}
    assert logs == [
        "ab: 3 items; left out 1 identical, 0 unmatched unit(s), 0 failed pair(s), "
        "0 missing pair(s)"
    ]
    sheet, _ = export_ab(two, "x", "y", pairs=["en>fr", "en>es"], log=logs.append)
    assert {r[1] for r in read(sheet)[1:]} == {"en>es"}
    assert logs[-1].endswith("0 failed pair(s), 1 missing pair(s)")


def test_a_failed_row_in_the_summary_is_left_out(tmp_path):
    write(tmp_path, "x", [unit(0, 0, "a")])
    write(tmp_path, "y", [unit(0, 0, "b")])
    write(tmp_path, "x", [], pair="en>fr", status="failed")
    write(tmp_path, "y", [unit(0, 0, "z")], pair="en>fr")
    logs = []
    sheet, _ = export_ab(tmp_path, "x", "y", log=logs.append)
    assert {r[1] for r in read(sheet)[1:]} == {"en>es"}
    assert logs[0].endswith("1 failed pair(s), 0 missing pair(s)")


def test_a_summary_row_without_its_file_is_missing(tmp_path):
    write(tmp_path, "x", [unit(0, 0, "a")])
    write(tmp_path, "y", [unit(0, 0, "b")])
    (tmp_path / "runs" / "y" / "en-es.json").unlink()
    logs = []
    export_ab(tmp_path, "x", "y", log=logs.append)
    assert logs[0].endswith("0 failed pair(s), 1 missing pair(s)")


@pytest.mark.parametrize("damage", ["missing", "not json", "not a table", "bad label"])
def test_no_summary_is_one_line(two, damage, capsys):
    damage_text = {
        "not json": "{",
        "not a table": '[{"a": 1}]',
        "bad label": '[{"system": "x", "pair": "enes", "status": "complete"}]',
    }
    if damage == "missing":
        (two / "summary.json").unlink()
    else:
        (two / "summary.json").write_text(damage_text[damage], "utf-8")
    with pytest.raises(ConfigError) as e:
        export_ab(two, "x", "y")
    assert str(e.value) == f"no summary in {two} (run coglossa bench run first)"
    assert main(["bench", "ab", "--out", str(two), "--a", "x", "--b", "y"]) == 1
    assert capsys.readouterr().err.strip() == (
        f"error: no summary in {two} (run coglossa bench run first)"
    )


def test_a_file_that_says_failed_counts_as_failed_whatever_the_summary_says(two):
    path = two / "runs" / "y" / "en-es.json"
    data = json.loads(path.read_text("utf-8"))
    data["status"] = "failed"
    path.write_text(json.dumps(data), "utf-8")  # summary.json still says complete
    logs = []
    sheet, _ = export_ab(two, "x", "y", log=logs.append)
    assert len(read(sheet)) == 1
    assert logs == [
        "ab: 0 items; left out 0 identical, 0 unmatched unit(s), 1 failed pair(s), "
        "0 missing pair(s)"
    ]


def test_a_system_left_out_of_the_config_is_never_read(tmp_path):
    from coglossa.bench.config import BenchConfig
    from coglossa.bench.run import _write_summary

    for name in ("x", "y", "gone"):
        write(tmp_path, name, [unit(0, 0, name)], in_summary=False)
    systems = [
        {"name": n, "kind": "baseline", "config": "c.yaml", "model": "m"} for n in ("x", "y")
    ]
    cfg = BenchConfig.model_validate(
        {
            "dataset": {"kind": "flores200", "path": tmp_path, "pairs": ["en>es"]},
            "systems": systems,
        }
    )
    _write_summary(cfg, tmp_path, log=lambda m: None)
    with pytest.raises(ConfigError, match="no results for system 'gone'"):
        export_ab(tmp_path, "x", "gone")
    (tmp_path / "runs" / "gone" / "en-es.json").write_text("not json", "utf-8")
    sheet, _ = export_ab(tmp_path, "x", "y")
    assert len(read(sheet)) == 2
