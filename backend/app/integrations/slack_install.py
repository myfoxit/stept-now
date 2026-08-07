"""Slack post-connect provisioning: find-or-create the channel inbox.

After ``oauth.v2.access`` stores the connection, the workspace immediately gets
a working Slack channel: an enabled slack inbox claiming the connection's
``team_id`` is updated in place (reconnect refreshes the stored bot token), or
one is created named after the team. The existing ``/api/channels/slack/events``
inbound + sender consume ``config.team_id`` and secrets ``{bot_token,
signing_secret}`` unchanged. Idempotent per team.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.events import Actor
from app.core.security import decrypt_secret
from app.integrations.oauth import IntegrationAuthError
from app.models.inbox import ChannelType, Inbox
from app.models.integration import IntegrationConnection
from app.services import inboxes as inboxes_service


async def provision_slack_inbox(
    session: AsyncSession,
    connection: IntegrationConnection,
    *,
    actor: Actor,
    credential_extra: dict[str, str] | None = None,
) -> Inbox:
    """Ensure the slack inbox for the connection's team exists and is current."""
    team_id = connection.meta.get("team_id")
    if not team_id or not connection.access_token_encrypted:
        raise IntegrationAuthError("slack: connection is missing its team or bot token")
    bot_token = decrypt_secret(connection.access_token_encrypted)
    # The signing secret is not part of the OAuth response: it comes from the
    # same app console as the client credentials (credential extra), falling
    # back to the instance-wide env secret.
    signing_secret = (credential_extra or {}).get("signing_secret") or (
        get_settings().slack_signing_secret or ""
    )

    inboxes = (
        (
            await session.execute(
                select(Inbox).where(
                    Inbox.workspace_id == connection.workspace_id,
                    Inbox.channel_type == ChannelType.SLACK,
                    Inbox.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    existing = next((i for i in inboxes if i.config.get("team_id") == team_id), None)

    secrets = {"bot_token": bot_token}
    if signing_secret:
        secrets["signing_secret"] = signing_secret

    if existing is not None:
        merged_secrets = {**inboxes_service.get_secrets(existing), **secrets}
        return await inboxes_service.update_inbox(
            session,
            connection.workspace_id,
            existing.id,
            actor=actor,
            config={**existing.config, "team_id": team_id, "connection_id": connection.id},
            secrets=merged_secrets,
        )
    return await inboxes_service.create_inbox(
        session,
        connection.workspace_id,
        actor=actor,
        name=connection.account_label or "Slack",
        channel_type=ChannelType.SLACK,
        config={"team_id": team_id, "connection_id": connection.id},
        secrets=secrets,
    )
