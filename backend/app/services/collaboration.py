"""Thread collaboration: participants (watchers) and @mentions on private notes.

Two rules drive everything here:

1. **Participation is mostly implicit.** Assigning someone, or having them write
   a note, makes them a participant. A participant who explicitly leaves gets a
   `muted` row rather than a deletion, so the next implicit add doesn't
   resurrect a subscription they opted out of.
2. **A mention is a notification with a receipt.** Parsing `@name` out of a note
   creates a `Mention` row (the audit trail + the "Mentions" view) *and* adds the
   author's target as a participant, so follow-up activity keeps reaching them.

Mentions only ever resolve to members of the conversation's workspace — an
`@name` matching a user outside it is left as plain text.

See docs/CHATWOOT-BACKLOG.md §1.2.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import NotFoundError
from app.core.pagination import clamp_limit
from app.models.collaboration import ConversationParticipant, Mention
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.models.workspace import Membership
from app.services import notifications

# `@` followed by a name that may contain single spaces — "@Ada Lovelace" and
# "@ada" both work. Bounded to three words so a note full of prose doesn't get
# swallowed into one candidate.
_MENTION_RE = re.compile(r"@([A-Za-z0-9._-]+(?:[ \t][A-Za-z0-9._-]+){0,2})")


async def _workspace_users(session: AsyncSession, workspace_id: str) -> list[User]:
    result = await session.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.workspace_id == workspace_id)
    )
    return list(result.scalars())


def extract_mention_names(content: str) -> list[str]:
    """Candidate names in the text, longest first.

    A handle has no terminator, so "@Ada Lovelace can you look" is
    indistinguishable from a three-word name at the lexical level. Rather than
    guess, every prefix of each run is emitted ("Ada Lovelace can", "Ada
    Lovelace", "Ada") and resolution picks the longest one that is actually a
    member — which is why the caller iterates longest-first.
    """
    found: list[str] = []
    for match in _MENTION_RE.finditer(content or ""):
        words = match.group(1).split()
        for size in range(len(words), 0, -1):
            found.append(" ".join(words[:size]))
    return sorted(dict.fromkeys(found), key=len, reverse=True)


async def resolve_mentions(session: AsyncSession, workspace_id: str, content: str) -> list[User]:
    """Users named in `content`, matched case-insensitively against member names
    (and the local part of their email). Unmatched `@handles` are ignored."""
    candidates = extract_mention_names(content)
    if not candidates:
        return []
    users = await _workspace_users(session, workspace_id)
    by_key: dict[str, User] = {}
    for user in users:
        by_key.setdefault(user.name.strip().lower(), user)
        if user.email:
            by_key.setdefault(user.email.split("@")[0].strip().lower(), user)
            by_key.setdefault(user.email.strip().lower(), user)

    matched: dict[str, User] = {}
    claimed: list[str] = []
    for candidate in candidates:  # longest first
        key = candidate.lower()
        # A shorter candidate that opens one already matched is the same mention
        # seen again ("@Ada" inside "@Ada Lovelace") — don't match it twice.
        if any(longer.startswith(key) for longer in claimed):
            continue
        found = by_key.get(key)
        if found is not None:
            matched.setdefault(found.id, found)
            claimed.append(key)
    return list(matched.values())


# ---------------------------------------------------------------------------
# participants
# ---------------------------------------------------------------------------


async def list_participants(
    session: AsyncSession, workspace_id: str, conversation_id: str
) -> list[ConversationParticipant]:
    result = await session.execute(
        select(ConversationParticipant)
        .where(
            ConversationParticipant.workspace_id == workspace_id,
            ConversationParticipant.conversation_id == conversation_id,
        )
        .order_by(ConversationParticipant.created_at)
    )
    return list(result.scalars())


async def _get_participant(
    session: AsyncSession, workspace_id: str, conversation_id: str, user_id: str
) -> ConversationParticipant | None:
    return (
        await session.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.workspace_id == workspace_id,
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def add_participant(
    session: AsyncSession,
    workspace_id: str,
    conversation_id: str,
    user_id: str,
    *,
    reason: str = "manual",
    force: bool = False,
) -> ConversationParticipant | None:
    """Idempotent. An implicit add (`force=False`) never un-mutes someone who
    explicitly left; an explicit one (`force=True`) does."""
    existing = await _get_participant(session, workspace_id, conversation_id, user_id)
    if existing is not None:
        if existing.muted and force:
            existing.muted = False
            existing.reason = reason
            await session.flush()
        return existing
    membership = (
        await session.execute(
            select(Membership.id).where(
                Membership.workspace_id == workspace_id, Membership.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        return None  # not a member of this workspace — never a participant
    participant = ConversationParticipant(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        user_id=user_id,
        reason=reason,
    )
    session.add(participant)
    await session.flush()
    return participant


async def remove_participant(
    session: AsyncSession, workspace_id: str, conversation_id: str, user_id: str
) -> None:
    """Leave the thread. Recorded as a mute so implicit re-adds stay quiet."""
    existing = await _get_participant(session, workspace_id, conversation_id, user_id)
    if existing is None:
        return
    existing.muted = True
    await session.flush()


async def participant_user_ids(
    session: AsyncSession, workspace_id: str, conversation_id: str, *, exclude: str | None = None
) -> list[str]:
    rows = await list_participants(session, workspace_id, conversation_id)
    return [p.user_id for p in rows if not p.muted and p.user_id != exclude]


# ---------------------------------------------------------------------------
# mentions
# ---------------------------------------------------------------------------


async def record_mentions(
    session: AsyncSession,
    conversation: Conversation,
    message: Message,
    *,
    author_user_id: str | None,
) -> list[Mention]:
    """Parse the note, persist mentions, subscribe the mentioned, notify them.

    Called from the message write path for private notes only — mentioning
    someone in a customer-visible reply would leak the handle to the contact.
    """
    users = await resolve_mentions(session, conversation.workspace_id, message.content)
    created: list[Mention] = []
    for user in users:
        if user.id == author_user_id:
            continue  # mentioning yourself is a no-op
        mention = Mention(
            workspace_id=conversation.workspace_id,
            conversation_id=conversation.id,
            message_id=message.id,
            user_id=user.id,
            author_id=author_user_id,
        )
        session.add(mention)
        created.append(mention)
        await add_participant(
            session,
            conversation.workspace_id,
            conversation.id,
            user.id,
            reason="mention",
            force=True,
        )
    if not created:
        return []
    await session.flush()

    excerpt = message.content.strip()
    if len(excerpt) > 140:
        excerpt = excerpt[:139].rstrip() + "…"
    for mention in created:
        await notifications.notify(
            session,
            conversation.workspace_id,
            mention.user_id,
            type="mention",
            title=f"{message.author_name or 'Someone'} mentioned you",
            body=excerpt,
            link=f"/inbox/{conversation.id}",
            meta={"conversation_id": conversation.id, "message_id": message.id},
        )
    return created


async def list_mentions(
    session: AsyncSession,
    workspace_id: str,
    user_id: str,
    *,
    unread_only: bool = False,
    limit: int | None = None,
) -> list[Mention]:
    query = select(Mention).where(Mention.workspace_id == workspace_id, Mention.user_id == user_id)
    if unread_only:
        query = query.where(Mention.read_at.is_(None))
    query = query.order_by(Mention.created_at.desc(), Mention.id.desc()).limit(
        clamp_limit(limit, default=50, maximum=200)
    )
    return list((await session.execute(query)).scalars())


async def mark_mentions_read(
    session: AsyncSession, workspace_id: str, user_id: str, conversation_id: str | None = None
) -> int:
    query = select(Mention).where(
        Mention.workspace_id == workspace_id,
        Mention.user_id == user_id,
        Mention.read_at.is_(None),
    )
    if conversation_id is not None:
        query = query.where(Mention.conversation_id == conversation_id)
    rows = list((await session.execute(query)).scalars())
    now = utcnow()
    for mention in rows:
        mention.read_at = now
    await session.flush()
    return len(rows)


async def mention_payload(session: AsyncSession, mentions: list[Mention]) -> list[dict[str, Any]]:
    """Hydrate mentions with just enough conversation context for a list view."""
    if not mentions:
        return []
    conversation_ids = {m.conversation_id for m in mentions}
    conversations = {
        c.id: c
        for c in (
            await session.execute(select(Conversation).where(Conversation.id.in_(conversation_ids)))
        ).scalars()
    }
    message_ids = {m.message_id for m in mentions}
    messages = {
        m.id: m
        for m in (
            await session.execute(select(Message).where(Message.id.in_(message_ids)))
        ).scalars()
    }
    out: list[dict[str, Any]] = []
    for mention in mentions:
        conversation = conversations.get(mention.conversation_id)
        message = messages.get(mention.message_id)
        out.append(
            {
                "id": mention.id,
                "conversation_id": mention.conversation_id,
                "conversation_number": conversation.number if conversation else None,
                "conversation_subject": conversation.subject if conversation else None,
                "message_id": mention.message_id,
                "excerpt": (message.content[:200] if message else ""),
                "author_name": (message.author_name if message else ""),
                "read_at": mention.read_at,
                "created_at": mention.created_at,
            }
        )
    return out


async def assert_conversation(
    session: AsyncSession, workspace_id: str, conversation_id: str
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.workspace_id != workspace_id:
        raise NotFoundError("Conversation not found")
    return conversation
