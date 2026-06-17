"""FastAPI app entrypoint.

Lifespan: configure logging, warm the roster snapshot off the user's path.
Middleware: NV Tools auth + CSP for iframe embedding. The built SPA is mounted
last as a catch-all under the URL prefix.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.meta import router as meta_router
from app.config import get_settings
from app.middleware import NVToolsAuthMiddleware
from app.observability.health import HEALTH
from app.observability.health import router as health_router
from app.observability.logging import configure_logging, log
from app.roster.snapshot import get_roster_store

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    HEALTH.data_source = settings.data_source

    async def _warm_roster() -> None:
        try:
            await get_roster_store(settings).get()
        except Exception as exc:
            log.warning("roster.warmup_failed", error=str(exc))

    warmup = asyncio.create_task(_warm_roster(), name="roster-warmup")
    log.info("app.ready")
    try:
        yield
    finally:
        warmup.cancel()
        log.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    prefix = settings.url_prefix
    app = FastAPI(title="NV Battle Reports", lifespan=lifespan)
    app.add_middleware(NVToolsAuthMiddleware)
    app.include_router(health_router, prefix=prefix)
    app.include_router(meta_router, prefix=prefix)
    # Mount the built SPA last so API routes take precedence and static assets
    # fall through to the catch-all.
    if _FRONTEND_DIST.is_dir():
        app.mount(
            f"{prefix}/" if prefix else "/",
            StaticFiles(directory=str(_FRONTEND_DIST), html=True),
            name="frontend",
        )
    return app


app = create_app()
