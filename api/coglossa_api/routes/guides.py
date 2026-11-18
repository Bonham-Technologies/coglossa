"""The guide editor (spec §8.3): a run's guide and its approval at the checkpoint, and the
project guide's versions and edits."""

import uuid
from typing import Annotated, Any

from coglossa import catalog, service
from coglossa.catalog import GlossaryFile
from coglossa.glossary_import import MAX_GLOSSARY_MB
from coglossa.schema.views import (
    LANGUAGE_CODE_PATTERN,
    GlossaryImportPreview,
    GlossaryImportResult,
    GuideVersionInfo,
    GuideView,
)
from fastapi import APIRouter, Form, Query, UploadFile

from coglossa_api.context import Ctx
from coglossa_api.models import ActionResult, ApproveGuide, ErrorBody, SaveGuide
from coglossa_api.routes._common import action_result
from coglossa_api.routes.projects import LanguageField, read_upload

router = APIRouter(tags=["guide"])
# Which language's guide (CGL-24); the set's first language when left out.
LanguageQuery = Annotated[str | None, Query(pattern=LANGUAGE_CODE_PATTERN, max_length=35)]


@router.get("/runs/{run_id}/guide")
async def run_guide(run_id: uuid.UUID, ctx: Ctx) -> GuideView:
    return await catalog.run_guide(ctx.db, run_id)


@router.post("/runs/{run_id}/guide/approve", status_code=202)
async def approve(run_id: uuid.UUID, ctx: Ctx, body: ApproveGuide | None = None) -> ActionResult:
    """Approve the guide of a run waiting at the checkpoint, as generated or edited; the run
    continues translating. Refused (409) when the project guide is no longer the one the draft
    was built from or, with an edited `guide` and a `base_version` of the project guide, no
    longer that version: reload it and approve the current guide. Refused (422): a
    `base_version` without a `guide`, or one that is neither the draft's nor the project
    guide's version."""
    body = body or ApproveGuide()
    version = await service.approve_guide(
        ctx.db, ctx.queue, run_id, body.guide, base_version=body.base_version
    )
    return await action_result(ctx, run_id, f"guide version {version} approved; translation queued")


@router.get("/projects/{project_id}/guide")
async def project_guide(
    project_id: uuid.UUID,
    ctx: Ctx,
    version: int | None = None,
    target_lang: LanguageQuery = None,
) -> GuideView:
    """The set guide of one language (`target_lang`, else the set's first language)."""
    return await catalog.project_guide(ctx.db, project_id, version, target_lang)


@router.get("/projects/{project_id}/guide/versions")
async def guide_versions(
    project_id: uuid.UUID, ctx: Ctx, target_lang: LanguageQuery = None
) -> list[GuideVersionInfo]:
    """The versions of one language's set guide, newest first."""
    return await catalog.guide_versions(ctx.db, project_id, target_lang)


@router.put("/projects/{project_id}/guide")
async def save_guide(
    project_id: uuid.UUID, body: SaveGuide, ctx: Ctx, target_lang: LanguageQuery = None
) -> GuideView:
    """A person's edit of one language's set guide (`target_lang`, else the set's first)."""
    return await catalog.save_project_guide(
        ctx.db, project_id, body.guide, base_version=body.base_version, target_lang=target_lang
    )


async def _glossary_file(ctx: Ctx, file: UploadFile, source_lang: str | None) -> GlossaryFile:
    """The uploaded glossary, refused (413) above the upload limit or MAX_GLOSSARY_MB."""
    if ctx.cfg.api.max_upload_mb <= MAX_GLOSSARY_MB:
        data = await read_upload(file, ctx.cfg.api.max_upload_mb)
    else:
        data = await read_upload(file, MAX_GLOSSARY_MB, "the limit for glossary files")
    return GlossaryFile(data, file.filename or "", source_lang)


_IMPORT_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorBody, "description": "No such project."},
    413: {"model": ErrorBody, "description": "The file is larger than the upload limit."},
    422: {
        "model": ErrorBody,
        "description": "The file cannot be read; the message names the line.",
    },
}


@router.post("/projects/{project_id}/guide/import/preview", responses=_IMPORT_ERRORS)
async def preview_glossary_import(
    project_id: uuid.UUID,
    file: UploadFile,
    ctx: Ctx,
    target_lang: LanguageQuery = None,
    source_lang: LanguageField = None,
) -> GlossaryImportPreview:
    """What importing a CSV, TSV or TBX glossary into one language's set guide would do: how
    many terms are new, already there, or there with another translation. Writes nothing.
    `source_lang`: the language of a TBX file's terms (default: the set's, else the file's
    one other language, else that of the set's documents)."""
    glossary = await _glossary_file(ctx, file, source_lang)
    return await catalog.preview_glossary_import(
        ctx.db, project_id, glossary, target_lang=target_lang
    )


@router.post(
    "/projects/{project_id}/guide/import",
    responses={
        **_IMPORT_ERRORS,
        409: {"model": ErrorBody, "description": "The guide changed since the preview."},
    },
)
async def import_glossary(
    project_id: uuid.UUID,
    file: UploadFile,
    ctx: Ctx,
    target_lang: LanguageQuery = None,
    source_lang: LanguageField = None,
    base_version: Annotated[int | None, Form()] = None,
    lock_all: Annotated[bool, Form()] = False,
    overwrite_conflicts: Annotated[bool, Form()] = False,
) -> GlossaryImportResult:
    """Import a glossary into one language's set guide as a new version by the user, all or
    nothing. `base_version`: the guide version the preview showed (none for a set without a
    guide); a newer one refuses the import (409 `guide_changed`). Terms the guide has with
    another translation keep it unless `overwrite_conflicts`, locked ones too; `lock_all` locks
    every imported term. With nothing to change, no version is added."""
    glossary = await _glossary_file(ctx, file, source_lang)
    return await catalog.import_glossary(
        ctx.db,
        project_id,
        glossary,
        base_version=base_version,
        target_lang=target_lang,
        lock_all=lock_all,
        overwrite_conflicts=overwrite_conflicts,
    )
