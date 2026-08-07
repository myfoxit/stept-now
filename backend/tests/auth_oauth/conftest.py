"""Harness for social-login tests.

Provider HTTP is respx-mocked against ``STEPT_OAUTH_BASE_OVERRIDE`` stub bases
(the same mechanism the integrations framework uses) — NO live HTTP anywhere.
The ``app`` fixture mounts the auth_oauth router under /api/v1/auth exactly the
way the orchestrator will register it.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt as pyjwt
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from app.core.config import reset_settings_cache
from app.core.db import get_session_factory
from app.models.user import User, UserIdentity

GOOGLE_STUB = "https://stub-google.test"
GITHUB_STUB = "https://stub-github.test"

# Real provider paths re-rooted onto the stub bases (how resolve_url maps them).
GOOGLE_AUTHORIZE_URL = f"{GOOGLE_STUB}/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = f"{GOOGLE_STUB}/token"
GITHUB_AUTHORIZE_URL = f"{GITHUB_STUB}/login/oauth/authorize"
GITHUB_TOKEN_URL = f"{GITHUB_STUB}/login/oauth/access_token"
GITHUB_USER_URL = f"{GITHUB_STUB}/user"
GITHUB_EMAILS_URL = f"{GITHUB_STUB}/user/emails"


@pytest.fixture(autouse=True)
def login_providers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "STEPT_OAUTH_BASE_OVERRIDE",
        json.dumps({"google": GOOGLE_STUB, "github": GITHUB_STUB}),
    )
    monkeypatch.setenv("STEPT_GOOGLE_LOGIN_CLIENT_ID", "google-login-id")
    monkeypatch.setenv("STEPT_GOOGLE_LOGIN_CLIENT_SECRET", "google-login-secret")
    monkeypatch.setenv("STEPT_GITHUB_CLIENT_ID", "github-login-id")
    monkeypatch.setenv("STEPT_GITHUB_CLIENT_SECRET", "github-login-secret")
    reset_settings_cache()


@pytest.fixture
async def app(login_providers_env):
    """create_app + the auth_oauth router mounted as the orchestrator mounts it."""
    from app.api.v1 import auth_oauth
    from app.main import create_app

    application = create_app()
    application.include_router(auth_oauth.router, prefix="/api/v1/auth", tags=["auth"])
    async with LifespanManager(application):
        yield application


# ---------------------------------------------------------------------------
# provider response builders
# ---------------------------------------------------------------------------


def make_google_id_token(
    sub: str = "google-sub-1",
    email: str | None = "social@example.com",
    email_verified: bool | str = True,
    name: str | None = "Social User",
    **over: Any,
) -> str:
    """An OIDC id_token as the callback consumes it (signature never verified)."""
    claims: dict[str, Any] = {"sub": sub, "email_verified": email_verified, **over}
    if email is not None:
        claims["email"] = email
    if name is not None:
        claims["name"] = name
    return pyjwt.encode(claims, "provider-key-not-checked", algorithm="HS256")


def google_token_response(id_token: str | None = None, **over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "access_token": "ya29.google-login-access",
        "expires_in": 3599,
        "scope": "openid email profile",
        "token_type": "Bearer",
        "id_token": id_token if id_token is not None else make_google_id_token(),
    }
    data.update(over)
    return data


def github_token_response(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "access_token": "gho_github-login-access",
        "token_type": "bearer",
        "scope": "read:user,user:email",
    }
    data.update(over)
    return data


def github_user(
    id: int = 9001, login: str = "octo", name: str | None = "Octo Cat"
) -> dict[str, Any]:
    return {"id": id, "login": login, "name": name, "email": None}


def github_email(email: str, *, primary: bool = False, verified: bool = True) -> dict[str, Any]:
    return {"email": email, "primary": primary, "verified": verified, "visibility": None}


# ---------------------------------------------------------------------------
# flow helpers
# ---------------------------------------------------------------------------


def query_of(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def state_of(url: str) -> str:
    return query_of(url)["state"]


async def start(client: httpx.AsyncClient, provider: str, **params: str) -> httpx.Response:
    return await client.get(f"/api/v1/auth/oauth/{provider}/start", params=params)


async def start_state(client: httpx.AsyncClient, provider: str, **params: str) -> str:
    response = await start(client, provider, **params)
    assert response.status_code == 302, response.text
    return state_of(response.headers["location"])


async def run_callback(
    client: httpx.AsyncClient,
    provider: str,
    *,
    state: str,
    code: str | None = "auth-code",
    **extra: str,
) -> httpx.Response:
    params: dict[str, str] = {"state": state, **extra}
    if code is not None:
        params["code"] = code
    return await client.get(f"/api/v1/auth/oauth/{provider}/callback", params=params)


async def fetch_users() -> list[User]:
    async with get_session_factory()() as session:
        rows = (await session.execute(select(User).order_by(User.created_at, User.id))).scalars()
        return list(rows.all())


async def fetch_identities() -> list[UserIdentity]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                select(UserIdentity).order_by(UserIdentity.created_at, UserIdentity.id)
            )
        ).scalars()
        return list(rows.all())
