"""browser_* MCP tools: auth + permission gates, server-side arg validation,
snapshot shaping (14k line-safe cap, coordinate space), record/run flows —
direct calls through the auth seam, plus JSON-RPC tools/call against the
mounted /mcp app with real workspace API keys."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.mcp import tools_browser
from app.services.remote_drive import NO_BROWSER_ERROR, gateway
from tests.remote_drive.conftest import DEFAULT_SNAPSHOT, register_auto_peer

# ---------------------------------------------------------------------------
# direct calls (auth seam)
# ---------------------------------------------------------------------------


async def test_unauthenticated_caller_gets_auth_error(mcp_caller):
    mcp_caller(None)
    payload = await tools_browser.browser_list()
    assert payload == {"error": "Authentication required. Provide a valid API key."}


async def test_read_scope_lacks_tours_manage(mcp_caller):
    mcp_caller("ws-1", scopes=["read"])
    payload = await tools_browser.browser_snapshot()
    assert "tours:manage" in payload["error"]


async def test_no_browser_connected_is_a_friendly_error(mcp_caller):
    mcp_caller("ws-1", scopes=["write"])
    payload = await tools_browser.browser_snapshot()
    assert payload == {"error": NO_BROWSER_ERROR}


async def test_browser_list_empty_notes_how_to_connect(mcp_caller):
    mcp_caller("ws-1", scopes=["write"])
    payload = await tools_browser.browser_list()
    assert payload["browsers"] == []
    assert payload["count"] == 0
    assert payload["note"] == NO_BROWSER_ERROR


async def test_act_validates_kind_without_a_round_trip(mcp_caller):
    mcp_caller("ws-1", scopes=["write"])  # deliberately no browser registered
    payload = await tools_browser.browser_act(kind="explode")
    assert "kind must be one of" in payload["error"]
    assert "double-click" in payload["error"]
    # A bad enum short-circuits BEFORE device pick — not the no-browser error.
    assert payload["error"] != NO_BROWSER_ERROR


async def test_scroll_validates_dir(mcp_caller):
    mcp_caller("ws-1", scopes=["write"])
    payload = await tools_browser.browser_scroll(dir="sideways")
    assert payload == {"error": "dir must be 'up' or 'down'"}


async def test_extract_validates_kind_and_attr(mcp_caller):
    mcp_caller("ws-1", scopes=["write"])
    bad_kind = await tools_browser.browser_extract(index=1, kind="html")
    assert "kind must be one of" in bad_kind["error"]
    missing_attr = await tools_browser.browser_extract(index=1, kind="attr")
    assert "attr is required" in missing_attr["error"]


async def test_open_returns_shaped_snapshot_with_coordinate_space(mcp_caller):
    mcp_caller("ws-1", scopes=["write"])
    data = {
        "url": "https://app.example/settings",
        "elements": "[0]<button Create key>\n[1]<a Members>",
        "count": 2,
        "screenshot": "aGVsbG8=",
        "screenshotSize": {"w": 1200, "h": 800},
        "note": "opened in a new tab",
    }
    _, sent = await register_auto_peer("ws-1", data=data)

    payload = await tools_browser.browser_open(url="https://app.example/settings")
    assert sent[0]["op"] == "open"
    assert sent[0]["args"] == {"url": "https://app.example/settings"}
    assert payload["url"] == "https://app.example/settings"
    assert payload["interactive_elements"] == data["elements"]
    assert payload["element_count"] == 2
    assert payload["note"] == "opened in a new tab"
    assert payload["screenshot_base64_jpeg"] == "aGVsbG8="
    assert payload["screenshot_size"] == {"w": 1200, "h": 800}
    assert "1200x800px" in payload["coordinate_space"]
    assert "x,y in this pixel space" in payload["coordinate_space"]


async def test_act_forwards_targeting_args_and_omits_screenshot_keys(mcp_caller):
    mcp_caller("ws-1", scopes=["write"])
    _, sent = await register_auto_peer("ws-1")
    payload = await tools_browser.browser_act(index=3, kind="type", text="hello", submit=True)
    assert sent[0]["args"] == {"kind": "type", "submit": True, "index": 3, "text": "hello"}
    assert payload["element_count"] == DEFAULT_SNAPSHOT["count"]
    assert "screenshot_base64_jpeg" not in payload
    assert "coordinate_space" not in payload


async def test_interactive_elements_truncate_on_a_line_boundary(mcp_caller):
    lines = [f"[{i}]<button Item {i} {'x' * 20}>" for i in range(600)]
    elements = "\n".join(lines)
    assert len(elements) > 14000
    mcp_caller("ws-1", scopes=["write"])
    await register_auto_peer("ws-1", data={"url": "u", "elements": elements, "count": 600})

    payload = await tools_browser.browser_snapshot()
    text = payload["interactive_elements"]
    assert len(text) <= 14000
    assert elements.startswith(text)  # pure prefix — nothing rewritten
    assert elements[len(text)] == "\n"  # the cut landed exactly on a line boundary
    assert text.rsplit("\n", 1)[-1] in lines  # last line survived whole
    assert payload["element_count"] == 600  # the model still sees the true count


async def test_reader_tools_shape_their_payloads(mcp_caller):
    mcp_caller("ws-1", scopes=["write"])
    data = {
        "url": "https://app.example/article",
        "elements": "",
        "count": 0,
        "pageText": "Long article body",
        "found": [{"index": 4, "text": "Create key", "tag": "button", "visible": True}],
        "extracted": {"kind": "attr", "value": "https://x/download"},
        "console": [{"level": "error", "text": "boom", "t": 1}],
        "network": [{"method": "GET", "url": "https://x/api", "status": 500, "t": 2}],
    }
    _, sent = await register_auto_peer("ws-1", data=data)

    text = await tools_browser.browser_page_text(max_chars=500)
    assert text == {"url": data["url"], "page_text": "Long article body"}
    assert sent[0]["op"] == "page-text"
    assert sent[0]["args"] == {"maxChars": 500}

    found = await tools_browser.browser_find(query="create", limit=5)
    assert found["found"][0]["index"] == 4
    assert sent[1]["args"] == {"query": "create", "limit": 5}

    console = await tools_browser.browser_console(pattern="boom")
    assert console["console"][0]["level"] == "error"
    assert sent[2]["args"] == {"limit": 40, "pattern": "boom"}

    network = await tools_browser.browser_network()
    assert network["network"][0]["status"] == 500
    assert sent[3]["args"] == {"limit": 40}

    extracted = await tools_browser.browser_extract(index=4, kind="attr", attr="href")
    assert extracted["extracted"] == {"kind": "attr", "value": "https://x/download"}
    assert sent[4]["args"] == {"index": 4, "extractKind": "attr", "attr": "href"}

    closed = await tools_browser.browser_close()
    assert closed["ok"] is True
    assert sent[5]["op"] == "close"


async def test_record_flow_and_run_tour(mcp_caller):
    mcp_caller("ws-1", scopes=["write"])
    _, sent = await register_auto_peer(
        "ws-1", record_stop_ack={"tour_id": "tour-9", "event_count": 3}
    )

    started = await tools_browser.browser_record_start(url="https://app.example")
    assert started["ok"] is True
    assert started["recording"] is True
    assert "browser_record_stop" in started["note"]
    assert sent[0]["type"] == "record-start"
    assert sent[0]["url"] == "https://app.example"

    stopped = await tools_browser.browser_record_stop(title="Checkout", description="pay flow")
    assert stopped["ok"] is True
    assert stopped["tour_id"] == "tour-9"
    assert stopped["event_count"] == 3
    assert sent[1]["type"] == "record-stop"
    assert sent[1]["title"] == "Checkout"
    assert sent[1]["description"] == "pay flow"

    run = await tools_browser.browser_run_tour(tour_id="tour-9")
    assert run == {"status": "completed"}
    assert sent[2]["type"] == "run-tour"
    assert sent[2]["mode"] == "driven"


# ---------------------------------------------------------------------------
# JSON-RPC through the mounted /mcp app (real API keys, real auth)
# ---------------------------------------------------------------------------

BROWSER_TOOL_NAMES = {
    "browser_list",
    "browser_open",
    "browser_snapshot",
    "browser_act",
    "browser_navigate",
    "browser_scroll",
    "browser_key",
    "browser_page_text",
    "browser_find",
    "browser_console",
    "browser_network",
    "browser_extract",
    "browser_close",
    "browser_record_start",
    "browser_record_stop",
    "browser_run_tour",
}


async def _mint_key(client: httpx.AsyncClient, workspace_ctx, scopes: list[str]) -> str:
    resp = await client.post(
        f"{workspace_ctx.base}/api-keys",
        json={"name": f"mcp {'+'.join(scopes)}", "scopes": scopes},
        headers=workspace_ctx.owner_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]


async def _rpc(client: httpx.AsyncClient, key: str, method: str, params: dict[str, Any]) -> dict:
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("error") is None, body
    return body["result"]


async def _call_tool(
    client: httpx.AsyncClient, key: str, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = await _rpc(client, key, "tools/call", {"name": name, "arguments": arguments or {}})
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    return json.loads(result["content"][0]["text"])


async def test_rpc_lists_every_browser_tool(client, workspace_ctx):
    key = await _mint_key(client, workspace_ctx, ["read"])
    result = await _rpc(client, key, "tools/list", {})
    names = {tool["name"] for tool in result["tools"]}
    assert BROWSER_TOOL_NAMES <= names


async def test_rpc_read_scope_key_gets_permission_error(client, workspace_ctx):
    key = await _mint_key(client, workspace_ctx, ["read"])
    payload = await _call_tool(client, key, "browser_list")
    assert "tours:manage" in payload["error"]
    assert "write scope" in payload["error"]


async def test_rpc_bogus_key_gets_auth_error(client):
    payload = await _call_tool(client, "sk_stept_bogus", "browser_list")
    assert payload == {"error": "Authentication required. Provide a valid API key."}


async def test_rpc_write_scope_key_sees_connected_browser(client, workspace_ctx):
    key = await _mint_key(client, workspace_ctx, ["write"])

    async def send(message: dict[str, Any]) -> None:
        pass

    await gateway.register(workspace_ctx.id, "dev-7", "user-1", "Work Chrome", send)
    payload = await _call_tool(client, key, "browser_list")
    assert payload["count"] == 1
    assert payload["browsers"][0]["device_id"] == "dev-7"
    assert payload["browsers"][0]["name"] == "Work Chrome"


async def test_rpc_act_validation_needs_no_browser(client, workspace_ctx):
    key = await _mint_key(client, workspace_ctx, ["write"])
    payload = await _call_tool(client, key, "browser_act", {"kind": "explode"})
    assert "kind must be one of" in payload["error"]
