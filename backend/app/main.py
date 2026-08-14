"""Stept API application factory."""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

import app as app_pkg
from app.api.channels import channels_router
from app.api.extension_assets import router as extension_assets_router
from app.api.oauth_public import router as oauth_public_router
from app.api.portal import router as portal_router
from app.api.stripe_webhooks import router as stripe_webhooks_router
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
from app.mcp.agent_endpoint import router as agent_mcp_router
from app.mcp.mount import McpAuthShim, build_inner_app, run_session_manager
from app.realtime.app_ws import router as app_ws_router
from app.realtime.extension_ws import router as extension_ws_router
from app.realtime.manager import manager as ws_manager
from app.realtime.widget_ws import router as widget_ws_router

logger = log("main")

# Paths embeddable third-party pages may call (open CORS, no credentials).
OPEN_CORS_PREFIXES = ("/api/widget", "/portal", "/widget-assets", "/extension-assets")

# The widget iframe app and the help-center portal are *meant* to be framed by
# customer sites, so they are the one place we must not send a framing ban.
FRAMEABLE_PREFIXES = ("/widget-assets", "/portal")

# Route trees that authenticate by other means (widget token, signed OAuth
# state, provider webhook signatures, refresh cookie) or not at all. The
# OpenAPI document lifts its global bearer requirement per-operation for these,
# so generated clients don't demand an Authorization header that would be
# wrong to send.
PUBLIC_PATH_PREFIXES = (
    "/api/widget",
    "/api/channels",
    "/portal",
    "/api/stripe",
    "/api/integrations",
    "/api/v1/auth",
    "/api/v1/healthz",
    "/extension-assets",
)

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

    mcp_inner = build_inner_app()

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
        # The mounted MCP app's own lifespan (its session manager) never runs
        # by itself — mounted lifespans are a Starlette no-op.
        async with run_session_manager(mcp_inner):
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
        # Bare "/mcp" would 307 off the Starlette Mount; MCP clients don't
        # reliably follow redirects, so land them on the mounted app directly.
        if request.scope["path"] == "/mcp":
            request.scope["path"] = "/mcp/"
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
    # OAuth browser redirects (unauthenticated; workspace rides in signed state).
    application.include_router(
        oauth_public_router,
        prefix="/api/integrations",
        dependencies=[Depends(RateLimit("integrations_oauth", times=60, seconds=60))],
    )
    # Stripe webhooks (unauthenticated; verified by signature on the raw body).
    application.include_router(
        stripe_webhooks_router,
        prefix="/api/stripe",
        dependencies=[Depends(RateLimit("stripe_webhooks", times=600, seconds=60))],
    )
    application.include_router(extension_assets_router, prefix="/extension-assets")
    application.include_router(app_ws_router)
    application.include_router(widget_ws_router)
    application.include_router(extension_ws_router)

    # MCP: the per-agent JSON-RPC routes MUST be registered before the /mcp
    # mount (routes are matched in order; the mount would otherwise swallow
    # every path under its prefix).
    application.include_router(agent_mcp_router)
    application.mount("/mcp", McpAuthShim(mcp_inner))

    # Built widget assets (loader.js + iframe app), when present.
    widget_dist = Path(__file__).resolve().parents[2] / "widget" / "dist"
    if widget_dist.is_dir():
        application.mount(
            "/widget-assets", StaticFiles(directory=widget_dist), name="widget-assets"
        )

    base_openapi = application.openapi

    def openapi_with_security() -> dict[str, Any]:
        """The generated schema plus an honest auth declaration.

        FastAPI only emits security metadata for routes using its Security()
        dependencies, which ours don't — so without this, generated clients
        never send Authorization. Declared here: a global bearer requirement
        (user JWTs and workspace API keys share the header), the widget's
        header token as a named scheme, and per-operation ``security: []`` on
        the public trees. ``base_openapi`` caches; every mutation below is
        idempotent, so re-entry on the cached schema is harmless.
        """
        schema = base_openapi()
        components = schema.setdefault("components", {})
        components["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": (
                    "`Authorization: Bearer <token>` — either a user access token "
                    "(JWT from POST /api/v1/auth/login) or a workspace API key "
                    "(`sk_stept_…`, minted in Settings → API keys)."
                ),
            },
            "WidgetToken": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Widget-Token",
                "description": (
                    "Visitor session token for the /api/widget tree, issued by the "
                    "widget bootstrap endpoint. Those operations are marked public "
                    "(`security: []`) because bootstrap itself runs pre-token."
                ),
            },
        }
        schema["security"] = [{"BearerAuth": []}]
        for path, item in schema.get("paths", {}).items():
            if not path.startswith(PUBLIC_PATH_PREFIXES):
                continue
            for operation in item.values():
                if isinstance(operation, dict):  # skips path-item strings/lists
                    operation["security"] = []
        return schema

    application.openapi = openapi_with_security  # type: ignore[method-assign]

    return application


app = create_app()
