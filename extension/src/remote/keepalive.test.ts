import { describe, expect, it } from 'vitest';
import { type KeepaliveInput, keepaliveNeeded } from './keepalive';

const idle: KeepaliveInput = {
  recording: false,
  guide: null,
  drive: null,
  hasRunClient: false,
  hasSession: false,
  remoteControl: true,
};

describe('keepaliveNeeded', () => {
  it('drops the alarm when signed out with nothing running', () => {
    expect(keepaliveNeeded(idle)).toBe(false);
  });

  it('drops the alarm when the user turned remote control off', () => {
    expect(keepaliveNeeded({ ...idle, hasSession: true, remoteControl: false })).toBe(false);
  });

  /** The regression this module exists for. The alarm revives the worker, and
   * its listener runs while `restore()` is still awaiting storage — so
   * `hasRunClient` is false even though a session is about to reconnect. Judging
   * on runClient alone cleared the alarm, and since RunClient's reconnect is a
   * setTimeout that died with the worker, nothing could ever wake it again:
   * the extension stayed offline until reloaded by hand. */
  it('keeps the alarm for a signed-in browser whose runClient is still connecting', () => {
    expect(keepaliveNeeded({ ...idle, hasSession: true, hasRunClient: false })).toBe(true);
  });

  it('keeps the alarm once the gateway client is installed', () => {
    expect(keepaliveNeeded({ ...idle, hasRunClient: true })).toBe(true);
  });

  it.each([
    ['a recording', { recording: true }],
    ['a guided tour', { guide: { stepIndex: 0 } }],
    ['a driven run', { drive: { status: 'running' } }],
  ])('keeps the alarm during %s even when signed out', (_label, patch) => {
    expect(keepaliveNeeded({ ...idle, ...patch })).toBe(true);
  });

  it('keeps the alarm for in-flight work regardless of the remote-control switch', () => {
    expect(keepaliveNeeded({ ...idle, recording: true, remoteControl: false })).toBe(true);
  });
});
