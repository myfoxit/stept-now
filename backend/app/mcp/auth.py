"""MCP key resolution (both MCP surfaces authenticate with workspace API keys).

The MCP transports execute tools outside FastAPI's dependency graph, so tools
resolve their own auth per call: raw ``sk_stept_…`` bearer key → sha256 lookup
→ :class:`ResolvedMcpKey` (the ApiKey row, its workspace, and the permission
set derived from its scopes). Resolution mirrors ``app.core.deps``: revoked
keys are refused and ``last_used_at`` is bumped on success.

Keys with ``agent_id`` set are AGENT-BOUND: they are only valid on that agent's
``/mcp/agents/{agent_id}`` endpoint, never on the workspace ``/mcp`` surface —
:func:`resolve_key` refuses them; the agent endpoint uses
:func:`resolve_key_for_agent`.

Auth failures inside tools RETURN the standard ``{"error": …}`` payloads
(:func:`authorization_error` / :func:`permission_error`) — they never raise
through the transport, matching the old repo's convention.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import get_session_factory, utcnow
from app.core.events import Actor
from app.core.permissions import Perm, scopes_to_permissions
from app.core.security import API_KEY_PREFIX, hash_api_key
from app.mcp.mount import current_authorization
from app.models.api_key import ApiKey

#: stdio transport has no HTTP headers — the key comes from the environment.
STDIO_KEY_ENV = "STEPT_API_KEY"

#: JSON-RPC error code for a rate-limited MCP request — server-error range,
#: next to the agent endpoint's tool codes (-32010…-32012).
RATE_LIMITED_RPC_CODE = -32013

RATE_LIMITED_MESSAGE = (
    "Rate limit exceeded for this API key — slow down and retry shortly. "
    "Operators tune the ceiling with STEPT_MCP_RATE_LIMIT_PER_MINUTE."
)

#: Set by ``app/mcp_stdio.py`` at startup. The env-var key is only honored when
#: this process IS the stdio bridge (see `request_raw_key`).
_stdio_mode = False


def enable_stdio_mode() -> None:
    """Called by the stdio entry point before the server starts serving."""
    global _stdio_mode
    _stdio_mode = True


def stdio_mode() -> bool:
    return _stdio_mode


@dataclass
class ResolvedMcpKey:
    """An authenticated MCP caller: the key row, its workspace, its permissions."""

    api_key: ApiKey
    workspace_id: str
    permissions: frozenset[Perm]

    def has(self, perm: Perm) -> bool:
        return perm in self.permissions

    @property
    def actor(self) -> Actor:
        return Actor(type="api_key", id=self.api_key.id, label=self.actor_label)

    @property
    def actor_label(self) -> str:
        return f"API key {self.api_key.name}"


async def _lookup(session: AsyncSession, raw: str | None) -> ApiKey | None:
    """Shared key lookup: prefix check, sha256 match, revoked → None, bumps
    ``last_used_at`` (persisted when the caller's session commits)."""
    if not raw or not raw.startswith(API_KEY_PREFIX):
        return None
    api_key = (
        await session.execute(select(ApiKey).where(ApiKey.hashed_key == hash_api_key(raw)))
    ).scalar_one_or_none()
    if api_key is None or api_key.revoked_at is not None:
        return None
    api_key.last_used_at = utcnow()
    return api_key


def _resolved(api_key: ApiKey) -> ResolvedMcpKey:
    return ResolvedMcpKey(
        api_key=api_key,
        workspace_id=api_key.workspace_id,
        permissions=scopes_to_permissions(list(api_key.scopes)),
    )


async def resolve_key(session: AsyncSession, raw: str | None) -> ResolvedMcpKey | None:
    """Resolve a raw key for the workspace ``/mcp`` surface.

    Agent-bound keys (``agent_id`` set) are refused here — they only work on
    their own agent endpoint.
    """
    api_key = await _lookup(session, raw)
    if api_key is None or api_key.agent_id is not None:
        return None
    return _resolved(api_key)


async def resolve_key_for_agent(
    session: AsyncSession, raw: str | None, agent_id: str
) -> ResolvedMcpKey | None:
    """Resolve a raw key for ``/mcp/agents/{agent_id}``: a workspace key, or a
    key bound to exactly this agent (anti-replay across agents)."""
    api_key = await _lookup(session, raw)
    if api_key is None:
        return None
    if api_key.agent_id is not None and api_key.agent_id != agent_id:
        return None
    return _resolved(api_key)


def request_raw_key() -> str | None:
    """The raw key of the MCP request being served.

    Over HTTP that is the ``Authorization`` header captured by the mount shim,
    and ONLY that. The ``STEPT_API_KEY`` fallback exists because the stdio
    transport has no headers, and it is gated on actually running under stdio:
    an operator who exports that variable for the API process (our own docs tell
    them to export it for stdio) would otherwise turn every unauthenticated
    request to ``/mcp`` into a fully-scoped one.
    """
    header = current_authorization()
    if header and header.lower().startswith("bearer "):
        return header[7:].strip() or None
    if stdio_mode():
        return os.environ.get(STDIO_KEY_ENV) or None
    return None


async def resolve_request_key(session: AsyncSession) -> ResolvedMcpKey | None:
    """Resolve the current MCP request's key for the workspace surface."""
    return await resolve_key(session, request_raw_key())


async def consume_rate_limit(raw_key: str | None) -> bool:
    """Spend one MCP call from the per-key window; True = allowed.

    Both HTTP surfaces call this once per bearer-carrying request (the /mcp
    mount shim and the /mcp/agents/{id} route), so a leaked key cannot turn
    ``ask_knowledge_base`` into unmetered LLM spend. The bucket keys on the
    hashed bearer — one bucket per physical key across both surfaces, whether
    or not the key is valid. Reuses ``app.core.ratelimit``'s window primitives
    (its private functions on purpose: the public ``RateLimit`` is a FastAPI
    dependency wanting a Request); without Redis the window is per-process,
    which that module's docstring accepts explicitly. stdio is exempt — it has
    no headers and only serves the operator's own machine.
    """
    settings = get_settings()
    limit = settings.mcp_rate_limit_per_minute
    if limit <= 0 or not settings.rate_limit_enabled or not raw_key:
        return True
    bucket = f"rl:mcp:{hash_api_key(raw_key)[:32]}"
    if settings.redis_url:
        return await ratelimit._check_redis(bucket, limit, 60.0)
    return ratelimit._check_memory(bucket, limit, 60.0)


def authorization_error() -> dict[str, str]:
    return {"error": "Authentication required. Provide a valid API key."}


def permission_error(perm: Perm) -> dict[str, str]:
    return {
        "error": (
            f"This API key lacks the {perm.value} permission — "
            "mint one with the write scope in Settings → MCP."
        )
    }


@contextlib.asynccontextmanager
async def open_session() -> AsyncIterator[AsyncSession]:
    """Tool-scoped unit of work (mirrors ``deps.get_db``): commit on success,
    rollback on any error."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
