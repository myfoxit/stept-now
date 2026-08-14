"""Outbound email rendering. `_to_text` builds BOTH the console fallback (dev:
SMTP unset) and the text/plain part of real emails — and invite/reset emails
carry their link only inside an ``<a href>``, so anchors must keep their URL."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.workspace import Invitation
from app.services.email import _to_text


def test_to_text_renders_an_anchor_as_label_and_url():
    html = (
        "<p>You have been invited to Acme.</p>"
        '<p><a href="https://app.test/accept-invite?token=tok-1">Accept the invitation</a> '
        "Valid for 7 days.</p>"
    )
    text = _to_text(html)
    assert "Accept the invitation (https://app.test/accept-invite?token=tok-1)" in text
    assert "<" not in text and ">" not in text
    assert "You have been invited to Acme." in text


def test_to_text_collapses_a_link_labeled_with_its_own_url():
    assert (
        _to_text('<p><a href="https://app.test/x">https://app.test/x</a></p>')
        == "https://app.test/x"
    )


def test_to_text_unescapes_entities_in_hrefs():
    text = _to_text('<a href="https://app.test/a?b=1&amp;c=2">Open</a>')
    assert text == "Open (https://app.test/a?b=1&c=2)"


def test_to_text_handles_attributes_and_nested_markup_in_anchors():
    html = "<a style=\"color:#333\" href='https://app.test/r'><strong>Reset</strong></a><br/>Bye"
    assert _to_text(html) == "Reset (https://app.test/r)\nBye"


async def test_invite_console_fallback_contains_the_accept_url(client, workspace_ctx, caplog):
    """SMTP unset (the dev default): the logged email is the only copy of the
    invite link anyone can act on — it must contain the actual accept URL."""
    with caplog.at_level(logging.INFO, logger="stept.email"):
        response = await client.post(
            f"{workspace_ctx.base}/invitations",
            json={"email": "newbie@example.com", "role": "agent"},
            headers=workspace_ctx.owner_headers,
        )
    assert response.status_code == 201, response.text

    async with get_session_factory()() as session:
        token = (
            await session.execute(
                select(Invitation.token).where(Invitation.email == "newbie@example.com")
            )
        ).scalar_one()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert f"accept-invite?token={token}" in logged
