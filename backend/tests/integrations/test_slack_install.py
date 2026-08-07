"""Slack one-click install: callback provisions the channel inbox, idempotently."""

from __future__ import annotations

import httpx
import respx
from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.inbox import ChannelType, Inbox
from app.services.inboxes import get_secrets
from tests.integrations.conftest import (
    SLACK_TOKEN_URL,
    fetch_connections,
    mint_authorize_url,
    put_credentials,
    run_callback,
    slack_token_response,
    state_of,
)


async def _slack_inboxes(workspace_id: str) -> list[Inbox]:
    async with get_session_factory()() as session:
        rows = (
            (
                await session.execute(
                    select(Inbox).where(
                        Inbox.workspace_id == workspace_id,
                        Inbox.channel_type == ChannelType.SLACK,
                    )
                )
            )
            .scalars()
            .all()
        )
        # Detach with secrets decrypted for assertions.
        return [(inbox, get_secrets(inbox)) for inbox in rows]  # type: ignore[misc]


async def _connect_slack(
    client, workspace_ctx, *, team_id="T0FIRST", team_name="Acme Team", bot_token="xoxb-bot-token"
):
    state = state_of(await mint_authorize_url(workspace_ctx, "slack"))
    with respx.mock:
        respx.post(SLACK_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json=slack_token_response(
                    team_id=team_id, team_name=team_name, access_token=bot_token
                ),
            )
        )
        response = await run_callback(client, "slack", state=state)
    assert response.status_code == 302, response.text
    assert "connected=slack" in response.headers["location"]
    return response


async def test_slack_callback_provisions_inbox(client, workspace_ctx):
    await put_credentials(workspace_ctx, "slack", extra={"signing_secret": "shhh-signing"})
    await _connect_slack(client, workspace_ctx)

    (connection,) = await fetch_connections(workspace_ctx.id)
    assert connection.provider == "slack"
    assert connection.account_label == "Acme Team"
    assert connection.meta == {"team_id": "T0FIRST", "bot_user_id": "U0BOT"}
    assert connection.token_expires_at is None  # bot tokens do not expire

    ((inbox, secrets),) = await _slack_inboxes(workspace_ctx.id)
    assert inbox.name == "Acme Team"
    assert inbox.enabled is True
    assert inbox.config["team_id"] == "T0FIRST"
    assert inbox.config["connection_id"] == connection.id
    assert secrets == {"bot_token": "xoxb-bot-token", "signing_secret": "shhh-signing"}


async def test_slack_install_idempotent_per_team(client, workspace_ctx):
    await put_credentials(workspace_ctx, "slack", extra={"signing_secret": "s1"})
    await _connect_slack(client, workspace_ctx, bot_token="xoxb-first")
    await _connect_slack(client, workspace_ctx, bot_token="xoxb-rotated")

    inboxes = await _slack_inboxes(workspace_ctx.id)
    assert len(inboxes) == 1
    _inbox, secrets = inboxes[0]
    assert secrets["bot_token"] == "xoxb-rotated"  # reconnect refreshes the token


async def test_slack_install_second_team_gets_second_inbox(client, workspace_ctx):
    await put_credentials(workspace_ctx, "slack", extra={"signing_secret": "s1"})
    await _connect_slack(client, workspace_ctx, team_id="T0FIRST", team_name="First")
    await _connect_slack(client, workspace_ctx, team_id="T9OTHER", team_name="Other")

    inboxes = await _slack_inboxes(workspace_ctx.id)
    assert sorted(inbox.config["team_id"] for inbox, _ in inboxes) == ["T0FIRST", "T9OTHER"]


async def test_slack_signing_secret_falls_back_to_env(client, workspace_ctx, monkeypatch):
    from app.core.config import reset_settings_cache

    monkeypatch.setenv("STEPT_SLACK_SIGNING_SECRET", "env-signing")
    reset_settings_cache()
    await put_credentials(workspace_ctx, "slack")  # no signing_secret in extra
    await _connect_slack(client, workspace_ctx)

    ((_inbox, secrets),) = await _slack_inboxes(workspace_ctx.id)
    assert secrets["signing_secret"] == "env-signing"


async def test_slack_install_adopts_existing_inbox_for_team(client, workspace_ctx, session):
    """A hand-configured slack inbox claiming the team is updated, not duplicated."""
    from app.core.events import Actor
    from app.services import inboxes as inboxes_service

    inbox = await inboxes_service.create_inbox(
        session,
        workspace_ctx.id,
        actor=Actor.system(),
        name="Hand-rolled Slack",
        channel_type="slack",
        config={"team_id": "T0FIRST", "greeting": "keep-me"},
        secrets={"bot_token": "xoxb-old", "signing_secret": "old-signing"},
    )
    await session.commit()

    await put_credentials(workspace_ctx, "slack", extra={"signing_secret": "new-signing"})
    await _connect_slack(client, workspace_ctx, bot_token="xoxb-new")

    inboxes = await _slack_inboxes(workspace_ctx.id)
    assert len(inboxes) == 1
    updated, secrets = inboxes[0]
    assert updated.id == inbox.id
    assert updated.config["greeting"] == "keep-me"  # existing config preserved
    assert secrets["bot_token"] == "xoxb-new"
    assert secrets["signing_secret"] == "new-signing"
