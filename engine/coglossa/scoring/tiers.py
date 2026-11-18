"""Language-pair reliability tiers (spec §6.4)."""

from pathlib import Path
from typing import Any

import yaml

from coglossa.config import ConfigError
from coglossa.schema import Reliability

DEFAULT_TIERS_FILE = Path(__file__).with_name("tiers.yaml")
_BY_NAME = {"A": Reliability.A, "B": Reliability.B, "C": Reliability.C}
_RANK = {Reliability.A: 0, Reliability.B: 1, Reliability.C: 2}
_NEXT = {Reliability.A: Reliability.B, Reliability.B: Reliability.C}


def base_language(code: str) -> str:
    return code.strip().replace("_", "-").split("-")[0].lower()


def downgrade(tier: Reliability) -> Reliability:
    return _NEXT.get(tier, tier)


class LanguageTiers:
    def __init__(
        self,
        languages: dict[str, Reliability],
        pairs: dict[tuple[str, str], Reliability],
        default: Reliability,
    ) -> None:
        self.languages = languages
        self.pairs = pairs
        self.default = default

    @classmethod
    def load(cls, path: Path | None = None) -> "LanguageTiers":
        path = path or DEFAULT_TIERS_FILE
        try:
            raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            raise ConfigError(f"cannot read language tiers file {path}: {e}") from e
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: expected a mapping")

        def tier_of(value: object, where: str) -> Reliability:
            if not isinstance(value, str) or value not in _BY_NAME:
                raise ConfigError(f"{path}: {where}: tier must be A, B or C, got {value!r}")
            return _BY_NAME[str(value)]

        default = tier_of(raw.get("default", "C"), "default")
        languages_section = raw.get("languages")
        if languages_section is None:
            languages_section = {}
        if not isinstance(languages_section, dict):
            raise ConfigError(f"{path}: languages must be a mapping of tier -> list")
        languages: dict[str, Reliability] = {}
        for tier_name, codes in languages_section.items():
            tier = tier_of(tier_name, "languages")
            if not isinstance(codes, list):
                raise ConfigError(f"{path}: languages.{tier_name} must be a list")
            for code in codes:
                if not isinstance(code, str):  # e.g. unquoted `no` (Norwegian) loads as False
                    raise ConfigError(
                        f"{path}: languages.{tier_name}: language code {code!r} is not a string; "
                        'quote it (e.g. "no")'
                    )
                languages[base_language(code)] = tier
        pairs_section = raw.get("pairs")
        if pairs_section is None:
            pairs_section = {}
        if not isinstance(pairs_section, dict):
            raise ConfigError(f"{path}: pairs must be a mapping of 'src>tgt' -> tier")
        pairs: dict[tuple[str, str], Reliability] = {}
        for key, value in pairs_section.items():
            src, sep, tgt = str(key).partition(">")
            if not sep or not src.strip() or not tgt.strip():
                raise ConfigError(f"{path}: pairs key {key!r} must look like 'src>tgt'")
            pairs[(base_language(src), base_language(tgt))] = tier_of(value, f"pairs.{key}")
        return cls(languages, pairs, default)

    def tier(self, source_lang: str, target_lang: str) -> Reliability:
        src, tgt = base_language(source_lang), base_language(target_lang)
        if (src, tgt) in self.pairs:
            return self.pairs[(src, tgt)]
        tiers = (self.languages.get(src, self.default), self.languages.get(tgt, self.default))
        return max(tiers, key=_RANK.__getitem__)
