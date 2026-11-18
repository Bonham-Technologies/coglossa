"""What every request handler needs, built once per process by the app's lifespan."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from coglossa.config import CoglossaConfig
from coglossa.people import People
from coglossa.prompts.library import PromptLibrary
from coglossa.service import JobQueue
from coglossa.storage import Storage
from coglossa.store.db import Database
from fastapi import Depends, Request


@dataclass(frozen=True)
class ApiContext:
    cfg: CoglossaConfig
    config_path: Path
    db: Database
    storage: Storage
    prompts: PromptLibrary
    queue: JobQueue
    people: People  # CGL-26: teams and admins from auth.people_file


def get_context(request: Request) -> ApiContext:
    return request.app.state.ctx


Ctx = Annotated[ApiContext, Depends(get_context)]
