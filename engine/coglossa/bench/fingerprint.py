"""The output hash: a digest of only the settings that shape a system's translated text, so
that edits to thresholds, timeouts and the like do not force a re-translation."""

import dataclasses
import hashlib
import json

from coglossa.bench.config import SystemConfig
from coglossa.config import CoglossaConfig, ConfigError
from coglossa.prompts.library import (
    BUILTIN_DIR,
    RENDER_VERSION,
    PromptLibrary,
    PromptNotFoundError,
)
from coglossa.segment.chunking import compute_budget


def _backend(cfg: CoglossaConfig, key: str) -> dict:
    b = cfg.backends[key]
    return {"kind": b.kind, "base_url": b.base_url}


def prompt_hashes(cfg: CoglossaConfig, ids: list[str] | None = None) -> dict:
    """Each prompt's version and the hash of its template text, so an edit in place counts."""
    library = PromptLibrary(cfg.prompts_dir)
    if ids is None:
        dirs = [BUILTIN_DIR] + ([cfg.prompts_dir] if cfg.prompts_dir else [])
        ids = sorted({p.name for d in dirs if d.is_dir() for p in d.iterdir() if p.is_dir()})
    out = {}
    for prompt_id in ids:
        try:
            version, path = library.resolve(prompt_id)
        except PromptNotFoundError:  # a directory with no template in it
            continue
        out[prompt_id] = [version, hashlib.sha256(path.read_bytes()).hexdigest()]
    return out


def output_hash(cfg: CoglossaConfig, spec: SystemConfig) -> str:
    """16 hex characters over what shapes the translated text, and the tokens and time spent."""
    if spec.kind == "baseline":
        model = cfg.models.get(spec.model or "")
        if model is None:
            raise ConfigError(f"model '{spec.model}' is not defined for system '{spec.name}'")
        shaping: dict = {
            "model_key": spec.model,
            "models": {spec.model: model.model_dump(mode="json")},
            "backends": {model.backend: _backend(cfg, model.backend)},
            "chunking": cfg.chunking.model_dump(mode="json"),
            # the chunk size also depends on every model of the roster (smallest window)
            "budget": dataclasses.asdict(compute_budget(cfg)),
            "prompts": prompt_hashes(cfg, ["contextual"]),
        }
    else:
        shaping = {
            "backends": {k: _backend(cfg, k) for k in cfg.backends},
            "models": {k: m.model_dump(mode="json") for k, m in cfg.models.items()},
            "roles": cfg.roles.model_dump(mode="json"),
            "chunking": cfg.chunking.model_dump(mode="json"),
            "profiles": cfg.profiles.model_dump(mode="json"),
            "prompts": prompt_hashes(cfg),
        }
    shaping["render_version"] = RENDER_VERSION
    blob = json.dumps(shaping, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
