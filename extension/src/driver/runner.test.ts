import { afterEach, describe, expect, it, vi } from 'vitest';
import { DriveRunner, type DriveReport } from './runner';
import type { TourStep } from '../types';

/**
 * The step-failure policy is what decides whether a dead run keeps the
 * browser's drive lock. It is reachable without a browser: an `action` step
 * with no action config fails before the runner touches `chrome.*`, so the
 * whole ask-vs-abort contract is testable with only the driver island stubbed
 * (run()'s `finally` clears the banner through it).
 */

function stubChrome(): void {
  vi.stubGlobal('chrome', {
    tabs: { sendMessage: vi.fn(async () => null) },
    scripting: { executeScript: vi.fn(async () => []) },
    debugger: { attach: vi.fn(async () => undefined), detach: vi.fn(async () => undefined) },
  });
}

/** Fails immediately inside runStep — no CDP, no page, no timers. */
const brokenStep = (): TourStep =>
  ({ id: 's1', type: 'action', selector: '', title: 'Click the thing' }) as unknown as TourStep;

function run(onStepFailure?: 'ask' | 'abort') {
  stubChrome();
  const reports: DriveReport[] = [];
  const runner = new DriveRunner({
    tabId: 1,
    steps: [brokenStep()],
    report: (patch) => reports.push(patch),
    ...(onStepFailure ? { onStepFailure } : {}),
  });
  return { runner, reports };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('DriveRunner step-failure policy', () => {
  it('parks on the prompt by default, so a watching user can skip or retry', async () => {
    const { runner, reports } = run();
    const settled = vi.fn();
    void runner.run().then(settled);
    await vi.waitFor(() => expect(reports.some((r) => r.awaitingDecision)).toBe(true));

    // still parked: the run must NOT resolve while it waits for a human
    expect(settled).not.toHaveBeenCalled();

    runner.resolveDecision('abort');
    await vi.waitFor(() => expect(settled).toHaveBeenCalled());
  });

  it('aborts instead of asking when nobody is at the panel (remote runs)', async () => {
    const { runner, reports } = run('abort');
    await runner.run();

    expect(reports.some((r) => r.status === 'error')).toBe(true);
    // The lock-up bug: any report leaving awaitingDecision set strands the run,
    // because no runner will ever be there to answer it.
    expect(reports.some((r) => r.awaitingDecision)).toBe(false);
  });

  it('reports the failure reason so a remote caller learns which step broke', async () => {
    const { runner, reports } = run('abort');
    await runner.run();

    const failure = reports.find((r) => r.status === 'error' && r.error);
    expect(failure?.error).toContain('action');
  });
});
