"""Public OAuth endpoints: the provider callback + the retry re-entry helper.

Mounted at /api/integrations. No auth — these are browser redirects; the
workspace and initiating user travel inside the signed state. A human is on the
other end, so every failure becomes a 302 back into the app with a safe
``?error=code`` — never a raw 500 or an error body.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.core.deps import Db
from app.core.logging import log
from app.integrations import oauth
from app.services import integrations as integrations_service

logger = log("oauth_public")

router = APIRouter()

DEFAULT_RETURN_TO = integrations_service.DEFAULT_RETURN_TO


def _safe_return_to(value: object) -> str:
    """Belt-and-braces re-check of the state's return_to (open-redirect guard)."""
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//"):
        return value
    return DEFAULT_RETURN_TO


def _app_redirect(return_to: str, **params: str) -> RedirectResponse:
    base = get_settings().app_base_url.rstrip("/")
    separator = "&" if "?" in return_to else "?"
    return RedirectResponse(base + return_to + separator + urlencode(params), status_code=302)


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    session: Db,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if not state:
        return _app_redirect(DEFAULT_RETURN_TO, error="invalid_state")
    try:
        claims = oauth.verify_state(state)
    except oauth.IntegrationAuthError:
        return _app_redirect(DEFAULT_RETURN_TO, error="invalid_state")
    return_to = _safe_return_to(claims.get("return_to"))
    if claims.get("provider") != provider:
        return _app_redirect(return_to, error="invalid_state")
    if error:
        # The provider bounced the user back without a code (denied consent,
        # misconfigured app, …). Only ever forward one of our own safe codes.
        safe = "access_denied" if error == "access_denied" else "provider_error"
        return _app_redirect(return_to, error=safe)
    if not code:
        return _app_redirect(return_to, error="missing_code")
    try:
        await integrations_service.complete_callback(session, claims=claims, code=code)
    except oauth.IntegrationAuthError:
        await session.rollback()
        return _app_redirect(return_to, error="connect_failed")
    except Exception:
        logger.exception("oauth callback for %s failed", provider)
        await session.rollback()
        return _app_redirect(return_to, error="connect_failed")
    return _app_redirect(return_to, connected=provider)


@router.get("/oauth/{provider}/start")
async def oauth_start(provider: str, session: Db, state: str) -> RedirectResponse:
    """Retry re-entry: rebuild the authorize redirect from a still-valid state."""
    try:
        claims = oauth.verify_state(state)
    except oauth.IntegrationAuthError:
        return _app_redirect(DEFAULT_RETURN_TO, error="invalid_state")
    return_to = _safe_return_to(claims.get("return_to"))
    if claims.get("provider") != provider:
        return _app_redirect(return_to, error="invalid_state")
    spec = oauth.spec_for(provider)
    if spec is None or spec.auth != "oauth2":
        return _app_redirect(return_to, error="invalid_state")
    credential = await oauth.resolve_credential(session, str(claims["ws"]), provider)
    if credential is None:
        return _app_redirect(return_to, error="integration_not_configured")
    url = oauth.build_authorize_url(
        spec, credential, state=state, redirect_uri=oauth.redirect_uri_for(provider)
    )
    return RedirectResponse(url, status_code=302)
