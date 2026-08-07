/**
 * The PURE half of the CDP session: chord parsing (modifier bits + macOS
 * editing commands), the per-character key layout, telemetry ring-buffer
 * folding, and the regex-safe pattern filter. Nothing here touches `chrome.*`
 * — the trusted-input dispatch itself is exercised by hand + the e2e harness.
 */
import { describe, expect, it } from 'vitest';
import {
  clampLimit,
  filterByPattern,
  keyInfoForChar,
  macCommandFor,
  MODIFIER_BITS,
  planChord,
  pushCapped,
  recordTelemetryEvent,
  stringifyRemoteObject,
  TELEMETRY_CAP,
  type ConsoleRow,
  type NetworkRow,
} from './cdp';

describe('planChord', () => {
  it('Meta+a carries the Meta bit, real key identity and the macOS selectAll command', () => {
    expect(planChord('Meta+a')).toEqual({
      modifiers: 4,
      key: 'a',
      code: 'KeyA',
      windowsVirtualKeyCode: 65,
      commands: ['selectAll'],
    });
  });

  it('ORs multiple modifier bits (Control+Shift+p) with no mac command', () => {
    const plan = planChord('Control+Shift+p');
    expect(plan.modifiers).toBe(2 | 8);
    expect(plan.code).toBe('KeyP');
    expect(plan.windowsVirtualKeyCode).toBe(80);
    expect(plan.commands).toBeUndefined();
  });

  it('a bare key is flagged single — the text-producing pressKey path handles it', () => {
    expect(planChord('Enter').single).toBe('Enter');
    expect(planChord('x').single).toBe('x');
  });

  it('understands modifier aliases and casing (cmd+shift+z → redo)', () => {
    const plan = planChord('cmd+shift+z');
    expect(plan.modifiers).toBe(4 | 8);
    expect(plan.commands).toEqual(['redo']);
  });

  it('named keys keep their KEY_DEFS identity (Meta+ArrowLeft → line start)', () => {
    expect(planChord('Meta+ArrowLeft')).toMatchObject({
      key: 'ArrowLeft',
      code: 'ArrowLeft',
      windowsVirtualKeyCode: 37,
      commands: ['moveToLeftEndOfLine'],
    });
  });

  it('Shift extends a navigation command into a selection command', () => {
    expect(planChord('Meta+Shift+ArrowLeft').commands).toEqual(['moveToLeftEndOfLineAndModifySelection']);
    expect(planChord('Shift+ArrowRight').commands).toEqual(['moveRightAndModifySelection']);
  });

  it('deletion helpers map (Alt+Backspace → deleteWordBackward)', () => {
    expect(planChord('Alt+Backspace')).toMatchObject({ modifiers: 1, commands: ['deleteWordBackward'] });
  });

  it('tolerates stray whitespace around the parts', () => {
    expect(planChord(' Meta + a ')).toMatchObject({ modifiers: 4, commands: ['selectAll'] });
  });
});

describe('macCommandFor', () => {
  it('meta+arrow up/down jump to the document ends', () => {
    expect(macCommandFor(['meta', 'arrowup'])).toBe('moveToBeginningOfDocument');
    expect(macCommandFor(['meta', 'arrowdown'])).toBe('moveToEndOfDocument');
  });

  it('alt+arrow moves by word; shift extends it', () => {
    expect(macCommandFor(['alt', 'arrowright'])).toBe('moveWordRight');
    expect(macCommandFor(['alt', 'shift', 'arrowleft'])).toBe('moveWordLeftAndModifySelection');
  });

  it('returns undefined for chords with no editing selector', () => {
    expect(macCommandFor(['ctrl', 'p'])).toBeUndefined();
    expect(macCommandFor(['meta', 'k'])).toBeUndefined();
  });
});

describe('keyInfoForChar', () => {
  it('letters report their physical key; uppercase adds shift', () => {
    expect(keyInfoForChar('a')).toEqual({ code: 'KeyA', keyCode: 65, shift: false });
    expect(keyInfoForChar('Z')).toEqual({ code: 'KeyZ', keyCode: 90, shift: true });
    expect(keyInfoForChar('5')).toEqual({ code: 'Digit5', keyCode: 53, shift: false });
  });

  it('shifted symbols share the base key and set shift', () => {
    expect(keyInfoForChar('!')).toEqual({ code: 'Digit1', keyCode: 49, shift: true });
    expect(keyInfoForChar('?')).toEqual({ code: 'Slash', keyCode: 191, shift: true });
  });

  it('returns null for chars with no physical key (insertText fallback)', () => {
    expect(keyInfoForChar('€')).toBeNull();
    expect(keyInfoForChar('嗨')).toBeNull();
    expect(keyInfoForChar('🎉')).toBeNull();
  });
});

describe('MODIFIER_BITS', () => {
  it('matches the CDP bitmask (Alt=1, Control=2, Meta=4, Shift=8)', () => {
    expect(MODIFIER_BITS.alt).toBe(1);
    expect(MODIFIER_BITS.control).toBe(2);
    expect(MODIFIER_BITS.meta).toBe(4);
    expect(MODIFIER_BITS.shift).toBe(8);
    expect(MODIFIER_BITS.cmd).toBe(4); // alias
  });
});

