"""ASGI glue for the workspace MCP server (validated by prototype — see
docs/MCP-CONTRACTS.md).

The streamable-HTTP transport executes tools outside FastAPI's dependency
graph, so the request's ``Authorization`` header is captured into a contextvar
by an ASGI shim wrapping the ``/mcp`` mount. Tools read it via
``current_authorization()``. Two hard-won constraints:

- Modern Starlette strips the mount prefix via ``root_path`` — the shim must
  forward the scope UNTOUCHED (rewriting ``path``/``root_path`` 404s the inner
  router).
- A bare ``POST /mcp`` gets a 307 from the outer ``Mount``; ``app/main.py``
  rewrites it to ``/mcp/`` in middleware because MCP clients don't reliably
  follow redirects.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import AsyncIterator
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_authorization: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mcp_authorization", default=None
)


def current_authorization() -> str | None:
    """The raw ``Authorization`` header of the MCP request being served."""
    return _authorization.get()


class McpAuthShim:
    """Capture the Authorization header, rate-limit per key, then forward the
    scope untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            auth = next(
                (
                    value.decode("latin-1")
                    for key, value in scope.get("headers") or []
                    if key == b"authorization"
                ),
                None,
            )
            _authorization.set(auth)
            if not await _rate_limit_allows():
                # A valid JSON-RPC error body (id unknowable pre-parse ⇒ null)
                # on a 429 — never a naked 500. MCP clients surface the message
                # and back off on the status.
                from app.mcp.auth import RATE_LIMITED_MESSAGE, RATE_LIMITED_RPC_CODE

                response = JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": RATE_LIMITED_RPC_CODE,
                            "message": RATE_LIMITED_MESSAGE,
                        },
                    },
                    status_code=429,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def _rate_limit_allows() -> bool:
    """Per-key limit for the mounted surface, keyed on the just-captured header.

    Imports lazily: ``app.mcp.auth`` imports this module for the contextvar, so
    the module-level dependency must only ever point this way.
    """
    from app.mcp.auth import consume_rate_limit, request_raw_key

    return await consume_rate_limit(request_raw_key())


def build_inner_app() -> Any:
    """The SDK's streamable-HTTP Starlette app, configured for our mount.

    DNS-rebinding protection is off on purpose: every tool call is bearer-key
    authenticated and the app runs behind the edge proxy, which owns Host.
    """
    from app.mcp.server import mcp

    return mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


@contextlib.asynccontextmanager
async def run_session_manager(inner: Any) -> AsyncIterator[None]:
    """Run the mounted app's lifespan (mounted lifespans don't run on their own)."""
    async with inner.router.lifespan_context(inner):
        yield
