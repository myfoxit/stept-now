"""Workspace + membership schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel
from app.schemas.user import UserOut


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    logo_url: str | None = Field(None, max_length=500)
    settings: dict[str, Any] | None = None


class WorkspaceOut(ORMModel):
    id: str
    name: str
    slug: str
    logo_url: str | None = None
    settings: dict[str, Any]
    created_at: datetime


class MembershipOut(ORMModel):
    id: str
    workspace_id: str
    role: str
    custom_role_id: str | None = None
    is_available: bool
    user: UserOut
    created_at: datetime


class MyMembership(BaseModel):
    """Membership as seen in GET /me — includes resolved permissions + workspace."""

    id: str
    role: str
    custom_role_id: str | None = None
    is_available: bool
    workspace: WorkspaceOut
    permissions: list[str]


class MeResponse(BaseModel):
    user: UserOut
    memberships: list[MyMembership]


class MemberUpdate(BaseModel):
    role: str | None = None
    custom_role_id: str | None = None
    is_available: bool | None = None


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = "agent"
    custom_role_id: str | None = None


class InvitationOut(ORMModel):
    id: str
    email: EmailStr
    role: str
    custom_role_id: str | None = None
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime


class InvitationAccept(BaseModel):
    token: str


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=400)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=400)
    permissions: list[str] | None = None


class RoleOut(ORMModel):
    id: str
    name: str
    description: str | None = None
    permissions: list[str]
    created_at: datetime


class PermissionCatalogOut(BaseModel):
    permissions: list[str]
    builtin_roles: dict[str, list[str]]
