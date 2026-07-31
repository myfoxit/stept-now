"""RBAC catalog: permissions, builtin roles, and resolution.

Builtin roles (owner > admin > agent > viewer) cover most teams; custom roles
(`CustomRole` rows) carry an explicit permission list for enterprise setups.
"""

from __future__ import annotations

from enum import StrEnum


class Perm(StrEnum):
    CONVERSATIONS_READ = "conversations:read"
    CONVERSATIONS_WRITE = "conversations:write"
    CONVERSATIONS_MANAGE = "conversations:manage"  # assign, resolve, snooze, priority
    CONTACTS_READ = "contacts:read"
    CONTACTS_WRITE = "contacts:write"
    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_WRITE = "knowledge:write"
    AI_READ = "ai:read"
    AI_MANAGE = "ai:manage"  # providers, models, agents config
    AI_APPROVE = "ai:approve"  # decide agent approval requests
    AUTOMATIONS_READ = "automations:read"
    AUTOMATIONS_MANAGE = "automations:manage"
    TOURS_READ = "tours:read"
    TOURS_MANAGE = "tours:manage"
    REPORTS_READ = "reports:read"
    CHANNELS_MANAGE = "channels:manage"
    MEMBERS_MANAGE = "members:manage"
    ROLES_MANAGE = "roles:manage"
    APIKEYS_MANAGE = "apikeys:manage"
    WEBHOOKS_MANAGE = "webhooks:manage"
    WORKSPACE_MANAGE = "workspace:manage"
    WORKSPACE_DELETE = "workspace:delete"
    AUDIT_READ = "audit:read"


ALL_PERMS: frozenset[Perm] = frozenset(Perm)

_READ_PERMS: frozenset[Perm] = frozenset(p for p in Perm if p.value.endswith(":read"))

BUILTIN_ROLES: dict[str, frozenset[Perm]] = {
    "owner": ALL_PERMS,
    "admin": ALL_PERMS - {Perm.WORKSPACE_DELETE},
    "agent": _READ_PERMS
    | {
        Perm.CONVERSATIONS_WRITE,
        Perm.CONVERSATIONS_MANAGE,
        Perm.CONTACTS_WRITE,
        Perm.AI_APPROVE,
    },
    "viewer": _READ_PERMS,
}

# Scopes attachable to API keys; "admin" intentionally excludes workspace deletion.
API_KEY_SCOPES: dict[str, frozenset[Perm]] = {
    "read": _READ_PERMS,
    "write": _READ_PERMS
    | {
        Perm.CONVERSATIONS_WRITE,
        Perm.CONVERSATIONS_MANAGE,
        Perm.CONTACTS_WRITE,
        Perm.KNOWLEDGE_WRITE,
        Perm.TOURS_MANAGE,
    },
    "admin": ALL_PERMS - {Perm.WORKSPACE_DELETE},
}


def is_builtin_role(role: str) -> bool:
    return role in BUILTIN_ROLES


def resolve_permissions(role: str, custom_permissions: list[str] | None = None) -> frozenset[Perm]:
    """Permissions for a membership: builtin matrix, or the custom role's list."""
    if role in BUILTIN_ROLES:
        return BUILTIN_ROLES[role]
    if custom_permissions is None:
        return frozenset()
    return frozenset(Perm(p) for p in custom_permissions if p in Perm._value2member_map_)


def scopes_to_permissions(scopes: list[str]) -> frozenset[Perm]:
    perms: set[Perm] = set()
    for scope in scopes:
        perms |= API_KEY_SCOPES.get(scope, frozenset())
    return frozenset(perms)
