"""The shipped topology: several uvicorn workers behind one load balancer.

Each gateway instance stands in for one API worker process; they share the
process-global pub/sub hub exactly as separate processes share Redis. The
extension's socket lives on ONE of them while an MCP ``browser_*`` call lands on
ANY of them, which is precisely the case the old process-local registry got
wrong (~75% of tool calls reported "no browser is connected" while one was).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from app.core.db import utcnow
from app.core.pubsub import get_pubsub
from app.services.remote_drive import (
    CTRL_TOPIC,
    DEVICE_GONE_ERROR,
    NO_BROWSER_ERROR,
    TIMEOUT_ERROR,
    RemoteDriveGateway,
    node_topic,
)
from tests.remote_drive.conftest import DEFAULT_SNAPSHOT, eventually, register_auto_peer


async def link(*workers: RemoteDriveGateway) -> None:
    """Bring every worker onto the bus and wait for the hello handshake.

    Without this the workers still find each other (the bootstrap window covers
    a cold worker — see the cold-start test), but tests that are about routing
    rather than about start-up read better once the rosters have converged.
    """
    for instance in workers:
        await instance.list_browsers("ws-handshake")
    node_ids = {instance.node_id for instance in workers}
    for instance in workers:
        await eventually(lambda w=instance: node_ids - {w.node_id} <= set(w._nodes))


def silent() -> tuple[list[dict[str, Any]], Any]:
    """A socket that records what it is asked to do and never acks."""
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    return sent, send


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


async def test_browser_on_one_worker_is_visible_from_another(worker):
    a, b = worker(), worker()
    await link(a, b)
    await register_auto_peer("ws-1", "dev-a", name="Work Chrome", on=a)

    browsers = await b.list_browsers("ws-1")
    assert [entry["device_id"] for entry in browsers] == ["dev-a"]
    assert browsers[0]["name"] == "Work Chrome"
    # Public shape unchanged — the routing node id stays internal.
    assert set(browsers[0]) == {"device_id", "name", "user_id", "connected_at", "last_seen_at"}
    assert browsers == await a.list_browsers("ws-1")


async def test_cold_worker_sees_browsers_registered_before_it_joined(worker):
    """The dangerous window: a freshly booted worker takes the first tool call
    while the extension is attached to a worker that started long ago."""
    a = worker()
    await register_auto_peer("ws-1", "dev-a", on=a)

    b = worker()  # never handshaken — its very first call must still see dev-a
    assert [entry["device_id"] for entry in await b.list_browsers("ws-1")] == ["dev-a"]


async def test_discovery_does_not_hang_when_a_worker_stops_answering(worker):
    a, b = worker(), worker()
    await link(a, b)
    await a.aclose()  # the worker dies without saying goodbye

    started = asyncio.get_running_loop().time()
    assert await b.list_browsers("ws-1") == []
    assert asyncio.get_running_loop().time() - started < 1.0


async def test_a_lone_worker_never_pays_for_a_round_trip(worker):
    """Single-worker dev/test deploys must behave (and cost) exactly as the old
    process-local registry did: once the start-up handshake has found nobody,
    discovery is answered straight out of the local registry."""
    a = worker()
    await a.list_browsers("ws-1")  # joins the bus, runs the hello handshake
    await eventually(lambda: asyncio.get_running_loop().time() >= a._bootstrap_until)

    seen: list[dict[str, Any]] = []
    watcher = await get_pubsub().subscribe(CTRL_TOPIC)

    async def collect() -> None:
        async for frame in watcher:
            seen.append(frame)

    task = asyncio.create_task(collect())
    try:
        _, sent = await register_auto_peer("ws-1", "dev-a", on=a)
        assert len(await a.list_browsers("ws-1")) == 1
        assert (await a.exec_op("ws-1", "snapshot"))["ok"] is True
        await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await watcher.close()
    assert [frame["type"] for frame in sent] == ["exec-op"]
    assert [frame for frame in seen if frame.get("kind") == "discover"] == []


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


async def test_exec_op_dispatched_on_another_worker_reaches_the_socket(worker):
    a, b = worker(), worker()
    await link(a, b)
    _, sent = await register_auto_peer("ws-1", "dev-a", on=a)

    result = await b.exec_op("ws-1", "open", {"url": "https://x"})
    assert result == {"ok": True, "data": DEFAULT_SNAPSHOT}
    assert [frame["type"] for frame in sent] == ["exec-op"]
    assert sent[0]["op"] == "open"
    assert sent[0]["args"] == {"url": "https://x"}


async def test_op_executes_exactly_once_across_workers(worker):
    """Both workers are subscribed and both hold sockets for the workspace:
    the op must be written to exactly one of them."""
    a, b = worker(), worker()
    await link(a, b)
    _, sent_a = await register_auto_peer("ws-1", "dev-a", on=a)
    _, sent_b = await register_auto_peer("ws-1", "dev-b", on=b)

    result = await b.exec_op("ws-1", "snapshot")
    assert result["ok"] is True
    ops = [f for f in sent_a if f["type"] == "exec-op"] + [
        f for f in sent_b if f["type"] == "exec-op"
    ]
    assert len(ops) == 1
    # …and the same holds when the request enters through the other worker.
    result = await a.exec_op("ws-1", "snapshot")
    assert result["ok"] is True
    ops = [f for f in sent_a if f["type"] == "exec-op"] + [
        f for f in sent_b if f["type"] == "exec-op"
    ]
    assert len(ops) == 2


async def test_dispatch_follows_global_recency_not_local(worker):
    """Keeps a drive session on ONE browser: consecutive tool calls land on
    different workers, so every worker must pick the same target."""
    a, b = worker(), worker()
    await link(a, b)
    peer_a, sent_a = await register_auto_peer("ws-1", "dev-a", on=a)
    _, sent_b = await register_auto_peer("ws-1", "dev-b", on=b)
    peer_a.last_seen_at = utcnow() + timedelta(seconds=5)  # dev-a pinged most recently

    # Worker b holds a socket of its own, but dev-a is the more recently seen
    # browser, so b must reach across instead of driving its local one.
    assert (await b.exec_op("ws-1", "snapshot"))["ok"] is True
    assert [f["type"] for f in sent_a] == ["exec-op"]
    assert sent_b == []

    assert (await a.exec_op("ws-1", "snapshot"))["ok"] is True
    assert len(sent_a) == 2
    assert sent_b == []


async def test_explicit_device_id_routes_to_the_owning_worker(worker):
    a, b = worker(), worker()
    await link(a, b)
    _, sent_a = await register_auto_peer("ws-1", "dev-a", on=a)
    _, sent_b = await register_auto_peer("ws-1", "dev-b", on=b)

    assert (await a.exec_op("ws-1", "snapshot", device_id="dev-b"))["ok"] is True
    assert sent_a == []
    assert [f["type"] for f in sent_b] == ["exec-op"]

    assert await a.exec_op("ws-1", "snapshot", device_id="dev-gone") == {"error": DEVICE_GONE_ERROR}


async def test_record_and_run_tour_cross_worker(worker):
    a, b = worker(), worker()
    await link(a, b)
    _, sent = await register_auto_peer(
        "ws-1", "dev-a", record_stop_ack={"tour_id": "tour-9", "event_count": 3}, on=a
    )

    assert await b.record_start("ws-1", url="https://app.example") == {
        "ok": True,
        "recording": True,
    }
    assert await b.record_stop("ws-1", "Checkout") == {
        "ok": True,
        "tour_id": "tour-9",
        "event_count": 3,
    }
    assert await b.run_tour("ws-1", "tour-9") == {"status": "completed"}
    assert [frame["type"] for frame in sent] == ["record-start", "record-stop", "run-tour"]


# ---------------------------------------------------------------------------
# failure modes
# ---------------------------------------------------------------------------


async def test_timeout_when_the_owning_socket_never_answers(worker):
    a, b = worker(), worker()
    await link(a, b)
    sent, send = silent()
    await a.register("ws-1", "dev-a", "user-1", "Chrome", send)

    assert await b.exec_op("ws-1", "snapshot", timeout=0.1) == {"error": TIMEOUT_ERROR}
    assert [frame["type"] for frame in sent] == ["exec-op"]

    # The ack limps in afterwards: the owner relays it, the requester has
    # nothing pending anymore — both sides must swallow it silently.
    await a.handle_message(
        "ws-1",
        "dev-a",
        {"type": "exec-result", "ctrl_id": sent[0]["ctrl_id"], "ok": True, "data": {}},
    )
    await asyncio.sleep(0.01)
    assert b._pending == {}
    assert a._inflight == {}


async def test_dead_socket_on_the_owner_answers_without_waiting_out_the_timeout(worker):
    a, b = worker(), worker()
    await link(a, b)

    async def send(message: dict[str, Any]) -> None:
        raise RuntimeError("socket is gone")

    await a.register("ws-1", "dev-a", "user-1", "Chrome", send)

    started = asyncio.get_running_loop().time()
    assert await b.exec_op("ws-1", "open", {"url": "https://x"}, timeout=30) == {
        "error": NO_BROWSER_ERROR
    }
    assert asyncio.get_running_loop().time() - started < 1.0
    assert a.local_browsers("ws-1") == []  # the zombie was pruned on its owner


async def test_no_cross_workspace_leakage_across_workers(worker):
    a, b = worker(), worker()
    await link(a, b)
    sent, send = silent()
    await a.register("ws-1", "dev-a", "user-1", "Chrome", send)
    _, other_send = silent()
    await a.register("ws-2", "dev-x", "user-2", "Other", other_send)

    assert await b.list_browsers("ws-3") == []
    assert await b.exec_op("ws-3", "snapshot") == {"error": NO_BROWSER_ERROR}
    assert [entry["device_id"] for entry in await b.list_browsers("ws-2")] == ["dev-x"]

    # A socket of another tenant cannot resolve this dispatch, on either hop.
    task = asyncio.create_task(b.exec_op("ws-1", "snapshot", timeout=0.15))
    await eventually(lambda: bool(sent))
    forged = {"type": "exec-result", "ctrl_id": sent[0]["ctrl_id"], "ok": True, "data": {}}
    await a.handle_message("ws-2", "dev-x", forged)
    assert await task == {"error": TIMEOUT_ERROR}


async def test_reconnect_on_another_worker_supersedes_the_first_socket(worker):
    """MV3 restarts the socket and the load balancer hands it to a different
    worker — the stale socket must still be closed 4000 "superseded"."""
    a, b = worker(), worker()
    await link(a, b)
    closes: list[tuple[int, str]] = []
    _, send_old = silent()

    async def close_old(code: int, reason: str) -> None:
        closes.append((code, reason))

    await a.register("ws-1", "dev-1", "user-1", "Old", send_old, close=close_old)
    _, sent_new = await register_auto_peer("ws-1", "dev-1", name="New", on=b)

    await eventually(lambda: bool(closes))
    assert closes == [(4000, "superseded")]
    assert a.local_browsers("ws-1") == []
    assert [entry["name"] for entry in await a.list_browsers("ws-1")] == ["New"]

    # Ops now route to the replacement, wherever they enter.
    assert (await a.exec_op("ws-1", "snapshot"))["ok"] is True
    assert [frame["type"] for frame in sent_new] == ["exec-op"]


async def test_stale_duplicate_registration_never_wins_a_dispatch(worker):
    """Until the supersede announcement lands, two workers can each hold a
    socket for one device_id; discovery must collapse them onto the newest."""
    a, b = worker(), worker()
    await link(a, b)
    stale, stale_send = silent()
    peer = await a.register("ws-1", "dev-1", "user-1", "Stale", stale_send)
    _, fresh = await register_auto_peer("ws-1", "dev-1", name="Fresh", on=b)
    # Re-plant the stale socket exactly as if the supersede had been missed.
    a._peers.setdefault("ws-1", {})["dev-1"] = peer

    browsers = await a.list_browsers("ws-1")
    assert [entry["name"] for entry in browsers] == ["Fresh"]
    assert (await a.exec_op("ws-1", "snapshot"))["ok"] is True
    assert stale == []
    assert [frame["type"] for frame in fresh] == ["exec-op"]


async def test_junk_on_the_bus_is_ignored(worker):
    a, b = worker(), worker()
    await link(a, b)
    _, sent = await register_auto_peer("ws-1", "dev-a", on=a)
    pubsub = get_pubsub()

    await pubsub.publish(node_topic(b.node_id), {"kind": "reply", "node": a.node_id})
    await pubsub.publish(
        node_topic(b.node_id),
        {"kind": "reply", "node": a.node_id, "ctrl_id": "nope", "ack": {"ok": True}},
    )
    await pubsub.publish(node_topic(b.node_id), {"kind": "dispatch", "node": a.node_id})
    await pubsub.publish(
        node_topic(b.node_id),
        {
            "kind": "dispatch",
            "node": a.node_id,
            "ctrl_id": "nope",
            "workspace_id": "ws-1",
            "device_id": "dev-nowhere",
            "message": {"type": "exec-op", "op": "snapshot", "args": {}},
            "reply_to": node_topic(a.node_id),
        },
    )
    await pubsub.publish(node_topic(b.node_id), {"kind": "who-knows", "node": a.node_id})
    await asyncio.sleep(0.01)

    # The bus survived it: a real op still round-trips.
    assert (await b.exec_op("ws-1", "snapshot"))["ok"] is True
    assert [frame["type"] for frame in sent] == ["exec-op"]
