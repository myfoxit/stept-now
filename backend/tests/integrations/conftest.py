"""Shared helpers for the integrations framework tests.

Every provider HTTP call is respx-mocked — NO live HTTP anywhere. The token
responses below mirror each provider's documented shape (cheat sheet in
docs/INTEGRATIONS-CONTRACTS.md).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt as pyjwt
from sqlalchemy import select

from app.core import events
from app.core.config import reset_settings_cache
from app.core.db import get_session_factory, utcnow
from app.core.security import encrypt_secret
from app.integrations.catalog import PROVIDERS
from app.models.integration import IntegrationConnection
from tests.conftest import WorkspaceCtx

GOOGLE_TOKEN_URL = PROVIDERS["google"].token_url
MICROSOFT_TOKEN_URL = PROVIDERS["microsoft"].token_url
SLACK_TOKEN_URL = PROVIDERS["slack"].token_url
NOTION_TOKEN_URL = PROVIDERS["notion"].token_url
CONFLUENCE_TOKEN_URL = PROVIDERS["confluence"].token_url


def make_id_token(**claims: Any) -> str:
    """An OIDC id_token as the callback consumes it (signature never verified)."""
    return pyjwt.encode(claims, "provider-key-not-checked", algorithm="HS256")


def google_token_response(email: str = "gmail-user@example.com", **over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "access_token": "ya29.google-access",
        "refresh_token": "1//google-refresh",
        "expires_in": 3599,
        "scope": "https://mail.google.com/ openid email",
        "token_type": "Bearer",
        "id_token": make_id_token(email=email),
    }
    data.update(over)
    return data


def microsoft_token_response(upn: str = "agent@contoso.com", **over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "access_token": "ms-access",
        "refresh_token": "ms-refresh",
        "expires_in": 3600,
        "scope": "offline_access openid email",
        "token_type": "Bearer",
        "id_token": make_id_token(preferred_username=upn, tid="tenant-123"),
    }
    data.update(over)
    return data


def slack_token_response(
    team_id: str = "T0FIRST", team_name: str = "Acme Team", **over: Any
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "ok": True,
        "access_token": "xoxb-bot-token",
        "token_type": "bot",
        "scope": "channels:history,chat:write",
        "bot_user_id": "U0BOT",
        "team": {"id": team_id, "name": team_name},
    }
    data.update(over)
    return data


def notion_token_response(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "access_token": "ntn-secret-token",
        "bot_id": "bot-abc",
        "workspace_name": "Acme Notes",
        "workspace_id": "ws-notion-1",
        "owner": {"type": "user"},
    }
    data.update(over)
    return data


def confluence_token_response(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "access_token": "atl-access",
        "refresh_token": "atl-refresh",
        "expires_in": 3600,
        "scope": "read:confluence-content.all offline_access",
        "token_type": "Bearer",
    }
    data.update(over)
    return data


def atlassian_site(cloud_id: str = "cloud-1", name: str = "Acme Wiki") -> dict[str, Any]:
    host = name.lower().replace(" ", "")
    return {"id": cloud_id, "name": name, "url": f"https://{host}.atlassian.net"}


@contextlib.contextmanager
def capture_events(*names: str) -> Iterator[list[events.Event]]:
    """Record emitted domain events without disturbing app subscribers."""
    captured: list[events.Event] = []

    async def _handler(session: Any, event: events.Event) -> None:
        captured.append(event)

    for name in names:
        events._subscribers.setdefault(name, []).append(_handler)
    try:
        yield captured
    finally:
        for name in names:
            events._subscribers[name].remove(_handler)


def set_env_credentials(
    monkeypatch: Any,
    provider: str,
    client_id: str = "env-client-id",
    client_secret: str = "env-client-secret",
) -> None:
    monkeypatch.setenv(f"STEPT_{provider.upper()}_CLIENT_ID", client_id)
    monkeypatch.setenv(f"STEPT_{provider.upper()}_CLIENT_SECRET", client_secret)
    reset_settings_cache()


async def put_credentials(
    ctx: WorkspaceCtx,
    provider: str,
    client_id: str = "ws-client-id",
    client_secret: str | None = "ws-client-secret",
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"client_id": client_id}
    if client_secret is not None:
        body["client_secret"] = client_secret
    if extra is not None:
        body["extra"] = extra
    response = await ctx.client.put(
        f"{ctx.base}/integrations/{provider}/credentials", json=body, headers=ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def mint_authorize_url(ctx: WorkspaceCtx, provider: str, return_to: str | None = None) -> str:
    body = {} if return_to is None else {"return_to": return_to}
    response = await ctx.client.post(
        f"{ctx.base}/integrations/{provider}/connect", json=body, headers=ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    return response.json()["authorize_url"]


def query_of(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def state_of(url: str) -> str:
    return query_of(url)["state"]


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
    return await client.get(f"/api/integrations/oauth/{provider}/callback", params=params)


async def fetch_connections(workspace_id: str) -> list[IntegrationConnection]:
    async with get_session_factory()() as session:
        rows = (
            (
                await session.execute(
                    select(IntegrationConnection)
                    .where(IntegrationConnection.workspace_id == workspace_id)
                    .order_by(IntegrationConnection.created_at, IntegrationConnection.id)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def make_connection(
    session: Any,
    workspace_id: str,
    provider: str = "google",
    *,
    access: str | None = "access-raw",
    refresh: str | None = "refresh-raw",
    expires_in: int | None = None,
    status: str = "connected",
    meta: dict[str, Any] | None = None,
    account_label: str = "someone@example.com",
) -> IntegrationConnection:
    connection = IntegrationConnection(
        workspace_id=workspace_id,
        provider=provider,
        category=PROVIDERS[provider].category,
        status=status,
        account_label=account_label,
        scopes=list(PROVIDERS[provider].scopes[:2]),
        access_token_encrypted=encrypt_secret(access) if access else None,
        refresh_token_encrypted=encrypt_secret(refresh) if refresh else None,
        token_expires_at=(
            utcnow() + timedelta(seconds=expires_in) if expires_in is not None else None
        ),
        meta=meta or {},
    )
    session.add(connection)
    await session.commit()
    return connection
