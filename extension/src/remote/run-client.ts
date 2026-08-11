import type { DriveOp, DriveSnapshot, ExtensionToGateway, GatewayToExtension } from '../types';

/**
 * The extension side of the remote-drive transport (docs/MCP-CONTRACTS.md,
 * "WS gateway"): one outbound WebSocket from the service worker to
 * `/ws/extension`, held open so the backend can route MCP browser tools into
 * this signed-in browser. Ported from the old repo's `run-client.ts`.
 *
 * Lifecycle rules (all load-bearing in an MV3 worker):
 * - onopen arms a 20s in-socket ping — WS activity resets Chrome's ~30s
 *   service-worker idle timer, so the socket keeps its own worker alive.
 * - a dropped socket reconnects on a FIXED 3s timer, single-shot guarded (a
 *   keepalive tick and an onclose can both try to schedule one).
 * - connect() detaches the handlers from any OPEN/CONNECTING socket before
 *   closing it, so a zombie's own onclose can never null out or reconnect over
 *   its replacement.
 */

const PING_INTERVAL_MS = 20_000;
const RECONNECT_DELAY_MS = 3_000;

const DEVICE_ID_KEY = 'stept.deviceId';

/** Single-flight guard for `ensureDeviceId`. Two overlapping calls used to
 * BOTH read an empty store and BOTH mint — registering this one browser under
 * two device ids, which the gateway then saw as twin browsers and flapped
 * between (the duplicate-registration bug). One promise per worker lifetime
 * makes every caller share one read-or-mint. */
let deviceIdInFlight: Promise<string> | null = null;

/** Stable per-profile device id, minted once into `chrome.storage.local` — the
 * gateway uses it to supersede a stale registration on reconnect. */
export function ensureDeviceId(): Promise<string> {
  deviceIdInFlight ??= (async () => {
    const bag = await chrome.storage.local
      .get(DEVICE_ID_KEY)
      .catch(() => ({}) as Record<string, unknown>);
    const existing = bag[DEVICE_ID_KEY];
    if (typeof existing === 'string' && existing) return existing;
    const minted = crypto.randomUUID();
    await chrome.storage.local.set({ [DEVICE_ID_KEY]: minted }).catch(() => {});
    // Re-read after write: if another context (a racing worker start) minted
    // concurrently, converge on whatever the store settled on so this browser
    // presents ONE identity to the gateway.
    const settled = await chrome.storage.local
      .get(DEVICE_ID_KEY)
      .catch(() => ({}) as Record<string, unknown>);
    const stored = settled[DEVICE_ID_KEY];
    return typeof stored === 'string' && stored ? stored : minted;
  })();
  return deviceIdInFlight;
}

/** Test seam: forget the in-flight/settled device id promise. */
export function resetDeviceIdCacheForTests(): void {
  deviceIdInFlight = null;
}

/** Human label the dashboard/MCP shows for this browser. */
export function deviceName(): string {
  const platform = typeof navigator !== 'undefined' ? navigator.platform : '';
  return platform ? `${platform} Chrome` : 'Chrome';
}

/** What the background wires in. Every handler is awaited and its outcome is
 * ALWAYS answered back on the same ctrl_id — a thrown handler becomes an
 * ok:false / failed reply, never silence (the gateway would sit on its future
 * until timeout otherwise). */
export interface RunClientHandlers {
  execOp(op: DriveOp['op'], args: DriveOp['args'] | undefined): Promise<DriveSnapshot>;
  recordStart(url?: string): Promise<{ ok: boolean; recording?: boolean; error?: string }>;
  recordStop(
    title: string,
    description?: string,
  ): Promise<{
    ok: boolean;
    recording?: boolean;
    tour_id?: string;
    event_count?: number;
    error?: string;
  }>;
  runTour(tourId: string): Promise<{ status: 'completed' | 'failed' | 'cancelled'; error?: string }>;
  /** Socket became live / died — the background mirrors this into panel state. */
  onConnectionChange?(connected: boolean): void;
}

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function detachHandlers(ws: WebSocket): void {
  ws.onopen = null;
  ws.onmessage = null;
  ws.onclose = null;
  ws.onerror = null;
}

