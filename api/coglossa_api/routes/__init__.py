"""Route modules. Each has a `router`; all are mounted under /api behind the auth check."""

from fastapi import APIRouter

from coglossa_api.routes import (
    exports,
    guides,
    progress,
    projects,
    review,
    review_summary,
    runs,
    system,
)

ROUTERS: list[APIRouter] = [
    projects.router,
    runs.router,
    progress.router,
    guides.router,
    review.router,
    exports.router,
    review_summary.router,
    system.router,
]
