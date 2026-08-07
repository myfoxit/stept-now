"""Contacts API: directory CRUD, notes, timeline events, tags, CSAT history.

The empty router is pre-registered; add routes here, never touch the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response

from app.core.deps import Db, Member, Principal, require_perm
from app.core.errors import ValidationFailure
from app.core.events import Actor
from app.core.pagination import CursorPage
from app.core.permissions import Perm
from app.models.contact import Contact
from app.models.tag import Tag
from app.schemas.bulk import (
    ContactBlockRequest,
    ContactImportOut,
    ContactImportPreview,
    ContactImportStart,
    ContactMergeRequest,
)
from app.schemas.common import Msg
from app.schemas.contacts import (
    ContactCreate,
    ContactEventCreate,
    ContactEventOut,
    ContactNoteCreate,
    ContactNoteOut,
    ContactOut,
    ContactTagAttach,
    ContactUpdate,
    CsatResponseOut,
)
from app.schemas.tags import TagOut
from app.services import contact_import as import_service
from app.services import contact_merge as contact_merge_service
from app.services import contacts as contacts_service
from app.services import csat as csat_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


def _contact_out(contact: Contact, tags: list[Tag] | None = None) -> ContactOut:
    out = ContactOut.model_validate(contact)
    out.tags = [TagOut.model_validate(t) for t in tags or []]
    return out


# ---------------------------------------------------------------------------
# merge, block, CSV import/export (docs/CHATWOOT-BACKLOG.md §1.7)
# ---------------------------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/merge",
    response_model=ContactOut,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def merge_contacts(
    contact_id: str, body: ContactMergeRequest, principal: Member, session: Db
) -> ContactOut:
    """Fold `loser_id` into this contact. The loser row survives as a tombstone
    so old links and channel source_ids keep resolving."""
    winner = await contact_merge_service.merge(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        winner_id=contact_id,
        loser_id=body.loser_id,
    )
    tags = await contacts_service.list_contact_tags(session, principal.workspace.id, winner.id)
    return _contact_out(winner, tags)


@router.post(
    "/contacts/{contact_id}/block",
    response_model=ContactOut,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def block_contact(
    contact_id: str, body: ContactBlockRequest, principal: Member, session: Db
) -> ContactOut:
    contact = await contact_merge_service.set_blocked(
        session,
        principal.workspace.id,
        contact_id,
        actor=_actor(principal),
        blocked=body.blocked,
    )
    tags = await contacts_service.list_contact_tags(session, principal.workspace.id, contact.id)
    return _contact_out(contact, tags)


@router.get(
    "/contacts/export",
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def export_contacts(principal: Member, session: Db) -> Response:
    csv_text = await import_service.export_rows(session, principal.workspace.id)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="contacts.csv"'},
    )


@router.get(
    "/contacts/imports",
    response_model=list[ContactImportOut],
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def list_contact_imports(principal: Member, session: Db) -> list[ContactImportOut]:
    rows = await import_service.list_imports(session, principal.workspace.id)
    return [ContactImportOut.model_validate(r) for r in rows]


@router.post(
    "/contacts/imports",
    response_model=ContactImportPreview,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def upload_contact_import(
    principal: Member, session: Db, file: UploadFile
) -> ContactImportPreview:
    """Upload a CSV and get back the detected headers, a suggested column
    mapping and a few sample rows. Nothing is written until `/start`."""
    raw = await file.read()
    if not raw:
        raise ValidationFailure("The uploaded file is empty")
    record, headers, sample = await import_service.create_import(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        user_id=principal.user.id if principal.user is not None else None,
        filename=file.filename or "contacts.csv",
        raw=raw,
    )
    return ContactImportPreview(
        contact_import=ContactImportOut.model_validate(record),
        headers=headers,
        sample_rows=sample,
    )


@router.get(
    "/contacts/imports/{import_id}",
    response_model=ContactImportOut,
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def get_contact_import(import_id: str, principal: Member, session: Db) -> ContactImportOut:
    record = await import_service.get_import(session, principal.workspace.id, import_id)
    return ContactImportOut.model_validate(record)


@router.post(
    "/contacts/imports/{import_id}/start",
    response_model=ContactImportOut,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def start_contact_import(
    import_id: str, body: ContactImportStart, principal: Member, session: Db
) -> ContactImportOut:
    record = await import_service.start_import(
        session,
        principal.workspace.id,
        import_id,
        actor=_actor(principal),
        mapping=body.mapping,
    )
    return ContactImportOut.model_validate(record)


@router.get(
    "/contacts",
    response_model=CursorPage[ContactOut],
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def list_contacts(
    principal: Member,
    session: Db,
    q: str | None = None,
    segment_id: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    contacts, next_cursor = await contacts_service.list_contacts(
        session, principal.workspace.id, q=q, segment_id=segment_id, cursor=cursor, limit=limit
    )
    tag_map = await contacts_service.tags_for_contacts(
        session, principal.workspace.id, [c.id for c in contacts]
    )
    return CursorPage(
        items=[_contact_out(c, tag_map.get(c.id)) for c in contacts], next_cursor=next_cursor
    )


@router.post(
    "/contacts",
    response_model=ContactOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def create_contact(body: ContactCreate, principal: Member, session: Db):
    contact = await contacts_service.create_contact(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        external_id=body.external_id,
        email=body.email,
        name=body.name,
        phone=body.phone,
        avatar_url=body.avatar_url,
        attributes=body.attributes,
    )
    return _contact_out(contact)


@router.get(
    "/contacts/{contact_id}",
    response_model=ContactOut,
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def get_contact(contact_id: str, principal: Member, session: Db):
    contact = await contacts_service.get_contact(session, principal.workspace.id, contact_id)
    tags = await contacts_service.list_contact_tags(session, principal.workspace.id, contact_id)
    return _contact_out(contact, tags)


@router.patch(
    "/contacts/{contact_id}",
    response_model=ContactOut,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def update_contact(contact_id: str, body: ContactUpdate, principal: Member, session: Db):
    contact = await contacts_service.update_contact(
        session,
        principal.workspace.id,
        contact_id,
        actor=_actor(principal),
        external_id=body.external_id,
        email=body.email,
        name=body.name,
        phone=body.phone,
        avatar_url=body.avatar_url,
        attributes=body.attributes,
    )
    tags = await contacts_service.list_contact_tags(session, principal.workspace.id, contact_id)
    return _contact_out(contact, tags)


@router.delete(
    "/contacts/{contact_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def delete_contact(contact_id: str, principal: Member, session: Db):
    await contacts_service.delete_contact(
        session, principal.workspace.id, contact_id, actor=_actor(principal)
    )
    return Msg(message="Contact deleted")


# --- notes ------------------------------------------------------------------


@router.get(
    "/contacts/{contact_id}/notes",
    response_model=list[ContactNoteOut],
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def list_notes(contact_id: str, principal: Member, session: Db):
    notes = await contacts_service.list_notes(session, principal.workspace.id, contact_id)
    return [ContactNoteOut.model_validate(n) for n in notes]


@router.post(
    "/contacts/{contact_id}/notes",
    response_model=ContactNoteOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def add_note(contact_id: str, body: ContactNoteCreate, principal: Member, session: Db):
    note = await contacts_service.add_note(
        session,
        principal.workspace.id,
        contact_id,
        author_id=principal.user.id if principal.user else None,
        body=body.body,
    )
    return ContactNoteOut.model_validate(note)


@router.delete(
    "/contacts/{contact_id}/notes/{note_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def delete_note(contact_id: str, note_id: str, principal: Member, session: Db):
    await contacts_service.delete_note(session, principal.workspace.id, contact_id, note_id)
    return Msg(message="Note deleted")


# --- timeline events --------------------------------------------------------


@router.get(
    "/contacts/{contact_id}/events",
    response_model=list[ContactEventOut],
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def list_events(contact_id: str, principal: Member, session: Db, limit: int | None = None):
    events = await contacts_service.list_events(
        session, principal.workspace.id, contact_id, limit=limit
    )
    return [ContactEventOut.model_validate(e) for e in events]


@router.post(
    "/contacts/{contact_id}/events",
    response_model=ContactEventOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def track_event(contact_id: str, body: ContactEventCreate, principal: Member, session: Db):
    """Track a timeline event. Also usable by API keys with the write scope."""
    event = await contacts_service.track_event(
        session, principal.workspace.id, contact_id, name=body.name, meta=body.meta
    )
    return ContactEventOut.model_validate(event)


# --- tags on a contact ------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/tags",
    response_model=list[TagOut],
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def attach_tag(contact_id: str, body: ContactTagAttach, principal: Member, session: Db):
    tags = await contacts_service.attach_tag(
        session, principal.workspace.id, contact_id, tag_id=body.tag_id
    )
    return [TagOut.model_validate(t) for t in tags]


@router.delete(
    "/contacts/{contact_id}/tags/{tag_id}",
    response_model=list[TagOut],
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def detach_tag(contact_id: str, tag_id: str, principal: Member, session: Db):
    tags = await contacts_service.detach_tag(
        session, principal.workspace.id, contact_id, tag_id=tag_id
    )
    return [TagOut.model_validate(t) for t in tags]


# --- CSAT history -----------------------------------------------------------


@router.get(
    "/contacts/{contact_id}/csat",
    response_model=list[CsatResponseOut],
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def list_csat(contact_id: str, principal: Member, session: Db):
    await contacts_service.get_contact(session, principal.workspace.id, contact_id)
    responses = await csat_service.list_for_contact(session, principal.workspace.id, contact_id)
    return [CsatResponseOut.model_validate(r) for r in responses]
