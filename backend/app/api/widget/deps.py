"""Widget public API dependencies: widget-key inbox resolution + contact-token auth.

The embeddable widget authenticates with a short signed contact token
(``X-Widget-Token``) minted by ``POST /api/widget/boot``. The token carries the
workspace + contact id (see ``create_widget_token``); the widget inbox and the
contact's per-inbox identity (``ContactInbox``) are resolved from those.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.deps import Db
from app.core.errors import NotFoundError, UnauthorizedError
from app.models.contact import Contact
from app.models.inbox import ChannelType, ContactInbox, Inbox
from app.models.workspace import Workspace


async def resolve_inbox(session: AsyncSession, widget_key: str) -> Inbox:
    """Load an enabled widget inbox by its public embed key (``wk_…``)."""
    inbox = (
        await session.execute(select(Inbox).where(Inbox.widget_key == widget_key))
    ).scalar_one_or_none()
    if inbox is None or not inbox.enabled or inbox.channel_type != ChannelType.WIDGET:
        raise NotFoundError("Unknown or disabled widget")
    return inbox


@dataclass
class WidgetPrincipal:
    """The authenticated visitor behind a widget request."""

    workspace: Workspace
    inbox: Inbox
    contact: Contact
    contact_inbox: ContactInbox


async def widget_auth(
    session: Db,
    x_widget_token: Annotated[str | None, Header()] = None,
) -> WidgetPrincipal:
    """Resolve the widget contact token into a full principal (401 on invalid).

    ``create_widget_token`` only carries ``{ws, sub=contact_id}``, so the inbox
    and contact_inbox are resolved from the contact's widget identity — the
    ContactInbox created at boot on the widget inbox the visitor came through.
    """
    if not x_widget_token:
        raise UnauthorizedError("Missing widget token")
    payload = security.decode_token(x_widget_token, "widget")
    workspace_id = payload.get("ws")
    contact_id = payload.get("sub")
    if not isinstance(workspace_id, str) or not isinstance(contact_id, str):
        raise UnauthorizedError("Invalid widget token")

    workspace = await session.get(Workspace, workspace_id)
    contact = await session.get(Contact, contact_id)
    if workspace is None or contact is None or contact.workspace_id != workspace_id:
        raise UnauthorizedError("Invalid widget token")

    row = (
        await session.execute(
            select(ContactInbox, Inbox)
            .join(Inbox, Inbox.id == ContactInbox.inbox_id)
            .where(
                ContactInbox.contact_id == contact_id,
                ContactInbox.workspace_id == workspace_id,
                Inbox.channel_type == ChannelType.WIDGET,
                Inbox.enabled.is_(True),
            )
            .order_by(ContactInbox.updated_at.desc(), ContactInbox.id.desc())
        )
    ).first()
    if row is None:
        raise UnauthorizedError("Invalid widget token")
    contact_inbox, inbox = row
    return WidgetPrincipal(
        workspace=workspace, inbox=inbox, contact=contact, contact_inbox=contact_inbox
    )


WidgetAuth = Annotated[WidgetPrincipal, Depends(widget_auth)]
