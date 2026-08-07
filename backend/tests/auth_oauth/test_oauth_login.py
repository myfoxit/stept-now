"""Social login: signup/link/reuse flows, provider quirks, state safety, cookies."""

from __future__ import annotations

from datetime import timedelta

import httpx
import respx
from sqlalchemy import select

from app.core.config import get_settings, reset_settings_cache
from app.core.db import get_session_factory
from app.integrations.oauth import mint_state as mint_integration_state
from app.models.workspace import Invitation, Membership
from app.services import auth_oauth as oauth_service
from tests.auth_oauth.conftest import (
    GITHUB_EMAILS_URL,
    GITHUB_TOKEN_URL,
    GITHUB_USER_URL,
    GOOGLE_AUTHORIZE_URL,
    GOOGLE_TOKEN_URL,
    fetch_identities,
    fetch_users,
    github_email,
    github_token_response,
    github_user,
    google_token_response,
    make_google_id_token,
    query_of,
    run_callback,
    start,
    start_state,
)


def _login_url(query: str) -> str:
    return get_settings().app_base_url.rstrip("/") + "/login?" + query


def _app_url(path: str = "/") -> str:
    return get_settings().app_base_url.rstrip("/") + path


# ---------------------------------------------------------------------------
# providers endpoint + start
# ---------------------------------------------------------------------------


async def test_providers_endpoint_lists_configured(client):
    response = await client.get("/api/v1/auth/oauth/providers")
    assert response.status_code == 200
    assert response.json() == {"providers": ["google", "github"]}


async def test_providers_endpoint_empty_when_unconfigured(client, monkeypatch):
    for var in (
        "STEPT_GOOGLE_LOGIN_CLIENT_ID",
        "STEPT_GOOGLE_LOGIN_CLIENT_SECRET",
        "STEPT_GITHUB_CLIENT_ID",
        "STEPT_GITHUB_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    reset_settings_cache()
    response = await client.get("/api/v1/auth/oauth/providers")
    assert response.json() == {"providers": []}


async def test_start_redirects_to_provider_authorize(client):
    response = await start(client, "google", next="/settings")
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(GOOGLE_AUTHORIZE_URL + "?")
    params = query_of(location)
    assert params["client_id"] == "google-login-id"
    assert params["response_type"] == "code"
    assert params["scope"] == "openid email profile"
    assert params["redirect_uri"] == (
        get_settings().public_base_url.rstrip("/") + "/api/v1/auth/oauth/google/callback"
    )
    claims = oauth_service.verify_login_state(params["state"])
    assert claims["provider"] == "google"
    assert claims["next"] == "/settings"
    assert claims["nonce"]


async def test_start_unknown_provider_is_400(client):
    response = await start(client, "gitlab")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


async def test_start_unconfigured_provider_is_400(client, monkeypatch):
    monkeypatch.delenv("STEPT_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("STEPT_GITHUB_CLIENT_SECRET", raising=False)
    reset_settings_cache()
    response = await start(client, "github")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


async def test_google_login_falls_back_to_integration_credentials(client, monkeypatch):
    monkeypatch.delenv("STEPT_GOOGLE_LOGIN_CLIENT_ID", raising=False)
    monkeypatch.delenv("STEPT_GOOGLE_LOGIN_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("STEPT_GOOGLE_CLIENT_ID", "google-integration-id")
    monkeypatch.setenv("STEPT_GOOGLE_CLIENT_SECRET", "google-integration-secret")
    reset_settings_cache()
    response = await start(client, "google")
    assert response.status_code == 302
    assert query_of(response.headers["location"])["client_id"] == "google-integration-id"


# ---------------------------------------------------------------------------
# google: signup, linking, reuse
# ---------------------------------------------------------------------------


async def test_google_signup_creates_passwordless_user_and_session(client):
    state = await start_state(client, "google")

    with respx.mock:
        route = respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response())
        )
        response = await run_callback(client, "google", state=state)

    assert response.status_code == 302
    assert response.headers["location"] == _app_url("/")

    sent = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "auth-code"
    assert sent["client_id"] == "google-login-id"
    assert sent["redirect_uri"].endswith("/api/v1/auth/oauth/google/callback")

    (user,) = await fetch_users()
    assert user.email == "social@example.com"
    assert user.name == "Social User"
    assert user.password_hash is None
    (identity,) = await fetch_identities()
    assert identity.user_id == user.id
    assert identity.provider == "google"
    assert identity.provider_user_id == "google-sub-1"
    assert identity.email == "social@example.com"

    # The refresh cookie carries exactly the attributes POST /auth/login sets.
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("stept_refresh=")
    assert "httponly" in cookie
    assert "path=/api/v1/auth" in cookie
    assert "samesite=lax" in cookie
    assert "secure" not in cookie  # env=test; prod adds Secure

    # SPA bootstrap: the just-set cookie must drive the normal refresh flow.
    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert body["access_token"]
    assert body["user"]["email"] == "social@example.com"


async def test_google_links_existing_user_by_verified_email(client):
    from tests.conftest import signup

    await signup(client, "social@example.com", name="Existing User")
    state = await start_state(client, "google")

    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response())
        )
        response = await run_callback(client, "google", state=state)

    assert response.status_code == 302
    assert response.headers["location"] == _app_url("/")
    (user,) = await fetch_users()  # no second account
    assert user.name == "Existing User"
    assert user.password_hash is not None  # linking must not drop the password
    (identity,) = await fetch_identities()
    assert identity.user_id == user.id


