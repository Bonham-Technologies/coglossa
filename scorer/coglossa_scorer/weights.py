"""coglossa-scorer-weights: fetch the scorer's model weights once, on a machine with internet
access, into the directory the bundle ships as models/ (spec §10), and check that directory.

MetricX publishes PyTorch pickles (pytorch_model*.bin); they are rewritten as safetensors,
which load without unpickling and faster. Only the mT5 tokenizer is fetched (MetricX carries
its own weights), and LaBSE is fetched without its TensorFlow, Flax and ONNX copies."""

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Resolve = Callable[[str, str | None], str]  # (repo, revision or None) -> commit sha
Download = Callable[..., str]  # huggingface_hub.snapshot_download


@dataclass(frozen=True)
class WeightSpec:
    repo: str
    allow: tuple[str, ...] | None = None  # only these files (None: all but IGNORE)
    convert: bool = False  # rewrite pytorch_model*.bin as safetensors
    required: tuple[str, ...] = ()  # files verify() expects
    ignore: tuple[str, ...] = ()  # skipped in addition to IGNORE
    require_safetensors: bool = False  # verify() wants *.safetensors (convert implies it)


_METRICX_FILES = (
    "config.json",
    "pytorch_model*.bin",
    "pytorch_model.bin.index.json",
    "*.safetensors",
    "model.safetensors.index.json",
)
IGNORE = (
    "*.h5",
    "*.msgpack",
    "*.onnx",
    "onnx/*",
    "openvino/*",
    "*.ot",
    "tf_model*",
    "flax_model*",
)
MODELS: dict[str, WeightSpec] = {
    "metricx-24-hybrid-xl-v2p6": WeightSpec(
        "google/metricx-24-hybrid-xl-v2p6",
        _METRICX_FILES,
        convert=True,
        required=("config.json",),
    ),
    "metricx-24-hybrid-large-v2p6": WeightSpec(
        "google/metricx-24-hybrid-large-v2p6",
        _METRICX_FILES,
        convert=True,
        required=("config.json",),
    ),
    "mt5-xl": WeightSpec(
        "google/mt5-xl",
        ("config.json", "spiece.model", "tokenizer_config.json", "special_tokens_map.json"),
        required=("spiece.model",),
    ),
    # LaBSE ships safetensors next to its pickles; the pickles are what this tool avoids
    "labse": WeightSpec(
        "sentence-transformers/LaBSE",
        required=("modules.json", "config.json", "2_Dense/model.safetensors"),
        ignore=("pytorch_model.bin", "2_Dense/pytorch_model.bin"),
        require_safetensors=True,
    ),
}
MANIFEST = "weights.json"


def _hub() -> tuple[Resolve, Download]:
    from huggingface_hub import HfApi, snapshot_download

    api = HfApi()
    return (lambda repo, revision: api.model_info(repo, revision=revision).sha), snapshot_download


