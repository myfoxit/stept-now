"""Stept API application factory."""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

import app as app_pkg
from app.api.channels import channels_router
from app.api.extension_assets import router as extension_assets_router
from app.api.portal import router as portal_router
from app.api.v1 import api_router
from app.api.widget import widget_router
from app.core.config import get_settings
from app.core.db import dispose_engine, init_db
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, log
from app.core.pubsub import get_pubsub, reset_pubsub
from app.core.queue import get_queue, reset_queue
from app.core.ratelimit import RateLimit
from app.core.scheduler import Scheduler
from app.realtime.app_ws import router as app_ws_router
from app.realtime.manager import manager as ws_manager
from app.realtime.widget_ws import router as widget_ws_router

logger = log("main")

# Paths embeddable third-party pages may call (open CORS, no credentials).
OPEN_CORS_PREFIXES = ("/api/widget", "/portal", "/widget-assets", "/extension-assets")

# The widget iframe app and the help-center portal are *meant* to be framed by
# customer sites, so they are the one place we must not send a framing ban.
FRAMEABLE_PREFIXES = ("/widget-assets", "/portal")

# Sent on every response. No CSP here: the dashboard is a Vite SPA served by the
# edge (which owns its policy), and the responses this app returns that could
# carry active content set their own policy at the route — see
# app/api/v1/files.py and app/api/widget/media.py.
BASE_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


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


def security_headers_for(path: str, *, https: bool) -> dict[str, str]:
    headers = dict(BASE_SECURITY_HEADERS)
    if not path.startswith(FRAMEABLE_PREFIXES):
        headers["X-Frame-Options"] = "DENY"
    if https:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    settings.assert_production_ready()

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

    docs = settings.docs_enabled
    application = FastAPI(
        title="Stept API",
        version=app_pkg.__version__,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json" if docs else None,
        docs_url="/api/v1/docs" if docs else None,
        redoc_url=None,
    )

    install_error_handlers(application)

    cors = PathAwareCORS({settings.app_base_url, *settings.cors_origins})

    @application.middleware("http")
    async def cors_and_timing(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Behind the edge proxy the scheme arrives in X-Forwarded-Proto; only
        # advertise HSTS on a connection that actually was TLS.
        https = (
            request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
            == "https"
        )
        secure_headers = security_headers_for(request.url.path, https=https)
        if request.method == "OPTIONS" and request.headers.get("origin"):
            return Response(
                status_code=204, headers={**secure_headers, **cors.headers_for(request)}
            )
        started = time.perf_counter()
        response = await call_next(request)
        for key, value in secure_headers.items():
            response.headers.setdefault(key, value)
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
    # Backstops for the two unauthenticated router trees. Deliberately far above
    # real traffic — provider webhook bursts and help-center crawls must pass —
    # so these cap floods without shaping normal use. The per-endpoint limits on
    # the expensive widget writes do the precise work.
    application.include_router(
        channels_router,
        prefix="/api/channels",
        dependencies=[Depends(RateLimit("channels_inbound", times=600, seconds=60))],
    )
    application.include_router(
        portal_router,
        prefix="/portal",
        dependencies=[Depends(RateLimit("portal", times=240, seconds=60))],
    )
    application.include_router(extension_assets_router, prefix="/extension-assets")
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