async def test_second_login_reuses_identity_even_if_email_changed(client):
    first = await start_state(client, "google")
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response())
        )
        await run_callback(client, "google", state=first)

    # Same Google account (sub) returns with a different email address.
    second = await start_state(client, "google")
    changed = make_google_id_token(sub="google-sub-1", email="renamed@example.com")
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response(id_token=changed))
        )
        response = await run_callback(client, "google", state=second)

    assert response.status_code == 302
    assert response.headers["location"] == _app_url("/")
    users = await fetch_users()
    identities = await fetch_identities()
    assert len(users) == 1 and len(identities) == 1
    assert users[0].email == "social@example.com"  # identity match wins; email unchanged


async def test_google_unverified_email_rejected(client):
    state = await start_state(client, "google")
    unverified = make_google_id_token(email_verified=False)
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response(id_token=unverified))
        )
        response = await run_callback(client, "google", state=state)
    assert response.status_code == 302
    assert response.headers["location"] == _login_url("error=email_unverified")
    assert await fetch_users() == []
    assert await fetch_identities() == []


# ---------------------------------------------------------------------------
# github
# ---------------------------------------------------------------------------


async def test_github_selects_primary_verified_email(client):
    state = await start_state(client, "github")
    with respx.mock:
        token_route = respx.post(GITHUB_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=github_token_response())
        )
        user_route = respx.get(GITHUB_USER_URL).mock(
            return_value=httpx.Response(200, json=github_user(id=9001, name="Octo Cat"))
        )
        respx.get(GITHUB_EMAILS_URL).mock(
            return_value=httpx.Response(
                200,
                json=[
                    github_email("noreply@users.github.test", primary=False, verified=True),
                    github_email("octo@example.com", primary=True, verified=True),
                    github_email("old@example.com", primary=False, verified=False),
                ],
            )
        )
        response = await run_callback(client, "github", state=state)

    assert response.status_code == 302
    assert response.headers["location"] == _app_url("/")
    assert token_route.calls.last.request.headers["accept"] == "application/json"
    assert user_route.calls.last.request.headers["authorization"] == (
        "Bearer gho_github-login-access"
    )
    (user,) = await fetch_users()
    assert user.email == "octo@example.com"  # the primary verified one
    assert user.name == "Octo Cat"
    assert user.password_hash is None
    (identity,) = await fetch_identities()
    assert identity.provider == "github"
    assert identity.provider_user_id == "9001"


async def test_github_no_verified_email_rejected(client):
    state = await start_state(client, "github")
    with respx.mock:
        respx.post(GITHUB_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=github_token_response())
        )
        respx.get(GITHUB_USER_URL).mock(return_value=httpx.Response(200, json=github_user()))
        respx.get(GITHUB_EMAILS_URL).mock(
            return_value=httpx.Response(
                200, json=[github_email("octo@example.com", primary=True, verified=False)]
            )
        )
        response = await run_callback(client, "github", state=state)
    assert response.headers["location"] == _login_url("error=email_unverified")
    assert await fetch_users() == []


