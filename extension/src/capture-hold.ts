/** Pre-capture pairing (ported verbatim from the old extension's
 * `capture-hold.ts`): a pointerdown fires a screenshot a few milliseconds
 * BEFORE the pointer/select/check event that will carry it. This holds the
 * freshly-uploaded key per tab and hands it to the next matching event — with a
 * freshness window so a stale shot from an abandoned pointerdown (drag,
 * cancelled click) never attaches to a later action.
 *
 * Token matching: the content script mints a token per pointerdown and echoes
 * it on the pointer event it emits. A held ref stamped with a token is only
 * ever handed to the event carrying that same token — so an interleaved event
 * (the debounced input flushed by the click, a change event racing the click)
 * can never STEAL the click's pre-capture and shift every following screenshot
 * by one step. Token-less holds/takes keep the old first-comer semantics.
 */

export interface HeldRef {
  ref: string;
  at: number;
  tabId: number;
  token?: string;
}

export const PRE_CAPTURE_FRESH_MS = 2500;

export class CaptureHold {
  private shots = new Map<number, HeldRef>();

  constructor(private now: () => number = Date.now) {}

  holdShot(tabId: number, ref: string, token?: string): void {
    this.shots.set(tabId, { ref, at: this.now(), tabId, token });
  }

  /** Consume the held screenshot for this tab if still fresh (one-shot). */
  takeShot(tabId: number, token?: string): string | null {
    const held = this.shots.get(tabId);
    if (!held) return null;
    // A tokened hold belongs to exactly one gesture; a token-less take must not
    // consume it (and a tokened take must not consume someone else's hold).
    if (held.token !== token) return null;
    this.shots.delete(tabId);
    if (this.now() - held.at > PRE_CAPTURE_FRESH_MS) return null;
    return held.ref;
  }

  /** Drop everything held for a tab — a navigation makes pre-nav refs stale. */
  clearTab(tabId: number): void {
    this.shots.delete(tabId);
  }

  clear(): void {
    this.shots.clear();
  }
}
