import { t } from '../i18n';
import type { BgToDriver, DriverResolveResult } from '../messages';
import type { DriveStepStatus, TourStep } from '../types';
import { wildcardMatch } from '../url-pattern';
import { CdpSession } from './cdp';
import { locate, type LocateHit } from './locate';

/**
 * Drive mode: Stept performs the tour on the user's tab instead of coaching
 * them through it. Local only — the old repo drove browsers over a server
 * WebSocket (`run-client.ts`); that whole transport is omitted here (see the
 * B4 report), so nothing outside this browser can command it.
 *
 * Division of labour, ported from the old `drive-controller` +
 * `executor-extension` split: THIS loop decides what happens next and dispatches
 * trusted input over CDP; the driver island resolves/measures/settles in the
 * page. When `chrome.debugger` attach is refused, the same loop runs with
 * synthetic events instead — every step still executes, just untrusted.
 */

const LOCATE_TIMEOUT_MS = 8_000;
/** How long a purely-informational step stays on screen at 1×. */
const TOOLTIP_DWELL_MS = 2_200;
const SETTLE_QUIET_MS = 250;
const SETTLE_MAX_MS = 2_000;
const NAV_SETTLE_MS = 600;

export type DriveDecision = 'skip' | 'abort' | 'retry';

export interface DriveReport {
  index?: number;
  stepStatus?: DriveStepStatus;
  status?: 'running' | 'paused' | 'completed' | 'error';
  error?: string | null;
  awaitingDecision?: boolean;
  transport?: 'cdp' | 'synthetic';
}

export interface DriveRunnerOptions {
  tabId: number;
  steps: readonly TourStep[];
  /** pushed to the panel after every meaningful transition */
  report: (patch: DriveReport) => void;
  /**
   * What a failed step does. `ask` (default) parks the run on the side panel's
   * Skip/Retry/Abort prompt — right when a human is watching. `abort` fails the
   * run immediately: remote (MCP) runs have no operator at the panel, so asking
   * would block forever on a decision nobody is there to make, holding the
   * browser's drive lock with it.
   */
  onStepFailure?: 'ask' | 'abort';
}

const sleep = (ms: number) => new Promise<void>((res) => setTimeout(res, Math.max(0, ms)));

/** Send to the driver island, injecting it first if the page pre-dates install. */
async function toDriver(tabId: number, msg: BgToDriver): Promise<DriverResolveResult | null> {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      return (await chrome.tabs.sendMessage(tabId, msg)) as DriverResolveResult;
    } catch {
      if (attempt === 1) return null;
      await chrome.scripting
        .executeScript({ target: { tabId }, files: ['content-scripts/driver.js'] })
        .catch(() => {});
    }
  }
  return null;
}

export class DriveRunner {
  private readonly tabId: number;
  private readonly steps: readonly TourStep[];
  private readonly report: (patch: DriveReport) => void;
  private cdp: CdpSession | null = null;
  private stopped = false;
  private paused = false;
  private speed = 1;
  private index = 0;
  private decide: ((d: DriveDecision) => void) | null = null;
  private readonly onStepFailure: 'ask' | 'abort';

  constructor(opts: DriveRunnerOptions) {
    this.tabId = opts.tabId;
    this.steps = opts.steps;
    this.report = opts.report;
    this.onStepFailure = opts.onStepFailure ?? 'ask';
  }

  setSpeed(speed: number): void {
    this.speed = speed > 0 ? speed : 1;
  }

  setPaused(paused: boolean): void {
    this.paused = paused;
    this.report({ status: paused ? 'paused' : 'running' });
  }

  stop(): void {
    this.stopped = true;
    // unblock a step sitting on the error prompt, then forget the resolver
    const pending = this.decide;
    this.decide = null;
    pending?.('abort');
    void this.cdp?.detach();
    this.cdp = null;
  }

  /** Resolve a pending error prompt (Skip / Retry / Abort). */
  resolveDecision(decision: DriveDecision): void {
    const fn = this.decide;
    this.decide = null;
    fn?.(decision);
  }

