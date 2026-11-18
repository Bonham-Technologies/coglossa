from dataclasses import dataclass

from coglossa.config import CoglossaConfig
from coglossa.llm.agent import AgentRunner


class StageError(Exception):
    """A required stage failed; the run cannot continue. Message is user-facing."""


@dataclass(frozen=True)
class StageContext:
    cfg: CoglossaConfig
    runner: AgentRunner
    target_lang: str
