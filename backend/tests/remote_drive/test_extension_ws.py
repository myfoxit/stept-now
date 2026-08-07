"""/ws/extension endpoint: auth-before-accept (4401/4403), registration,
ping→pong, ctrl round trip over a live connection, supersede-on-reconnect —
driven through queue-fed fake sockets (widget test_ws pattern)."""

from __future__ import annotations

import asyncio
import contextlib

from app.core.db import uuid7
from app.core.security import create_access_token, create_extension_token
from app.realtime.extension_ws import extension_websocket
from app.services.remote_drive import gateway
from tests.remote_drive.conftest import FakeExtensionSocket, eventually
from tests.tours.conftest import user_id_from_headers


def _token(workspace_ctx) -> str:
    return create_extension_token(
        workspace_ctx.id, user_id_from_headers(workspace_ctx.owner_headers)
    )


async def test_garbage_token_closes_4401():
    sock = FakeExtensionSocket()
    await extension_websocket(sock, token="not-a-token", device_id="dev-1")  # type: ignore[arg-type]
    assert sock.accepted is False
    assert sock.close_code == 4401


async def test_wrong_token_type_closes_4401():
    sock = FakeExtensionSocket()
    token = create_access_token("some-user")
    await extension_websocket(sock, token=token, device_id="dev-1")  # type: ignore[arg-type]
    assert sock.accepted is False
    assert sock.close_code == 4401


async def test_well_signed_non_member_closes_4403(client, workspace_ctx):
    sock = FakeExtensionSocket()
    stranger = create_extension_token(workspace_ctx.id, uuid7())
    await extension_websocket(sock, token=stranger, device_id="dev-1")  # type: ignore[arg-type]
    assert sock.accepted is False
    assert sock.close_code == 4403
    assert await gateway.list_browsers(workspace_ctx.id) == []


async def test_viewer_cannot_open_a_browser_socket(client, workspace_ctx):
    """The token is minted behind tours:manage, so the socket re-checks it.

    Membership alone would let a member demoted to viewer keep using the 30-day
    token they were issued while they still had the permission.
    """
    viewer_headers = await workspace_ctx.add_member("viewer@example.com", role="viewer")
    viewer_token = create_extension_token(workspace_ctx.id, user_id_from_headers(viewer_headers))
    sock = FakeExtensionSocket()
    await extension_websocket(sock, token=viewer_token, device_id="dev-1")  # type: ignore[arg-type]
    assert sock.accepted is False
    assert sock.close_code == 4403
    assert await gateway.list_browsers(workspace_ctx.id) == []


async def test_a_member_cannot_hijack_another_members_device_slot(client, workspace_ctx):
    """Reusing someone else's device_id must not evict their browser.

    The device_id is chosen by the client, so without namespacing an agent who
    learned the owner's id (it is listed by browser_list) could close the
    owner's socket as "superseded" and have subsequent drive ops — and the
    results the AI sees — routed into their own browser.
    """
    owner_id = user_id_from_headers(workspace_ctx.owner_headers)
    agent_headers = await workspace_ctx.add_member("agent@example.com", role="admin")
    agent_id = user_id_from_headers(agent_headers)

    owner_sock = FakeExtensionSocket()
    owner_task = asyncio.create_task(
        extension_websocket(  # type: ignore[arg-type]
            owner_sock, token=_token(workspace_ctx), device_id="dev-1", name="Owner Chrome"
        )
    )
    await eventually(lambda: bool(gateway.local_browsers(workspace_ctx.id)))

    attacker_sock = FakeExtensionSocket()
    attacker_token = create_extension_token(workspace_ctx.id, agent_id)
    attacker_task = asyncio.create_task(
        extension_websocket(  # type: ignore[arg-type]
            attacker_sock, token=attacker_token, device_id="dev-1", name="Other Chrome"
        )
    )
    await eventually(lambda: len(gateway.local_browsers(workspace_ctx.id)) == 2)

    # Both browsers are registered under distinct slots; the owner's socket was
    # never closed as superseded.
    assert owner_sock.close_code is None
    slots = {b["device_id"]: b["user_id"] for b in await gateway.list_browsers(workspace_ctx.id)}
    assert slots == {f"{owner_id}:dev-1": owner_id, f"{agent_id}:dev-1": agent_id}

    for task in (owner_task, attacker_task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_connect_registers_pings_and_answers_ctrl_ops(client, workspace_ctx):
    owner_id = user_id_from_headers(workspace_ctx.owner_headers)
    sock = FakeExtensionSocket()
    endpoint = asyncio.create_task(
        extension_websocket(  # type: ignore[arg-type]
            sock, token=_token(workspace_ctx), device_id="dev-1", name="Work Chrome"
        )
    )
    await eventually(lambda: bool(gateway.local_browsers(workspace_ctx.id)))
    assert sock.accepted is True
    browser = (await gateway.list_browsers(workspace_ctx.id))[0]
    # The client-chosen device_id is namespaced by the authenticated user, so
    # one member cannot claim another's slot (see the hijack test below).
    assert browser["device_id"] == f"{owner_id}:dev-1"
    assert browser["name"] == "Work Chrome"
    assert browser["user_id"] == owner_id

    # A malformed (non-dict) frame is ignored, the connection stays up.
    await sock.push(["not", "a", "frame"])
    await sock.push({"type": "ping"})
    await eventually(lambda: {"type": "pong"} in sock.sent)

    # ctrl round trip: exec-op goes out with a ctrl_id, the pushed exec-result
    # resolves the pending future.
    exec_task = asyncio.create_task(gateway.exec_op(workspace_ctx.id, "snapshot", {}, timeout=2))
    await eventually(lambda: any(m.get("type") == "exec-op" for m in sock.sent))
    wire = next(m for m in sock.sent if m["type"] == "exec-op")
    assert wire["op"] == "snapshot"
    assert wire["ctrl_id"]
    await sock.push(
        {
            "type": "exec-result",
            "ctrl_id": wire["ctrl_id"],
            "ok": True,
            "data": {"url": "https://x", "elements": "", "count": 0},
        }
    )
    result = await exec_task
    assert result["ok"] is True
    assert result["data"]["url"] == "https://x"

    sock.drop()
    await endpoint
    assert await gateway.list_browsers(workspace_ctx.id) == []


async def test_reconnect_supersedes_old_socket_but_not_replacement(client, workspace_ctx):
    token = _token(workspace_ctx)
    sock1 = FakeExtensionSocket()
    task1 = asyncio.create_task(
        extension_websocket(sock1, token=token, device_id="dev-1", name="One")  # type: ignore[arg-type]
    )
    await eventually(lambda: bool(gateway.local_browsers(workspace_ctx.id)))

    sock2 = FakeExtensionSocket()
    task2 = asyncio.create_task(
        extension_websocket(sock2, token=token, device_id="dev-1", name="Two")  # type: ignore[arg-type]
    )
    # The gateway closes the superseded socket with 4000 "superseded" …
    await eventually(lambda: sock1.close_code is not None)
    assert sock1.close_code == 4000
    assert sock1.close_reason == "superseded"
    # … which ends the old endpoint task; its teardown fires AFTER the new
    # socket registered and must not evict the replacement.
    await task1
    browsers = await gateway.list_browsers(workspace_ctx.id)
    assert [b["name"] for b in browsers] == ["Two"]

    # The replacement is live: ping → pong.
    await sock2.push({"type": "ping"})
    await eventually(lambda: {"type": "pong"} in sock2.sent)
    assert sock1.sent == []  # the old socket never got the replacement's frames

    sock2.drop()
    await task2
    assert await gateway.list_browsers(workspace_ctx.id) == []
