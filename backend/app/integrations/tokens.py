"""Connection/token seam consumed by the email channel and knowledge connectors.

Pinned contract (their tests monkeypatch these two):

    async def get_connection(session, workspace_id, connection_id) -> IntegrationConnection
    async def get_valid_access_token(session, connection) -> str

Refresh happens when the token is within ``REFRESH_SKEW_SECONDS`` of expiry,
single-flight per connection id. Rotated refresh tokens (Atlassian and Notion
rotate on every refresh) are persisted immediately, committed via the caller's
session — losing a rotated token is losing the connection. Refresh failure
flips the connection to ``reauth_required``, emits the event, and raises
``IntegrationAuthError`` so callers surface "reauthorize in Settings".
"""

from __future__ import annotations

import asyncio
import weakref
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import NotFoundError
from app.core.events import Event, EventNames, emit
from app.core.security import decrypt_secret, encrypt_secret
from app.integrations.catalog import PROVIDERS
from app.integrations.oauth import (
    HTTP_TIMEOUT,
    IntegrationAuthError,
    parse_token_response,
    resolve_credential,
    resolve_url,
)
from app.models.integration import ConnectionStatus, IntegrationConnection

REFRESH_SKEW_SECONDS = 300

# Single-flight guards, scoped per event loop (the test suite spins up many).
# Bounded by the number of distinct connections a process touches.
_refresh_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def _lock_for(connection_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _refresh_locks.get(loop)
    if locks is None:
        locks = {}
        _refresh_locks[loop] = locks
    lock = locks.get(connection_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[connection_id] = lock
    return lock


async def get_connection(
    session: AsyncSession, workspace_id: str, connection_id: str
) -> IntegrationConnection:
    """Workspace-scoped connection lookup; refuses revoked connections."""
    connection = await session.get(IntegrationConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise NotFoundError("Integration connection not found")
    if connection.status == ConnectionStatus.REVOKED:
        raise IntegrationAuthError("Integration connection has been revoked")
    return connection


def _expiring(connection: IntegrationConnection) -> bool:
    if connection.token_expires_at is None:
        return False  # non-expiring token (Slack bot tokens)
    return connection.token_expires_at <= utcnow() + timedelta(seconds=REFRESH_SKEW_SECONDS)


async def get_valid_access_token(session: AsyncSession, connection: IntegrationConnection) -> str:
    """A live access token for the connection, refreshing (once) when stale."""
    if connection.access_token_encrypted and not _expiring(connection):
        return decrypt_secret(connection.access_token_encrypted)
    async with _lock_for(connection.id):
        # A concurrent flight may have refreshed while we waited on the lock.
        # populate_existing (not session.refresh) so the instance is never left
        # expired mid-await — waiters do plain attribute reads outside the lock.
        await session.get(IntegrationConnection, connection.id, populate_existing=True)
        if connection.access_token_encrypted and not _expiring(connection):
            return decrypt_secret(connection.access_token_encrypted)
        return await _refresh(session, connection)


async def _reauth_required(
    session: AsyncSession, connection: IntegrationConnection, message: str
) -> IntegrationAuthError:
    """Persist the reauth flag + event (committed — the caller is about to raise)."""
    connection.status = ConnectionStatus.REAUTH_REQUIRED
    await emit(
        session,
        Event(
            name=EventNames.INTEGRATION_REAUTH_REQUIRED,
            workspace_id=connection.workspace_id,
            payload={"connection_id": connection.id, "provider": connection.provider},
        ),
    )
    await session.commit()
    return IntegrationAuthError(message)


async def _refresh(session: AsyncSession, connection: IntegrationConnection) -> str:
    spec = PROVIDERS.get(connection.provider)
    refresh_token = (
        decrypt_secret(connection.refresh_token_encrypted)
        if connection.refresh_token_encrypted
        else None
    )
    if spec is None or not spec.token_url or not refresh_token:
        raise await _reauth_required(
            session, connection, f"{connection.provider}: no refresh token — reauthorize"
        )
    credential = await resolve_credential(session, connection.workspace_id, connection.provider)
    if credential is None:
        raise await _reauth_required(
            session, connection, f"{connection.provider}: OAuth app credential is gone"
        )

    token_url = resolve_url(spec.id, spec.token_url)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            if spec.token_auth == "basic":
                response = await client.post(
                    token_url,
                    json={"grant_type": "refresh_token", "refresh_token": refresh_token},
                    auth=(credential.client_id, credential.client_secret),
                )
            else:
                response = await client.post(
                    token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": credential.client_id,
                        "client_secret": credential.client_secret,
                    },
                )
        data: dict[str, Any] = parse_token_response(spec.id, response)
    except (httpx.HTTPError, IntegrationAuthError) as exc:
        raise await _reauth_required(
            session, connection, f"{connection.provider}: token refresh failed — reauthorize"
        ) from exc

    access_token = str(data["access_token"])
    connection.access_token_encrypted = encrypt_secret(access_token)
    expires_in = data.get("expires_in")
    if expires_in is not None:
        connection.token_expires_at = utcnow() + timedelta(seconds=int(expires_in))
    rotated = data.get("refresh_token")
    if rotated:  # Atlassian/Notion rotate; Google/Microsoft usually re-send the same one
        connection.refresh_token_encrypted = encrypt_secret(str(rotated))
    connection.status = ConnectionStatus.CONNECTED
    await session.commit()
    return access_token
