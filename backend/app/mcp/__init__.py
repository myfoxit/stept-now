"""MCP (Model Context Protocol) surface.

Two inbound servers, one auth model:

- ``/mcp`` — workspace server (SDK v2 ``MCPServer``, streamable HTTP, stateless,
  JSON responses). Tools live in ``tools_knowledge.py`` / ``tools_browser.py``
  and register themselves on import via the ``@mcp.tool()`` decorator.
- ``/mcp/agents/{agent_id}`` — one AI agent exposed as a channel to external LLM
  clients (hand-rolled JSON-RPC in ``agent_endpoint.py``; the tool list is
  computed per request from the agent's config, which the SDK can't do).

Both authenticate with workspace API keys (``sk_stept_…``) sent as
``Authorization: Bearer`` — see ``auth.py``. The ASGI glue that makes headers
reachable from SDK tools is in ``mount.py``; ``app/main.py`` wires it.
"""
