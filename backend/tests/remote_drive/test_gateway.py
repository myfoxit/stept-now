"""RemoteDriveGateway unit tests: registry + supersede, ctrl_id correlation,
timeouts, device routing, friendly errors — fake send callables, no sockets."""

from __future__ import annotations

import asyncio
import inspect
from datetime import timedelta
from typing import Any

from app.core.db import utcnow
from app.services.remote_drive import (
    DEVICE_GONE_ERROR,
    NO_BROWSER_ERROR,
    TIMEOUT_ERROR,
    gateway,
)
from tests.remote_drive.conftest import eventually, register_auto_peer


def collector() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    return sent, send


# ---------------------------------------------------------------------------
# exec round trip + errors
# ---------------------------------------------------------------------------


async def test_exec_op_round_trip_resolves_by_ctrl_id():
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        await gateway.handle_message(
            "ws-1",
            "dev-1",
            {
                "type": "exec-result",
                "ctrl_id": message["ctrl_id"],
                "ok": True,
                "data": {"url": "https://x", "elements": "[0]<button>", "count": 1},
            },
        )

    await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)
    result = await gateway.exec_op("ws-1", "open", {"url": "https://x"})
    assert result == {
        "ok": True,
        "data": {"url": "https://x", "elements": "[0]<button>", "count": 1},
    }
    wire = sent[0]
    assert wire["type"] == "exec-op"
    assert wire["op"] == "open"
    assert wire["args"] == {"url": "https://x"}
    assert isinstance(wire["ctrl_id"], str) and wire["ctrl_id"]


async def test_failed_exec_ack_becomes_error():
    async def send(message: dict[str, Any]) -> None:
        await gateway.handle_message(
            "ws-1",
            "dev-1",
            {
                "type": "exec-result",
                "ctrl_id": message["ctrl_id"],
                "ok": False,
                "error": "element 7 is not on the page anymore",
            },
        )

    await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)
    result = await gateway.exec_op("ws-1", "act", {"index": 7})
    assert result == {"error": "element 7 is not on the page anymore"}


async def test_timeout_returns_contract_wording():
    _, send = collector()
    await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)
    result = await gateway.exec_op("ws-1", "snapshot", timeout=0.02)
    assert result == {"error": "the browser did not respond in time"}
    assert result["error"] == TIMEOUT_ERROR


async def test_no_browser_returns_contract_wording():
    result = await gateway.exec_op("ws-none", "open", {"url": "https://x"})
    assert result == {
        "error": (
            "no browser extension is connected for this workspace — "
            "open the Stept extension side panel and sign in"
        )
    }
    assert result["error"] == NO_BROWSER_ERROR


async def test_explicit_device_gone_returns_contract_wording():
    _, send = collector()
    await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)
    result = await gateway.exec_op("ws-1", "open", {}, device_id="dev-vanished")
    assert result == {
        "error": (
            "that browser is not connected right now — "
            "open its Chrome side panel or pick another one"
        )
    }
    assert result["error"] == DEVICE_GONE_ERROR


async def test_late_ack_after_timeout_is_ignored():
    sent, send = collector()
    await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)
    result = await gateway.exec_op("ws-1", "snapshot", timeout=0.01)
    assert result == {"error": TIMEOUT_ERROR}
    # The ack limps in late: nothing pending anymore — must be a silent no-op.
    await gateway.handle_message(
        "ws-1",
        "dev-1",
        {"type": "exec-result", "ctrl_id": sent[0]["ctrl_id"], "ok": True, "data": {}},
    )


async def test_send_failure_prunes_the_dead_socket():
    async def send(message: dict[str, Any]) -> None:
        raise RuntimeError("socket is gone")

    await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)
    result = await gateway.exec_op("ws-1", "open", {"url": "https://x"})
    assert result == {"error": NO_BROWSER_ERROR}
    assert await gateway.list_browsers("ws-1") == []


async def test_ack_from_another_workspace_cannot_resolve_the_wait():
    sent, send = collector()
    await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)
    _, send_other = collector()
    await gateway.register("ws-2", "dev-x", "user-2", "Other", send_other)

    task = asyncio.create_task(gateway.exec_op("ws-1", "snapshot", timeout=0.05))
    await eventually(lambda: bool(sent))
    forged = {"type": "exec-result", "ctrl_id": sent[0]["ctrl_id"], "ok": True, "data": {}}
    await gateway.handle_message("ws-2", "dev-x", forged)
    assert await task == {"error": TIMEOUT_ERROR}


# ---------------------------------------------------------------------------
# registry: listing, supersede, routing
# ---------------------------------------------------------------------------


async def test_list_browsers_shape_and_recency_order():
    _, send = collector()
    peer_a = await gateway.register("ws-1", "dev-a", "user-1", "Work Chrome", send)
    await gateway.register("ws-1", "dev-b", "user-2", "Home Chrome", send)
    peer_a.last_seen_at = utcnow() + timedelta(seconds=5)  # a pinged most recently

    browsers = await gateway.list_browsers("ws-1")
    assert [b["device_id"] for b in browsers] == ["dev-a", "dev-b"]
    entry = browsers[0]
    assert entry["name"] == "Work Chrome"
    assert entry["user_id"] == "user-1"
    assert isinstance(entry["connected_at"], str) and "T" in entry["connected_at"]
    assert isinstance(entry["last_seen_at"], str)
    assert await gateway.list_browsers("ws-elsewhere") == []


