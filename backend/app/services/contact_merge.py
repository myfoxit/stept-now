"""Contact merge and blocking.

**Merge** reparents everything the loser owns onto the winner and keeps the
loser row (flagged `merged_into_id`) so old links, channel `source_id`s and
webhook payloads still resolve instead of 404-ing. Scalars follow "winner wins,
loser fills the blanks": the winner's name/email/phone survive, and any field
the winner left empty is taken from the loser. Attributes deep-merge with the
same precedence.

Duplicate contacts are inevitable the moment you run more than one channel — the
same human arrives by email and by widget and becomes two rows.

**Blocking** is a flag checked at every ingress point; blocked contacts can't
open conversations and their inbound messages are dropped.

See docs/CHATWOOT-BACKLOG.md §1.7.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationFailure
from app.core.events import Actor
from app.models.contact import Contact, ContactEvent, ContactNote
from app.models.conversation import Conversation
from app.models.csat import CsatResponse
from app.models.inbox import ContactInbox
from app.models.tag import ContactTag
from app.services import audit


async def _get(session: AsyncSession, workspace_id: str, contact_id: str) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.workspace_id != workspace_id:
        raise NotFoundError("Contact not found")
    return contact


def _merge_scalars(winner: Contact, loser: Contact) -> None:
    for field in ("name", "email", "phone", "avatar_url", "external_id"):
        current = getattr(winner, field)
        if not current:
            setattr(winner, field, getattr(loser, field))
    # Union semantics for the flags that mean "we know something extra".
    winner.verified = winner.verified or loser.verified
    if loser.first_seen_at and (
        winner.first_seen_at is None or loser.first_seen_at < winner.first_seen_at
    ):
        winner.first_seen_at = loser.first_seen_at
    if loser.last_seen_at and (
        winner.last_seen_at is None or loser.last_seen_at > winner.last_seen_at
    ):
        winner.last_seen_at = loser.last_seen_at
    merged_attributes: dict[str, Any] = dict(loser.attributes or {})
    merged_attributes.update(dict(winner.attributes or {}))
    winner.attributes = merged_attributes


async def merge(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    winner_id: str,
    loser_id: str,
) -> Contact:
    """Fold `loser_id` into `winner_id` and return the winner."""
    if winner_id == loser_id:
        raise ValidationFailure("Cannot merge a contact into itself")
    winner = await _get(session, workspace_id, winner_id)
    loser = await _get(session, workspace_id, loser_id)
    if loser.merged_into_id is not None:
        raise ValidationFailure("That contact has already been merged")
    if winner.merged_into_id is not None:
        raise ValidationFailure("Cannot merge into an already-merged contact")

    _merge_scalars(winner, loser)

    # Reparent owned rows. ContactInbox carries a unique (inbox, source_id), so
    # a collision means both contacts already had an identity on that inbox —
    # the loser's link is dropped rather than failing the whole merge.
    for model in (Conversation, ContactNote, ContactEvent, CsatResponse):
        await session.execute(
            update(model)
            .where(model.contact_id == loser.id, model.workspace_id == workspace_id)
            .values(contact_id=winner.id)
        )

    winner_inbox_keys = {
        (ci.inbox_id, ci.source_id)
        for ci in (
            await session.execute(select(ContactInbox).where(ContactInbox.contact_id == winner.id))
        ).scalars()
    }
    for link in (
        await session.execute(select(ContactInbox).where(ContactInbox.contact_id == loser.id))
    ).scalars():
        if (link.inbox_id, link.source_id) in winner_inbox_keys:
            await session.delete(link)
        else:
            link.contact_id = winner.id

    winner_tag_ids = {
        row
        for row in (
            await session.execute(
                select(ContactTag.tag_id).where(ContactTag.contact_id == winner.id)
            )
        ).scalars()
    }
    for tag_link in (
        await session.execute(select(ContactTag).where(ContactTag.contact_id == loser.id))
    ).scalars():
        if tag_link.tag_id in winner_tag_ids:
            await session.delete(tag_link)
        else:
            tag_link.contact_id = winner.id

    # Keep the tombstone: external systems and channel source_ids still point here.
    loser.merged_into_id = winner.id
    loser.external_id = None  # free the unique (workspace, external_id) slot
    await session.flush()

    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="contact.merge",
        target_type="contact",
        target_id=winner.id,
        meta={"merged_from": loser.id, "email": winner.email},
    )
    return winner


async def set_blocked(
    session: AsyncSession,
    workspace_id: str,
    contact_id: str,
    *,
    actor: Actor,
    blocked: bool,
) -> Contact:
    contact = await _get(session, workspace_id, contact_id)
    if contact.blocked == blocked:
        return contact
    contact.blocked = blocked
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="contact.block" if blocked else "contact.unblock",
        target_type="contact",
        target_id=contact.id,
        meta={"email": contact.email},
    )
    return contact


async def resolve_active(session: AsyncSession, workspace_id: str, contact_id: str) -> Contact:
    """Follow a merge tombstone to the surviving contact (one hop is enough —
    merging into an already-merged contact is rejected above)."""
    contact = await _get(session, workspace_id, contact_id)
    if contact.merged_into_id is None:
        return contact
    return await _get(session, workspace_id, contact.merged_into_id)


def assert_not_blocked(contact: Contact) -> None:
    """Guard for ingress paths (widget boot, channel webhooks, public API)."""
    if contact.blocked:
        raise ForbiddenError("This contact is blocked")