  /** Attach trusted input if the user allows it; otherwise degrade quietly. */
  async prepare(): Promise<'cdp' | 'synthetic'> {
    this.cdp = await CdpSession.attach(this.tabId);
    const transport = this.cdp ? 'cdp' : 'synthetic';
    this.report({ transport });
    return transport;
  }

  async run(): Promise<void> {
    try {
      for (this.index = 0; this.index < this.steps.length; this.index += 1) {
        if (this.stopped) break;
        const step = this.steps[this.index];
        if (!step) continue;
        this.report({ index: this.index, stepStatus: 'active', error: null });
        await this.waitWhilePaused();
        if (this.stopped) break;

        const outcome = await this.runStepWithRecovery(step);
        if (outcome === 'abort') {
          this.report({ status: 'error', stepStatus: 'error', awaitingDecision: false });
          break;
        }
        this.report({ index: this.index, stepStatus: outcome === 'skip' ? 'skipped' : 'done' });
      }
      if (!this.stopped && this.index >= this.steps.length) {
        await toDriver(this.tabId, { type: 'driver-banner', text: '', ms: 0 });
        this.report({ status: 'completed', index: this.steps.length, awaitingDecision: false });
      }
    } finally {
      await this.cdp?.detach();
      this.cdp = null;
      await toDriver(this.tabId, { type: 'driver-banner', text: '', ms: 0 }).catch(() => null);
    }
  }

  /** Run one step; on failure surface it and honour the user's decision. */
  private async runStepWithRecovery(step: TourStep): Promise<'done' | 'skip' | 'abort'> {
    for (;;) {
      let failure: string | null = null;
      try {
        failure = await this.runStep(step);
      } catch (err) {
        failure = err instanceof Error ? err.message : String(err);
      }
      if (!failure || this.stopped) return this.stopped ? 'abort' : 'done';
      if (this.onStepFailure === 'abort') {
        // No operator to prompt — surface the reason and end the run. Never
        // set awaitingDecision here: nothing would ever clear it.
        this.report({ status: 'error', error: failure, awaitingDecision: false, stepStatus: 'error' });
        return 'abort';
      }
      this.report({ status: 'error', error: failure, awaitingDecision: true, stepStatus: 'error' });
      const decision = await new Promise<DriveDecision>((res) => {
        this.decide = res;
      });
      if (decision === 'abort') return 'abort';
      this.report({ status: 'running', error: null, awaitingDecision: false, stepStatus: 'active' });
      if (decision === 'skip') return 'skip';
      // retry → loop
    }
  }

  /** @returns null on success, or a human-readable failure reason. */
  private async runStep(step: TourStep): Promise<string | null> {
    switch (step.type) {
      case 'wait':
        return this.runWait(step);
      case 'action':
        return this.runAction(step);
      default:
        return this.runInformational(step);
    }
  }

  // ---- step kinds --------------------------------------------------------

  private async runInformational(step: TourStep): Promise<string | null> {
    await toDriver(this.tabId, {
      type: 'driver-banner',
      text: step.title || t('runner.driving'),
      ms: 0,
    });
    // an authored `advance: {on:"delay"}` is a deliberate dwell — honour it
    // instead of the default, still scaled by the speed dial
    const dwell = step.advance.on === 'delay' ? (step.advance.delay_ms ?? TOOLTIP_DWELL_MS) : TOOLTIP_DWELL_MS;
    if (step.selector || step.target) {
      const hit = await this.locateStep(step);
      if (!hit) return t('runner.element_not_found', { title: step.title });
      await toDriver(this.tabId, { type: 'driver-highlight', step, ms: dwell / this.speed });
    }
    await this.pace(dwell);
    return null;
  }

