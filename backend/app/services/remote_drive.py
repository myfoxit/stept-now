"""Extension remote-drive gateway (docs/MCP-CONTRACTS.md, W10).

Each signed-in Chrome extension holds one outbound WebSocket (``/ws/extension``,
NAT-friendly). The MCP browser tools dispatch ``ctrl_id``-correlated control
messages onto that socket and await the extension's ack. This module is the
transport-agnostic core: the WS endpoint feeds it ``register`` / ``unregister``
/ ``handle_message``, so the whole protocol is testable without a socket.

Wire shapes follow ``extension/src/types.ts`` (``GatewayToExtension`` /
``ExtensionToGateway``) exactly — snake_case envelope keys (``ctrl_id``,
``tour_id``), op args keyed as in ``DriveOp['args']``.

Topology
--------
The API runs several uvicorn worker processes (``STEPT_WEB_CONCURRENCY``,
4 by default — see ``deploy/docker-compose.prod.yml``). An extension's socket
lives on exactly ONE worker, while an MCP ``browser_*`` call is load-balanced to
ANY worker, so neither the registry nor the pending-ack futures can be
process-local. Sockets themselves stay local — only the process holding the
connection can write to it — but *discovery* and *dispatch* travel over
``app.core.pubsub`` (in-memory in dev/tests, Redis in prod), the same bridge
``app.realtime.manager`` uses so behavior is identical in single- and
multi-process deploys. Two topic families:

``drive:ctrl``
    Small broadcast frames every live gateway sees: node hellos (roster
    keep-alive), discovery requests, and cross-worker supersede announcements.
``drive:node:{node_id}``
    One inbox per gateway instance: discovery replies, addressed dispatches and
    their acks. Payload-heavy frames (screenshots) are unicast, never broadcast.

A dispatch first resolves the target to a single ``(node_id, device_id)`` pair
via a discovery round, then unicasts the op to that node's inbox — so exactly
one process ever writes an op to a socket, and every worker agrees on which
browser "most recently seen" means (drive sessions stay on one browser even
though consecutive tool calls land on different workers). A gateway that knows
of no sibling nodes short-circuits the round trip entirely, so a single-worker
dev/test process behaves exactly like the old process-local registry.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.db import utcnow, uuid7
from app.core.logging import log
from app.core.pubsub import Subscription, get_pubsub

logger = log("remote_drive")

# Friendly error wording — fixed by docs/MCP-CONTRACTS.md and asserted verbatim in tests.
NO_BROWSER_ERROR = (
    "no browser extension is connected for this workspace — "
    "open the Stept extension side panel and sign in"
)
DEVICE_GONE_ERROR = (
    "that browser is not connected right now — open its Chrome side panel or pick another one"
)
TIMEOUT_ERROR = "the browser did not respond in time"

SendFn = Callable[[dict[str, Any]], Awaitable[None]]
CloseFn = Callable[[int, str], Awaitable[None]]

# Message types that answer a pending ctrl_id-keyed control request.
_ACK_TYPES = frozenset({"exec-result", "record-ack", "run-result"})

# Broadcast control topic every worker subscribes to (hello / discover / supersede).
CTRL_TOPIC = "drive:ctrl"

# Hard ceiling on a discovery round. Rounds normally end far sooner — as soon as
# every node in the roster has answered — so this only bounds a straggler.
DISCOVERY_WINDOW = 0.25
# A just-started worker does not know its siblings yet, so until the hello
# handshake has had time to land it always waits out a round instead of
# concluding it is alone. Shrunk to a small multiple of the observed pub/sub
# round trip once our own hello echoes back (sub-millisecond in-process).
BOOTSTRAP_WINDOW = 0.25
BOOTSTRAP_MIN_GRACE = 0.02
# Nodes re-announce on this cadence (fast at first, so a cold worker is learned
# quickly); a roster entry survives NODE_TTL seconds without a frame.
HEARTBEAT_INTERVAL = 15.0
HEARTBEAT_FIRST = 1.0
NODE_TTL = 45.0

_EPOCH = datetime.min.replace(tzinfo=UTC)


def node_topic(node_id: str) -> str:
    """Inbox topic of one gateway instance (one API worker process)."""
    return f"drive:node:{node_id}"


def _parse(value: Any) -> datetime:
    """Parse an ISO timestamp off the wire; unparsable sorts oldest."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return _EPOCH


