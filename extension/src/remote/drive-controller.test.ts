import { describe, expect, it } from 'vitest';
import type { ExecOpName } from '../messages';
import { RemoteDriveController, type DriveDeps, type RemoteCdp } from './drive-controller';

/** CDP stub logging every trusted-input call — the controller's op logic runs
 * pure against it (no chrome.debugger, no tabs). */
interface CdpStub extends RemoteCdp {
  log: string[];
}

function makeCdp(overrides: Partial<RemoteCdp> = {}): CdpStub {
  const log: string[] = [];
  const stub: CdpStub = {
    log,
    click: async (x, y, button = 'left', clickCount = 1) => {
      log.push(`click ${x},${y} ${button} x${clickCount}`);
    },
    hover: async (x, y) => {
      log.push(`hover ${x},${y}`);
    },
    insertText: async (text) => {
      log.push(`insert ${text}`);
    },
    typeText: async (text) => {
      log.push(`type ${text}`);
    },
    pressKey: async (key) => {
      log.push(`key ${key}`);
    },
    pressChord: async (chord) => {
      log.push(`chord ${chord}`);
    },
    clearField: async () => {
      log.push('clear');
    },
    scrollBy: async (_dx, dy) => {
      log.push(`wheel ${dy}`);
    },
    drag: async (fx, fy, tx, ty) => {
      log.push(`drag ${fx},${fy}->${tx},${ty}`);
    },
    navigate: async (url) => {
      log.push(`nav ${url}`);
    },
    awaitIdle: async () => {},
    evaluate: (async () => undefined) as RemoteCdp['evaluate'],
    // screenshot is 2× the viewport → the screenshot→CSS ratio under test is 0.5
    screenshot: async () => ({ data: 'JPEG64', w: 2560, h: 1600 }),
    viewportSize: async () => ({ w: 1280, h: 800 }),
    consoleMessages: () => [{ level: 'error', text: 'boom', t: 1 }],
    networkRequests: () => [{ method: 'GET', url: 'https://x.test/api', status: 200, t: 1 }],
    detach: async () => {
      log.push('detach');
    },
    ...overrides,
  };
  return stub;
}

type ExecFn = (op: ExecOpName, args?: Record<string, unknown>) => unknown | Promise<unknown>;

function defaultExec(op: ExecOpName): unknown {
  switch (op) {
    case 'url':
      return { url: 'https://x.test/', title: 'X' };
    case 'compact-dom':
      return { text: '[0]<button Save>', count: 1 };
    case 'dom-settle':
      return true;
    case 'resolve-index':
      return { found: true, x: 40, y: 60, tag: 'button', occluded: false, contentEditable: false, editable: false };
    case 'overlay-open':
      return { open: false };
    case 'scroll-at':
      return { moved: true };
    case 'page-text':
      return 'hello world';
    case 'find':
      return [{ index: 3, text: 'Save', tag: 'button', visible: true }];
    case 'extract':
      return { kind: 'text', value: 'v1' };
    default:
      return true;
  }
}

function makeHarness(exec?: ExecFn, cdp: CdpStub = makeCdp()) {
  const execCalls: Array<{ op: ExecOpName; args?: Record<string, unknown> }> = [];
  const sleeps: number[] = [];
  const closedTabs: number[] = [];
  const deps: DriveDeps = {
    attach: async () => cdp,
    exec: async (_tabId, op, args) => {
      execCalls.push({ op, args });
      return exec ? exec(op, args) : defaultExec(op);
    },
    createTab: async () => ({ id: 7 }),
    tabExists: async () => true,
    closeTab: async (tabId) => {
      closedTabs.push(tabId);
    },
    resizeWindow: async () => {},
    onTabCreated: () => {},
    offTabCreated: () => {},
    windowType: async () => 'normal',
    sleep: async (ms) => {
      sleeps.push(ms);
    },
  };
  const controller = new RemoteDriveController(deps);
  return { controller, cdp, execCalls, sleeps, closedTabs };
}

