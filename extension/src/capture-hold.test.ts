import { describe, expect, it } from 'vitest';
import { CaptureHold, PRE_CAPTURE_FRESH_MS } from './capture-hold';

/** Ported from the old repo's `capture-hold.test.ts`. The token pairing is the
 * fix for the "every screenshot is one step behind" bug: an interleaved event
 * must never steal the click's pre-capture. */

function hold(startAt = 0) {
  let now = startAt;
  const h = new CaptureHold(() => now);
  return { h, advance: (ms: number) => (now += ms) };
}

describe('CaptureHold', () => {
  it('hands a tokened hold only to the event echoing that token', () => {
    const { h } = hold();
    h.holdShot(1, 'key-a', 'tok-1');
    expect(h.takeShot(1, 'tok-2')).toBeNull(); // a different gesture
    expect(h.takeShot(1)).toBeNull(); // a token-less interleaved event
    expect(h.takeShot(1, 'tok-1')).toBe('key-a');
  });

  it('is one-shot: a consumed hold is gone', () => {
    const { h } = hold();
    h.holdShot(1, 'key-a', 'tok');
    expect(h.takeShot(1, 'tok')).toBe('key-a');
    expect(h.takeShot(1, 'tok')).toBeNull();
  });

  it('keeps tabs independent', () => {
    const { h } = hold();
    h.holdShot(1, 'tab-one', 'tok');
    h.holdShot(2, 'tab-two', 'tok');
    expect(h.takeShot(2, 'tok')).toBe('tab-two');
    expect(h.takeShot(1, 'tok')).toBe('tab-one');
  });

  it('drops a stale shot from an abandoned pointerdown', () => {
    const { h, advance } = hold();
    h.holdShot(1, 'key-a', 'tok');
    advance(PRE_CAPTURE_FRESH_MS + 1);
    expect(h.takeShot(1, 'tok')).toBeNull();
  });

  it('clears a tab on navigation (pre-nav frames are pictures of a dead page)', () => {
    const { h } = hold();
    h.holdShot(1, 'key-a', 'tok');
    h.clearTab(1);
    expect(h.takeShot(1, 'tok')).toBeNull();
  });

  it('clears everything when a new recording starts', () => {
    const { h } = hold();
    h.holdShot(1, 'a', 't');
    h.holdShot(2, 'b', 't');
    h.clear();
    expect(h.takeShot(1, 't')).toBeNull();
    expect(h.takeShot(2, 't')).toBeNull();
  });
});
