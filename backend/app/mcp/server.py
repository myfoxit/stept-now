"""The workspace MCP server instance.

Importing this module registers every tool: the ``tools_*`` modules attach
themselves to ``mcp`` via decorators at import time. Keep this module free of
tool bodies — it exists so ``app.main`` and ``app/mcp_stdio.py`` have one thing
to import.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    "Stept",
    instructions=(
        "Stept is the team's customer-support and product-adoption workspace. "
        "Use search_knowledge/ask_knowledge_base for questions, the tour tools for "
        "product walkthroughs, the conversation tools for support threads, and the "
        "browser_* tools to see and drive the user's Chrome (via the Stept extension)."
    ),
)

# Tool registration by import side effect — order is alphabetical, not meaningful.
from app.mcp import tools_browser as _tools_browser  # noqa: E402,F401
from app.mcp import tools_knowledge as _tools_knowledge  # noqa: E402,F401
