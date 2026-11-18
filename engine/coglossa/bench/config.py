"""The bench config file: the dataset, the systems to compare, and the optional scorer and
injector (spec: M6)."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from coglossa.config import ConfigError, _first_error

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Pair:
    src: str
    tgt: str

    @property
    def label(self) -> str:
        return f"{self.src}>{self.tgt}"

    @property
    def slug(self) -> str:
        return f"{self.src}-{self.tgt}"


def parse_pair(text: str) -> Pair:
    src, sep, tgt = text.partition(">")
    if not sep or not src.strip() or not tgt.strip() or ">" in tgt:
        raise ValueError(f"pairs must be written src>tgt, e.g. en>es; got '{text}'")
    return Pair(src.strip(), tgt.strip())


class DatasetConfig(_Strict):
    kind: Literal["flores200", "parallel"]
    path: Path
    split: Literal["dev", "devtest"] = "devtest"
    pairs: list[str] = Field(min_length=1)
    sentences: int = Field(100, ge=1)
    seed: int = 1
    source_file: Path | None = None  # parallel only
    reference_file: Path | None = None  # parallel only

    @model_validator(mode="after")
    def _check(self) -> "DatasetConfig":
        for text in self.pairs:
            parse_pair(text)
        if self.kind == "parallel":
            if self.source_file is None:
                raise ValueError("a parallel dataset needs source_file")
            if self.reference_file is None:
                raise ValueError("a parallel dataset needs reference_file")
        return self

    def parsed_pairs(self) -> list[Pair]:
        return [parse_pair(text) for text in self.pairs]


class SystemConfig(_Strict):
    name: str
    kind: Literal["pipeline", "baseline"]
    config: Path
    model: str | None = None  # the model key a baseline translates with

    @model_validator(mode="after")
    def _check(self) -> "SystemConfig":
        if not _SAFE_NAME.fullmatch(self.name):
            raise ValueError(
                f"system name '{self.name}' must use only letters, digits, '.', '_' and '-'"
            )
        if self.kind == "baseline" and not self.model:
            raise ValueError("a baseline needs model: the model key it translates with")
        return self


class InjectorConfig(_Strict):
    config: Path
    model: str


class TierCuts(_Strict):
    a_max: float = 5.0
    b_max: float = 10.0

    @model_validator(mode="after")
    def _ordered(self) -> "TierCuts":
        if self.a_max > self.b_max:
            raise ValueError(f"a_max ({self.a_max:g}) must not be above b_max ({self.b_max:g})")
        return self


class BenchConfig(_Strict):
    dataset: DatasetConfig
    systems: list[SystemConfig] = Field(min_length=1)
    scorer_url: str | None = None
    injector: InjectorConfig | None = None
    tiers: TierCuts = Field(default_factory=TierCuts)

    @model_validator(mode="after")
    def _unique_names(self) -> "BenchConfig":
        seen: set[str] = set()
        for s in self.systems:
            if s.name in seen:
                raise ValueError(f"system name '{s.name}' is used twice")
            seen.add(s.name)
        return self


def _bench_error(e: ValidationError) -> str:
    """Task 1's one-line format, with list positions written systems[0] and pydantic's
    'Value error, ' prefix dropped from our own messages."""
    line = _first_error(e).replace(": Value error, ", ": ", 1)
    return re.sub(r"\.(\d+)(?=[.:])", r"[\1]", line)


def load_bench_config(path: Path) -> BenchConfig:
    if not path.is_file():
        raise ConfigError(f"bench config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {' '.join(str(e).split())}") from e
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"cannot read bench config file {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping of sections")
    try:
        cfg = BenchConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{path}: {_bench_error(e)}") from e
    base = path.resolve().parent

    def resolve(p: Path) -> Path:
        return (base / p).resolve()

    ds = cfg.dataset
    dataset = ds.model_copy(
        update={
            "path": resolve(ds.path),
            "source_file": resolve(ds.source_file) if ds.source_file else None,
            "reference_file": resolve(ds.reference_file) if ds.reference_file else None,
        }
    )
    systems = [s.model_copy(update={"config": resolve(s.config)}) for s in cfg.systems]
    injector = (
        cfg.injector.model_copy(update={"config": resolve(cfg.injector.config)})
        if cfg.injector
        else None
    )
    return cfg.model_copy(update={"dataset": dataset, "systems": systems, "injector": injector})
