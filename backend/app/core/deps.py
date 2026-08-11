"""FastAPI dependencies: db session, authentication, workspace membership, RBAC.

Two principal kinds share one interface:
- Users authenticate with a Bearer access JWT.
- Integrations authenticate with a Bearer API key (`sk_stept_…`), workspace-bound.

Routers use `Member = Depends(require_member)` for workspace scoping and
`Depends(require_perm(Perm.X))` for permission checks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import i18n, security
from app.core.db import get_session_factory, utcnow
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.permissions import Perm, resolve_permissions, scopes_to_permissions
from app.models.api_key import ApiKey
from app.models.user import User
from app.models.workspace import Membership, Workspace


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped unit of work: commit on success, rollback on any error."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


Db = Annotated[AsyncSession, Depends(get_db)]


def request_locale(request: Request) -> str:
    """Language for anything this request renders server-side.

    Deliberately auth-free so it also works on the unauthenticated routes that
    send the most important email we have (password reset). Order is explicit
    `?locale=` — used by help-center links and email previews, where the URL
    *is* the choice — then `Accept-Language`, then English.

    Authenticated callers should prefer the stored preference over this:
    ``negotiate(request.headers.get("accept-language"), preferred=user.locale)``.
    """
    return i18n.negotiate(
        request.headers.get("accept-language"),
        preferred=request.query_params.get("locale"),
    )


RequestLocale = Annotated[str, Depends(request_locale)]


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


async def get_current_user(request: Request, session: Db) -> User:
    token = _bearer_token(request)
    if not token:
        raise UnauthorizedError("Missing bearer token")
    if token.startswith(security.API_KEY_PREFIX):
        raise UnauthorizedError("API keys cannot access user endpoints")
    payload = security.decode_token(token, "access")
    user = await session.get(User, payload["sub"])
    if user is None:
        raise UnauthorizedError("Unknown user")
    request.state.principal_id = user.id
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass
class Principal:
    """Authenticated actor inside a workspace."""

    kind: str  # "user" | "api_key"
    workspace: Workspace
    permissions: frozenset[Perm]
    user: User | None = None
    membership: Membership | None = None
    api_key: ApiKey | None = None

    @property
    def actor_id(self) -> str:
        if self.user is not None:
            return self.user.id
        assert self.api_key is not None
        return self.api_key.id

    @property
    def label(self) -> str:
        if self.user is not None:
            return self.user.name
        assert self.api_key is not None
        return f"API key {self.api_key.name}"

    def has(self, perm: Perm) -> bool:
        return perm in self.permissions


async def require_member(
    request: Request,
    session: Db,
    workspace_id: Annotated[str, Path()],
) -> Principal:
    token = _bearer_token(request)
    if not token:
        raise UnauthorizedError("Missing bearer token")

    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise ForbiddenError("No access to this workspace")

    if token.startswith(security.API_KEY_PREFIX):
        hashed = security.hash_api_key(token)
        api_key = (
            await session.execute(select(ApiKey).where(ApiKey.hashed_key == hashed))
        ).scalar_one_or_none()
        if api_key is None or api_key.revoked_at is not None:
            raise UnauthorizedError("Invalid API key")
        # Agent-bound keys are minted for one MCP agent endpoint and nothing
        # else: they carry the workspace's scopes, so accepting them here would
        # silently widen "let Claude talk to this one agent" into full API
        # access.
        if api_key.agent_id is not None:
            raise UnauthorizedError("Agent-bound MCP keys cannot access the REST API")
        if api_key.workspace_id != workspace_id:
            raise ForbiddenError("API key belongs to another workspace")
        api_key.last_used_at = utcnow()
        request.state.principal_id = api_key.id
        return Principal(
            kind="api_key",
            workspace=workspace,
            permissions=scopes_to_permissions(list(api_key.scopes)),
            api_key=api_key,
        )

    payload = security.decode_token(token, "access")
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == payload["sub"],
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise ForbiddenError("No access to this workspace")
    custom_perms = (
        list(membership.custom_role.permissions)
        if membership.role == "custom" and membership.custom_role is not None
        else None
    )
    request.state.principal_id = membership.user_id
    return Principal(
        kind="user",
        workspace=workspace,
        permissions=resolve_permissions(membership.role, custom_perms),
        user=membership.user,
        membership=membership,
    )


Member = Annotated[Principal, Depends(require_member)]


def require_perm(perm: Perm):
    async def checker(principal: Member) -> Principal:
        if not principal.has(perm):
            raise ForbiddenError(f"Requires permission {perm.value}")
        return principal

    return checker
