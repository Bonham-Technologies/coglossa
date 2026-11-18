"""Upload the scorer weights to the Modal Volume coglossa-weights (spec §6.3).

    modal run deploy/hosted/modal/upload_weights.py --source ~/coglossa-weights [--force]

Uploads the three model directories and weights.json (the pinned revisions), skipping names
already in the Volume unless --force. Prints each name with its size."""

from pathlib import Path

import modal

NAMES = ["metricx-24-hybrid-large-v2p6", "mt5-xl", "labse", "weights.json"]

app = modal.App("coglossa-upload-weights")
weights = modal.Volume.from_name("coglossa-weights", create_if_missing=True)


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}"
        n /= 1024
    raise AssertionError("unreachable")


@app.local_entrypoint()
def main(source: str = "~/coglossa-weights", force: bool = False):
    src = Path(source).expanduser()
    missing = [n for n in NAMES if not (src / n).exists()]
    if missing:
        raise SystemExit(f"{src} has no {', '.join(missing)}")
    present = {entry.path.strip("/") for entry in weights.listdir("/")}
    for name in NAMES:
        local = src / name
        if name in present and not force:
            print(f"skip   {name} (already in the Volume; --force to replace)")
            continue
        with weights.batch_upload(force=force) as batch:
            if local.is_dir():
                batch.put_directory(local, f"/{name}")
            else:
                batch.put_file(local, f"/{name}")
        print(f"upload {name}  {human(size_of(local))}")
