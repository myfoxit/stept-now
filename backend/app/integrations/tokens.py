"""Connection/token seam (BE-A fills; see docs/INTEGRATIONS-CONTRACTS.md).

Contract consumed by app.channels.email_transports / email_sync and
app.rag.connectors (their tests monkeypatch these):

    async def get_connection(session, workspace_id, connection_id) -> IntegrationConnection
    async def get_valid_access_token(session, connection) -> str
"""

from __future__ import annotations
