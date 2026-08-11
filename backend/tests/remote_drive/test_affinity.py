"""Drive-session affinity: implicit routing pins to the browser that owns the
active drive session for its lifetime.

The field failure this guards against: one Chrome accidentally registered
twice (twin device ids), tool routing targeted "most recently seen", and the
twins' heartbeats flapped routing between the connection that held the driven
tab and the one that did not — four "no driven tab" failures in 20 minutes.
A heartbeat must never steal routing from a live session; only browser_close
(or the owner disappearing) releases the pin.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.core.db import utcnow
from app.services.remote_drive import RemoteDriveGateway, gateway
from tests.remote_drive.conftest import eventually, register_auto_peer


async def test_successful_open_pins_implicit_routing_to_that_browser():
    peer_a, sent_a = await register_auto_peer("ws-1", "dev-a", name="A")
    _, sent_b = await register_auto_peer("ws-1", "dev-b", name="B")

    # A is most recently seen → open lands on A and claims the session.
    peer_a.last_seen_at = utcnow() + timedelta(seconds=5)
    result = await gateway.exec_op("ws-1", "open", {"url": "https://x.test"})
    assert result["ok"] is True
    assert sent_a and not sent_b
    assert gateway.drive_owner("ws-1") == "dev-a"

    # The twin now heartbeats "more recently" — routing must NOT flap to it.
    twin = gateway._peers["ws-1"]["dev-b"]
    twin.last_seen_at = utcnow() + timedelta(seconds=60)
    await gateway.handle_message("ws-1", "dev-b", {"type": "ping"})
    await gateway.exec_op("ws-1", "snapshot")
    assert not [m for m in sent_b if m.get("type") == "exec-op"]
    assert [m for m in sent_a if m.get("type") == "exec-op"][-1]["op"] == "snapshot"


async def test_close_releases_the_pin_back_to_recency_routing():
    peer_a, sent_a = await register_auto_peer("ws-1", "dev-a", name="A")
    _, sent_b = await register_auto_peer("ws-1", "dev-b", name="B")
    peer_a.last_seen_at = utcnow() + timedelta(seconds=5)

    await gateway.exec_op("ws-1", "open", {"url": "https://x.test"})
    assert gateway.drive_owner("ws-1") == "dev-a"
    await gateway.exec_op("ws-1", "close")
    assert gateway.drive_owner("ws-1") is None

    # After the release, plain recency picks the target again.
    twin = gateway._peers["ws-1"]["dev-b"]
    twin.last_seen_at = utcnow() + timedelta(seconds=60)
    await gateway.exec_op("ws-1", "open", {"url": "https://y.test"})
    assert [m for m in sent_b if m.get("type") == "exec-op"]
    assert gateway.drive_owner("ws-1") == "dev-b"
    assert len([m for m in sent_a if m.get("type") == "exec-op"]) == 2  # open + close only


async def test_owner_gone_falls_back_to_most_recent_without_erroring():
    peer_a, _ = await register_auto_peer("ws-1", "dev-a", name="A")
    _, sent_b = await register_auto_peer("ws-1", "dev-b", name="B")
    peer_a.last_seen_at = utcnow() + timedelta(seconds=5)

    await gateway.exec_op("ws-1", "open", {"url": "https://x.test"})
    gateway.unregister(peer_a)  # the owner's socket dies

    result = await gateway.exec_op("ws-1", "snapshot")
    assert result["ok"] is True
    assert [m for m in sent_b if m.get("type") == "exec-op"]
    # The claim itself survives (the owner may reconnect and reattach) but a
    # successful op on the stand-in re-pins to the browser actually answering.
    assert gateway.drive_owner("ws-1") == "dev-b"


async def test_explicit_device_id_always_overrides_the_pin():
    peer_a, _ = await register_auto_peer("ws-1", "dev-a", name="A")
    _, sent_b = await register_auto_peer("ws-1", "dev-b", name="B")
    peer_a.last_seen_at = utcnow() + timedelta(seconds=5)
    await gateway.exec_op("ws-1", "open", {"url": "https://x.test"})

    result = await gateway.exec_op("ws-1", "snapshot", device_id="dev-b")
    assert result["ok"] is True
    assert [m for m in sent_b if m.get("type") == "exec-op"]


async def test_failed_ops_never_claim_ownership():
    async def send(_message: dict[str, Any]) -> None:  # never acks → timeout
        return None

    await gateway.register("ws-1", "dev-a", "user-1", "A", send)
    result = await gateway.exec_op("ws-1", "open", {"url": "https://x.test"}, timeout=0.01)
    assert "error" in result
    assert gateway.drive_owner("ws-1") is None


async def test_ownership_is_workspace_scoped():
    await register_auto_peer("ws-1", "dev-a", name="A")
    await register_auto_peer("ws-2", "dev-z", name="Z")
    await gateway.exec_op("ws-1", "open", {"url": "https://x.test"})
    assert gateway.drive_owner("ws-1") == "dev-a"
    assert gateway.drive_owner("ws-2") is None


# ---------------------------------------------------------------------------
# multi-worker: the claim must be honoured by dispatches from OTHER workers
# ---------------------------------------------------------------------------


async def test_claim_broadcast_pins_routing_on_a_sibling_worker(worker):
    worker_b: RemoteDriveGateway = worker()

    peer_a, sent_a = await register_auto_peer("ws-1", "dev-a", name="A")
    _, sent_b = await register_auto_peer("ws-1", "dev-b", name="B", on=worker_b)
    peer_a.last_seen_at = utcnow() + timedelta(seconds=5)

    # Worker A's gateway runs the open (dev-a most recent) and claims it.
    result = await gateway.exec_op("ws-1", "open", {"url": "https://x.test"})
    assert result["ok"] is True
    await eventually(lambda: worker_b.drive_owner("ws-1") == "dev-a")

    # The twin on worker B heartbeats more recently — a dispatch ISSUED BY
    # worker B must still route to the owner on worker A.
    twin = worker_b._peers["ws-1"]["dev-b"]
    twin.last_seen_at = utcnow() + timedelta(seconds=60)
    ops_before = len([m for m in sent_a if m.get("type") == "exec-op"])
    result = await worker_b.exec_op("ws-1", "snapshot")
    assert result["ok"] is True
    assert not [m for m in sent_b if m.get("type") == "exec-op"]
    assert len([m for m in sent_a if m.get("type") == "exec-op"]) == ops_before + 1


async def test_late_joining_worker_learns_the_owner_from_discovery(worker):
    peer_a, sent_a = await register_auto_peer("ws-1", "dev-a", name="A")
    peer_a.last_seen_at = utcnow() + timedelta(seconds=5)
    await gateway.exec_op("ws-1", "open", {"url": "https://x.test"})

    # This worker starts AFTER the claim broadcast — it must still converge,
    # because owner knowledge rides discovery replies.
    worker_late: RemoteDriveGateway = worker()
    _, sent_b = await register_auto_peer("ws-1", "dev-b", name="B", on=worker_late)
    twin = worker_late._peers["ws-1"]["dev-b"]
    twin.last_seen_at = utcnow() + timedelta(seconds=60)

    result = await worker_late.exec_op("ws-1", "snapshot")
    assert result["ok"] is True
    assert not [m for m in sent_b if m.get("type") == "exec-op"]
    assert [m for m in sent_a if m.get("type") == "exec-op"][-1]["op"] == "snapshot"
