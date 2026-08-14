"""STEPT_ALLOW_SIGNUP=false: uninvited registration refused, invitations unharmed."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.config import get_settings, reset_settings_cache
from app.core.db import get_session_factory, utcnow
from app.models.workspace import Invitation
from app.services.auth import SIGNUP_DISABLED_MESSAGE
from tests.conftest import signup


def _close_signup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flip the instance to invite-only mid-test (settings are read per call)."""
    monkeypatch.setenv("STEPT_ALLOW_SIGNUP", "false")
    reset_settings_cache()


async def test_signup_is_open_by_default(client):
    assert get_settings().allow_signup is True
    auth = await signup(client, "walkin@example.com")  # asserts 201 itself
    assert auth["user"]["email"] == "walkin@example.com"


async def test_uninvited_signup_refused_when_disabled(client, monkeypatch):
    _close_signup(monkeypatch)
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": "stranger@example.com", "name": "Stranger", "password": "password-123"},
    )
    assert response.status_code == 403, response.text
    body = response.json()["error"]
    assert body["code"] == "forbidden"
    assert body["message"] == SIGNUP_DISABLED_MESSAGE
    assert "stept_refresh" not in response.cookies


async def test_login_still_works_when_disabled(client, monkeypatch):
    await signup(client, "existing@example.com")
    _close_signup(monkeypatch)
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "existing@example.com", "password": "password-123"},
    )
    assert response.status_code == 200, response.text


async def test_invited_email_signs_up_and_accepts_when_disabled(client, workspace_ctx, monkeypatch):
    """The full invitation flow (invite → signup → accept) must survive the gate:
    a pending invitation row is what authorizes the account creation."""
    _close_signup(monkeypatch)
    member_headers = await workspace_ctx.add_member("invited@example.com")  # asserts each step
    me = await client.get("/api/v1/me", headers=member_headers)
    assert me.status_code == 200
    assert [m["workspace"]["id"] for m in me.json()["memberships"]] == [workspace_ctx.id]

    # The gate still holds for everyone else.
    refused = await client.post(
        "/api/v1/auth/signup",
        json={"email": "uninvited@example.com", "name": "Nope", "password": "password-123"},
    )
    assert refused.status_code == 403


async def test_expired_invitation_does_not_bypass_the_gate(client, workspace_ctx, monkeypatch):
    invite = await client.post(
        f"{workspace_ctx.base}/invitations",
        json={"email": "late@example.com", "role": "agent"},
        headers=workspace_ctx.owner_headers,
    )
    assert invite.status_code == 201, invite.text
    async with get_session_factory()() as db:
        await db.execute(
            update(Invitation)
            .where(Invitation.email == "late@example.com")
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
        await db.commit()

    _close_signup(monkeypatch)
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": "late@example.com", "name": "Late", "password": "password-123"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == SIGNUP_DISABLED_MESSAGE