  private async runAction(step: TourStep): Promise<string | null> {
    const action = step.action;
    if (!action) return t('runner.no_action');

    if (action.kind === 'navigate') {
      if (!action.url) return t('runner.no_url');
      await toDriver(this.tabId, {
        type: 'driver-banner',
        text: t('runner.opening', { url: action.url }),
        ms: 0,
      });
      await chrome.tabs.update(this.tabId, { url: action.url }).catch(() => {});
      await sleep(NAV_SETTLE_MS);
      await toDriver(this.tabId, {
        type: 'driver-settle',
        quietMs: SETTLE_QUIET_MS,
        maxMs: SETTLE_MAX_MS,
      });
      return null;
    }

    await toDriver(this.tabId, {
      type: 'driver-banner',
      text: step.title || (action.kind === 'fill' ? t('runner.filling') : t('runner.clicking')),
      ms: 0,
    });
    const hit = await this.locateStep(step);
    if (!hit) return t('runner.element_not_found', { title: step.title });
    await toDriver(this.tabId, { type: 'driver-highlight', step, ms: 600 });
    await this.pace(320);

    if (this.cdp) {
      if (action.kind === 'click') {
        await this.cdp.click(hit.x, hit.y);
      } else {
        // focus the field with a real click, clear it, then insert the value.
        await this.cdp.click(hit.x, hit.y);
        await this.cdp.clearField();
        await this.cdp.insertText(action.value ?? '', hit.contentEditable === true);
      }
    } else {
      const res = await toDriver(this.tabId, {
        type: 'driver-act',
        step,
        kind: action.kind,
        value: action.value,
      });
      if (!res?.found) return t('runner.act_failed', { title: step.title });
    }

    await toDriver(this.tabId, {
      type: 'driver-settle',
      quietMs: SETTLE_QUIET_MS,
      maxMs: SETTLE_MAX_MS,
    });
    await this.pace(240);
    return null;
  }

  private async runWait(step: TourStep): Promise<string | null> {
    const spec = step.wait;
    if (!spec) return null;
    const timeout = spec.timeout_ms || 10_000;
    await toDriver(this.tabId, { type: 'driver-banner', text: step.title || t('runner.waiting'), ms: 0 });
    if (spec.for === 'url') {
      const pattern = spec.url_pattern ?? '';
      const deadline = Date.now() + timeout;
      for (;;) {
        if (this.stopped) return null;
        const tab = await chrome.tabs.get(this.tabId).catch(() => null);
        if (tab?.url && (!pattern || wildcardMatch(pattern, tab.url))) return null;
        if (Date.now() >= deadline) {
          return t('runner.never_reached', { target: pattern || t('runner.expected_url') });
        }
        await sleep(300);
      }
    }
    const res = await toDriver(this.tabId, { type: 'driver-wait-element', step, timeoutMs: timeout });
    return res?.found ? null : t('runner.element_never_appeared');
  }

  // ---- shared plumbing ---------------------------------------------------

  /** Locate with the ported confidence-banking loop, re-measuring once when the
   * click point is occluded (a spinner/menu animating over the target). */
  private async locateStep(step: TourStep): Promise<LocateHit | null> {
    const attempt = async (): Promise<LocateHit | null> => {
      const res = await toDriver(this.tabId, { type: 'driver-prepare', step });
      if (!res?.found || res.x === undefined || res.y === undefined) return null;
      return {
        x: res.x,
        y: res.y,
        confidence: res.confidence ?? 1,
        healed: res.healed ?? false,
        via: res.via ?? '',
        detail: res.detail ?? '',
        contentEditable: res.contentEditable,
        occluded: res.occluded,
      };
    };

    const outcome = await locate(
      {
        resolve: attempt,
        now: () => Date.now(),
        sleep,
        cancelled: () => this.stopped,
      },
      LOCATE_TIMEOUT_MS,
    );
    if (!outcome.ok) return null;
    if (outcome.hit.occluded) {
      await sleep(300);
      const fresh = await attempt();
      if (fresh) return fresh;
    }
    return outcome.hit;
  }

  private async waitWhilePaused(): Promise<void> {
    while (this.paused && !this.stopped) await sleep(150);
  }

  /** Every deliberate pause runs through here so the speed control is real. */
  private pace(ms: number): Promise<void> {
    return sleep(ms / this.speed);
  }
}
