"""Standalone stdio entry point for the Stept MCP server.

Run from ``backend/``:  ``STEPT_API_KEY=sk_stept_… uv run python -m app.mcp_stdio``
Auth comes from the ``STEPT_API_KEY`` environment variable (there are no HTTP
headers on stdio). Logs go to stderr so they never corrupt the JSON-RPC stream.
"""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(
    level=logging.DEBUG if os.environ.get("MCP_DEBUG") else logging.INFO,
    format="%(asctime)s [MCP] %(message)s",
    stream=sys.stderr,
)

from app.mcp.server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run(transport="stdio")
