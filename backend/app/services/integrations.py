"""Integrations service (BE-A fills; see docs/INTEGRATIONS-CONTRACTS.md).

Catalog+state listing, credential upsert/resolve (workspace row -> env),
connect/reconnect state minting, disconnect with best-effort provider revoke.
Services take (session, workspace_id, actor, ...) and audit mutations.
"""

from __future__ import annotations