describe('RemoteDriveController', () => {
  it('open creates the tab, attaches, and returns a full snapshot', async () => {
    const { controller } = makeHarness();
    const snap = await controller.handle({ op: 'open', args: { url: 'https://x.test/' } });
    expect(snap.url).toBe('https://x.test/');
    expect(snap.elements).toBe('[0]<button Save>');
    expect(snap.count).toBe(1);
    expect(snap.screenshot).toBe('JPEG64');
    expect(snap.screenshotSize).toEqual({ w: 2560, h: 1600 });
    expect(snap.note).toContain('opened a new tab');
    expect(controller.sessionState).toMatchObject({ tabId: 7, url: 'https://x.test/', opCount: 1 });
  });

  it('any driven op before open fails with the no-driven-tab error', async () => {
    const { controller } = makeHarness();
    await expect(controller.handle({ op: 'snapshot' })).rejects.toThrow(
      'no driven tab — call browser_open first',
    );
  });

  it('a click that changes nothing visible appends the advisory note', async () => {
    const { controller } = makeHarness();
    await controller.handle({ op: 'open' });
    const snap = await controller.handle({ op: 'act', args: { index: 0, kind: 'click' } });
    expect(snap.note).toContain('the click produced no visible change');
  });

  it('a click that changes the page does not warn', async () => {
    const pages = ['[0]<button Save>', '[0]<dialog Saved>'];
    const { controller } = makeHarness((op) =>
      op === 'compact-dom' ? { text: pages.shift() ?? '[0]<dialog Saved>', count: 1 } : defaultExec(op),
    );
    await controller.handle({ op: 'open' });
    const snap = await controller.handle({ op: 'act', args: { index: 0 } });
    expect(snap.elements).toBe('[0]<dialog Saved>');
    expect(snap.note).toBeUndefined();
  });

  it('coordinate acts map screenshot pixels to CSS pixels through the calibrated ratio', async () => {
    const { controller, cdp } = makeHarness();
    await controller.handle({ op: 'open' }); // calibrates 2560x1600 → 1280x800
    await controller.handle({ op: 'act', args: { kind: 'click', x: 200, y: 300 } });
    expect(cdp.log).toContain('click 100,150 left x1');
  });

  it('act without an index or coordinates is rejected', async () => {
    const { controller } = makeHarness();
    await controller.handle({ op: 'open' });
    await expect(controller.handle({ op: 'act', args: { kind: 'click' } })).rejects.toThrow(
      'act needs an element index (from snapshot) or x/y coordinates',
    );
  });

  it('drag requires a drop point', async () => {
    const { controller } = makeHarness();
    await controller.handle({ op: 'open' });
    await expect(
      controller.handle({ op: 'act', args: { kind: 'drag', index: 0 } }),
    ).rejects.toThrow('drag needs toX/toY (the drop point)');
  });

  it('select/check act through the exec island and need an index', async () => {
    const { controller, execCalls } = makeHarness();
    await controller.handle({ op: 'open' });
    await controller.handle({ op: 'act', args: { kind: 'select', index: 2, text: 'Monthly' } });
    expect(execCalls).toContainEqual({ op: 'select', args: { index: 2, value: 'Monthly' } });
    await expect(controller.handle({ op: 'act', args: { kind: 'check' } })).rejects.toThrow(
      'check needs an element index',
    );
  });

  it('type into an indexed field clicks to focus, clears, inserts, then submits', async () => {
    const { controller, cdp } = makeHarness();
    await controller.handle({ op: 'open' });
    cdp.log.length = 0;
    await controller.handle({ op: 'act', args: { kind: 'type', index: 0, text: 'hi', submit: true } });
    expect(cdp.log).toEqual(['click 40,60 left x1', 'clear', 'insert hi', 'key Enter']);
  });

  it('refuses to type into a password field', async () => {
    // Typing goes through CDP, which never passes the island's set-value
    // password guard — so the refusal has to live here too.
    const { controller, cdp } = makeHarness((op) =>
      op === 'resolve-index'
        ? { found: true, x: 40, y: 60, tag: 'input', contentEditable: false, isPassword: true }
        : defaultExec(op),
    );
    await controller.handle({ op: 'open' });
    cdp.log.length = 0;

    await expect(
      controller.handle({ op: 'act', args: { kind: 'type', index: 0, text: 'hunter2' } }),
    ).rejects.toThrow(/password/i);
    expect(cdp.log).toEqual([]);
  });

  it('type with no target at all types into whatever has focus — no click, no clear', async () => {
    const { controller, cdp } = makeHarness();
    await controller.handle({ op: 'open' });
    cdp.log.length = 0;
    await controller.handle({ op: 'act', args: { kind: 'type', text: 'hi' } });
    expect(cdp.log).toEqual(['type hi']);
  });

  it('wait clamps to 8 seconds', async () => {
    const { controller, sleeps } = makeHarness();
    await controller.handle({ op: 'open' });
    await controller.handle({ op: 'wait', args: { ms: 60_000 } });
    expect(sleeps).toContain(8000);
    expect(sleeps).not.toContain(60_000);
  });

  it('an Enter that fails to dismiss the overlay warns instead of implying success', async () => {
    const { controller } = makeHarness((op) =>
      op === 'overlay-open' ? { open: true } : defaultExec(op),
    );
    await controller.handle({ op: 'open' });
    const snap = await controller.handle({ op: 'key', args: { key: 'Enter' } });
    expect(snap.note).toContain('the overlay is still open — Enter had no effect');
  });

  it('key chords route to pressChord, single keys to pressKey', async () => {
    const { controller, cdp } = makeHarness();
    await controller.handle({ op: 'open' });
    await controller.handle({ op: 'key', args: { key: 'Meta+a' } });
    await controller.handle({ op: 'key', args: { key: 'Escape' } });
    expect(cdp.log).toContain('chord Meta+a');
    expect(cdp.log).toContain('key Escape');
  });

  it('scroll falls back to a trusted wheel when the island reports nothing moved', async () => {
    const { controller, cdp } = makeHarness((op) =>
      op === 'scroll-at' ? { moved: false } : defaultExec(op),
    );
    await controller.handle({ op: 'open' });
    await controller.handle({ op: 'scroll', args: { dir: 'down', amount: 500 } });
    expect(cdp.log).toContain('wheel 500');
  });

  it('console/network reads return telemetry without the screenshot cost', async () => {
    const { controller } = makeHarness();
    await controller.handle({ op: 'open' });
    const c = await controller.handle({ op: 'console', args: { pattern: 'boo' } });
    expect(c.console).toEqual([{ level: 'error', text: 'boom', t: 1 }]);
    expect(c.screenshot).toBeUndefined();
    const n = await controller.handle({ op: 'network' });
    expect(n.network).toEqual([{ method: 'GET', url: 'https://x.test/api', status: 200, t: 1 }]);
  });

  it('ops are strictly serialized — the second op waits for the first', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    let armed = false; // open()'s own snapshot must sail through un-gated
    const { controller, execCalls } = makeHarness(async (op) => {
      if (op === 'dom-settle' && armed) {
        armed = false;
        await gate; // the FIRST queued snapshot hangs mid-op
      }
      return defaultExec(op);
    });
    await controller.handle({ op: 'open' });
    armed = true;
    execCalls.length = 0;
    const first = controller.handle({ op: 'snapshot' });
    const second = controller.handle({ op: 'page-text' });
    await new Promise((r) => setTimeout(r, 0));
    // the first op is parked on the gate; the second must not have started
    expect(execCalls.some((c) => c.op === 'page-text')).toBe(false);
    release();
    await first;
    await second;
    const pageTextAt = execCalls.findIndex((c) => c.op === 'page-text');
    const firstSnapshotDomAt = execCalls.findIndex((c) => c.op === 'compact-dom');
    expect(pageTextAt).toBeGreaterThan(firstSnapshotDomAt);
  });

  it('close detaches, closes the tab it created, and reports — and is benign when already closed', async () => {
    const { controller, cdp, closedTabs } = makeHarness();
    await controller.handle({ op: 'open' });
    const snap = await controller.handle({ op: 'close' });
    expect(snap.note).toBe('drive session closed');
    expect(cdp.log).toContain('detach');
    expect(closedTabs).toEqual([7]);
    expect(controller.sessionState).toBeNull();
    // double-close: nothing to do, still succeeds
    const again = await controller.handle({ op: 'close' });
    expect(again.note).toBe('drive session closed');
  });
});
