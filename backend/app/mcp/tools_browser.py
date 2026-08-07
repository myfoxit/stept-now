"""Remote browser-drive MCP tools (Chrome extension over the WS gateway).

Every tool resolves the caller's workspace API key through ``app.mcp.auth``,
requires the ``tours:manage`` permission, and delegates to the
``app.services.remote_drive`` gateway, which routes the op to whichever API
worker holds the user's signed-in Chrome extension socket (``/ws/extension``)
and awaits the ack.

Auth failures return ``{"error": …}`` payloads — never exceptions through the
transport. Tool docstrings double as the LLM-facing descriptions: they teach
the ``[index]`` convention, the screenshot coordinate space, and the
record / run flows.

``app.mcp.auth`` is built by a parallel wave agent; it is accessed late-bound
(via :func:`_auth_module`) so this module imports cleanly either way and tests
can monkeypatch the seam.
"""

from __future__ import annotations

import inspect
from typing import Any

from app.core.permissions import Perm
from app.mcp.server import mcp
from app.services import remote_drive

ACT_KINDS = (
    "click",
    "double-click",
    "right-click",
    "hover",
    "type",
    "select",
    "check",
    "uncheck",
    "drag",
)
EXTRACT_KINDS = ("text", "attr", "url")
SCROLL_DIRS = ("up", "down")

# Interactive-elements listing cap (chars) — contract-fixed, line-safe truncation.
ELEMENTS_CAP = 14000


def _auth_module() -> Any:
    """Late-bound ``app.mcp.auth`` (the monkeypatch seam for tests)."""
    from app.mcp import auth

    return auth


async def _drive_auth() -> str | dict[str, Any]:
    """Resolve the MCP caller and require ``tours:manage``.

    Returns the workspace_id when authorized, else the standard ``{"error": …}``
    payload to hand straight back to the client.
    """
    auth = _auth_module()
    async with auth.open_session() as session:
        resolved = auth.resolve_request_key(session)
        if inspect.isawaitable(resolved):
            resolved = await resolved
    if resolved is None:
        error: dict[str, Any] = auth.authorization_error()
        return error
    if Perm.TOURS_MANAGE not in resolved.permissions:
        error = auth.permission_error(Perm.TOURS_MANAGE)
        return error
    return str(resolved.workspace_id)


def _truncate_lines(text: str, cap: int = ELEMENTS_CAP) -> str:
    """Cap text on a line boundary so the LLM never sees half an element row."""
    if len(text) <= cap:
        return text
    cut = text.rfind("\n", 0, cap + 1)
    return text[:cut] if cut > 0 else text[:cap]


def _snapshot_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Shape a drive-op ack for the LLM (old-stept ``_snapshot_payload`` parity):
    numbered interactive elements + screenshot as base64 (clients render
    data-URI images inline) + the coordinate space that screenshot defines."""
    data = result.get("data") or {}
    if not isinstance(data, dict):
        return {"data": data}
    out: dict[str, Any] = {
        "url": data.get("url"),
        "interactive_elements": _truncate_lines(str(data.get("elements") or "")),
        "element_count": data.get("count", 0),
    }
    if data.get("note"):
        out["note"] = data["note"]
    if data.get("screenshot"):
        size = data.get("screenshotSize") or {}
        out["screenshot_base64_jpeg"] = data["screenshot"]
        out["screenshot_size"] = size
        out["coordinate_space"] = (
            f"screenshot is {size.get('w')}x{size.get('h')}px; "
            "for coordinate clicks pass x,y in this pixel space (0,0 = top-left)"
        )
    return out


async def _exec(op: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Auth-gate, then run one drive op through the gateway."""
    gate = await _drive_auth()
    if isinstance(gate, dict):
        return gate
    return await remote_drive.gateway.exec_op(gate, op, args or {})


