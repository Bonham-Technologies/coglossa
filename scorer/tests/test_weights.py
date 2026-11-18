import fnmatch
import json
import logging
import stat
from pathlib import Path

import pytest
import torch
from safetensors.torch import load_file
from transformers import MT5Config, MT5ForConditionalGeneration

from coglossa_scorer import weights
from coglossa_scorer.weights import MODELS, convert_to_safetensors, fetch, main, verify


def tiny_mt5() -> MT5ForConditionalGeneration:
    torch.manual_seed(0)
    config = MT5Config(
        vocab_size=64,
        d_model=16,
        d_kv=4,
        d_ff=32,
        num_layers=1,
        num_decoder_layers=1,
        num_heads=2,
        decoder_start_token_id=0,
    )
    return MT5ForConditionalGeneration(config)


def save_bin(model, directory: Path, shards: int = 1) -> dict[str, torch.Tensor]:
    """A checkpoint as MetricX publishes it: torch.save pickles, tied embeddings sharing storage."""
    directory.mkdir(parents=True, exist_ok=True)
    model.config.save_pretrained(directory)
    state = model.state_dict()
    if shards == 1:
        torch.save(state, directory / "pytorch_model.bin")
        return state
    names = sorted(state)
    weight_map = {}
    for i in range(shards):
        shard = f"pytorch_model-{i + 1:05d}-of-{shards:05d}.bin"
        part = {n: state[n] for n in names[i::shards]}
        torch.save(part, directory / shard)
        weight_map |= dict.fromkeys(part, shard)
    (directory / "pytorch_model.bin.index.json").write_text(
        json.dumps({"metadata": {}, "weight_map": weight_map})
    )
    return state


