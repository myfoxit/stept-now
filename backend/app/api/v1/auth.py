"""Auth endpoints. Access token in the response body (client keeps it in memory);
refresh token in an httpOnly cookie scoped to /api/v1/auth."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.core.config import get_settings
from app.core.deps import Db
from app.core.errors import UnauthorizedError
from app.core.ratelimit import RateLimit
from app.core.security import create_access_token
from app.schemas.auth import (
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    SignupRequest,
    TokenResponse,
)
from app.schemas.common import Msg
from app.schemas.user import UserOut
from app.services import auth as auth_service
from app.services.email import send_email

router = APIRouter()

REFRESH_COOKIE = "stept_refresh"


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        httponly=True,
        secure=settings.env == "prod",
        samesite="lax",
        path="/api/v1/auth",
    )


def _token_response(user_out: UserOut, access: str) -> TokenResponse:
    settings = get_settings()
    return TokenResponse(
        access_token=access,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=user_out,
    )


def _client_meta(request: Request) -> dict[str, str | None]:
    return {
        "user_agent": request.headers.get("user-agent", "")[:400] or None,
        "ip": request.client.host if request.client else None,
    }


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=201,
    dependencies=[Depends(RateLimit("signup", times=10, seconds=3600))],
)
async def signup(body: SignupRequest, request: Request, response: Response, session: Db):
    user = await auth_service.signup(
        session, email=body.email, name=body.name, password=body.password
    )
    refresh = await auth_service.issue_refresh_token(session, user, **_client_meta(request))
    _set_refresh_cookie(response, refresh)
    return _token_response(UserOut.model_validate(user), create_access_token(user.id))


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(RateLimit("login", times=20, seconds=300))],
)
async def login(body: LoginRequest, request: Request, response: Response, session: Db):
    user = await auth_service.authenticate(session, email=body.email, password=body.password)
    refresh = await auth_service.issue_refresh_token(session, user, **_client_meta(request))
    _set_refresh_cookie(response, refresh)
    return _token_response(UserOut.model_validate(user), create_access_token(user.id))


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(RateLimit("refresh", times=30, seconds=60))],
)
async def refresh(request: Request, response: Response, session: Db):
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise UnauthorizedError("No refresh token")
    user, new_refresh = await auth_service.rotate_refresh_token(
        session, raw, **_client_meta(request)
    )
    _set_refresh_cookie(response, new_refresh)
    return _token_response(UserOut.model_validate(user), create_access_token(user.id))


@router.post("/logout", response_model=Msg)
async def logout(request: Request, response: Response, session: Db):
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        await auth_service.revoke_refresh_token(session, raw)
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    return Msg(message="Logged out")


@router.post(
    "/password-reset",
    response_model=Msg,
    dependencies=[Depends(RateLimit("pwreset", times=5, seconds=3600))],
)
async def password_reset(body: PasswordResetRequest, session: Db):
    token = await auth_service.request_password_reset(session, body.email)
    if token is not None:
        settings = get_settings()
        link = f"{settings.app_base_url}/reset-password?token={token}"
        await send_email(
            body.email,
            "Reset your Stept password",
            f"<p>Click to reset your password (valid for 1 hour):</p>"
            f'<p><a href="{link}">{link}</a></p>',
        )
    return Msg(message="If that account exists, a reset email is on its way")


@router.post("/password-reset/confirm", response_model=Msg)
async def password_reset_confirm(body: PasswordResetConfirm, session: Db):
    await auth_service.reset_password(session, token=body.token, new_password=body.new_password)
    return Msg(message="Password updated; please log in")