async def test_supersede_closes_old_with_4000_and_keeps_replacement():
    closes: list[tuple[int, str]] = []
    _, send_old = collector()

    async def close_old(code: int, reason: str) -> None:
        closes.append((code, reason))

    old = await gateway.register("ws-1", "dev-1", "user-1", "Old", send_old, close=close_old)
    new_sent, send_new = collector()
    new = await gateway.register("ws-1", "dev-1", "user-1", "New", send_new)

    assert closes == [(4000, "superseded")]
    assert [b["name"] for b in await gateway.list_browsers("ws-1")] == ["New"]

    # The race the old gateway handled: the superseded socket's close event
    # fires AFTER the replacement registered — its unregister must not tear
    # down the new peer.
    gateway.unregister(old)
    assert [b["name"] for b in await gateway.list_browsers("ws-1")] == ["New"]

    # Ops route to the replacement socket.
    result = await gateway.exec_op("ws-1", "snapshot", timeout=0.02)
    assert result == {"error": TIMEOUT_ERROR}
    assert new_sent and new_sent[0]["type"] == "exec-op"

    gateway.unregister(new)
    assert await gateway.list_browsers("ws-1") == []


async def test_dispatch_picks_most_recently_seen_else_explicit_device():
    sent_a, send_a = collector()
    sent_b, send_b = collector()
    peer_a = await gateway.register("ws-1", "dev-a", "user-1", "A", send_a)
    await gateway.register("ws-1", "dev-b", "user-1", "B", send_b)

    peer_a.last_seen_at = utcnow() + timedelta(seconds=5)
    await gateway.exec_op("ws-1", "snapshot", timeout=0.01)
    assert sent_a and not sent_b

    await gateway.exec_op("ws-1", "snapshot", device_id="dev-b", timeout=0.01)
    assert sent_b and sent_b[0]["type"] == "exec-op"


# ---------------------------------------------------------------------------
# ping/pong + unknown frames
# ---------------------------------------------------------------------------


async def test_ping_pongs_and_bumps_liveness():
    sent, send = collector()
    peer = await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)
    peer.last_seen_at = peer.last_seen_at - timedelta(seconds=30)
    before = peer.last_seen_at
    await gateway.handle_message("ws-1", "dev-1", {"type": "ping"})
    assert sent == [{"type": "pong"}]
    assert peer.last_seen_at > before


async def test_unknown_and_unregistered_frames_are_ignored():
    await gateway.handle_message("ws-none", "dev-none", {"type": "ping"})
    await gateway.handle_message("ws-none", "dev-none", {"type": "mystery"})
    await gateway.handle_message("ws-none", "dev-none", {"type": "exec-result"})  # no ctrl_id


# ---------------------------------------------------------------------------
# record + run-tour dispatchers
# ---------------------------------------------------------------------------


async def test_record_start_stop_round_trip_and_wire_shape():
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message["type"] == "record-start":
            ack: dict[str, Any] = {
                "type": "record-ack",
                "ctrl_id": message["ctrl_id"],
                "ok": True,
                "recording": True,
            }
        else:
            ack = {
                "type": "record-ack",
                "ctrl_id": message["ctrl_id"],
                "ok": True,
                "tour_id": "tour-7",
                "event_count": 12,
            }
        await gateway.handle_message("ws-1", "dev-1", ack)

    await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)

    started = await gateway.record_start("ws-1", url="https://app.example/checkout")
    assert started == {"ok": True, "recording": True}
    assert sent[0]["type"] == "record-start"
    assert sent[0]["url"] == "https://app.example/checkout"

    stopped = await gateway.record_stop("ws-1", "Checkout", description="How to pay")
    assert stopped == {"ok": True, "tour_id": "tour-7", "event_count": 12}
    assert sent[1]["type"] == "record-stop"
    assert sent[1]["title"] == "Checkout"
    assert sent[1]["description"] == "How to pay"


async def test_record_start_without_url_omits_the_key():
    _, sent = await register_auto_peer("ws-1")
    await gateway.record_start("ws-1")
    assert sent[0]["type"] == "record-start"
    assert "url" not in sent[0]


async def test_failed_record_ack_becomes_error():
    async def send(message: dict[str, Any]) -> None:
        await gateway.handle_message(
            "ws-1",
            "dev-1",
            {
                "type": "record-ack",
                "ctrl_id": message["ctrl_id"],
                "ok": False,
                "error": "no recording is active",
            },
        )

    await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)
    assert await gateway.record_stop("ws-1", "T") == {"error": "no recording is active"}


async def test_run_tour_round_trip_and_wire_shape():
    _, sent = await register_auto_peer("ws-1")
    result = await gateway.run_tour("ws-1", "tour-42")
    assert result == {"status": "completed"}
    wire = sent[0]
    assert wire["type"] == "run-tour"
    assert wire["tour_id"] == "tour-42"
    assert wire["mode"] == "driven"
    assert wire["ctrl_id"]


async def test_dispatch_timeout_defaults_and_run_tour_override():
    # Contract defaults: exec 60s, record-start 30s, record-stop 60s, run 900s.
    assert inspect.signature(gateway.exec_op).parameters["timeout"].default == 60
    assert inspect.signature(gateway.record_start).parameters["timeout"].default == 30
    assert inspect.signature(gateway.record_stop).parameters["timeout"].default == 60
    assert inspect.signature(gateway.run_tour).parameters["timeout"].default == 900

    _, send = collector()
    await gateway.register("ws-1", "dev-1", "user-1", "Chrome", send)
    assert await gateway.run_tour("ws-1", "tour-42", timeout=0.02) == {"error": TIMEOUT_ERROR}
