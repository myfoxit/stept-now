"""Conversations API: the inbox feed, threads, messages, tags, read state.

Permissions: read → conversations:read, posting messages / starting
conversations / marking read → conversations:write, workflow mutations
(status, assignment, priority, tags) → conversations:manage.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.deps import Db, Member, Principal, require_perm
from app.core.errors import NotFoundError, ValidationFailure
from app.core.events import Actor
from app.core.pagination import CursorPage
from app.core.permissions import Perm
from app.models.contact import Contact
from app.models.message import AuthorType, MessageDirection
from app.schemas.bulk import BulkActionRequest, BulkActionResult
from app.schemas.collaboration import (
    MentionOut,
    MentionReadRequest,
    MentionReadResult,
    ParticipantAdd,
    ParticipantOut,
)
from app.schemas.common import Msg
from app.schemas.conversations import (
    ConversationCounts,
    ConversationCreate,
    ConversationListItem,
    ConversationOut,
    ConversationPatch,
    TagRequest,
)
from app.schemas.messages import MessageCreate, MessageOut
from app.schemas.saved_views import FilterQuery
from app.services import bulk as bulk_service
from app.services import collaboration as collaboration_service
from app.services import conversations as conversations_service
from app.services import custom_attributes as attrs_service
from app.services import inboxes as inboxes_service
from app.services import saved_views as views_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


def _author(principal: Principal) -> tuple[str, str | None, str]:
    """(author_type, author_id, author_name) for messages created via this API."""
    if principal.user is not None:
        return AuthorType.USER.value, principal.user.id, principal.user.name
    return AuthorType.SYSTEM.value, principal.actor_id, principal.label


@router.get(
    "/conversations",
    response_model=CursorPage[ConversationListItem],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_conversations(
    principal: Member,
    session: Db,
    status: Annotated[list[str] | None, Query()] = None,
    inbox_id: str | None = None,
    assignee: str | None = None,
    team_id: str | None = None,
    contact_id: str | None = None,
    tag_id: str | None = None,
    priority: str | None = None,
    q: str | None = None,
    view_id: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> CursorPage[ConversationListItem]:
    """`view_id` layers a saved view's filter document on top of the query
    params, so a view can be combined with an ad-hoc status/assignee narrowing."""
    view_query: dict[str, Any] | None = None
    if view_id:
        view = await views_service.get_view(
            session,
            principal.workspace.id,
            view_id,
            user_id=principal.user.id if principal.user is not None else None,
        )
        view_query = dict(view.query or {})
    items, next_cursor = await conversations_service.list_conversations(
        session,
        principal.workspace.id,
        status=status,
        inbox_id=inbox_id,
        assignee=assignee,
        team_id=team_id,
        contact_id=contact_id,
        tag_id=tag_id,
        priority=priority,
        q=q,
        view_query=view_query,
        cursor=cursor,
        limit=limit,
        current_user_id=principal.user.id if principal.user is not None else None,
    )
    return CursorPage(items=items, next_cursor=next_cursor)


@router.post(
    "/conversations/search",
    response_model=CursorPage[ConversationListItem],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def search_conversations(
    body: FilterQuery,
    principal: Member,
    session: Db,
    cursor: str | None = None,
    limit: int | None = None,
) -> CursorPage[ConversationListItem]:
    """Run an ad-hoc filter document without saving it as a view — the endpoint
    the filter builder previews against and report drill-down links to."""
    items, next_cursor = await conversations_service.list_conversations(
        session,
        principal.workspace.id,
        view_query=body.model_dump(),
        cursor=cursor,
        limit=limit,
        current_user_id=principal.user.id if principal.user is not None else None,
    )
    return CursorPage(items=items, next_cursor=next_cursor)


@router.get(
    "/conversations/counts",
    response_model=ConversationCounts,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def conversation_counts(principal: Member, session: Db) -> ConversationCounts:
    return await conversations_service.counts(
        session,
        principal.workspace.id,
        user_id=principal.user.id if principal.user is not None else None,
    )


@router.post(
    "/conversations",
    response_model=ConversationOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_WRITE))],
)
async def create_conversation(
    body: ConversationCreate, principal: Member, session: Db
) -> ConversationOut:
    """Outbound start: open a conversation with a contact and send the first message."""
    workspace_id = principal.workspace.id
    contact = await session.get(Contact, body.contact_id)
    if contact is None or contact.workspace_id != workspace_id:
        raise NotFoundError("Contact not found")
    inbox = await inboxes_service.get_inbox(session, workspace_id, body.inbox_id)
    actor = _actor(principal)
    conversation = await conversations_service.create_conversation(
        session,
        inbox=inbox,
        contact=contact,
        subject=body.subject,
        actor=actor,
    )
    author_type, author_id, author_name = _author(principal)
    await conversations_service.add_message(
        session,
        conversation,
        direction=MessageDirection.OUT.value,
        author_type=author_type,
        author_id=author_id,
        author_name=author_name,
        content=body.content,
        actor=actor,
    )
    return await conversations_service.conversation_out(session, conversation, inbox=inbox)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def get_conversation(conversation_id: str, principal: Member, session: Db) -> ConversationOut:
    conversation = await conversations_service.get_conversation(
        session, principal.workspace.id, conversation_id
    )
    return await conversations_service.conversation_out(session, conversation)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def update_conversation(
    conversation_id: str, body: ConversationPatch, principal: Member, session: Db
) -> ConversationOut:
    conversation = await conversations_service.get_conversation(
        session, principal.workspace.id, conversation_id
    )
    actor = _actor(principal)
    provided = body.model_fields_set
    if body.status is not None:
        await conversations_service.update_status(
            session, conversation, body.status, actor=actor, snoozed_until=body.snoozed_until
        )
    if body.priority is not None:
        await conversations_service.set_priority(session, conversation, body.priority, actor=actor)
    if "assignee_user_id" in provided or "team_id" in provided:
        await conversations_service.assign(
            session,
            conversation,
            assignee_user_id=(
                body.assignee_user_id
                if "assignee_user_id" in provided
                else conversations_service.UNSET
            ),
            team_id=(body.team_id if "team_id" in provided else conversations_service.UNSET),
            actor=actor,
        )
    if body.attributes is not None:
        coerced = await attrs_service.validate_attributes(
            session, principal.workspace.id, "conversation", body.attributes
        )
        merged = dict(conversation.attributes or {})
        for key, value in coerced.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        conversation.attributes = merged
        await session.flush()
    return await conversations_service.conversation_out(session, conversation)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=CursorPage[MessageOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_messages(
    conversation_id: str,
    principal: Member,
    session: Db,
    cursor: str | None = None,
    limit: int | None = None,
) -> CursorPage[MessageOut]:
    """Newest page first; the cursor walks toward older messages. Items within a
    page are ascending (chat order)."""
    conversation = await conversations_service.get_conversation(
        session, principal.workspace.id, conversation_id
    )
    messages, next_cursor = await conversations_service.list_messages(
        session, conversation, cursor=cursor, limit=limit
    )
    return CursorPage(
        items=[MessageOut.model_validate(m) for m in messages], next_cursor=next_cursor
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_WRITE))],
)
async def create_message(
    conversation_id: str, body: MessageCreate, principal: Member, session: Db
) -> MessageOut:
    conversation = await conversations_service.get_conversation(
        session, principal.workspace.id, conversation_id
    )
    author_type, author_id, author_name = _author(principal)
    message = await conversations_service.add_message(
        session,
        conversation,
        direction=MessageDirection.OUT.value,
        author_type=author_type,
        author_id=author_id,
        author_name=author_name,
        content=body.content,
        visibility=body.visibility,
        attachments=[a.model_dump() for a in body.attachments],
        actor=_actor(principal),
    )
    return MessageOut.model_validate(message)


@router.post(
    "/conversations/{conversation_id}/tags",
    response_model=ConversationOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def add_tag(
    conversation_id: str, body: TagRequest, principal: Member, session: Db
) -> ConversationOut:
    conversation = await conversations_service.get_conversation(
        session, principal.workspace.id, conversation_id
    )
    await conversations_service.add_tag(session, conversation, body.tag_id, actor=_actor(principal))
    return await conversations_service.conversation_out(session, conversation)


@router.delete(
    "/conversations/{conversation_id}/tags/{tag_id}",
    response_model=ConversationOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def remove_tag(
    conversation_id: str, tag_id: str, principal: Member, session: Db
) -> ConversationOut:
    conversation = await conversations_service.get_conversation(
        session, principal.workspace.id, conversation_id
    )
    await conversations_service.remove_tag(session, conversation, tag_id, actor=_actor(principal))
    return await conversations_service.conversation_out(session, conversation)


@router.post(
    "/conversations/{conversation_id}/read",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_WRITE))],
)
async def mark_read(conversation_id: str, principal: Member, session: Db) -> Msg:
    conversation = await conversations_service.get_conversation(
        session, principal.workspace.id, conversation_id
    )
    await conversations_service.mark_read(session, conversation)
    return Msg(message="Conversation marked read")


# ---------------------------------------------------------------------------
# bulk actions (docs/CHATWOOT-BACKLOG.md §1.4)
# ---------------------------------------------------------------------------


@router.post(
    "/conversations/bulk",
    response_model=BulkActionResult,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def bulk_action(body: BulkActionRequest, principal: Member, session: Db) -> BulkActionResult:
    """Apply one action to many conversations. Per-conversation failures are
    reported rather than rolling the batch back."""
    result = await bulk_service.run(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        actor_user_id=principal.user.id if principal.user is not None else None,
        action=body.action,
        params=body.params,
        conversation_ids=body.conversation_ids,
        query=body.query.model_dump() if body.query is not None else None,
    )
    return BulkActionResult(
        requested=result.requested,
        succeeded=result.succeeded,
        failed=result.failed,
        errors=result.errors,
    )


# ---------------------------------------------------------------------------
# participants & mentions (docs/CHATWOOT-BACKLOG.md §1.2)
# ---------------------------------------------------------------------------


@router.get(
    "/conversations/{conversation_id}/participants",
    response_model=list[ParticipantOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_participants(
    conversation_id: str, principal: Member, session: Db
) -> list[ParticipantOut]:
    await collaboration_service.assert_conversation(
        session, principal.workspace.id, conversation_id
    )
    rows = await collaboration_service.list_participants(
        session, principal.workspace.id, conversation_id
    )
    return [ParticipantOut.model_validate(r) for r in rows]


@router.post(
    "/conversations/{conversation_id}/participants",
    response_model=list[ParticipantOut],
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_WRITE))],
)
async def add_participant(
    conversation_id: str, body: ParticipantAdd, principal: Member, session: Db
) -> list[ParticipantOut]:
    await collaboration_service.assert_conversation(
        session, principal.workspace.id, conversation_id
    )
    added = await collaboration_service.add_participant(
        session,
        principal.workspace.id,
        conversation_id,
        body.user_id,
        reason="manual",
        force=True,
    )
    if added is None:
        raise ValidationFailure("That user is not a member of this workspace")
    rows = await collaboration_service.list_participants(
        session, principal.workspace.id, conversation_id
    )
    return [ParticipantOut.model_validate(r) for r in rows]


@router.delete(
    "/conversations/{conversation_id}/participants/{user_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_WRITE))],
)
async def remove_participant(
    conversation_id: str, user_id: str, principal: Member, session: Db
) -> Msg:
    await collaboration_service.assert_conversation(
        session, principal.workspace.id, conversation_id
    )
    await collaboration_service.remove_participant(
        session, principal.workspace.id, conversation_id, user_id
    )
    return Msg(message="Left the conversation")


@router.get(
    "/mentions",
    response_model=list[MentionOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_mentions(
    principal: Member,
    session: Db,
    unread_only: bool = Query(False),
    limit: int | None = None,
) -> list[MentionOut]:
    """The signed-in user's mentions. API-key principals have none by definition."""
    if principal.user is None:
        return []
    rows = await collaboration_service.list_mentions(
        session,
        principal.workspace.id,
        principal.user.id,
        unread_only=unread_only,
        limit=limit,
    )
    payload = await collaboration_service.mention_payload(session, rows)
    return [MentionOut.model_validate(item) for item in payload]


@router.post(
    "/mentions/read",
    response_model=MentionReadResult,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def mark_mentions_read(
    body: MentionReadRequest, principal: Member, session: Db
) -> MentionReadResult:
    if principal.user is None:
        return MentionReadResult(marked=0)
    marked = await collaboration_service.mark_mentions_read(
        session, principal.workspace.id, principal.user.id, body.conversation_id
    )
    return MentionReadResult(marked=marked)