def _seen_key(entry: dict[str, Any]) -> datetime:
    """Liveness of a browser entry: the later of connect time and last frame."""
    return max(_parse(entry.get("connected_at")), _parse(entry.get("last_seen_at")))


def _socket_key(entry: dict[str, Any]) -> tuple[datetime, str]:
    """Which of two sockets claiming one device_id is the live one.

    Newest connection wins (that is what "supersede on reconnect" means); the
    node id breaks a microsecond-exact tie so every worker picks the same one.
    """
    return _parse(entry.get("connected_at")), str(entry.get("node") or "")


@dataclass
class Peer:
    """One connected extension socket (a signed-in browser)."""

    workspace_id: str
    device_id: str
    user_id: str
    name: str
    send: SendFn
    close: CloseFn | None
    connected_at: datetime
    last_seen_at: datetime

    @property
    def seen(self) -> datetime:
        """Liveness for routing: the later of connect time and last frame."""
        return max(self.connected_at, self.last_seen_at)


@dataclass
class _Inflight:
    """A dispatch this worker is running on behalf of another worker."""

    workspace_id: str
    reply_to: str
    explicit_device: bool
    expires_at: float


@dataclass
class _Round:
    """One in-flight discovery round awaiting the other workers' rosters."""

    workspace_id: str
    entries: list[dict[str, Any]]
    awaiting: set[str]
    strict: bool
    done: asyncio.Event