async def _exec_snapshot(op: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a drive op and shape the resulting snapshot."""
    result = await _exec(op, args)
    if "error" in result:
        return result
    return _snapshot_payload(result)


def _data(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") or {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# session tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def browser_list() -> dict[str, Any]:
    """List the browsers connected to this workspace through the Stept Chrome
    extension (one entry per signed-in side panel). The other browser_* tools
    automatically drive the most recently seen browser; an empty list means
    nobody has the extension connected right now."""
    gate = await _drive_auth()
    if isinstance(gate, dict):
        return gate
    browsers = await remote_drive.gateway.list_browsers(gate)
    out: dict[str, Any] = {"browsers": browsers, "count": len(browsers)}
    if not browsers:
        out["note"] = remote_drive.NO_BROWSER_ERROR
    return out


@mcp.tool()
async def browser_open(url: str) -> dict[str, Any]:
    """Open a new tab in the user's logged-in Chrome (via the Stept extension)
    and start a drive session. Returns a fresh snapshot: the page's interactive
    elements as a numbered list — each line starts with an [index] you pass to
    browser_act — plus a viewport screenshot whose pixel space is used for
    coordinate clicks. Drive the page with browser_act / browser_navigate /
    browser_scroll / browser_key, read it with browser_page_text /
    browser_find / browser_extract, and end the session with browser_close."""
    return await _exec_snapshot("open", {"url": url})


@mcp.tool()
async def browser_snapshot() -> dict[str, Any]:
    """Take a fresh snapshot of the driven tab: interactive elements as
    [index]-numbered lines (the handles browser_act targets) plus a screenshot.
    Element indexes go stale after navigation or page changes — re-snapshot
    before acting if the page may have moved under you."""
    return await _exec_snapshot("snapshot")


@mcp.tool()
async def browser_act(
    index: int | None = None,
    kind: str = "click",
    text: str | None = None,
    submit: bool = False,
    x: float | None = None,
    y: float | None = None,
) -> dict[str, Any]:
    """Act on the driven tab, then return a fresh snapshot. Target an element
    by its [index] from the latest snapshot (preferred) or by raw screenshot
    pixels x,y in the snapshot's coordinate space. kind: click (default),
    double-click, right-click, hover, type (needs text; submit=True also
    presses Enter), select (text = the option's label), check, uncheck,
    drag."""
    if kind not in ACT_KINDS:
        return {"error": f"kind must be one of: {', '.join(ACT_KINDS)}"}
    args: dict[str, Any] = {"kind": kind, "submit": submit}
    if index is not None:
        args["index"] = index
    if text is not None:
        args["text"] = text
    if x is not None:
        args["x"] = x
    if y is not None:
        args["y"] = y
    return await _exec_snapshot("act", args)


@mcp.tool()
async def browser_navigate(url: str) -> dict[str, Any]:
    """Navigate the driven tab to a URL; returns a fresh snapshot (element
    indexes from before the navigation are no longer valid)."""
    return await _exec_snapshot("navigate", {"url": url})


@mcp.tool()
async def browser_scroll(dir: str = "down", amount: int = 600) -> dict[str, Any]:
    """Scroll the driven tab up or down by `amount` CSS pixels (default 600);
    returns a fresh snapshot of what is now in view."""
    if dir not in SCROLL_DIRS:
        return {"error": "dir must be 'up' or 'down'"}
    return await _exec_snapshot("scroll", {"dir": dir, "amount": amount})


@mcp.tool()
async def browser_key(key: str) -> dict[str, Any]:
    """Press a key on the driven tab — Enter, Tab, Escape, ArrowDown, or a
    chord like 'Meta+a' — and return a fresh snapshot."""
    return await _exec_snapshot("key", {"key": key})


@mcp.tool()
async def browser_page_text(max_chars: int | None = None) -> dict[str, Any]:
    """Read the driven tab's visible text content (a readability-style
    extraction, not raw HTML). Use it to read articles or long pages that the
    element listing does not capture. max_chars caps the returned text."""
    args: dict[str, Any] = {}
    if max_chars is not None:
        args["maxChars"] = max_chars
    result = await _exec("page-text", args)
    if "error" in result:
        return result
    data = _data(result)
    out: dict[str, Any] = {"url": data.get("url"), "page_text": data.get("pageText") or ""}
    if data.get("note"):
        out["note"] = data["note"]
    return out


@mcp.tool()
async def browser_find(query: str, limit: int = 10) -> dict[str, Any]:
    """Find elements on the driven tab by visible text or accessible name.
    Each match carries the [index] you can pass straight to browser_act, plus
    its text, tag and visibility. Cheaper than reading a full snapshot when
    you already know what you are looking for."""
    result = await _exec("find", {"query": query, "limit": limit})
    if "error" in result:
        return result
    data = _data(result)
    return {"url": data.get("url"), "found": data.get("found") or []}


@mcp.tool()
async def browser_console(pattern: str | None = None, limit: int = 40) -> dict[str, Any]:
    """Read the driven tab's recent console messages (errors, warnings, logs),
    newest last. pattern filters by substring/regex; limit caps the rows.
    Useful for debugging why a page misbehaves."""
    args: dict[str, Any] = {"limit": limit}
    if pattern is not None:
        args["pattern"] = pattern
    result = await _exec("console", args)
    if "error" in result:
        return result
    data = _data(result)
    return {"url": data.get("url"), "console": data.get("console") or []}


@mcp.tool()
async def browser_network(pattern: str | None = None, limit: int = 40) -> dict[str, Any]:
    """Read the driven tab's recent network requests (method, URL, status),
    newest last. pattern filters by URL substring/regex; limit caps the rows."""
    args: dict[str, Any] = {"limit": limit}
    if pattern is not None:
        args["pattern"] = pattern
    result = await _exec("network", args)
    if "error" in result:
        return result
    data = _data(result)
    return {"url": data.get("url"), "network": data.get("network") or []}


@mcp.tool()
async def browser_extract(
    index: int, kind: str = "text", attr: str | None = None
) -> dict[str, Any]:
    """Extract a value from one element of the driven tab by its [index] from
    the latest snapshot — e.g. a freshly generated key shown once. kind: text
    (default), attr (needs attr, e.g. 'href' or 'value'), url."""
    if kind not in EXTRACT_KINDS:
        return {"error": f"kind must be one of: {', '.join(EXTRACT_KINDS)}"}
    if kind == "attr" and not attr:
        return {"error": "attr is required when kind is 'attr' (e.g. attr='href')"}
    args: dict[str, Any] = {"index": index, "extractKind": kind}
    if attr is not None:
        args["attr"] = attr
    result = await _exec("extract", args)
    if "error" in result:
        return result
    data = _data(result)
    return {"url": data.get("url"), "extracted": data.get("extracted")}


@mcp.tool()
async def browser_close() -> dict[str, Any]:
    """End the drive session and close the driven tab in the user's browser.
    Call it when you are done so the user gets their browser back."""
    result = await _exec("close")
    if "error" in result:
        return result
    out: dict[str, Any] = {"ok": True}
    note = _data(result).get("note")
    if note:
        out["note"] = note
    return out


# ---------------------------------------------------------------------------
# recording + driven playback
# ---------------------------------------------------------------------------


@mcp.tool()
async def browser_record_start(url: str | None = None) -> dict[str, Any]:
    """Arm the Stept tour recorder in the user's connected browser (optionally
    opening url first). Every click, keystroke and navigation that follows is
    captured — performed by the user OR by you through the browser_* drive
    tools. Finish with browser_record_stop to save the capture as a draft
    product tour."""
    gate = await _drive_auth()
    if isinstance(gate, dict):
        return gate
    result = await remote_drive.gateway.record_start(gate, url=url)
    if "error" in result:
        return result
    return {
        **result,
        "note": (
            "Recording armed. Perform the task in the connected browser (or drive it "
            "with the browser_* tools), then call browser_record_stop with a title."
        ),
    }


@mcp.tool()
async def browser_record_stop(title: str, description: str | None = None) -> dict[str, Any]:
    """Stop the active recording and save it as a draft tour in Stept. Returns
    the new tour_id (and the captured event count); the user reviews and
    publishes the draft in the Stept dashboard."""
    gate = await _drive_auth()
    if isinstance(gate, dict):
        return gate
    return await remote_drive.gateway.record_stop(gate, title, description=description)


@mcp.tool()
async def browser_run_tour(tour_id: str) -> dict[str, Any]:
    """Play an existing Stept tour in DRIVEN mode in the user's connected
    browser: the extension performs each step live so the user can watch the
    flow run end-to-end. Long-running — waits up to 15 minutes and returns
    {status: completed|failed|cancelled}. Get tour ids from list_tours."""
    gate = await _drive_auth()
    if isinstance(gate, dict):
        return gate
    return await remote_drive.gateway.run_tour(gate, tour_id)
