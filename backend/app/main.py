"""Stept API application factory."""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

import app as app_pkg
from app.api.channels import channels_router
from app.api.portal import router as portal_router
from app.api.v1 import api_router
from app.api.widget import widget_router
from app.core.config import get_settings
from app.core.db import dispose_engine, init_db
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, log
from app.core.pubsub import get_pubsub, reset_pubsub
from app.core.queue import get_queue, reset_queue
from app.core.scheduler import Scheduler
from app.realtime.app_ws import router as app_ws_router
from app.realtime.manager import manager as ws_manager
from app.realtime.widget_ws import router as widget_ws_router

logger = log("main")

# Paths embeddable third-party pages may call (open CORS, no credentials).
OPEN_CORS_PREFIXES = ("/api/widget", "/portal", "/widget-assets")


class PathAwareCORS:
    """Strict CORS (allow-listed origins + credentials) for the dashboard API,
    wildcard CORS for widget/portal endpoints that run on customer sites."""

    def __init__(self, app_origins: set[str]):
        self.app_origins = app_origins

    def headers_for(self, request: Request) -> dict[str, str]:
        origin = request.headers.get("origin")
        if not origin:
            return {}
        path = request.url.path
        if path.startswith(OPEN_CORS_PREFIXES):
            return {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Widget-Token",
                "Access-Control-Max-Age": "600",
            }
        if origin in self.app_origins:
            return {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "600",
                "Vary": "Origin",
            }
        return {}


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    scheduler = (
        Scheduler(settings.scheduler_tick_seconds)
        if settings.scheduler_enabled and settings.env != "test"
        else None
    )

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.env != "prod":
            await init_db()
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        get_pubsub()
        get_queue()
        if scheduler is not None:
            scheduler.start()
        logger.info(
            "stept api ready (env=%s, db=%s)",
            settings.env,
            "sqlite" if settings.is_sqlite else "postgres",
        )
        yield
        if scheduler is not None:
            await scheduler.stop()
        await ws_manager.shutdown()
        await reset_queue()
        await reset_pubsub()
        await dispose_engine()

    application = FastAPI(
        title="Stept API",
        version=app_pkg.__version__,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )

    install_error_handlers(application)

    cors = PathAwareCORS({settings.app_base_url, *settings.cors_origins})

    @application.middleware("http")
    async def cors_and_timing(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method == "OPTIONS" and request.headers.get("origin"):
            return Response(status_code=204, headers=cors.headers_for(request))
        started = time.perf_counter()
        response = await call_next(request)
        for key, value in cors.headers_for(request).items():
            response.headers.setdefault(key, value)
        if settings.env == "dev":
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "%s %s → %d (%.0fms)",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
        return response

    application.include_router(api_router, prefix="/api/v1")
    application.include_router(widget_router, prefix="/api/widget")
    application.include_router(channels_router, prefix="/api/channels")
    application.include_router(portal_router, prefix="/portal")
    application.include_router(app_ws_router)
    application.include_router(widget_ws_router)

    # Built widget assets (loader.js + iframe app), when present.
    widget_dist = Path(__file__).resolve().parents[2] / "widget" / "dist"
    if widget_dist.is_dir():
        application.mount(
            "/widget-assets", StaticFiles(directory=widget_dist), name="widget-assets"
        )

    return application


app = create_app()