describe('filterByPattern', () => {
  const rows = [{ text: 'GET /api/users 200' }, { text: 'TypeError: boom' }, { text: 'ws connected' }];

  it('filters by case-insensitive regex', () => {
    expect(filterByPattern(rows, 'typeerror|WS', (r) => r.text)).toHaveLength(2);
  });

  it('an invalid regex degrades to substring match instead of throwing', () => {
    const weird = [{ text: 'call(x) failed' }, { text: 'fine' }];
    expect(filterByPattern(weird, '(x)', (r) => r.text)).toEqual([{ text: 'call(x) failed' }]);
  });

  it('no pattern returns a copy of everything', () => {
    const out = filterByPattern(rows, undefined, (r) => r.text);
    expect(out).toEqual(rows);
    expect(out).not.toBe(rows);
  });
});

describe('pushCapped / clampLimit', () => {
  it('drops the oldest rows past the cap', () => {
    const buf: number[] = [];
    for (let i = 0; i < TELEMETRY_CAP + 5; i++) pushCapped(buf, i);
    expect(buf).toHaveLength(TELEMETRY_CAP);
    expect(buf[0]).toBe(5);
    expect(buf[buf.length - 1]).toBe(TELEMETRY_CAP + 4);
  });

  it('clamps a read limit into [1, cap] with a default of 40', () => {
    expect(clampLimit(undefined)).toBe(40);
    expect(clampLimit(0)).toBe(1);
    expect(clampLimit(10_000)).toBe(TELEMETRY_CAP);
    expect(clampLimit(7)).toBe(7);
  });
});

describe('recordTelemetryEvent', () => {
  const fresh = (): { console: ConsoleRow[]; network: NetworkRow[] } => ({ console: [], network: [] });

  it('folds console API calls with stringified args', () => {
    const bufs = fresh();
    recordTelemetryEvent(
      bufs,
      'Runtime.consoleAPICalled',
      { type: 'warn', args: [{ value: 'rate limit' }, { value: { retry: 3 } }] },
      111,
    );
    expect(bufs.console).toEqual([{ level: 'warn', text: 'rate limit {"retry":3}', t: 111 }]);
  });

  it('folds page exceptions as error rows', () => {
    const bufs = fresh();
    recordTelemetryEvent(
      bufs,
      'Runtime.exceptionThrown',
      { exceptionDetails: { exception: { description: 'TypeError: x is not a function' } } },
      1,
    );
    expect(bufs.console[0]).toMatchObject({ level: 'error', text: 'TypeError: x is not a function' });
  });

  it('folds browser Log entries', () => {
    const bufs = fresh();
    recordTelemetryEvent(bufs, 'Log.entryAdded', { entry: { level: 'warning', text: 'mixed content' } }, 2);
    expect(bufs.console[0]).toMatchObject({ level: 'warning', text: 'mixed content' });
  });

  it('merges the response status into the pending request row by requestId', () => {
    const bufs = fresh();
    recordTelemetryEvent(
      bufs,
      'Network.requestWillBeSent',
      { requestId: 'r1', request: { method: 'POST', url: 'https://api.test/things' } },
      10,
    );
    recordTelemetryEvent(
      bufs,
      'Network.responseReceived',
      { requestId: 'r1', response: { status: 201, url: 'https://api.test/things' } },
      12,
    );
    expect(bufs.network).toEqual([
      { method: 'POST', url: 'https://api.test/things', status: 201, t: 10, requestId: 'r1' },
    ]);
  });

  it('matches by requestId, not URL — two calls to the same URL resolve independently', () => {
    const bufs = fresh();
    const url = 'https://api.test/poll';
    recordTelemetryEvent(bufs, 'Network.requestWillBeSent', { requestId: 'a', request: { method: 'GET', url } }, 1);
    recordTelemetryEvent(bufs, 'Network.requestWillBeSent', { requestId: 'b', request: { method: 'GET', url } }, 2);
    recordTelemetryEvent(bufs, 'Network.responseReceived', { requestId: 'a', response: { status: 500, url } }, 3);
    expect(bufs.network.find((n) => n.requestId === 'a')?.status).toBe(500);
    expect(bufs.network.find((n) => n.requestId === 'b')?.status).toBeUndefined();
  });

  it('a response whose request scrolled out of the ring gets its own row', () => {
    const bufs = fresh();
    recordTelemetryEvent(
      bufs,
      'Network.responseReceived',
      { requestId: 'ghost', response: { status: 404, url: 'https://api.test/missing' } },
      9,
    );
    expect(bufs.network).toEqual([
      { method: 'GET', url: 'https://api.test/missing', status: 404, t: 9, requestId: 'ghost' },
    ]);
  });

  it('both buffers ring at the cap', () => {
    const bufs = fresh();
    for (let i = 0; i < TELEMETRY_CAP + 10; i++) {
      recordTelemetryEvent(bufs, 'Runtime.consoleAPICalled', { type: 'log', args: [{ value: `m${i}` }] }, i);
    }
    expect(bufs.console).toHaveLength(TELEMETRY_CAP);
    expect(bufs.console[0]!.text).toBe('m10');
  });
});

describe('stringifyRemoteObject', () => {
  it('renders primitives, JSON values, descriptions and sentinels', () => {
    expect(stringifyRemoteObject({ value: 'plain' })).toBe('plain');
    expect(stringifyRemoteObject({ value: { a: 1 } })).toBe('{"a":1}');
    expect(stringifyRemoteObject({ description: 'Error: boom\n  at x' })).toBe('Error: boom\n  at x');
    expect(stringifyRemoteObject({ type: 'undefined' })).toBe('undefined');
    expect(stringifyRemoteObject({ type: 'object', subtype: 'null' })).toBe('null');
    expect(stringifyRemoteObject({ type: 'function', className: 'Function' })).toBe('Function');
  });
});
