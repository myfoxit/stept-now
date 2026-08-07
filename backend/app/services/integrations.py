"""Integrations service: catalog+state listing, credentials, connect, disconnect.

Credential resolution order is workspace row → instance env (see
``app.integrations.oauth.resolve_credential``). Secrets are write-only: they
enter through ``upsert_credential`` and only ever leave as ``has_secret``.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationFailure
from app.core.events import Actor, Event, EventNames, emit
from app.core.logging import log
from app.core.security import decrypt_secret, encrypt_secret
from app.integrations import oauth, slack_install
from app.integrations.catalog import PROVIDERS, ProviderSpec
from app.integrations.oauth import IntegrationAuthError
from app.models.integration import (
    ConnectionStatus,
    IntegrationAppCredential,
    IntegrationConnection,
)
from app.schemas.integrations import (
    ConnectionOut,
    CredentialOut,
    IntegrationsOut,
    ProviderOut,
)
from app.services import audit

logger = log("integrations")

DEFAULT_RETURN_TO = "/settings/integrations"


class IntegrationNotConfiguredError(ConflictError):
    """Connect attempted with no usable OAuth app credential (409)."""

    code = "integration_not_configured"


def _spec(provider: str) -> ProviderSpec:
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise NotFoundError(f"Unknown provider: {provider}")
    return spec


def _oauth_spec(provider: str) -> ProviderSpec:
    spec = _spec(provider)
    if spec.auth != "oauth2":
        raise ValidationFailure(f"Provider {provider} does not use OAuth")
    return spec


def validate_return_to(return_to: str | None) -> str:
    """In-app path only: must start with "/" (and not "//" — scheme-relative)."""
    if return_to is None:
        return DEFAULT_RETURN_TO
    if not return_to.startswith("/") or return_to.startswith("//"):
        raise ValidationFailure("return_to must be a path within the app")
    return return_to


async def _credential_row(
    session: AsyncSession, workspace_id: str, provider: str
) -> IntegrationAppCredential | None:
    return (
        await session.execute(
            select(IntegrationAppCredential).where(
                IntegrationAppCredential.workspace_id == workspace_id,
                IntegrationAppCredential.provider == provider,
            )
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------


def _credential_out(
    spec: ProviderSpec, row: IntegrationAppCredential | None
) -> tuple[CredentialOut, bool]:
    """(credential view, configured) for one oauth2 provider."""
    settings = get_settings()
    fields = list(spec.credential_fields)
    redirect_uri = oauth.redirect_uri_for(spec.id)
    if row is not None:
        has_secret = bool(row.client_secret_encrypted)
        return (
            CredentialOut(
                client_id=row.client_id,
                has_secret=has_secret,
                fields=fields,
                redirect_uri=redirect_uri,
                from_env=False,
            ),
            has_secret,
        )
    env_id = getattr(settings, f"{spec.id}_client_id", None)
    env_secret = getattr(settings, f"{spec.id}_client_secret", None)
    return (
        CredentialOut(
            client_id=env_id,
            has_secret=bool(env_secret),
            fields=fields,
            redirect_uri=redirect_uri,
            from_env=bool(env_id or env_secret),
        ),
        bool(env_id and env_secret),
    )


async def list_providers(session: AsyncSession, workspace_id: str) -> IntegrationsOut:
    """The full catalog merged with this workspace's connections + credentials.

    Revoked connections are disconnect tombstones — they keep audit history but
    never show up here.
    """
    connections = (
        (
            await session.execute(
                select(IntegrationConnection)
                .where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.status != ConnectionStatus.REVOKED,
                )
                .order_by(IntegrationConnection.created_at, IntegrationConnection.id)
            )
        )
        .scalars()
        .all()
    )
    by_provider: dict[str, list[ConnectionOut]] = {}
    for connection in connections:
        by_provider.setdefault(connection.provider, []).append(
            ConnectionOut.model_validate(connection)
        )
    credential_rows = {
        row.provider: row
        for row in (
            await session.execute(
                select(IntegrationAppCredential).where(
                    IntegrationAppCredential.workspace_id == workspace_id
                )
            )
        ).scalars()
    }

    providers: list[ProviderOut] = []
    for spec in PROVIDERS.values():
        if spec.auth == "oauth2":
            credential, configured = _credential_out(spec, credential_rows.get(spec.id))
        else:
            # Token-auth providers (zendesk) have nothing to configure here; the
            # knowledge-source dialog collects their credentials.
            credential, configured = None, True
        providers.append(
            ProviderOut(
                id=spec.id,
                name=spec.name,
                category=spec.category,  # type: ignore[arg-type]
                auth=spec.auth,  # type: ignore[arg-type]
                description=spec.description,
                doc_slug=spec.doc_slug,
                configured=configured,
                connections=by_provider.get(spec.id, []),
                credential=credential,
            )
        )
    return IntegrationsOut(providers=providers)


# ---------------------------------------------------------------------------
# app credentials
# ---------------------------------------------------------------------------


async def upsert_credential(
    session: AsyncSession,
    workspace_id: str,
    provider: str,
    *,
    actor: Actor,
    client_id: str,
    client_secret: str | None,
    extra: dict[str, str],
) -> CredentialOut:
    """Create/update the workspace's OAuth app credential.

    ``client_secret``: None keeps the stored secret, "" clears it. ``extra``
    keys merge the same way (only keys the provider declares are accepted).
    """
    spec = _oauth_spec(provider)
    allowed_extra = set(spec.credential_fields) - {"client_id", "client_secret"}
    unknown = set(extra) - allowed_extra
    if unknown:
        raise ValidationFailure(
            f"Unknown credential fields for {provider}: {', '.join(sorted(unknown))}"
        )

    row = await _credential_row(session, workspace_id, provider)
    if row is None:
        row = IntegrationAppCredential(
            workspace_id=workspace_id, provider=provider, client_id=client_id
        )
        session.add(row)
    row.client_id = client_id
    if client_secret is not None:
        row.client_secret_encrypted = encrypt_secret(client_secret) if client_secret else None
    merged = dict(row.extra or {})
    for key, value in extra.items():
        if value:
            merged[key] = value
        else:
            merged.pop(key, None)
    row.extra = merged
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="integration.credentials.update",
        target_type="integration_credential",
        target_id=row.id,
        meta={"provider": provider, "has_secret": bool(row.client_secret_encrypted)},
    )
    credential, _configured = _credential_out(spec, row)
    return credential


async def delete_credential(
    session: AsyncSession, workspace_id: str, provider: str, *, actor: Actor
) -> None:
    """Drop the workspace credential; resolution falls back to instance env."""
    _oauth_spec(provider)
    row = await _credential_row(session, workspace_id, provider)
    if row is None:
        raise NotFoundError(f"No stored credential for {provider}")
    await session.delete(row)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="integration.credentials.delete",
        target_type="integration_credential",
        target_id=row.id,
        meta={"provider": provider},
    )


# ---------------------------------------------------------------------------
# connect / reconnect / disconnect
# ---------------------------------------------------------------------------


async def mint_connect_url(
    session: AsyncSession,
    workspace_id: str,
    provider: str,
    *,
    actor: Actor,
    return_to: str | None = None,
    connection_id: str | None = None,
) -> str:
    spec = _oauth_spec(provider)
    destination = validate_return_to(return_to)
    credential = await oauth.resolve_credential(session, workspace_id, provider)
    if credential is None:
        raise IntegrationNotConfiguredError(
            f"No OAuth app credential configured for {provider} — add one in settings"
        )
    state = oauth.mint_state(
        workspace_id=workspace_id,
        provider=provider,
        user_id=actor.id,
        return_to=destination,
        connection_id=connection_id,
    )
    return oauth.build_authorize_url(
        spec, credential, state=state, redirect_uri=oauth.redirect_uri_for(provider)
    )


async def _get_connection_any_status(
    session: AsyncSession, workspace_id: str, connection_id: str
) -> IntegrationConnection:
    connection = await session.get(IntegrationConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise NotFoundError("Integration connection not found")
    return connection


async def mint_reconnect_url(
    session: AsyncSession,
    workspace_id: str,
    connection_id: str,
    *,
    actor: Actor,
    return_to: str | None = None,
) -> str:
    """Same round trip as connect, but the callback updates this connection."""
    connection = await _get_connection_any_status(session, workspace_id, connection_id)
    if connection.status == ConnectionStatus.REVOKED:
        raise NotFoundError("Integration connection not found")
    return await mint_connect_url(
        session,
        workspace_id,
        connection.provider,
        actor=actor,
        return_to=return_to,
        connection_id=connection.id,
    )


async def _best_effort_revoke(connection: IntegrationConnection) -> None:
    """Invalidate the grant at the provider where a server-side API exists.

    Per-provider choice:
    - google: documented revoke endpoint; POSTing the refresh token kills the
      whole grant (access tokens die with it).
    - slack: ``auth.revoke`` does not work for bot (xoxb) tokens — skip; the
      workspace admin uninstalls the app from Slack to fully revoke.
    - microsoft: no app-callable revocation endpoint for v2 refresh tokens
      (user/admin revokes in account settings) — skip.
    - notion / confluence: no public token-revocation endpoint — skip; marking
      the connection revoked and dropping the tokens is the whole story.
    """
    if connection.provider != "google":
        return
    encrypted = connection.refresh_token_encrypted or connection.access_token_encrypted
    if not encrypted:
        return
    url = oauth.resolve_url("google", oauth.GOOGLE_REVOKE_URL)
    try:
        async with httpx.AsyncClient(timeout=oauth.HTTP_TIMEOUT) as client:
            await client.post(url, data={"token": decrypt_secret(encrypted)})
    except httpx.HTTPError:
        logger.info("google token revoke failed; connection marked revoked anyway")


async def disconnect(
    session: AsyncSession, workspace_id: str, connection_id: str, *, actor: Actor
) -> None:
    connection = await _get_connection_any_status(session, workspace_id, connection_id)
    if connection.status == ConnectionStatus.REVOKED:
        return  # idempotent
    await _best_effort_revoke(connection)
    connection.status = ConnectionStatus.REVOKED
    connection.access_token_encrypted = None
    connection.refresh_token_encrypted = None
    connection.token_expires_at = None
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="integration.disconnect",
        target_type="integration_connection",
        target_id=connection.id,
        meta={"provider": connection.provider, "account_label": connection.account_label},
    )
    await emit(
        session,
        Event(
            name=EventNames.INTEGRATION_DISCONNECTED,
            workspace_id=workspace_id,
            payload={"connection_id": connection.id, "provider": connection.provider},
            actor=actor,
        ),
    )


# ---------------------------------------------------------------------------
# OAuth callback completion
# ---------------------------------------------------------------------------


def _granted_scopes(token_data: dict[str, object], spec: ProviderSpec) -> list[str]:
    """Prefer the scopes the provider actually granted over the ones we asked for."""
    raw = token_data.get("scope")
    if isinstance(raw, str) and raw:
        return raw.split(",") if "," in raw else raw.split()
    return list(spec.scopes)


async def complete_callback(
    session: AsyncSession, *, claims: dict[str, object], code: str
) -> IntegrationConnection:
    """Exchange the code and create (or, on reconnect, update) the connection."""
    workspace_id = str(claims["ws"])
    provider = str(claims["provider"])
    spec = PROVIDERS.get(provider)
    if spec is None or spec.auth != "oauth2":
        raise IntegrationAuthError(f"Unknown provider: {provider}")
    credential = await oauth.resolve_credential(session, workspace_id, provider)
    if credential is None:
        raise IntegrationAuthError(f"No OAuth app credential configured for {provider}")

    token_data = await oauth.exchange_code(
        spec, credential, code=code, redirect_uri=oauth.redirect_uri_for(provider)
    )
    account_label, meta = await oauth.whoami(spec, token_data)
    scopes = _granted_scopes(token_data, spec)
    expires_in = token_data.get("expires_in")
    expires_at = utcnow() + timedelta(seconds=int(expires_in)) if expires_in is not None else None
    access_token = str(token_data["access_token"])
    refresh_token = token_data.get("refresh_token")
    user_id = claims.get("sub")
    actor = Actor(type="user", id=str(user_id) if user_id else None)

    connection_id = claims.get("connection_id")
    if connection_id:
        connection = await _get_connection_any_status(session, workspace_id, str(connection_id))
        if connection.provider != provider:
            raise IntegrationAuthError("Reconnect state does not match the connection")
        connection.access_token_encrypted = encrypt_secret(access_token)
        if refresh_token:
            # Providers that only hand out refresh tokens on first consent
            # (prompt overrides aside) keep the previously stored one.
            connection.refresh_token_encrypted = encrypt_secret(str(refresh_token))
        connection.token_expires_at = expires_at
        connection.account_label = account_label or connection.account_label
        connection.scopes = scopes
        connection.meta = {**connection.meta, **meta}
        connection.status = ConnectionStatus.CONNECTED
    else:
        connection = IntegrationConnection(
            workspace_id=workspace_id,
            provider=provider,
            category=spec.category,
            status=ConnectionStatus.CONNECTED,
            account_label=account_label,
            scopes=scopes,
            access_token_encrypted=encrypt_secret(access_token),
            refresh_token_encrypted=encrypt_secret(str(refresh_token)) if refresh_token else None,
            token_expires_at=expires_at,
            meta=meta,
            created_by=str(user_id) if user_id else None,
        )
        session.add(connection)
    await session.flush()

    if provider == "slack":
        await slack_install.provision_slack_inbox(
            session, connection, actor=actor, credential_extra=credential.extra
        )

    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="integration.connect",
        target_type="integration_connection",
        target_id=connection.id,
        meta={
            "provider": provider,
            "account_label": account_label,
            "reconnect": bool(connection_id),
        },
    )
    await emit(
        session,
        Event(
            name=EventNames.INTEGRATION_CONNECTED,
            workspace_id=workspace_id,
            payload={
                "connection_id": connection.id,
                "provider": provider,
                "account_label": account_label,
            },
            actor=actor,
        ),
    )
    return connection
