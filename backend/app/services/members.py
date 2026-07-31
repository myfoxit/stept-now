"""Members, invitations, and custom roles."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.events import Actor, Event, EventNames, emit
from app.core.permissions import Perm, is_builtin_role
from app.core.security import new_token
from app.models.user import User
from app.models.workspace import CustomRole, Invitation, Membership
from app.services import audit
from app.services.email import send_email


async def list_members(session: AsyncSession, workspace_id: str) -> list[Membership]:
    result = await session.execute(
        select(Membership)
        .where(Membership.workspace_id == workspace_id)
        .order_by(Membership.created_at)
    )
    return list(result.scalars())


async def _owner_count(session: AsyncSession, workspace_id: str) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.workspace_id == workspace_id, Membership.role == "owner")
        )
    ).scalar_one()


async def _validate_role(
    session: AsyncSession, workspace_id: str, role: str, custom_role_id: str | None
) -> None:
    if role == "custom":
        if not custom_role_id:
            raise BadRequestError("custom_role_id is required for the custom role")
        custom = await session.get(CustomRole, custom_role_id)
        if custom is None or custom.workspace_id != workspace_id:
            raise NotFoundError("Custom role not found")
    elif not is_builtin_role(role):
        raise BadRequestError(f"Unknown role: {role}")


async def update_member(
    session: AsyncSession,
    workspace_id: str,
    member_id: str,
    *,
    actor: Actor,
    role: str | None = None,
    custom_role_id: str | None = None,
    is_available: bool | None = None,
) -> Membership:
    membership = await session.get(Membership, member_id)
    if membership is None or membership.workspace_id != workspace_id:
        raise NotFoundError("Member not found")
    if role is not None:
        await _validate_role(session, workspace_id, role, custom_role_id)
        if (
            membership.role == "owner"
            and role != "owner"
            and await _owner_count(session, workspace_id) <= 1
        ):
            raise ConflictError("Cannot demote the last owner")
        membership.role = role
        membership.custom_role_id = custom_role_id if role == "custom" else None
    if is_available is not None:
        membership.is_available = is_available
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="member.update",
        target_type="membership",
        target_id=membership.id,
        meta={"role": membership.role, "is_available": membership.is_available},
    )
    return membership


async def remove_member(
    session: AsyncSession, workspace_id: str, member_id: str, *, actor: Actor
) -> None:
    membership = await session.get(Membership, member_id)
    if membership is None or membership.workspace_id != workspace_id:
        raise NotFoundError("Member not found")
    if membership.role == "owner" and await _owner_count(session, workspace_id) <= 1:
        raise ConflictError("Cannot remove the last owner")
    await session.delete(membership)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="member.remove",
        target_type="membership",
        target_id=member_id,
        meta={"user_id": membership.user_id},
    )


# ---------------------------------------------------------------------------
# invitations
# ---------------------------------------------------------------------------


async def create_invitation(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    workspace_name: str,
    email: str,
    role: str,
    custom_role_id: str | None = None,
) -> Invitation:
    email = email.strip().lower()
    await _validate_role(session, workspace_id, role, custom_role_id)
    if role == "owner":
        raise BadRequestError("Invite as admin, then promote to owner")

    existing_member = (
        await session.execute(
            select(Membership)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == workspace_id, User.email == email)
        )
    ).scalar_one_or_none()
    if existing_member is not None:
        raise ConflictError("This person is already a member")

    pending = (
        await session.execute(
            select(Invitation).where(
                Invitation.workspace_id == workspace_id,
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
                Invitation.expires_at > utcnow(),
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise ConflictError("An invitation for this email is already pending")

    settings = get_settings()
    invitation = Invitation(
        workspace_id=workspace_id,
        email=email,
        role=role,
        custom_role_id=custom_role_id,
        token=new_token(24),
        invited_by=actor.id,
        expires_at=utcnow() + timedelta(days=settings.invitation_ttl_days),
    )
    session.add(invitation)
    await session.flush()

    link = f"{settings.app_base_url}/accept-invite?token={invitation.token}"
    await send_email(
        email,
        f"You've been invited to {workspace_name} on Stept",
        f"<p>{actor.label or 'A teammate'} invited you to join <b>{workspace_name}</b> "
        f'on Stept.</p><p><a href="{link}">Accept the invitation</a> '
        f"(valid for {settings.invitation_ttl_days} days).</p>",
    )
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="invitation.create",
        target_type="invitation",
        target_id=invitation.id,
        meta={"email": email, "role": role},
    )
    return invitation


async def list_invitations(session: AsyncSession, workspace_id: str) -> list[Invitation]:
    result = await session.execute(
        select(Invitation)
        .where(Invitation.workspace_id == workspace_id, Invitation.accepted_at.is_(None))
        .order_by(Invitation.created_at.desc())
    )
    return list(result.scalars())


async def revoke_invitation(
    session: AsyncSession, workspace_id: str, invitation_id: str, *, actor: Actor
) -> None:
    invitation = await session.get(Invitation, invitation_id)
    if invitation is None or invitation.workspace_id != workspace_id:
        raise NotFoundError("Invitation not found")
    await session.delete(invitation)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="invitation.revoke",
        target_type="invitation",
        target_id=invitation_id,
        meta={"email": invitation.email},
    )


async def accept_invitation(session: AsyncSession, user: User, *, token: str) -> Membership:
    invitation = (
        await session.execute(select(Invitation).where(Invitation.token == token))
    ).scalar_one_or_none()
    if invitation is None or invitation.accepted_at is not None:
        raise NotFoundError("Invitation not found or already used")
    if invitation.expires_at <= utcnow():
        raise BadRequestError("Invitation expired")
    if invitation.email != user.email:
        raise BadRequestError("This invitation was sent to a different email address")

    existing = (
        await session.execute(
            select(Membership).where(
                Membership.workspace_id == invitation.workspace_id,
                Membership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        invitation.accepted_at = utcnow()
        return existing

    membership = Membership(
        workspace_id=invitation.workspace_id,
        user_id=user.id,
        role=invitation.role,
        custom_role_id=invitation.custom_role_id,
    )
    session.add(membership)
    invitation.accepted_at = utcnow()
    await session.flush()
    await emit(
        session,
        Event(
            name=EventNames.MEMBER_JOINED,
            workspace_id=invitation.workspace_id,
            payload={"user_id": user.id, "role": invitation.role},
            actor=Actor(type="user", id=user.id, label=user.name),
        ),
    )
    return membership


# ---------------------------------------------------------------------------
# custom roles
# ---------------------------------------------------------------------------


def _validate_permissions(permissions: list[str]) -> list[str]:
    valid = {p.value for p in Perm}
    unknown = [p for p in permissions if p not in valid]
    if unknown:
        raise BadRequestError(f"Unknown permissions: {', '.join(unknown)}")
    return sorted(set(permissions))


async def create_role(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    description: str | None,
    permissions: list[str],
) -> CustomRole:
    role = CustomRole(
        workspace_id=workspace_id,
        name=name.strip(),
        description=description,
        permissions=_validate_permissions(permissions),
    )
    session.add(role)
    try:
        await session.flush()
    except Exception as exc:
        raise ConflictError("A role with this name already exists") from exc
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="role.create",
        target_type="custom_role",
        target_id=role.id,
        meta={"name": role.name},
    )
    return role


async def list_roles(session: AsyncSession, workspace_id: str) -> list[CustomRole]:
    result = await session.execute(
        select(CustomRole).where(CustomRole.workspace_id == workspace_id).order_by(CustomRole.name)
    )
    return list(result.scalars())


async def update_role(
    session: AsyncSession,
    workspace_id: str,
    role_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    description: str | None = None,
    permissions: list[str] | None = None,
) -> CustomRole:
    role = await session.get(CustomRole, role_id)
    if role is None or role.workspace_id != workspace_id:
        raise NotFoundError("Role not found")
    if name is not None:
        role.name = name.strip()
    if description is not None:
        role.description = description
    if permissions is not None:
        role.permissions = _validate_permissions(permissions)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="role.update",
        target_type="custom_role",
        target_id=role.id,
        meta={"name": role.name},
    )
    return role


async def delete_role(
    session: AsyncSession, workspace_id: str, role_id: str, *, actor: Actor
) -> None:
    role = await session.get(CustomRole, role_id)
    if role is None or role.workspace_id != workspace_id:
        raise NotFoundError("Role not found")
    in_use = (
        await session.execute(
            select(func.count()).select_from(Membership).where(Membership.custom_role_id == role_id)
        )
    ).scalar_one()
    if in_use:
        raise ConflictError("Role is assigned to members; reassign them first")
    await session.delete(role)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="role.delete",
        target_type="custom_role",
        target_id=role_id,
        meta={"name": role.name},
    )
