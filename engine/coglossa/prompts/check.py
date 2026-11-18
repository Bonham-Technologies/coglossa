"""Fail fast on unusable prompts: test-render every prompt a run uses, before any model call."""

from jinja2 import TemplateError

from coglossa.config import CoglossaConfig, ConfigError
from coglossa.prompts.library import PromptLibrary, PromptNotFoundError

_LANGS = {"source_lang": "xx", "target_lang": "yy"}


def validate_prompts(cfg: CoglossaConfig, prompts: PromptLibrary) -> None:
    translators = cfg.roles.translators()
    checks: list[tuple[str, dict[str, object]]] = [
        ("profile", {"target_lang": "yy"}),
        *(("guide", {**_LANGS, "depth": depth}) for depth in ("light", "full", "partial")),
        ("guide_merge", dict(_LANGS)),
        *((t.prompt, {**_LANGS, "genre": "other", "register": "neutral"}) for t in translators),
        ("reconcile", {**_LANGS, "translators": [t.name for t in translators]}),
        ("sweep", dict(_LANGS)),
    ]
    if cfg.roles.judge is not None:
        checks.append(("judge", dict(_LANGS)))
    if cfg.roles.roundtrip is not None:
        checks.append(("back_translate", {"from_lang": "yy", "to_lang": "xx"}))
    for prompt_id, variables in checks:
        try:
            prompts.render(prompt_id, **variables)
        except (PromptNotFoundError, TemplateError, OSError, UnicodeDecodeError) as e:
            raise ConfigError(f"prompt '{prompt_id}' is unusable: {e}") from e


def run_prompt_ids(cfg: CoglossaConfig) -> list[str]:
    """Prompt ids a run may use, in report order (spec §4.6: every result names its prompt)."""
    translators = list(dict.fromkeys(t.prompt for t in cfg.roles.translators()))
    ids = ["profile", "guide", "guide_merge", "reconcile", "sweep", *translators]
    if cfg.roles.judge is not None:
        ids.append("judge")
    if cfg.roles.roundtrip is not None:
        ids.append("back_translate")
    return ids
