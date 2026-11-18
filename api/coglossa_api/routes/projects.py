"""Projects, their documents, and uploads that start a run (spec §8.1, §8.2)."""

import uuid
from typing import Annotated

from coglossa import catalog, service
from coglossa.config import AuthConfig
from coglossa.people import team_key
from coglossa.schema import PipelineProfile
from coglossa.schema.views import LANGUAGE_CODE_PATTERN, DocumentSummary, ProjectInfo, RunSummary
from coglossa.storage import display_name
from fastapi import APIRouter, Form, HTTPException, Query, Response, UploadFile

from coglossa_api.auth import ViewerDep
from coglossa_api.context import Ctx
from coglossa_api.errors import UploadTooLargeError
from coglossa_api.models import AddLanguage, CreateProject, ErrorBody, UpdateProject
from coglossa_api.routes._common import run_options

router = APIRouter(tags=["projects"])
_PIECE = 1024 * 1024
LanguageField = Annotated[str | None, Form(pattern=LANGUAGE_CODE_PATTERN, max_length=35)]


def _check_team(auth: AuthConfig, team: str | None) -> None:
    """CGL-74: a set goes only to a team the configured filter allows (admins included), so no
    set lands in a group that is never a team and later shows to everyone in it."""
    if team is not None and not auth.team_filter().allows(team):
        if team_key(team) == team_key(auth.admin_group):
            raise service.InvalidRequestError(
                f"'{team}' is the administrators' group, not a team: choose another team"
            )
        rule = auth.team_rule()
        allowed = f"only groups whose names {rule} are teams" if rule else "there are no teams"
        raise service.InvalidRequestError(f"'{team}' is not a team in Coglossa: {allowed}")


@router.get("/projects")
async def list_projects(ctx: Ctx, viewer: ViewerDep) -> list[ProjectInfo]:
    """The sets the person may see (CGL-26)."""
    return await catalog.list_projects(ctx.db, viewer)


@router.post("/projects", status_code=201)
async def create_project(body: CreateProject, ctx: Ctx, viewer: ViewerDep) -> ProjectInfo:
    """A new set in one of the person's teams (admins: any team), or, without a team, personal
    to the person who makes it (CGL-26)."""
    if body.team is not None and not viewer.admin and team_key(body.team) not in viewer.teams:
        raise service.InvalidRequestError(f"you are not in team '{body.team}'")
    _check_team(ctx.cfg.auth, body.team)
    return await catalog.create_project(
        ctx.db,
        body.name,
        target_lang=body.target_lang,
        source_lang=body.source_lang,
        pipeline_profile=body.pipeline_profile,
        team=body.team,
        created_by=viewer.name,
    )


@router.get("/projects/{project_id}")
async def get_project(project_id: uuid.UUID, ctx: Ctx) -> ProjectInfo:
    return await catalog.get_project(ctx.db, project_id)


@router.patch(
    "/projects/{project_id}",
    responses={
        403: {"model": ErrorBody, "description": "Only an administrator moves a set."},
        404: {"model": ErrorBody, "description": "No such project."},
        409: {"model": ErrorBody, "description": "Another project already has that name."},
    },
)
async def update_project(
    project_id: uuid.UUID, body: UpdateProject, ctx: Ctx, viewer: ViewerDep
) -> ProjectInfo:
    """Rename a project (names stay unique, 409, and cannot be blank, 422); admins can also
    move it to another team, or with `team: null` make it personal to its creator (CGL-26)."""
    move = "team" in body.model_fields_set
    if move and not viewer.admin:
        raise HTTPException(
            status_code=403, detail="only an administrator can move a set to another team"
        )
    if move:
        _check_team(ctx.cfg.auth, body.team)
    # One transaction (CGL-31): a taken name never leaves the set moved but not renamed.
    return await catalog.update_project(
        ctx.db, project_id, name=body.name, team=body.team, move=move
    )


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID, ctx: Ctx, confirm: Annotated[str, Query()] = ""
) -> Response:
    """Deletes every document, run, stored file and model-call payload of the project
    (spec §5). `confirm` must repeat the project's name."""
    project = await catalog.get_project(ctx.db, project_id)
    if confirm != project.name:
        raise service.InvalidRequestError(
            f"to delete project '{project.name}', pass confirm={project.name}"
        )
    await service.delete_project_id(ctx.db, ctx.storage, project_id)
    return Response(status_code=204)


@router.get("/projects/{project_id}/documents")
async def list_documents(project_id: uuid.UUID, ctx: Ctx) -> list[DocumentSummary]:
    return await catalog.list_documents(ctx.db, project_id)


async def read_upload(
    file: UploadFile, max_mb: int, limit_name: str = "api.max_upload_mb"
) -> bytes:
    limit = max_mb * _PIECE
    pieces: list[bytes] = []
    size = 0
    while piece := await file.read(_PIECE):
        size += len(piece)
        if size > limit:
            name = display_name(file.filename or "") or "the upload"
            raise UploadTooLargeError(
                f"{name} is larger than the {max_mb} MB upload limit ({limit_name})"
            )
        pieces.append(piece)
    return b"".join(pieces)


@router.post("/projects/{project_id}/runs", status_code=202)
async def upload(
    project_id: uuid.UUID,
    file: UploadFile,
    ctx: Ctx,
    target_lang: LanguageField = None,
    source_lang: LanguageField = None,
    pipeline_profile: Annotated[PipelineProfile | None, Form()] = None,
    checkpoint: Annotated[bool | None, Form()] = None,
    use_project_guide: Annotated[bool, Form()] = False,
    all_languages: Annotated[bool, Form()] = False,
) -> RunSummary:
    """Upload a document and queue a run. Form fields left out take the project's defaults.
    `all_languages`: also start the file in every other language of the set (CGL-24); the run
    returned is the one in `target_lang`."""
    project = await catalog.get_project(ctx.db, project_id)
    opts = run_options(
        project,
        target_lang=target_lang,
        source_lang=source_lang,
        pipeline_profile=pipeline_profile,
        checkpoint=checkpoint,
        use_project_guide=use_project_guide,
    )
    data = await read_upload(file, ctx.cfg.api.max_upload_mb)
    run_id = await service.submit_bytes(
        ctx.db,
        ctx.storage,
        ctx.queue,
        ctx.cfg,
        ctx.prompts,
        file.filename or "",
        data,
        opts,
        project=project_id,
        all_languages=all_languages,
    )
    return (await catalog.run_detail(ctx.db, run_id)).run


@router.post(
    "/projects/{project_id}/languages",
    status_code=202,
    responses={
        404: {"model": ErrorBody, "description": "No such project."},
        409: {"model": ErrorBody, "description": "The set is already in that language."},
    },
)
async def add_language(project_id: uuid.UUID, body: AddLanguage, ctx: Ctx) -> list[RunSummary]:
    """Translate every file of the set into another language, with nothing uploaded again
    (CGL-24). Each language has its own translation guide. Refused: a language the set already
    has (409, code `language_exists`), a set with no files (422)."""
    project = await catalog.get_project(ctx.db, project_id)
    opts = run_options(
        project,
        target_lang=body.target_lang,
        source_lang=None,
        pipeline_profile=None,
        checkpoint=False,
        use_project_guide=False,
    )
    run_ids = await service.add_language(ctx.db, ctx.queue, ctx.cfg, ctx.prompts, project_id, opts)
    return await catalog.run_summaries(ctx.db, run_ids)
