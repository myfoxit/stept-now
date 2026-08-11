import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ensureDeviceId,
  resetDeviceIdCacheForTests,
  RunClient,
  type RunClientHandlers,
} from './run-client';

/** The gateway socket, scripted by hand: tests open/close/feed it explicitly.
 * `close()` only marks the call — a real socket fires `onclose` async, and the
 * zombie-guard tests specifically assert the handlers were detached BEFORE it
 * could fire. */
class FakeWS {
  static instances: FakeWS[] = [];
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSING = 2;
  static CLOSED = 3;
  url: string;
  readyState = FakeWS.CONNECTING;
  sent: string[] = [];
  closeCalls = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closeCalls += 1;
    this.readyState = FakeWS.CLOSED;
  }

  open(): void {
    this.readyState = FakeWS.OPEN;
    this.onopen?.();
  }

  receive(payload: unknown): void {
    this.onmessage?.({ data: typeof payload === 'string' ? payload : JSON.stringify(payload) });
  }

  sentJson(): Array<Record<string, unknown>> {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }
}

function makeHandlers(overrides: Partial<RunClientHandlers> = {}): RunClientHandlers {
  return {
    execOp: vi.fn(async () => ({ url: 'https://x.test/', elements: '[0]<button Save>', count: 1 })),
    recordStart: vi.fn(async () => ({ ok: true, recording: true })),
    recordStop: vi.fn(async () => ({ ok: true, recording: false, tour_id: 't1', event_count: 4 })),
    runTour: vi.fn(async () => ({ status: 'completed' as const })),
    ...overrides,
  };
}

function makeClient(handlers = makeHandlers()): { client: RunClient; handlers: RunClientHandlers } {
  const client = new RunClient('http://localhost:8600', 'tok/1', 'dev 1', 'MacIntel Chrome', handlers);
  return { client, handlers };
}

const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeWS.instances = [];
});

function stubWs(): void {
  vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket);
}

describe('RunClient transport', () => {
  it('connects to /ws/extension with every query part encoded', () => {
    stubWs();
    const { client } = makeClient();
    client.start();
    expect(FakeWS.instances).toHaveLength(1);
    expect(FakeWS.instances[0]!.url).toBe(
      'ws://localhost:8600/ws/extension?token=tok%2F1&device_id=dev%201&name=MacIntel%20Chrome',
    );
  });

  it('arms the 20s in-socket ping when the socket opens', () => {
    vi.useFakeTimers();
    stubWs();
    const { client } = makeClient();
    client.start();
    const ws = FakeWS.instances[0]!;
    ws.open();
    expect(ws.sent).toHaveLength(0);
    vi.advanceTimersByTime(20_000);
    expect(ws.sentJson()).toEqual([{ type: 'ping' }]);
    vi.advanceTimersByTime(20_000);
    expect(ws.sentJson()).toEqual([{ type: 'ping' }, { type: 'ping' }]);
  });

  it('reconnects on a single-shot 3s timer — a double close cannot stack two', () => {
    vi.useFakeTimers();
    stubWs();
    const { client } = makeClient();
    client.start();
    const ws = FakeWS.instances[0]!;
    ws.open();
    ws.onclose?.();
    ws.onclose?.(); // a second close event must not schedule a second timer
    vi.advanceTimersByTime(2_999);
    expect(FakeWS.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeWS.instances).toHaveLength(2);
    // the single-shot guard collapsed the double schedule: no third socket
    vi.advanceTimersByTime(10_000);
    expect(FakeWS.instances).toHaveLength(2);
  });

  it('detaches a still-connecting socket before replacing it (zombie guard)', () => {
    stubWs();
    const { client } = makeClient();
    client.start();
    const zombie = FakeWS.instances[0]!; // never opened — still CONNECTING
    client.ensureConnected(); // not connected → connect() replaces it
    expect(FakeWS.instances).toHaveLength(2);
    expect(zombie.closeCalls).toBe(1);
    // handlers were nulled FIRST, so its close can't touch the replacement
    expect(zombie.onopen).toBeNull();
    expect(zombie.onmessage).toBeNull();
    expect(zombie.onclose).toBeNull();
    expect(zombie.onerror).toBeNull();
  });

  it('ensureConnected pings an open socket instead of reconnecting', () => {
    stubWs();
    const { client } = makeClient();
    client.start();
    const ws = FakeWS.instances[0]!;
    ws.open();
    client.ensureConnected();
    expect(FakeWS.instances).toHaveLength(1);
    expect(ws.sentJson()).toEqual([{ type: 'ping' }]);
  });

  it('stop() closes the socket, reports disconnected, and never reconnects', () => {
    vi.useFakeTimers();
    stubWs();
    const connection: boolean[] = [];
    const { client } = makeClient(makeHandlers({ onConnectionChange: (c) => connection.push(c) }));
    client.start();
    const ws = FakeWS.instances[0]!;
    ws.open();
    client.stop();
    expect(ws.closeCalls).toBe(1);
    expect(connection).toEqual([true, false]);
    vi.advanceTimersByTime(30_000);
    expect(FakeWS.instances).toHaveLength(1);
  });
});