def assert_world_readable(directory: Path) -> None:
    """The scorer container runs as another uid than whoever extracted the bundle."""
    for path in directory.glob("*.safetensors*"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o644, path.name


def assert_same_model(directory: Path, state: dict[str, torch.Tensor]) -> None:
    """Loads like MetricXQE does, and every weight comes out equal and was read from the file."""
    messages: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("transformers")
    handler = Capture()
    logger.addHandler(handler)
    try:
        loaded, info = MT5ForConditionalGeneration.from_pretrained(
            directory, local_files_only=True, output_loading_info=True
        )
    finally:
        logger.removeHandler(handler)
    assert not info["missing_keys"] and not info["unexpected_keys"], info
    assert not [m for m in messages if "newly initialized" in m or "not used" in m], messages
    for name, tensor in loaded.state_dict().items():
        assert torch.equal(tensor, state[name]), name


def assert_stored_once(directory: Path, state: dict[str, torch.Tensor]) -> None:
    """Aliased tensors are stored once: as many stored tensors as distinct tensors in state."""
    distinct = {
        (t.untyped_storage().data_ptr(), t.storage_offset(), tuple(t.shape), t.stride())
        for t in state.values()
    }
    stored: dict[str, torch.Tensor] = {}
    for file in directory.glob("*.safetensors"):
        stored |= load_file(file)
    assert len(stored) == len(distinct)


def test_a_single_file_checkpoint_becomes_safetensors(tmp_path):
    state = save_bin(tiny_mt5(), tmp_path)
    convert_to_safetensors(tmp_path)
    assert not list(tmp_path.glob("*.bin"))
    stored = load_file(tmp_path / "model.safetensors")
    # the tied embeddings are stored once; transformers re-ties them on load
    assert len([n for n in stored if n.endswith("embed_tokens.weight")]) == 0
    assert_stored_once(tmp_path, state)
    assert_world_readable(tmp_path)
    assert_same_model(tmp_path, state)


def test_a_sharded_checkpoint_becomes_sharded_safetensors(tmp_path):
    state = save_bin(tiny_mt5(), tmp_path, shards=2)
    convert_to_safetensors(tmp_path)
    assert not list(tmp_path.glob("*.bin"))
    assert not (tmp_path / "pytorch_model.bin.index.json").exists()
    index = json.loads((tmp_path / "model.safetensors.index.json").read_text())
    assert set(index["weight_map"].values()) == {
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
    }
    assert index["metadata"]["total_size"] > 0
    assert len(list(tmp_path.glob("*.safetensors*"))) == 3  # two shards and the index
    assert_world_readable(tmp_path)
    assert_same_model(tmp_path, state)


def test_overlapping_views_are_stored_as_copies(tmp_path):
    base = torch.arange(6.0)
    torch.save({"a": base[:4], "b": base[2:]}, tmp_path / "pytorch_model.bin")
    convert_to_safetensors(tmp_path)
    stored = load_file(tmp_path / "model.safetensors")
    assert stored["a"].tolist() == [0, 1, 2, 3] and stored["b"].tolist() == [2, 3, 4, 5]


def test_nothing_to_convert_is_left_alone(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"x")
    convert_to_safetensors(tmp_path)
    assert (tmp_path / "model.safetensors").read_bytes() == b"x"


class FakeHub:
    """Stands in for huggingface_hub: records calls, writes the files a download would."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def resolve(self, repo: str, revision: str | None) -> str:
        if self.fail:
            raise OSError("connection refused")
        return revision or f"sha-of-{repo}"

    def download(self, **kwargs) -> str:
        self.calls.append(kwargs)
        target = Path(kwargs["local_dir"])
        (target / ".cache").mkdir(parents=True, exist_ok=True)
        if "metricx" in kwargs["repo_id"]:
            save_bin(tiny_mt5(), target)
        elif kwargs["repo_id"] == "google/mt5-xl":
            (target / "spiece.model").write_bytes(b"sp")
        else:
            (target / "2_Dense").mkdir(exist_ok=True)
            for f in ("modules.json", "config.json", "model.safetensors", "pytorch_model.bin"):
                (target / f).write_text("[]")
            for f in ("model.safetensors", "pytorch_model.bin"):
                (target / "2_Dense" / f).write_text("[]")
        for path in list(target.rglob("*")):  # what huggingface_hub does with ignore_patterns
            rel = str(path.relative_to(target))
            if path.is_file() and any(
                fnmatch.fnmatch(rel, pat) for pat in kwargs.get("ignore_patterns") or []
            ):
                path.unlink()
        return str(target)


def manifest_files(out: Path, name: str) -> list[str]:
    return json.loads((out / "weights.json").read_text())[name]["files"]


def test_fetch_downloads_converts_and_records(tmp_path):
    hub = FakeHub()
    manifest = fetch(
        tmp_path,
        ["metricx-24-hybrid-large-v2p6", "mt5-xl"],
        revisions={"mt5-xl": "abc123"},
        hub=(hub.resolve, hub.download),
        log=lambda _: None,
    )
    assert [c["repo_id"] for c in hub.calls] == [
        "google/metricx-24-hybrid-large-v2p6",
        "google/mt5-xl",
    ]
    assert hub.calls[1]["revision"] == "abc123"
    assert hub.calls[1]["allow_patterns"] == list(MODELS["mt5-xl"].allow)
    assert not (tmp_path / "mt5-xl" / ".cache").exists()
    assert list((tmp_path / "metricx-24-hybrid-large-v2p6").glob("*.safetensors"))
    assert manifest["mt5-xl"] == {
        "repo": "google/mt5-xl",
        "revision": "abc123",
        "files": ["spiece.model"],
    }
    assert json.loads((tmp_path / "weights.json").read_text()) == manifest
    # a second fetch adds to the manifest instead of replacing it
    fetch(tmp_path, ["labse"], hub=(hub.resolve, hub.download), log=lambda _: None)
    # LaBSE keeps its safetensors and drops the duplicate pickles
    assert {"pytorch_model.bin", "2_Dense/pytorch_model.bin"} <= set(
        hub.calls[2]["ignore_patterns"]
    )
    assert manifest_files(tmp_path, "labse") == [
        "2_Dense/model.safetensors",
        "config.json",
        "model.safetensors",
        "modules.json",
    ]
    assert set(json.loads((tmp_path / "weights.json").read_text())) == {
        "metricx-24-hybrid-large-v2p6",
        "mt5-xl",
        "labse",
    }


def test_verify_lists_every_problem(tmp_path):
    names = ["metricx-24-hybrid-large-v2p6", "mt5-xl", "labse"]
    assert verify(tmp_path, names) == [
        "metricx-24-hybrid-large-v2p6: missing; fetch it: coglossa-scorer-weights fetch --out "
        f"{tmp_path} --model metricx-24-hybrid-large-v2p6",
        f"mt5-xl: missing; fetch it: coglossa-scorer-weights fetch --out {tmp_path} --model mt5-xl",
        f"labse: missing; fetch it: coglossa-scorer-weights fetch --out {tmp_path} --model labse",
        "weights.json is missing; fetch the weights with coglossa-scorer-weights fetch",
    ]
    hub = FakeHub()
    fetch(tmp_path, names, hub=(hub.resolve, hub.download), log=lambda _: None)
    assert verify(tmp_path, names) == []
    (tmp_path / "mt5-xl" / "spiece.model").unlink()
    (tmp_path / "labse" / "model.safetensors").unlink()
    torch.save({}, tmp_path / "metricx-24-hybrid-large-v2p6" / "pytorch_model.bin")
    for f in (tmp_path / "metricx-24-hybrid-large-v2p6").glob("*.safetensors"):
        f.unlink()
    assert verify(tmp_path, names) == [
        "metricx-24-hybrid-large-v2p6: has unconverted .bin files; fetch it again",
        "metricx-24-hybrid-large-v2p6: has no *.safetensors weights; fetch it again",
        "mt5-xl: spiece.model is missing; fetch it again",
        "labse: has no *.safetensors weights; fetch it again",
    ]


def test_cli_fetch_and_verify(tmp_path, monkeypatch, capsys):
    hub = FakeHub()
    monkeypatch.setattr(weights, "_hub", lambda: (hub.resolve, hub.download))
    out = str(tmp_path)
    assert main(["fetch", "--out", out, "--model", "mt5-xl", "--revision", "mt5-xl=r1"]) == 0
    assert hub.calls[0]["revision"] == "r1"
    assert main(["verify", out, "--model", "mt5-xl"]) == 0
    assert main(["verify", out]) == 1  # the other three are missing
    assert "error: labse: missing" in capsys.readouterr().err


def test_cli_errors_are_one_line(tmp_path, monkeypatch, capsys):
    hub = FakeHub(fail=True)
    monkeypatch.setattr(weights, "_hub", lambda: (hub.resolve, hub.download))
    assert main(["fetch", "--out", str(tmp_path), "--model", "labse"]) == 1
    assert capsys.readouterr().err == (
        "error: cannot fetch sentence-transformers/LaBSE: connection refused\n"
    )
    with pytest.raises(SystemExit) as e:
        main(["fetch", "--out", str(tmp_path), "--model", "nope"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        main(["fetch", "--out", str(tmp_path), "--revision", "no-equals-sign"])
    assert e.value.code == 2


def test_the_real_hub_is_wired_without_calling_it():
    resolve, download = weights._hub()
    assert callable(resolve) and download.__name__ == "snapshot_download"


def test_cli_any_failure_is_one_line_and_non_zero(tmp_path, monkeypatch, capsys):
    hub = FakeHub()
    monkeypatch.setattr(weights, "_hub", lambda: (hub.resolve, hub.download))

    def boom(_):
        raise RuntimeError("bad pickle\nsecond line\n  third")

    monkeypatch.setattr(weights, "convert_to_safetensors", boom)
    assert main(["fetch", "--out", str(tmp_path), "--model", "metricx-24-hybrid-large-v2p6"]) == 1
    assert capsys.readouterr().err == (
        "error: cannot fetch google/metricx-24-hybrid-large-v2p6: bad pickle second line third\n"
    )
    bad = tmp_path / "file"
    bad.write_text("x")
    assert main(["fetch", "--out", str(bad / "sub"), "--model", "mt5-xl"]) == 1
    assert capsys.readouterr().err.count("\n") == 1


def test_refetch_removes_stale_files_and_manifest_lists_only_current(tmp_path):
    hub = FakeHub()
    fetch(tmp_path, ["mt5-xl"], hub=(hub.resolve, hub.download), log=lambda _: None)
    stale = tmp_path / "mt5-xl" / "old-shard.safetensors"
    stale.write_text("x")
    fetch(tmp_path, ["mt5-xl"], hub=(hub.resolve, hub.download), log=lambda _: None)
    assert not stale.exists()
    assert manifest_files(tmp_path, "mt5-xl") == ["spiece.model"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mt5-xl", "weights.json"]


def test_failed_refetch_keeps_the_old_files(tmp_path):
    hub = FakeHub()
    fetch(tmp_path, ["mt5-xl"], hub=(hub.resolve, hub.download), log=lambda _: None)

    def fail(**_):
        raise OSError("down")

    with pytest.raises(OSError):
        fetch(tmp_path, ["mt5-xl"], hub=(hub.resolve, fail), log=lambda _: None)
    assert (tmp_path / "mt5-xl" / "spiece.model").is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mt5-xl", "weights.json"]


def test_revision_for_an_unselected_model_is_refused(tmp_path, monkeypatch, capsys):
    hub = FakeHub()
    monkeypatch.setattr(weights, "_hub", lambda: (hub.resolve, hub.download))
    with pytest.raises(SystemExit) as e:
        main(["fetch", "--out", str(tmp_path), "--model", "mt5-xl", "--revision", "labse=abc"])
    assert e.value.code == 2
    err = capsys.readouterr().err.strip().splitlines()[-1]
    assert "--revision for labse" in err and "--model" in err
    assert hub.calls == []
    with pytest.raises(SystemExit):  # no --model: all are selected, so it is accepted
        main(["fetch", "--out", str(tmp_path), "--revision", "nope=abc"])
    assert main(["fetch", "--out", str(tmp_path), "--revision", "labse=abc"]) == 0


def test_verify_fails_on_a_missing_indexed_shard(tmp_path):
    hub = FakeHub()
    name = "metricx-24-hybrid-large-v2p6"
    save_bin(tiny_mt5(), tmp_path / "src", shards=2)
    fetch(tmp_path, [name], hub=(hub.resolve, hub.download), log=lambda _: None)
    d = tmp_path / name
    for f in d.iterdir():  # swap in the sharded layout
        f.unlink()
    convert_dir = tmp_path / "src"
    for f in convert_dir.iterdir():
        f.rename(d / f.name)
    convert_to_safetensors(d)
    assert verify(tmp_path, [name]) == []
    shard = sorted(d.glob("model-0000*-of-*.safetensors"))[0]
    shard.unlink()
    assert verify(tmp_path, [name]) == [
        f"{name}: {shard.name} is missing (named in model.safetensors.index.json); fetch it again"
    ]
    (d / "model.safetensors.index.json").write_text("{")
    assert verify(tmp_path, [name]) == [
        f"{name}: model.safetensors.index.json is unreadable; fetch it again"
    ]


def test_verify_fails_on_a_missing_labse_dense_layer(tmp_path):
    hub = FakeHub()
    fetch(tmp_path, ["labse"], hub=(hub.resolve, hub.download), log=lambda _: None)
    assert verify(tmp_path, ["labse"]) == []
    (tmp_path / "labse" / "2_Dense" / "model.safetensors").unlink()
    assert verify(tmp_path, ["labse"]) == [
        "labse: 2_Dense/model.safetensors is missing; fetch it again"
    ]


def test_a_failed_fetch_is_resumed_and_another_revision_starts_afresh(tmp_path):
    hub = FakeHub()
    seen: list[list[str]] = []

    def half(**kwargs):  # the first file arrives, then the connection drops
        local = Path(kwargs["local_dir"])
        seen.append(sorted(p.name for p in local.iterdir()) if local.is_dir() else [])
        local.mkdir(parents=True, exist_ok=True)
        (local / "spiece.model").write_bytes(b"sp")
        raise OSError("connection reset")

    for _ in range(2):  # the retry finds what the first attempt downloaded
        with pytest.raises(OSError):
            fetch(tmp_path, ["mt5-xl"], revisions={"mt5-xl": "r1"}, hub=(hub.resolve, half))
    assert seen == [[], ["spiece.model"]]
    assert not (tmp_path / "mt5-xl").exists()
    fetch(tmp_path, ["mt5-xl"], revisions={"mt5-xl": "r2"}, hub=(hub.resolve, hub.download))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mt5-xl", "weights.json"]


def test_a_symlinked_model_directory_is_replaced_without_deleting_its_target(tmp_path):
    hub = FakeHub()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep").write_text("x")
    out = tmp_path / "out"
    out.mkdir()
    (out / "mt5-xl").symlink_to(elsewhere)
    fetch(out, ["mt5-xl"], hub=(hub.resolve, hub.download), log=lambda _: None)
    assert not (out / "mt5-xl").is_symlink() and (out / "mt5-xl" / "spiece.model").is_file()
    assert (elsewhere / "keep").is_file()
    assert sorted(p.name for p in out.iterdir()) == ["mt5-xl", "weights.json"]
