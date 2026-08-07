"""Contact directory: identity find-or-create, search, notes, timeline, tags."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.events import Actor, Event, EventNames, emit
from app.core.pagination import clamp_limit, decode_cursor, encode_cursor
from app.models.contact import Contact, ContactEvent, ContactNote
from app.models.csat import CsatResponse
from app.models.tag import ContactTag, Tag
from app.services import audit, custom_attributes
from app.services.segments import PostFilter, compile_filters, get_segment


def _ordering() -> tuple[Any, ...]:
    """Directory sort: last_seen desc nulls-last, then created desc (id tiebreak)."""
    return (
        Contact.last_seen_at.desc().nulls_last(),
        Contact.created_at.desc(),
        Contact.id.desc(),
    )


async def get_contact(session: AsyncSession, workspace_id: str, contact_id: str) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.workspace_id != workspace_id:
        raise NotFoundError("Contact not found")
    return contact


# ---------------------------------------------------------------------------
# identity spine
# ---------------------------------------------------------------------------


async def find_or_create(
    session: AsyncSession,
    workspace_id: str,
    *,
    external_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
    attributes: dict[str, Any] | None = None,
    verified: bool = False,
    actor: Actor = Actor.system(),
) -> tuple[Contact, bool]:
    """Resolve a contact by identity, creating one if needed.

    Match priority: external_id first, then email (most recent). Provided
    name/email/attributes update the match (attributes shallow-merge); verified
    is never downgraded True -> False. Returns (contact, created).
    """
    email = email.strip().lower() if email else None
    external_id = external_id.strip() if external_id else None

    contact: Contact | None = None
    if external_id:
        contact = (
            await session.execute(
                select(Contact).where(
                    Contact.workspace_id == workspace_id, Contact.external_id == external_id
                )
            )
        ).scalar_one_or_none()
    if contact is None and email:
        candidate = (
            await session.execute(
                select(Contact)
                .where(Contact.workspace_id == workspace_id, Contact.email == email)
                .order_by(Contact.created_at.desc(), Contact.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        # Only claim an email match for an external_id if it isn't already
        # bound to a different one (that would be a different person).
        if candidate is not None and (not external_id or candidate.external_id is None):
            contact = candidate

    now = utcnow()
    if contact is None:
        contact = Contact(
            workspace_id=workspace_id,
            external_id=external_id,
            email=email,
            name=(name or "").strip(),
            attributes=dict(attributes or {}),
            verified=verified,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(contact)
        await session.flush()
        await emit(
            session,
            Event(
                name=EventNames.CONTACT_CREATED,
                workspace_id=workspace_id,
                payload={
                    "contact_id": contact.id,
                    "email": contact.email,
                    "external_id": contact.external_id,
                    "name": contact.name,
                },
                actor=actor,
            ),
        )
        return contact, True

    if external_id and contact.external_id is None:
        contact.external_id = external_id
    if email:
        contact.email = email
    if name and name.strip():
        contact.name = name.strip()
    if attributes:
        contact.attributes = {**contact.attributes, **attributes}
    if verified:
        contact.verified = True  # never downgrade a verified identity
    if contact.first_seen_at is None:
        contact.first_seen_at = now
    contact.last_seen_at = now
    await session.flush()
    return contact, False


# ---------------------------------------------------------------------------
# listing (search + segment + cursor pagination)
# ---------------------------------------------------------------------------


def _cursor_key(contact: Contact) -> str:
    return encode_cursor(
        contact.last_seen_at.isoformat() if contact.last_seen_at else None,
        contact.created_at.isoformat(),
        contact.id,
    )


def _after_cursor(
    last_seen: datetime | None, created: datetime, contact_id: str
) -> ColumnElement[bool]:
    """Keyset predicate matching rows strictly after the cursor position under
    the (last_seen desc nulls-last, created desc, id desc) ordering."""
    created_tail = or_(
        Contact.created_at < created,
        and_(Contact.created_at == created, Contact.id < contact_id),
    )
    if last_seen is None:
        return and_(Contact.last_seen_at.is_(None), created_tail)
    return or_(
        Contact.last_seen_at < last_seen,
        and_(Contact.last_seen_at == last_seen, created_tail),
        Contact.last_seen_at.is_(None),
    )


def _decode_contact_cursor(cursor: str) -> ColumnElement[bool]:
    raw_seen, raw_created, contact_id = decode_cursor(cursor, 3)
    try:
        last_seen = datetime.fromisoformat(raw_seen) if raw_seen else None
        created = datetime.fromisoformat(raw_created)
    except (TypeError, ValueError) as exc:
        raise BadRequestError("Malformed cursor") from exc
    return _after_cursor(last_seen, created, str(contact_id))


async def list_contacts(
    session: AsyncSession,
    workspace_id: str,
    *,
    q: str | None = None,
    segment_id: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> tuple[list[Contact], str | None]:
    """Directory page: optional case-insensitive search over name/email/
    external_id, optional segment filters, cursor pagination."""
    page_size = clamp_limit(limit)
    conditions: list[ColumnElement[bool]] = [Contact.workspace_id == workspace_id]
    if q and q.strip():
        needle = f"%{q.strip()}%"
        conditions.append(
            or_(
                Contact.name.ilike(needle),
                Contact.email.ilike(needle),
                Contact.external_id.ilike(needle),
            )
        )
    post_filters: list[PostFilter] = []
    if segment_id:
        segment = await get_segment(session, workspace_id, segment_id)
        segment_conditions, post_filters = compile_filters(segment.filters)
        conditions.extend(segment_conditions)

    # Python-side attribute filters can shrink a page, so scan raw batches with
    # a keyset until we have page_size+1 matches (or the table is exhausted).
    after = _decode_contact_cursor(cursor) if cursor else None
    batch_size = max(page_size + 1, 50)
    matched: list[Contact] = []
    while len(matched) <= page_size:
        query = select(Contact).where(*conditions)
        if after is not None:
            query = query.where(after)
        query = query.order_by(*_ordering()).limit(batch_size)
        rows = list((await session.execute(query)).scalars())
        if not rows:
            break
        for contact in rows:
            if all(check(contact) for check in post_filters):
                matched.append(contact)
                if len(matched) > page_size:
                    break
        tail = rows[-1]
        after = _after_cursor(tail.last_seen_at, tail.created_at, tail.id)
        if len(rows) < batch_size:
            break

    items = matched[:page_size]
    next_cursor = _cursor_key(items[-1]) if len(matched) > page_size else None
    return items, next_cursor


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_contact(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    external_id: str | None = None,
    email: str | None = None,
    name: str = "",
    phone: str | None = None,
    avatar_url: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Contact:
    coerced = await custom_attributes.validate_attributes(
        session, workspace_id, "contact", dict(attributes or {})
    )
    contact = Contact(
        workspace_id=workspace_id,
        external_id=external_id.strip() if external_id else None,
        email=email.strip().lower() if email else None,
        name=(name or "").strip(),
        phone=phone,
        avatar_url=avatar_url,
        attributes=await custom_attributes.apply_defaults(
            session, workspace_id, "contact", coerced
        ),
    )
    session.add(contact)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A contact with this external_id already exists") from exc
    await emit(
        session,
        Event(
            name=EventNames.CONTACT_CREATED,
            workspace_id=workspace_id,
            payload={
                "contact_id": contact.id,
                "email": contact.email,
                "external_id": contact.external_id,
                "name": contact.name,
            },
            actor=actor,
        ),
    )
    return contact


async def update_contact(
    session: AsyncSession,
    workspace_id: str,
    contact_id: str,
    *,
    actor: Actor,
    external_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
    phone: str | None = None,
    avatar_url: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Contact:
    contact = await get_contact(session, workspace_id, contact_id)
    if external_id is not None:
        contact.external_id = external_id.strip() or None
    if email is not None:
        contact.email = email.strip().lower()
    if name is not None:
        contact.name = name.strip()
    if phone is not None:
        contact.phone = phone
    if avatar_url is not None:
        contact.avatar_url = avatar_url
    if attributes is not None:
        contact.attributes = await custom_attributes.validate_attributes(
            session, workspace_id, "contact", dict(attributes)
        )
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A contact with this external_id already exists") from exc
    return contact


async def delete_contact(
    session: AsyncSession, workspace_id: str, contact_id: str, *, actor: Actor
) -> None:
    contact = await get_contact(session, workspace_id, contact_id)
    meta = {"email": contact.email, "name": contact.name}
    # SQLite doesn't enforce FK cascades by default — clean dependents explicitly.
    await session.execute(delete(ContactTag).where(ContactTag.contact_id == contact_id))
    await session.execute(delete(ContactNote).where(ContactNote.contact_id == contact_id))
    await session.execute(delete(ContactEvent).where(ContactEvent.contact_id == contact_id))
    await session.execute(delete(CsatResponse).where(CsatResponse.contact_id == contact_id))
    await session.delete(contact)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="contact.delete",
        target_type="contact",
        target_id=contact_id,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


async def list_notes(
    session: AsyncSession, workspace_id: str, contact_id: str
) -> list[ContactNote]:
    await get_contact(session, workspace_id, contact_id)
    result = await session.execute(
        select(ContactNote)
        .where(ContactNote.workspace_id == workspace_id, ContactNote.contact_id == contact_id)
        .order_by(ContactNote.created_at.desc(), ContactNote.id.desc())
    )
    return list(result.scalars())


async def add_note(
    session: AsyncSession,
    workspace_id: str,
    contact_id: str,
    *,
    author_id: str | None,
    body: str,
) -> ContactNote:
    await get_contact(session, workspace_id, contact_id)
    note = ContactNote(
        workspace_id=workspace_id, contact_id=contact_id, author_id=author_id, body=body
    )
    session.add(note)
    await session.flush()
    return note


async def delete_note(
    session: AsyncSession, workspace_id: str, contact_id: str, note_id: str
) -> None:
    note = await session.get(ContactNote, note_id)
    if note is None or note.workspace_id != workspace_id or note.contact_id != contact_id:
        raise NotFoundError("Note not found")
    await session.delete(note)
    await session.flush()


# ---------------------------------------------------------------------------
# timeline events
# ---------------------------------------------------------------------------


async def list_events(
    session: AsyncSession, workspace_id: str, contact_id: str, *, limit: int | None = None
) -> list[ContactEvent]:
    await get_contact(session, workspace_id, contact_id)
    result = await session.execute(
        select(ContactEvent)
        .where(ContactEvent.workspace_id == workspace_id, ContactEvent.contact_id == contact_id)
        .order_by(ContactEvent.created_at.desc(), ContactEvent.id.desc())
        .limit(clamp_limit(limit, default=50, maximum=200))
    )
    return list(result.scalars())


async def track_event(
    session: AsyncSession,
    workspace_id: str,
    contact_id: str,
    *,
    name: str,
    meta: dict[str, Any] | None = None,
) -> ContactEvent:
    contact = await get_contact(session, workspace_id, contact_id)
    event = ContactEvent(
        workspace_id=workspace_id, contact_id=contact_id, name=name, meta=dict(meta or {})
    )
    session.add(event)
    now = utcnow()
    if contact.first_seen_at is None:
        contact.first_seen_at = now
    contact.last_seen_at = now  # an event means activity
    await session.flush()
    return event


# ---------------------------------------------------------------------------
# tags on a contact
# ---------------------------------------------------------------------------


async def tags_for_contacts(
    session: AsyncSession, workspace_id: str, contact_ids: list[str]
) -> dict[str, list[Tag]]:
    """Batch-load tags for a page of contacts: {contact_id: [Tag, ...]}."""
    if not contact_ids:
        return {}
    result = await session.execute(
        select(ContactTag.contact_id, Tag)
        .join(Tag, Tag.id == ContactTag.tag_id)
        .where(ContactTag.workspace_id == workspace_id, ContactTag.contact_id.in_(contact_ids))
        .order_by(Tag.name)
    )
    grouped: dict[str, list[Tag]] = {}
    for contact_id, tag in result.all():
        grouped.setdefault(contact_id, []).append(tag)
    return grouped


async def list_contact_tags(session: AsyncSession, workspace_id: str, contact_id: str) -> list[Tag]:
    await get_contact(session, workspace_id, contact_id)
    grouped = await tags_for_contacts(session, workspace_id, [contact_id])
    return grouped.get(contact_id, [])


async def attach_tag(
    session: AsyncSession, workspace_id: str, contact_id: str, *, tag_id: str
) -> list[Tag]:
    """Idempotently attach a tag; returns the contact's updated tag list."""
    await get_contact(session, workspace_id, contact_id)
    tag = await session.get(Tag, tag_id)
    if tag is None or tag.workspace_id != workspace_id:
        raise NotFoundError("Tag not found")
    existing = (
        await session.execute(
            select(ContactTag).where(
                ContactTag.contact_id == contact_id, ContactTag.tag_id == tag_id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(ContactTag(workspace_id=workspace_id, contact_id=contact_id, tag_id=tag_id))
        await session.flush()
    return await list_contact_tags(session, workspace_id, contact_id)


async def detach_tag(
    session: AsyncSession, workspace_id: str, contact_id: str, *, tag_id: str
) -> list[Tag]:
    """Idempotently detach a tag; returns the contact's updated tag list."""
    await get_contact(session, workspace_id, contact_id)
    await session.execute(
        delete(ContactTag).where(
            ContactTag.workspace_id == workspace_id,
            ContactTag.contact_id == contact_id,
            ContactTag.tag_id == tag_id,
        )
    )
    await session.flush()
    return await list_contact_tags(session, workspace_id, contact_id)