describe('RunClient message routing', () => {
  function openClient(handlers = makeHandlers()): { ws: FakeWS; handlers: RunClientHandlers } {
    stubWs();
    const { client } = makeClient(handlers);
    client.start();
    // the socket THIS client just opened (an earlier client's may still exist)
    const ws = FakeWS.instances[FakeWS.instances.length - 1]!;
    ws.open();
    return { ws, handlers };
  }

  it('routes exec-op to the handler and replies exec-result on the same ctrl_id', async () => {
    const { ws, handlers } = openClient();
    ws.receive({ type: 'exec-op', ctrl_id: 'c1', op: 'snapshot', args: { offset: 0 } });
    await flush();
    expect(handlers.execOp).toHaveBeenCalledWith('snapshot', { offset: 0 });
    expect(ws.sentJson()).toEqual([
      {
        type: 'exec-result',
        ctrl_id: 'c1',
        ok: true,
        data: { url: 'https://x.test/', elements: '[0]<button Save>', count: 1 },
      },
    ]);
  });

  it('a throwing exec handler still replies — ok:false with the message', async () => {
    const { ws } = openClient(
      makeHandlers({
        execOp: vi.fn(async () => {
          throw new Error('no driven tab — call browser_open first');
        }),
      }),
    );
    ws.receive({ type: 'exec-op', ctrl_id: 'c2', op: 'act' });
    await flush();
    expect(ws.sentJson()).toEqual([
      { type: 'exec-result', ctrl_id: 'c2', ok: false, error: 'no driven tab — call browser_open first' },
    ]);
  });

  it('record-start and record-stop ack with recording state, tour id and event count', async () => {
    const { ws, handlers } = openClient();
    ws.receive({ type: 'record-start', ctrl_id: 'r1', url: 'https://x.test/flow' });
    ws.receive({ type: 'record-stop', ctrl_id: 'r2', title: 'Checkout' });
    await flush();
    expect(handlers.recordStart).toHaveBeenCalledWith('https://x.test/flow');
    expect(handlers.recordStop).toHaveBeenCalledWith('Checkout', undefined);
    const acks = ws.sentJson();
    expect(acks).toContainEqual({ type: 'record-ack', ctrl_id: 'r1', ok: true, recording: true });
    expect(acks).toContainEqual({
      type: 'record-ack',
      ctrl_id: 'r2',
      ok: true,
      recording: false,
      tour_id: 't1',
      event_count: 4,
    });
  });

  it('run-tour resolves to a run-result, and a thrown runner fails the ctrl', async () => {
    const { ws } = openClient();
    ws.receive({ type: 'run-tour', ctrl_id: 'x1', tour_id: 'tour-9', mode: 'driven' });
    await flush();
    expect(ws.sentJson()).toContainEqual({ type: 'run-result', ctrl_id: 'x1', status: 'completed' });

    const { ws: ws2 } = openClient(
      makeHandlers({
        runTour: vi.fn(async () => {
          throw new Error('tour not found');
        }),
      }),
    );
    ws2.receive({ type: 'run-tour', ctrl_id: 'x2', tour_id: 'nope', mode: 'driven' });
    await flush();
    expect(ws2.sentJson()).toContainEqual({
      type: 'run-result',
      ctrl_id: 'x2',
      status: 'failed',
      error: 'tour not found',
    });
  });

  it('ignores pong and malformed frames without replying', async () => {
    const { ws, handlers } = openClient();
    ws.receive({ type: 'pong' });
    ws.receive('{not json');
    ws.receive({ nonsense: true });
    await flush();
    expect(ws.sent).toHaveLength(0);
    expect(handlers.execOp).not.toHaveBeenCalled();
  });
});

describe('ensureDeviceId', () => {
  function stubStorage(store: Record<string, unknown>, getDelayMs = 0): void {
    vi.stubGlobal('chrome', {
      storage: {
        local: {
          get: vi.fn(async (key: string) => {
            if (getDelayMs) await new Promise((r) => setTimeout(r, getDelayMs));
            return { [key]: store[key] };
          }),
          set: vi.fn(async (items: Record<string, unknown>) => {
            Object.assign(store, items);
          }),
        },
      },
    });
  }

  it('mints once into chrome.storage.local and reuses it after', async () => {
    resetDeviceIdCacheForTests();
    const store: Record<string, unknown> = {};
    stubStorage(store);
    let minted = 0;
    vi.stubGlobal('crypto', { randomUUID: () => `uuid-${++minted}` });

    const first = await ensureDeviceId();
    resetDeviceIdCacheForTests(); // drop the in-memory cache — the STORE must answer
    const second = await ensureDeviceId();
    expect(first).toBe('uuid-1');
    expect(second).toBe('uuid-1');
    expect(minted).toBe(1);
    expect(store['stept.deviceId']).toBe('uuid-1');
  });

  it('two overlapping calls share ONE mint — never twin device ids', async () => {
    // The duplicate-registration bug: both callers read an empty store, both
    // minted, and this browser registered twice under different ids. The
    // single-flight guard makes overlapping callers share one read-or-mint.
    resetDeviceIdCacheForTests();
    const store: Record<string, unknown> = {};
    stubStorage(store, 5); // slow read so the calls genuinely overlap
    let minted = 0;
    vi.stubGlobal('crypto', { randomUUID: () => `uuid-${++minted}` });

    const [a, b] = await Promise.all([ensureDeviceId(), ensureDeviceId()]);
    expect(a).toBe(b);
    expect(minted).toBe(1);
    expect(store['stept.deviceId']).toBe(a);
  });

  it('converges on the stored winner when another context minted concurrently', async () => {
    // Cross-context race (two worker starts): whatever the store settled on
    // after our write is the identity this browser presents.
    resetDeviceIdCacheForTests();
    const store: Record<string, unknown> = {};
    vi.stubGlobal('chrome', {
      storage: {
        local: {
          get: vi.fn(async (key: string) => ({ [key]: store[key] })),
          set: vi.fn(async () => {
            // the OTHER context's write lands last
            store['stept.deviceId'] = 'their-uuid';
          }),
        },
      },
    });
    vi.stubGlobal('crypto', { randomUUID: () => 'our-uuid' });

    expect(await ensureDeviceId()).toBe('their-uuid');
  });
});
