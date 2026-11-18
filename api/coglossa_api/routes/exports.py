"""The canonical report and its exports (spec §7.5): HTML, a clean DOCX, a bilingual DOCX
with flags as comments, the report as a JSON download and (CGL-89) a TMX translation memory.
All of them show the current revisions, except a TMX limited to the passages a person checked,
which holds the text the person approved."""

import asyncio
import re
import uuid
from enum import StrEnum
from urllib.parse import quote

from coglossa import review, service
from coglossa.export.docx import bilingual_docx, clean_docx
from coglossa.export.html import render_html
from coglossa.export.rows import export_basename
from coglossa.export.tmx import tmx_bytes
from coglossa.schema import Report
from fastapi import APIRouter, Response

from coglossa_api.context import Ctx
from coglossa_api.headers import EXPORT_CSP

router = APIRouter(tags=["exports"])
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TMX = "application/x-tmx+xml"
_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


class ExportKind(StrEnum):
    HTML = "html"
    DOCX = "docx"
    BILINGUAL_DOCX = "bilingual-docx"
    JSON = "json"
    TMX = "tmx"


def content_disposition(filename: str) -> str:
    """RFC 6266/5987: an ASCII fallback and the exact UTF-8 name."""
    fallback = _UNSAFE.sub("_", filename) or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/runs/{run_id}/report")
async def get_report(run_id: uuid.UUID, ctx: Ctx) -> Report:
    return await service.build_report(ctx.db, run_id)


@router.get(
    "/runs/{run_id}/exports/{kind}",
    response_class=Response,
    responses={200: {"content": {"text/html": {}, DOCX: {}, "application/json": {}, TMX: {}}}},
)
async def export(
    run_id: uuid.UUID,
    kind: ExportKind,
    ctx: Ctx,
    comments: bool = True,
    reviewed_only: bool = False,
) -> Response:
    """`comments` applies to bilingual-docx: the flags as Word comments. `reviewed_only` applies
    to tmx: only the passages a person accepted or edited, with the text they approved."""
    report = await service.build_report(ctx.db, run_id, include_llm_calls=kind is ExportKind.JSON)
    base = export_basename(report)
    headers: dict[str, str] = {}
    if kind is ExportKind.HTML:
        body: bytes = (await asyncio.to_thread(render_html, report)).encode("utf-8")
        filename, media_type = f"{base}.html", "text/html; charset=utf-8"
        headers["Content-Security-Policy"] = EXPORT_CSP  # its inline styles, nothing else
    elif kind is ExportKind.DOCX:
        body = await asyncio.to_thread(clean_docx, report)
        filename, media_type = f"{base}.docx", DOCX
    elif kind is ExportKind.BILINGUAL_DOCX:
        body = await asyncio.to_thread(bilingual_docx, report, comments=comments)
        filename, media_type = f"{base}.bilingual.docx", DOCX
    elif kind is ExportKind.TMX:
        approved = await review.approved_texts(ctx.db, run_id) if reviewed_only else None
        body = await asyncio.to_thread(tmx_bytes, report, approved=approved)
        filename, media_type = f"{base}.checked.tmx" if reviewed_only else f"{base}.tmx", TMX
    else:
        body = report.model_dump_json(indent=2).encode("utf-8")
        filename, media_type = f"{base}.report.json", "application/json"
    return Response(
        content=body,
        media_type=media_type,
        headers={**headers, "Content-Disposition": content_disposition(filename)},
    )