export class RunClient {
  private ws: WebSocket | null = null;
  private closed = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly apiBase: string,
    private readonly token: string,
    private readonly deviceId: string,
    private readonly name: string,
    private readonly handlers: RunClientHandlers,
  ) {}

  /** ws(s)://…/ws/extension?token=…&device_id=…&name=… — every part encoded. */
  gatewayUrl(): string {
    const base = this.apiBase.replace(/^http/, 'ws').replace(/\/+$/, '');
    const q =
      `token=${encodeURIComponent(this.token)}` +
      `&device_id=${encodeURIComponent(this.deviceId)}` +
      `&name=${encodeURIComponent(this.name)}`;
    return `${base}/ws/extension?${q}`;
  }

  start(): void {
    this.closed = false;
    this.connect();
  }

  stop(): void {
    this.closed = true;
    if (this.pingTimer != null) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
    if (this.reconnectTimer != null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      detachHandlers(ws);
      try {
        ws.close();
      } catch {
        /* already closing */
      }
    }
    this.handlers.onConnectionChange?.(false);
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  /** Called by the background's keepalive alarm: revive the socket if the MV3
   * worker slept and dropped it, otherwise nudge it so the gateway's last_seen
   * stays fresh. */
  ensureConnected(): void {
    if (this.closed) return;
    if (this.isConnected()) this.send({ type: 'ping' });
    else this.connect();
  }

  private connect(): void {
    if (this.closed) return;
    // Never stack a second live socket on top of one still OPEN or CONNECTING
    // (a keepalive tick and an onclose can both get here): the old socket would
    // linger as a zombie the gateway keeps routing ops to. Detach its handlers
    // FIRST so its close can't null out / reconnect over the replacement.
    const existing = this.ws;
    if (
      existing &&
      (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)
    ) {
      detachHandlers(existing);
      try {
        existing.close();
      } catch {
        /* already closing */
      }
      this.ws = null;
    }
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.gatewayUrl());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      if (this.pingTimer != null) clearInterval(this.pingTimer);
      this.pingTimer = setInterval(() => this.send({ type: 'ping' }), PING_INTERVAL_MS);
      this.handlers.onConnectionChange?.(true);
    };
    ws.onmessage = (ev) => this.route(ev);
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      if (this.pingTimer != null) {
        clearInterval(this.pingTimer);
        this.pingTimer = null;
      }
      this.handlers.onConnectionChange?.(false);
      this.scheduleReconnect();
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* already closing */
      }
    };
  }

  /** Fixed 3s backoff, single-shot: one pending timer at a time. */
  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer != null) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, RECONNECT_DELAY_MS);
  }

  private send(payload: ExtensionToGateway): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload));
  }

  private route(ev: MessageEvent): void {
    let msg: GatewayToExtension;
    try {
      msg = JSON.parse(String(ev.data)) as GatewayToExtension;
    } catch {
      return; // malformed frame — ignore, the gateway's timeout covers it
    }
    if (!msg || typeof (msg as { type?: unknown }).type !== 'string') return;
    switch (msg.type) {
      case 'pong':
        return;
      case 'exec-op':
        void this.onExecOp(msg);
        return;
      case 'record-start':
        void this.onRecordStart(msg);
        return;
      case 'record-stop':
        void this.onRecordStop(msg);
        return;
      case 'run-tour':
        void this.onRunTour(msg);
        return;
      default:
        return; // unknown server message — forward compatibility
    }
  }

  private async onExecOp(msg: Extract<GatewayToExtension, { type: 'exec-op' }>): Promise<void> {
    try {
      const data = await this.handlers.execOp(msg.op, msg.args);
      this.send({ type: 'exec-result', ctrl_id: msg.ctrl_id, ok: true, data });
    } catch (err) {
      this.send({ type: 'exec-result', ctrl_id: msg.ctrl_id, ok: false, error: errText(err) });
    }
  }

  private async onRecordStart(
    msg: Extract<GatewayToExtension, { type: 'record-start' }>,
  ): Promise<void> {
    try {
      const r = await this.handlers.recordStart(msg.url);
      this.send({
        type: 'record-ack',
        ctrl_id: msg.ctrl_id,
        ok: r.ok,
        recording: r.recording,
        error: r.error,
      });
    } catch (err) {
      this.send({ type: 'record-ack', ctrl_id: msg.ctrl_id, ok: false, error: errText(err) });
    }
  }

  private async onRecordStop(
    msg: Extract<GatewayToExtension, { type: 'record-stop' }>,
  ): Promise<void> {
    try {
      const r = await this.handlers.recordStop(msg.title, msg.description);
      this.send({
        type: 'record-ack',
        ctrl_id: msg.ctrl_id,
        ok: r.ok,
        recording: r.recording,
        tour_id: r.tour_id,
        event_count: r.event_count,
        error: r.error,
      });
    } catch (err) {
      this.send({ type: 'record-ack', ctrl_id: msg.ctrl_id, ok: false, error: errText(err) });
    }
  }

  private async onRunTour(msg: Extract<GatewayToExtension, { type: 'run-tour' }>): Promise<void> {
    try {
      const r = await this.handlers.runTour(msg.tour_id);
      this.send({ type: 'run-result', ctrl_id: msg.ctrl_id, status: r.status, error: r.error });
    } catch (err) {
      this.send({
        type: 'run-result',
        ctrl_id: msg.ctrl_id,
        status: 'failed',
        error: errText(err),
      });
    }
  }
}
