"""Helpers shared by route modules."""

import uuid

from coglossa import service
from coglossa.schema import PipelineProfile
from coglossa.schema.views import ProjectInfo

from coglossa_api.context import Ctx
from coglossa_api.models import ActionResult


async def action_result(ctx: Ctx, run_id: uuid.UUID, message: str) -> ActionResult:
    return ActionResult(message=message, progress=await service.run_progress(ctx.db, run_id))


def run_options(
    project: ProjectInfo,
    *,
    target_lang: str | None,
    source_lang: str | None,
    pipeline_profile: PipelineProfile | None,
    checkpoint: bool | None,
    use_project_guide: bool,
) -> service.RunOptions:
    """A new run's options: what the request leaves out takes the project's defaults."""
    target = target_lang or project.target_lang
    if target is None:
        raise service.InvalidRequestError(
            f"choose a target language: project '{project.name}' has no default"
        )
    return service.RunOptions(
        target_lang=target,
        source_lang=source_lang or project.source_lang,
        pipeline_profile=pipeline_profile or project.pipeline_profile,
        checkpoint=checkpoint,
        use_project_guide=use_project_guide,
    )