class RemoteDriveGateway:
    """Local socket registry + pub/sub-routed discovery and ctrl dispatch.

    One instance per API worker process (the module singleton ``gateway``);
    tests build several to stand in for several workers.
    """

    def __init__(self) -> None:
        # Identity of this worker on the bus — dispatches are addressed to it.
        self.node_id = uuid7()
        # workspace_id -> device_id -> Peer (sockets THIS process owns)
        self._peers: dict[str, dict[str, Peer]] = {}
        # ctrl_id -> (workspace_id, device the op was addressed to, pending future)
        self._pending: dict[str, tuple[str, str, asyncio.Future[dict[str, Any]]]] = {}
        # ctrl_id -> dispatch we are executing for another worker
        self._inflight: dict[str, _Inflight] = {}
        # req_id -> discovery round we are collecting answers for
        self._rounds: dict[str, _Round] = {}
        # node_id -> roster expiry (loop clock), siblings only
        self._nodes: dict[str, float] = {}
        self._subscriptions: list[Subscription] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._start_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._generation = 0
        self._bootstrap_until = 0.0
        self._hello_at: float | None = None

    # -- pub/sub wiring ------------------------------------------------------

    async def _ensure_started(self) -> None:
        """Subscribe to the bus once, lazily (no app-lifespan hook needed)."""
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # A worker process keeps one loop for life; a test suite does not,
            # and tasks/futures cannot cross loops — rewire instead of blowing up.
            self.reset()
            self._loop = loop
        if self._start_task is None:
            self._start_task = loop.create_task(self._start())
        await asyncio.shield(self._start_task)

    async def _start(self) -> None:
        generation = self._generation
        pubsub = get_pubsub()
        try:
            ctrl = await pubsub.subscribe(CTRL_TOPIC)
            inbox = await pubsub.subscribe(node_topic(self.node_id))
        except Exception:
            # Degrade to this process' own sockets rather than failing the tool
            # call; the operator sees the pub/sub outage in the logs.
            logger.exception("remote-drive pub/sub unavailable — staying process-local")
            return
        if generation != self._generation:  # reset() raced us
            for subscription in (ctrl, inbox):
                with contextlib.suppress(Exception):
                    await subscription.close()
            return
        loop = asyncio.get_running_loop()
        self._subscriptions = [ctrl, inbox]
        self._bootstrap_until = loop.time() + BOOTSTRAP_WINDOW
        self._tasks = [
            loop.create_task(self._pump(ctrl), name="drive-ctrl"),
            loop.create_task(self._pump(inbox), name=f"drive-inbox:{self.node_id}"),
            loop.create_task(self._heartbeat(), name="drive-heartbeat"),
        ]
        self._hello_at = loop.time()
        await self._publish(CTRL_TOPIC, {"kind": "hello", "node": self.node_id})

    async def _pump(self, subscription: Subscription) -> None:
        try:
            async for frame in subscription:
                try:
                    await self._on_frame(frame)
                except Exception:
                    logger.exception("remote-drive frame handling failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("remote-drive pub/sub pump died")

    async def _heartbeat(self) -> None:
        """Re-announce so idle workers stay in every roster, and expire dead ones."""
        delay = HEARTBEAT_FIRST
        while True:
            await asyncio.sleep(delay)
            delay = min(delay * 3, HEARTBEAT_INTERVAL)
            self._prune_nodes()
            await self._publish(CTRL_TOPIC, {"kind": "hello", "node": self.node_id})

    async def _publish(self, topic: str, frame: dict[str, Any]) -> None:
        try:
            await get_pubsub().publish(topic, frame)
        except Exception:
            logger.exception("remote-drive publish to %s failed", topic)

    def _prune_nodes(self) -> None:
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return
        for node_id, expires_at in list(self._nodes.items()):
            if expires_at <= now:
                del self._nodes[node_id]

    async def _on_frame(self, frame: dict[str, Any]) -> None:
        if not isinstance(frame, dict):
            return
        node_id = str(frame.get("node") or "")
        kind = frame.get("kind")
        if node_id == self.node_id:
            if kind == "hello":
                self._note_hello_echo()
            return  # our own broadcast, looped back on drive:ctrl
        if not node_id:
            return  # every frame we act on names its sender
        # Any frame is proof of life: that is what keeps the roster (and with it
        # the early exit from a discovery round) accurate without extra chatter.
        self._nodes[node_id] = asyncio.get_running_loop().time() + NODE_TTL
        if kind == "hello":
            # Unicast back so the newcomer learns us without another broadcast.
            await self._publish(node_topic(node_id), {"kind": "hello-ack", "node": self.node_id})
        elif kind == "hello-ack":
            return  # roster refresh only (done above)
        elif kind == "discover":
            await self._on_discover(frame)
        elif kind == "discover-reply":
            self._on_discover_reply(frame, node_id)
        elif kind == "supersede":
            await self._on_supersede(frame)
        elif kind == "dispatch":
            await self._on_dispatch(frame)
        elif kind == "reply":
            self._on_reply(frame)

    def _note_hello_echo(self) -> None:
        """Our own hello came back: one bus round trip took that long, so a
        sibling's answer to it needs about two. Cut the bootstrap wait to a
        small multiple of the measured latency instead of the blind ceiling."""
        if self._hello_at is None:
            return
        loop = asyncio.get_running_loop()
        rtt = max(loop.time() - self._hello_at, 0.0)
        self._hello_at = None
        self._bootstrap_until = min(
            self._bootstrap_until, loop.time() + max(4 * rtt, BOOTSTRAP_MIN_GRACE)
        )

    # -- registry -----------------------------------------------------------

    async def register(
        self,
        workspace_id: str,
        device_id: str,
        user_id: str,
        name: str,
        send: SendFn,
        close: CloseFn | None = None,
    ) -> Peer:
        """Register a connected extension socket; supersede a same-device one.

        A reconnect (MV3 service-worker restart, network blip) usually lands
        BEFORE the dead socket's close event: the replacement takes the registry
        slot first, then the old socket is closed with code 4000 "superseded".
        The old socket's teardown calls :meth:`unregister` with ITS peer object,
        which is identity-checked and therefore cannot evict the replacement.

        The reconnect may also land on a DIFFERENT worker than the dead socket,
        so the same supersede is announced on the bus for whichever worker still
        holds an older socket for this device.
        """
        await self._ensure_started()
        now = utcnow()
        peer = Peer(
            workspace_id=workspace_id,
            device_id=device_id,
            user_id=user_id,
            name=name,
            send=send,
            close=close,
            connected_at=now,
            last_seen_at=now,
        )
        room = self._peers.setdefault(workspace_id, {})
        superseded = room.get(device_id)
        room[device_id] = peer
        if superseded is not None:
            await self._close_superseded(superseded)
        await self._publish(
            CTRL_TOPIC,
            {
                "kind": "supersede",
                "node": self.node_id,
                "workspace_id": workspace_id,
                "device_id": device_id,
                "connected_at": now.isoformat(),
            },
        )
        return peer

    def unregister(self, peer: Peer) -> None:
        """Remove a peer — identity-checked so a superseded socket's late
        disconnect never tears down the socket that replaced it."""
        room = self._peers.get(peer.workspace_id)
        if room is None:
            return
        if room.get(peer.device_id) is peer:
            del room[peer.device_id]
            if not room:
                self._peers.pop(peer.workspace_id, None)

    def touch(self, peer: Peer) -> None:
        """Bump liveness (any inbound frame counts, pings included)."""
        peer.last_seen_at = utcnow()

    async def _close_superseded(self, peer: Peer) -> None:
        if peer.close is not None:
            with contextlib.suppress(Exception):  # already dead
                await peer.close(4000, "superseded")

    async def _on_supersede(self, frame: dict[str, Any]) -> None:
        """Another worker took over a device we still hold a socket for."""
        workspace_id = str(frame.get("workspace_id") or "")
        device_id = str(frame.get("device_id") or "")
        peer = self._peers.get(workspace_id, {}).get(device_id)
        if peer is None:
            return
        mine = (peer.connected_at, self.node_id)
        theirs = (_parse(frame.get("connected_at")), str(frame.get("node") or ""))
        if theirs <= mine:
            return  # ours is the newer socket — our own announcement evicts theirs
        self.unregister(peer)
        await self._close_superseded(peer)

    def local_browsers(self, workspace_id: str) -> list[dict[str, Any]]:
        """Browsers whose socket THIS process owns, most recently seen first."""
        peers = sorted(
            self._peers.get(workspace_id, {}).values(), key=lambda p: p.seen, reverse=True
        )
        return [
            {
                "device_id": p.device_id,
                "name": p.name,
                "user_id": p.user_id,
                "connected_at": p.connected_at.isoformat(),
                "last_seen_at": p.last_seen_at.isoformat(),
                "node": self.node_id,
            }
            for p in peers
        ]

    async def list_browsers(self, workspace_id: str) -> list[dict[str, Any]]:
        """Connected browsers for a workspace across every worker, newest first."""
        return [
            {key: value for key, value in entry.items() if key != "node"}
            for entry in await self._discover(workspace_id)
        ]

    def reset(self) -> None:
        """Drop every peer, cancel pending waits, forget the bus wiring.

        Cancellation is best effort: the futures and tasks may belong to an
        event loop that is already gone (test isolation), which is exactly when
        dropping them matters most.
        """
        self._generation += 1
        self._peers.clear()
        for *_, future in self._pending.values():
            with contextlib.suppress(Exception):
                if not future.done():
                    future.cancel()
        self._pending.clear()
        self._inflight.clear()
        for round_ in self._rounds.values():
            with contextlib.suppress(Exception):
                round_.done.set()
        self._rounds.clear()
        self._nodes.clear()
        for task in self._tasks:
            with contextlib.suppress(Exception):
                task.cancel()
        self._tasks = []
        self._subscriptions = []
        self._start_task = None
        self._loop = None
        self._bootstrap_until = 0.0
        self._hello_at = None

    async def aclose(self) -> None:
        """:meth:`reset` plus awaiting the bus teardown (tests, shutdown)."""
        tasks, subscriptions = self._tasks, self._subscriptions
        self.reset()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for subscription in subscriptions:
            with contextlib.suppress(Exception):
                await subscription.close()

    # -- discovery ----------------------------------------------------------

    async def _discover(self, workspace_id: str) -> list[dict[str, Any]]:
        """Every browser connected to this workspace, on any worker.

        Our own sockets answer synchronously; siblings answer over the bus. The
        round ends as soon as every node we know of has replied, so the common
        cases cost either nothing (no siblings) or one bus round trip.
        """
        await self._ensure_started()
        entries = self.local_browsers(workspace_id)
        loop = asyncio.get_running_loop()
        self._prune_nodes()
        others = set(self._nodes)
        if not others and loop.time() >= self._bootstrap_until:
            return _merge(entries)  # nobody else is on the bus
        req_id = uuid7()
        round_ = _Round(
            workspace_id=workspace_id,
            entries=entries,
            awaiting=set(others),
            strict=bool(others),
            done=asyncio.Event(),
        )
        self._rounds[req_id] = round_
        try:
            await self._publish(
                CTRL_TOPIC,
                {
                    "kind": "discover",
                    "node": self.node_id,
                    "req": req_id,
                    "workspace_id": workspace_id,
                    "reply_to": node_topic(self.node_id),
                },
            )
            budget = DISCOVERY_WINDOW if others else max(0.0, self._bootstrap_until - loop.time())
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(budget):
                    await round_.done.wait()
        finally:
            self._rounds.pop(req_id, None)
        return _merge(round_.entries)

    async def _on_discover(self, frame: dict[str, Any]) -> None:
        """Answer another worker's discovery round (empty answers count too —
        they are what lets the requester stop waiting early)."""
        reply_to = str(frame.get("reply_to") or "")
        req_id = str(frame.get("req") or "")
        workspace_id = str(frame.get("workspace_id") or "")
        if not reply_to or not req_id:
            return
        await self._publish(
            reply_to,
            {
                "kind": "discover-reply",
                "node": self.node_id,
                "req": req_id,
                "workspace_id": workspace_id,
                "browsers": self.local_browsers(workspace_id),
            },
        )

    def _on_discover_reply(self, frame: dict[str, Any], node_id: str) -> None:
        round_ = self._rounds.get(str(frame.get("req") or ""))
        if round_ is None:
            return  # round already finished — drop
        if frame.get("workspace_id") != round_.workspace_id:
            return  # answer for another tenant's round id
        browsers = frame.get("browsers")
        if isinstance(browsers, list):
            for browser in browsers:
                if isinstance(browser, dict):
                    # The publisher's node id wins over anything in the payload.
                    round_.entries.append({**browser, "node": node_id})
        round_.awaiting.discard(node_id)
        if round_.strict and not round_.awaiting:
            round_.done.set()

    # -- inbound (extension → server) ---------------------------------------

    async def handle_message(
        self, workspace_id: str, device_id: str, message: dict[str, Any]
    ) -> None:
        """Process one extension frame: pong pings, resolve pending ctrl acks."""
        kind = message.get("type")
        peer = self._peers.get(workspace_id, {}).get(device_id)
        if peer is not None:
            self.touch(peer)
        if kind == "ping":
            if peer is not None:
                with contextlib.suppress(Exception):
                    await peer.send({"type": "pong"})
            return
        if kind in _ACK_TYPES:
            await self._on_ack(workspace_id, device_id, message)
            return
        logger.debug("ignoring unknown extension frame type %r (device %s)", kind, device_id)

    async def _on_ack(self, workspace_id: str, device_id: str, message: dict[str, Any]) -> None:
        """Route one ack: to the local waiter, or back to the worker that asked."""
        ctrl_id = str(message.get("ctrl_id") or "")
        entry = self._pending.get(ctrl_id)
        if entry is not None:
            expected_workspace, expected_device, future = entry
            # Only the socket the op was addressed to may answer it — same
            # workspace AND same device. ctrl_ids are unguessable, so this is
            # depth: it stops one browser in a workspace from answering (and
            # fabricating the result of) an op sent to another.
            if (
                expected_workspace == workspace_id
                and expected_device == device_id
                and not future.done()
            ):
                future.set_result(message)
            return
        inflight = self._inflight.get(ctrl_id)
        if inflight is None:
            return  # late ack for a timed-out request — drop
        if inflight.workspace_id != workspace_id:
            return  # same tenant boundary, for a dispatch owned by another worker
        del self._inflight[ctrl_id]
        await self._publish(
            inflight.reply_to,
            {
                "kind": "reply",
                "node": self.node_id,
                "ctrl_id": ctrl_id,
                "workspace_id": workspace_id,
                # Which socket actually answered, so the requester can confirm
                # it is the one it addressed.
                "device_id": device_id,
                "ack": message,
            },
        )

    def _on_reply(self, frame: dict[str, Any]) -> None:
        """An ack relayed by the worker that owns the socket."""
        entry = self._pending.get(str(frame.get("ctrl_id") or ""))
        if entry is None:
            return  # timed out or duplicate — drop
        expected_workspace, expected_device, future = entry
        ack = frame.get("ack")
        if expected_workspace != frame.get("workspace_id") or not isinstance(ack, dict):
            return
        # The relaying worker echoes which device answered; it must be the one
        # we addressed (same check as the local path in `_on_ack`).
        relayed_device = frame.get("device_id")
        if relayed_device is not None and relayed_device != expected_device:
            return
        if not future.done():
            future.set_result(ack)

    # -- dispatch (server → extension) --------------------------------------

    async def _resolve_target(
        self, workspace_id: str, device_id: str | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Pick the target socket across all workers: explicit device_id, else
        most recently seen. Resolving BEFORE dispatching is what keeps a
        dispatch addressed to exactly one worker."""
        browsers = await self._discover(workspace_id)
        if device_id is not None:
            match = next((b for b in browsers if b.get("device_id") == device_id), None)
            return (match, None) if match is not None else (None, DEVICE_GONE_ERROR)
        if not browsers:
            return None, NO_BROWSER_ERROR
        return browsers[0], None

    async def _send_local(self, workspace_id: str, device_id: str, frame: dict[str, Any]) -> bool:
        """Write one frame to a socket this process owns; prune it if it died."""
        peer = self._peers.get(workspace_id, {}).get(device_id)
        if peer is None:
            return False
        try:
            await peer.send(frame)
        except Exception:
            # The socket died mid-send: prune the zombie so routing stops
            # picking it, and report the connection state honestly.
            logger.exception(
                "control send failed (workspace %s, device %s)", workspace_id, device_id
            )
            self.unregister(peer)
            return False
        return True

    async def _control(
        self,
        workspace_id: str,
        message: dict[str, Any],
        *,
        device_id: str | None,
        timeout: float,
    ) -> dict[str, Any]:
        """Send one ctrl_id-keyed message and await its ack.

        Returns the raw ack frame (has a ``type`` key) or a gateway-level
        ``{"error": …}`` (no browser / device gone / timeout).
        """
        target, error = await self._resolve_target(workspace_id, device_id)
        if target is None:
            return {"error": error}
        gone = DEVICE_GONE_ERROR if device_id is not None else NO_BROWSER_ERROR
        target_device = str(target.get("device_id") or "")
        target_node = str(target.get("node") or "")
        ctrl_id = uuid7()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[ctrl_id] = (workspace_id, target_device, future)
        try:
            if target_node == self.node_id:
                sent = await self._send_local(
                    workspace_id, target_device, {**message, "ctrl_id": ctrl_id}
                )
                if not sent:
                    return {"error": gone}
            else:
                await self._publish(
                    node_topic(target_node),
                    {
                        "kind": "dispatch",
                        "node": self.node_id,
                        "ctrl_id": ctrl_id,
                        "workspace_id": workspace_id,
                        "device_id": target_device,
                        "explicit_device": device_id is not None,
                        "ttl": timeout,
                        "message": message,
                        "reply_to": node_topic(self.node_id),
                    },
                )
            async with asyncio.timeout(timeout):
                return await future
        except TimeoutError:
            return {"error": TIMEOUT_ERROR}
        finally:
            self._pending.pop(ctrl_id, None)

    async def _on_dispatch(self, frame: dict[str, Any]) -> None:
        """Run a dispatch addressed to this worker (we own the target socket).

        Only the node the requester resolved to ever receives this frame, so an
        op is written to a socket exactly once no matter how many workers run.
        """
        workspace_id = str(frame.get("workspace_id") or "")
        device_id = str(frame.get("device_id") or "")
        ctrl_id = str(frame.get("ctrl_id") or "")
        reply_to = str(frame.get("reply_to") or "")
        message = frame.get("message")
        if not ctrl_id or not reply_to or not isinstance(message, dict):
            return
        explicit = bool(frame.get("explicit_device"))
        if self._peers.get(workspace_id, {}).get(device_id) is None:
            await self._reply_error(reply_to, ctrl_id, workspace_id, explicit)
            return
        ttl = frame.get("ttl")
        self._remember_inflight(
            ctrl_id,
            _Inflight(
                workspace_id=workspace_id,
                reply_to=reply_to,
                explicit_device=explicit,
                expires_at=asyncio.get_running_loop().time()
                + (float(ttl) if isinstance(ttl, int | float) else DISCOVERY_WINDOW),
            ),
        )
        if not await self._send_local(workspace_id, device_id, {**message, "ctrl_id": ctrl_id}):
            self._inflight.pop(ctrl_id, None)
            await self._reply_error(reply_to, ctrl_id, workspace_id, explicit)

    def _remember_inflight(self, ctrl_id: str, inflight: _Inflight) -> None:
        now = asyncio.get_running_loop().time()
        for key, entry in list(self._inflight.items()):
            if entry.expires_at <= now:  # its requester gave up long ago
                del self._inflight[key]
        self._inflight[ctrl_id] = inflight

    async def _reply_error(
        self, reply_to: str, ctrl_id: str, workspace_id: str, explicit: bool
    ) -> None:
        """Tell the requesting worker the socket vanished, so it need not wait
        out its whole timeout for a browser that is no longer there."""
        await self._publish(
            reply_to,
            {
                "kind": "reply",
                "node": self.node_id,
                "ctrl_id": ctrl_id,
                "workspace_id": workspace_id,
                "ack": {"error": DEVICE_GONE_ERROR if explicit else NO_BROWSER_ERROR},
            },
        )

    async def exec_op(
        self,
        workspace_id: str,
        op: str,
        args: dict[str, Any] | None = None,
        device_id: str | None = None,
        timeout: float = 60,
    ) -> dict[str, Any]:
        """One live-drive operation (open/snapshot/act/…) in the user's browser.

        Returns ``{"ok": True, "data": <DriveSnapshot>}`` or ``{"error": …}``.
        """
        ack = await self._control(
            workspace_id,
            {"type": "exec-op", "op": op, "args": args or {}},
            device_id=device_id,
            timeout=timeout,
        )
        if ack.get("type") is None:
            return ack  # gateway-level {"error": …}
        if not ack.get("ok"):
            return {"error": str(ack.get("error") or "the browser reported a failure")}
        return {"ok": True, "data": ack.get("data") or {}}

    async def record_start(
        self,
        workspace_id: str,
        url: str | None = None,
        device_id: str | None = None,
        timeout: float = 30,
    ) -> dict[str, Any]:
        """Arm the tour recorder in the user's browser."""
        message: dict[str, Any] = {"type": "record-start"}
        if url is not None:
            message["url"] = url
        ack = await self._control(workspace_id, message, device_id=device_id, timeout=timeout)
        return self._shape_record_ack(ack, failure="the browser could not start recording")

    async def record_stop(
        self,
        workspace_id: str,
        title: str,
        description: str | None = None,
        device_id: str | None = None,
        timeout: float = 60,
    ) -> dict[str, Any]:
        """Stop the recording; the ack carries the saved draft ``tour_id``."""
        message: dict[str, Any] = {"type": "record-stop", "title": title}
        if description is not None:
            message["description"] = description
        ack = await self._control(workspace_id, message, device_id=device_id, timeout=timeout)
        return self._shape_record_ack(ack, failure="the browser could not save the recording")

    async def run_tour(
        self,
        workspace_id: str,
        tour_id: str,
        device_id: str | None = None,
        timeout: float = 900,
    ) -> dict[str, Any]:
        """Play a tour in driven mode in the user's browser (long-running)."""
        ack = await self._control(
            workspace_id,
            {"type": "run-tour", "tour_id": tour_id, "mode": "driven"},
            device_id=device_id,
            timeout=timeout,
        )
        if ack.get("type") is None:
            return ack
        # run-result: {status: completed|failed|cancelled, error?}
        return {key: value for key, value in ack.items() if key not in ("type", "ctrl_id")}

    @staticmethod
    def _shape_record_ack(ack: dict[str, Any], *, failure: str) -> dict[str, Any]:
        if ack.get("type") is None:
            return ack
        if not ack.get("ok"):
            return {"error": str(ack.get("error") or failure)}
        return {key: value for key, value in ack.items() if key not in ("type", "ctrl_id")}


def _merge(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the workers' rosters into one list, most recently seen first.

    A device_id can briefly appear on two workers (a reconnect landed elsewhere
    before the old socket's close event); the newest connection is the live one.
    """
    best: dict[str, dict[str, Any]] = {}
    for entry in entries:
        device_id = str(entry.get("device_id") or "")
        if not device_id:
            continue
        current = best.get(device_id)
        if current is None or _socket_key(entry) > _socket_key(current):
            best[device_id] = entry
    return sorted(best.values(), key=_seen_key, reverse=True)


gateway = RemoteDriveGateway()
