"""MCP key resolution — FILLED BY WAVE AGENT BE-A1 (see docs/MCP-CONTRACTS.md).

Contract: ``resolve_key(raw_or_none) -> ResolvedMcpKey | None`` where the
resolved object carries the ApiKey row, workspace_id and the permission set;
``authorization_error()`` / ``permission_error(perm)`` return the standard
``{"error": …}`` payloads tools respond with.
"""

from __future__ import annotations