def _read_manifest(out: Path) -> dict[str, Any]:
    path = out / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _discard(path: Path) -> None:
    """Remove a directory, or a symlink without what it points to."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path, ignore_errors=True)


def fetch(
    out: Path,
    names: Iterable[str],
    *,
    revisions: dict[str, str] | None = None,
    hub: tuple[Resolve, Download] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    resolve, download = hub or _hub()
    manifest = _read_manifest(out)
    for name in names:
        spec = MODELS[name]
        sha = resolve(spec.repo, (revisions or {}).get(name))
        target = out / name
        log(f"fetching {spec.repo} at {sha[:12]} into {target}")
        # into a directory of its own that then replaces the old one, so files the new revision
        # lacks go. A failed fetch leaves it, and a retry of the same revision skips the files
        # it already holds; another revision's leftover is removed.
        staging = out / f".{name}-{sha[:12]}.partial"
        for leftover in out.glob(f".{name}-*.partial"):
            if leftover != staging:
                _discard(leftover)
        download(
            repo_id=spec.repo,
            revision=sha,
            local_dir=staging,
            allow_patterns=list(spec.allow) if spec.allow else None,
            ignore_patterns=[*IGNORE, *spec.ignore],
        )
        shutil.rmtree(staging / ".cache", ignore_errors=True)  # huggingface_hub's bookkeeping
        if spec.convert:
            convert_to_safetensors(staging)
        staging.mkdir(exist_ok=True)
        # the old directory is moved aside, not deleted, until the new one is in its place
        old = out / f".{name}.old"
        _discard(old)
        if target.exists() or target.is_symlink():
            target.rename(old)
        staging.rename(target)
        _discard(old)
        files = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())
        manifest[name] = {"repo": spec.repo, "revision": sha, "files": files}
        (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _safetensors_name(shard: str) -> str:
    """pytorch_model-00001-of-00002.bin -> model-00001-of-00002.safetensors."""
    return shard.removeprefix("pytorch_").removesuffix(".bin") + ".safetensors"


def _canonical(names: list[str]) -> str:
    """The name an aliased tensor is stored under: mT5's shared.weight when it is one of them
    (transformers 5 also lists lm_head.weight among the tied names, and sorts it first), else
    the shortest name."""
    return min(names, key=lambda n: (n != "shared.weight", n.count("."), n))


def _deduplicate(state: dict[str, Any]) -> dict[str, Any]:
    """safetensors stores each tensor once. Names that alias one tensor (mT5's shared, encoder
    and decoder embeddings) keep the shortest name, which transformers re-ties on load;
    tensors that only overlap in memory are stored as copies."""
    aliases: dict[tuple[Any, ...], list[str]] = {}
    for name, t in state.items():
        key = (t.untyped_storage().data_ptr(), t.storage_offset(), tuple(t.shape), t.stride())
        aliases.setdefault(key, []).append(name)
    kept = {_canonical(names): state[names[0]] for names in aliases.values()}
    shared = Counter(t.untyped_storage().data_ptr() for t in kept.values())
    return {
        name: (t.clone() if shared[t.untyped_storage().data_ptr()] > 1 else t).contiguous()
        for name, t in kept.items()
    }


def convert_to_safetensors(model_dir: Path) -> None:
    """Rewrite pytorch_model.bin, or its shards and index, as safetensors; delete the pickles."""
    import torch
    from safetensors.torch import save_file

    index = model_dir / "pytorch_model.bin.index.json"
    if index.is_file():
        old_map: dict[str, str] = json.loads(index.read_text(encoding="utf-8"))["weight_map"]
        shards = sorted(set(old_map.values()))
    elif (model_dir / "pytorch_model.bin").is_file():
        shards = ["pytorch_model.bin"]
    else:
        return
    weight_map: dict[str, str] = {}
    total = 0
    for shard in shards:
        state = _deduplicate(torch.load(model_dir / shard, map_location="cpu", weights_only=True))
        converted = model_dir / _safetensors_name(shard)
        save_file(state, converted, metadata={"format": "pt"})
        os.chmod(converted, 0o644)  # save_file writes 0600; the container's uid must read it
        weight_map |= dict.fromkeys(state, _safetensors_name(shard))
        total += sum(t.numel() * t.element_size() for t in state.values())
        (model_dir / shard).unlink()  # one shard at a time: the disk never holds two copies
    if index.is_file():
        new_index = {
            "metadata": {"total_size": total},
            "weight_map": dict(sorted(weight_map.items())),
        }
        new_index_path = model_dir / "model.safetensors.index.json"
        new_index_path.write_text(json.dumps(new_index, indent=2) + "\n", encoding="utf-8")
        os.chmod(new_index_path, 0o644)
        index.unlink()


def _missing_shards(name: str, directory: Path) -> list[str]:
    index = directory / "model.safetensors.index.json"
    if not index.is_file():
        return []
    try:
        shards = set(json.loads(index.read_text(encoding="utf-8"))["weight_map"].values())
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return [f"{name}: model.safetensors.index.json is unreadable; fetch it again"]
    return [
        f"{name}: {shard} is missing (named in model.safetensors.index.json); fetch it again"
        for shard in sorted(s for s in shards if not (directory / str(s)).is_file())
    ]


def verify(out: Path, names: Iterable[str]) -> list[str]:
    problems: list[str] = []
    for name in names:
        spec, directory = MODELS[name], out / name
        if not directory.is_dir():
            problems.append(
                f"{name}: missing; fetch it: "
                f"coglossa-scorer-weights fetch --out {out} --model {name}"
            )
            continue
        problems += [
            f"{name}: {f} is missing; fetch it again"
            for f in spec.required
            if not (directory / f).is_file()
        ]
        problems += _missing_shards(name, directory)
        if spec.convert and any(directory.glob("*.bin")):
            problems.append(f"{name}: has unconverted .bin files; fetch it again")
        if (spec.convert or spec.require_safetensors) and not any(directory.glob("*.safetensors")):
            problems.append(f"{name}: has no *.safetensors weights; fetch it again")
    if not (out / MANIFEST).is_file():
        problems.append(
            f"{MANIFEST} is missing; fetch the weights with coglossa-scorer-weights fetch"
        )
    return problems


def _revision(text: str) -> tuple[str, str]:
    name, sep, sha = text.partition("=")
    if not sep or name not in MODELS or not sha:
        raise argparse.ArgumentTypeError(
            f"expected NAME=REVISION with NAME one of {', '.join(MODELS)}"
        )
    return name, sha


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coglossa-scorer-weights", description=__doc__.split("\n\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_p = sub.add_parser("fetch", help="download (needs internet access) and convert")
    fetch_p.add_argument("--out", type=Path, required=True)
    verify_p = sub.add_parser("verify", help="check a weights directory")
    verify_p.add_argument("out", type=Path)
    for p in (fetch_p, verify_p):
        p.add_argument("--model", action="append", choices=list(MODELS), help="default: all")
    fetch_p.add_argument("--revision", action="append", type=_revision, default=[], help="NAME=SHA")
    return parser


def _one_line(error: BaseException) -> str:
    text = " ".join(str(error).split())
    return text or type(error).__name__


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    names = args.model or list(MODELS)
    try:
        if args.command == "verify":
            problems = verify(args.out, names)
            for problem in problems:
                print(f"error: {' '.join(problem.split())}", file=sys.stderr)
            return 1 if problems else 0
        unselected = sorted({n for n, _ in args.revision} - set(names))
        if unselected:
            parser.error(f"--revision for {', '.join(unselected)}, which --model does not select")
        args.out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"error: {_one_line(e)}", file=sys.stderr)
        return 1
    for name in names:
        try:
            fetch(args.out, [name], revisions=dict(args.revision))
        except Exception as e:  # hub, network, disk and conversion errors alike  # noqa: BLE001
            print(f"error: cannot fetch {MODELS[name].repo}: {_one_line(e)}", file=sys.stderr)
            return 1
    return 0