async def test_github_error_token_response_fails_cleanly(client):
    """GitHub reports a bad code as HTTP 200 + {"error": ...} — no access_token."""
    state = await start_state(client, "github")
    with respx.mock:
        respx.post(GITHUB_TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"error": "bad_verification_code"})
        )
        response = await run_callback(client, "github", state=state)
    assert response.headers["location"] == _login_url("error=oauth_failed")
    assert await fetch_users() == []


# ---------------------------------------------------------------------------
# state safety + failure redirects
# ---------------------------------------------------------------------------


async def test_tampered_state_redirects_to_login_error(client):
    response = await run_callback(client, "google", state="tampered-garbage")
    assert response.status_code == 302
    assert response.headers["location"] == _login_url("error=oauth_failed")


async def test_expired_state_redirects_to_login_error(client, monkeypatch):
    monkeypatch.setattr(oauth_service, "STATE_TTL", timedelta(seconds=-10))
    state = await start_state(client, "google")
    response = await run_callback(client, "google", state=state)
    assert response.headers["location"] == _login_url("error=oauth_failed")


async def test_integration_state_cannot_be_replayed_as_login(client):
    """typ=integration_state must never pass the typ=login_state check."""
    integration_state = mint_integration_state(
        workspace_id="ws-1", provider="google", user_id="user-1", return_to="/settings"
    )
    response = await run_callback(client, "google", state=integration_state)
    assert response.headers["location"] == _login_url("error=oauth_failed")


async def test_state_provider_mismatch_rejected(client):
    state = await start_state(client, "google")
    response = await run_callback(client, "github", state=state)
    assert response.headers["location"] == _login_url("error=oauth_failed")


async def test_user_denied_at_provider(client):
    state = await start_state(client, "google")
    response = await run_callback(client, "google", state=state, code=None, error="access_denied")
    assert response.headers["location"] == _login_url("error=oauth_denied")
    assert await fetch_users() == []


async def test_exchange_failure_redirects_not_500(client):
    state = await start_state(client, "google")
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        response = await run_callback(client, "google", state=state)
    assert response.status_code == 302
    assert response.headers["location"] == _login_url("error=oauth_failed")
    assert await fetch_users() == []


async def test_next_path_round_trip_and_open_redirect_guard(client):
    state = await start_state(client, "google", next="/settings/members")
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response())
        )
        response = await run_callback(client, "google", state=state)
    assert response.headers["location"] == _app_url("/settings/members")

    # Protocol-relative escape attempts collapse to "/".
    evil = await start_state(client, "google", next="//evil.example")
    changed = make_google_id_token(sub="google-sub-1")
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response(id_token=changed))
        )
        response = await run_callback(client, "google", state=evil)
    assert response.headers["location"] == _app_url("/")


# ---------------------------------------------------------------------------
# invites + password interplay
# ---------------------------------------------------------------------------


async def test_invite_token_in_state_joins_workspace(client, workspace_ctx):
    invite = await client.post(
        f"{workspace_ctx.base}/invitations",
        json={"email": "social@example.com", "role": "agent"},
        headers=workspace_ctx.owner_headers,
    )
    assert invite.status_code == 201, invite.text
    async with get_session_factory()() as db:
        token = (
            await db.execute(
                select(Invitation.token).where(Invitation.email == "social@example.com")
            )
        ).scalar_one()

    state = await start_state(client, "google", invite=token)
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response())
        )
        response = await run_callback(client, "google", state=state)
    assert response.status_code == 302

    async with get_session_factory()() as db:
        social_user = [u for u in await fetch_users() if u.email == "social@example.com"][0]
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.workspace_id == workspace_ctx.id,
                    Membership.user_id == social_user.id,
                )
            )
        ).scalar_one_or_none()
    assert membership is not None
    assert membership.role == "agent"


async def test_password_login_on_passwordless_account_gives_clear_error(client):
    state = await start_state(client, "google")
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response())
        )
        await run_callback(client, "google", state=state)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "social@example.com", "password": "whatever-123"},
    )
    assert response.status_code == 401
    assert "social login" in response.json()["error"]["message"].lower()
