"""The signed-in user and the System page (spec §8.6): doctor results, the active config
(read-only) and the language tier table."""

from typing import Any

from coglossa import catalog
from coglossa.config import config_hash
from coglossa.doctor import run_doctor
from coglossa.schema import DoctorReport
from coglossa.schema.views import TierTable
from coglossa.scoring.tiers import LanguageTiers
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from coglossa_api.auth import ViewerDep, require_admin
from coglossa_api.context import Ctx

router = APIRouter(tags=["system"])


class Me(BaseModel):
    user: str | None  # None when auth.mode is none
    admin: bool  # CGL-26: sees every set and the operator screens
    teams: list[str]  # the teams whose sets this person sees
    max_upload_mb: int  # the upload limit (here, since /system/config is for admins)
    logout_url: str | None = None  # the proxy's sign-out address, or None


class ConfigView(BaseModel):
    path: str
    hash: str  # the hash new runs record (operational sections excluded)
    config: dict[str, Any]  # API keys are environment variables, never in the config


@router.get("/me")
async def get_me(viewer: ViewerDep, ctx: Ctx) -> Me:
    return Me(
        user=viewer.name,
        admin=viewer.admin,
        teams=list(viewer.team_names),
        max_upload_mb=ctx.cfg.api.max_upload_mb,
        logout_url=ctx.cfg.auth.logout_url,
    )


@router.get("/teams", dependencies=[Depends(require_admin)])
async def teams(viewer: ViewerDep, ctx: Ctx) -> list[str]:
    """CGL-26: team names to suggest when moving a set (admins only)."""
    return await catalog.known_teams(ctx.db, ctx.people, viewer, ctx.cfg.auth.team_filter())


@router.get("/system/doctor", dependencies=[Depends(require_admin)])
async def doctor(ctx: Ctx, probe: bool = False) -> DoctorReport:
    """Installation checks (spec §7.4). `probe=true` adds the slow context-window probe."""
    return await run_doctor(ctx.config_path, probe_context=probe)


@router.get("/system/config", dependencies=[Depends(require_admin)])
async def config(ctx: Ctx) -> ConfigView:
    return ConfigView(
        path=str(ctx.config_path),
        hash=config_hash(ctx.cfg),
        config=ctx.cfg.model_dump(mode="json"),
    )


@router.get("/system/tiers")
async def tiers(ctx: Ctx) -> TierTable:
    table = LanguageTiers.load(ctx.cfg.scoring.language_tiers_file)
    return TierTable(
        default=table.default,
        languages=dict(sorted(table.languages.items())),
        pairs={f"{s}>{t}": tier for (s, t), tier in sorted(table.pairs.items())},
    )
