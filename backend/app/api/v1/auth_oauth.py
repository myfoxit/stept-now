"""Social login endpoints (Google, GitHub). Mounted under /api/v1/auth.

Start/callback are full-page browser redirects, not XHRs: the callback sets the
same ``stept_refresh`` cookie POST /auth/login does (reusing that route's own
helper) and 302s into the SPA, which bootstraps through its normal refresh
flow — no token ever rides in a URL. Mid-flight failures 302 back to
``{app_base_url}/login?error=<code>`` (codes: oauth_denied, email_unverified,
oauth_failed) — never a raw error page in the user's face.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from app.api.v1.auth import _client_meta, _set_refresh_cookie
from app.core.config import get_settings
from app.core.deps import Db
from app.core.errors import AppError, BadRequestError
from app.core.logging import log
from app.core.ratelimit import RateLimit
from app.services import auth as auth_service
from app.services import auth_oauth as oauth
from app.services import members as members_service

logger = log("auth_oauth")

router = APIRouter()

# Mirrors the state JWT's nonce into the browser that starts the flow: the
# callback only accepts a state whose nonce matches this cookie, so an attacker
# cannot complete a flow in a victim's browser that they started in their own
# (login CSRF → silently signing the victim into the attacker's account).
NONCE_COOKIE = "stept_oauth_nonce"
NONCE_COOKIE_PATH = "/api/v1/auth/oauth"


def _set_nonce_cookie(response: RedirectResponse, nonce: str) -> None:
    response.set_cookie(
        NONCE_COOKIE,
        nonce,
        max_age=int(oauth.STATE_TTL.total_seconds()),
        httponly=True,
        secure=get_settings().env == "prod",
        samesite="lax",  # sent on the top-level GET redirect back from the provider
        path=NONCE_COOKIE_PATH,
    )


@router.get("/oauth/providers")
async def social_login_providers() -> dict[str, list[str]]:
    """Public: which social-login buttons the SPA should render."""
    return {"providers": oauth.configured_providers()}


@router.get(
    "/oauth/{provider}/start",
    dependencies=[Depends(RateLimit("oauth_login_start", times=20, seconds=300))],
)
async def social_login_start(
    provider: str,
    next_path: str | None = Query(default=None, alias="next"),
    invite: str | None = Query(default=None),
) -> RedirectResponse:
    spec = oauth.LOGIN_PROVIDERS.get(provider)
    credential = oauth.resolve_login_credential(provider)
    if spec is None or credential is None:
        raise BadRequestError(f"Social login with '{provider}' is not available")
    state, nonce = oauth.mint_login_state(
        provider=provider, next_path=next_path, invite_token=invite
    )
    url = oauth.build_authorize_url(
        spec, credential[0], state=state, redirect_uri=oauth.redirect_uri_for(provider)
    )
    response = RedirectResponse(url, status_code=302)
    _set_nonce_cookie(response, nonce)
    return response


def _login_error_redirect(code: str) -> RedirectResponse:
    base = get_settings().app_base_url.rstrip("/")
    response = RedirectResponse(f"{base}/login?error={code}", status_code=302)
    response.delete_cookie(NONCE_COOKIE, path=NONCE_COOKIE_PATH)
    return response


@router.get(
    "/oauth/{provider}/callback",
    dependencies=[Depends(RateLimit("oauth_login_callback", times=20, seconds=300))],
)
async def social_login_callback(
    provider: str,
    request: Request,
    session: Db,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if not state:
        return _login_error_redirect("oauth_failed")
    try:
        claims = oauth.verify_login_state(state)
    except oauth.SocialLoginError:
        return _login_error_redirect("oauth_failed")
    if claims.get("provider") != provider:
        return _login_error_redirect("oauth_failed")
    nonce = claims.get("nonce")
    browser_nonce = request.cookies.get(NONCE_COOKIE)
    if not nonce or not browser_nonce or nonce != browser_nonce:
        # The browser finishing the flow is not the one that started it.
        return _login_error_redirect("oauth_failed")
    if error:
        # The provider bounced the user back without a code. Only ever forward
        # one of our own safe codes, never the provider's raw string.
        return _login_error_redirect("oauth_denied" if error == "access_denied" else "oauth_failed")
    if not code:
        return _login_error_redirect("oauth_failed")
    spec = oauth.LOGIN_PROVIDERS.get(provider)
    credential = oauth.resolve_login_credential(provider)
    if spec is None or credential is None:
        return _login_error_redirect("oauth_failed")

    try:
        token_data = await oauth.exchange_code(
            spec, credential, code=code, redirect_uri=oauth.redirect_uri_for(provider)
        )
        identity = await oauth.fetch_identity(spec, token_data)
        user = await oauth.resolve_user(session, identity)
        invite_token = claims.get("invite")
        if invite_token:
            # Best effort, like the signup page: a bad invite must not sink the login.
            try:
                await members_service.accept_invitation(session, user, token=str(invite_token))
            except AppError as exc:
                logger.warning("invite accept during %s login failed: %s", provider, exc.message)
        refresh = await auth_service.issue_refresh_token(session, user, **_client_meta(request))
    except oauth.SocialLoginError as exc:
        await session.rollback()
        logger.info("%s login failed: %s", provider, exc.message)
        return _login_error_redirect(exc.redirect_code)
    except Exception:
        logger.exception("%s login callback failed", provider)
        await session.rollback()
        return _login_error_redirect("oauth_failed")

    app_base = get_settings().app_base_url.rstrip("/")
    response = RedirectResponse(
        app_base + oauth.safe_next_path(claims.get("next")), status_code=302
    )
    # Exactly the attributes POST /auth/login sets — same helper, no fork.
    _set_refresh_cookie(response, refresh)
    response.delete_cookie(NONCE_COOKIE, path=NONCE_COOKIE_PATH)  # single-use
    return response
