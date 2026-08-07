/**
 * The trusted-input path for drive mode: a `chrome.debugger` CDP session.
 *
 * This is the ONLY way an extension can dispatch input a page sees as
 * `isTrusted` — `el.click()` and synthetic KeyboardEvents are visibly fake to
 * any app that checks, and React's synthetic-event layer swallows a naive
 * `value =` assignment. Ported (subset) from the old repo's
 * `executor-extension.ts`: attach/reattach, `Input.dispatchMouseEvent` clicks,
 * `Input.insertText` typing, `Input.dispatchKeyEvent` keys, wheel scrolling.
 *
 * Attaching shows Chrome's "started debugging this browser" banner. If the user
 * refuses (or another debugger already owns the tab), `attach` returns null and
 * the runner falls back to the synthetic executor in the driver island.
 */

const CDP_TIMEOUT_MS = 15_000;

function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) => setTimeout(() => reject(new Error(`${label} timed out`)), ms)),
  ]);
}

/** Key metadata for the handful of non-printable keys drive mode presses. */
const KEY_DEFS: Record<string, Record<string, unknown>> = {
  Enter: { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' },
  Tab: { key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 },
  Escape: { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 },
  Backspace: { key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 },
  ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40 },
  ArrowUp: { key: 'ArrowUp', code: 'ArrowUp', windowsVirtualKeyCode: 38 },
};

export class CdpSession {
  private attached = false;

  private constructor(private readonly tabId: number) {}

  /** Attach + enable the domains we drive. Returns null when the user (or
   * another debugger client) refuses — the caller degrades to synthetic input
   * rather than failing the run. */
  static async attach(tabId: number): Promise<CdpSession | null> {
    const session = new CdpSession(tabId);
    try {
      await session.doAttach();
      return session;
    } catch {
      return null;
    }
  }

  private async doAttach(): Promise<void> {
    // detach-first for a clean slate (clears a stale attach from a prior run)
    await chrome.debugger.detach({ tabId: this.tabId }).catch(() => {});
    await chrome.debugger.attach({ tabId: this.tabId }, '1.3');
    this.attached = true;
    // NB: we deliberately do NOT focus or activate the tab — trusted CDP
    // Input.* lands on unfocused tabs, and activating would steal the window.
    await chrome.debugger.sendCommand({ tabId: this.tabId }, 'Page.enable', {}).catch(() => {});
    await chrome.debugger.sendCommand({ tabId: this.tabId }, 'Runtime.enable', {}).catch(() => {});
  }

  private async cdp<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
    retry = 0,
  ): Promise<T> {
    try {
      return (await withTimeout(
        chrome.debugger.sendCommand({ tabId: this.tabId }, method, params),
        CDP_TIMEOUT_MS,
        `CDP ${method}`,
      )) as T;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // A cross-document navigation or a service-worker restart detaches the
      // debugger mid-run; the next command then throws "not attached".
      // Reattach once and retry — the trick that survives SPA navigations.
      if (retry < 1 && /not attached|detached while handling/i.test(message)) {
        this.attached = false;
        await this.doAttach().catch(() => {});
        if (this.attached) return this.cdp<T>(method, params, retry + 1);
      }
      throw err;
    }
  }

  /** Hover first (so hover-reveal menus open), then press/release. */
  async click(x: number, y: number, button: 'left' | 'middle' | 'right' = 'left', clickCount = 1): Promise<void> {
    const buttons = button === 'left' ? 1 : button === 'right' ? 2 : 4;
    await this.cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none', buttons: 0 });
    for (let n = 1; n <= clickCount; n++) {
      await this.cdp('Input.dispatchMouseEvent', {
        type: 'mousePressed',
        x,
        y,
        button,
        buttons,
        clickCount: n,
        force: 0.5,
      });
      await this.cdp('Input.dispatchMouseEvent', {
        type: 'mouseReleased',
        x,
        y,
        button,
        buttons: 0,
        clickCount: n,
      });
    }
  }

  async hover(x: number, y: number): Promise<void> {
    await this.cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  }

  /** Trusted text entry. `Input.insertText` is one atomic insertion — fast and
   * React-safe. Canvas/rich editors that only listen to key events get the
   * per-character path instead. */
  async insertText(text: string, perCharacter = false): Promise<void> {
    if (!perCharacter) {
      await this.cdp('Input.insertText', { text });
      return;
    }
    for (const ch of text) {
      await this.cdp('Input.dispatchKeyEvent', { type: 'keyDown', key: ch, text: ch, unmodifiedText: ch });
      await this.cdp('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
    }
  }

  async pressKey(key: string): Promise<void> {
    const defn = KEY_DEFS[key] ?? (key.length === 1
      ? { key, text: key, unmodifiedText: key }
      : { key, code: key });
    await this.cdp('Input.dispatchKeyEvent', {
      type: 'text' in defn && defn.text ? 'keyDown' : 'rawKeyDown',
      ...defn,
    });
    await this.cdp('Input.dispatchKeyEvent', { type: 'keyUp', ...defn });
  }

  async scrollBy(deltaX: number, deltaY: number, x = 10, y = 200): Promise<void> {
    await this.cdp('Input.dispatchMouseEvent', { type: 'mouseWheel', x, y, deltaX, deltaY });
  }

  /** Clear the field before typing into it (select-all + delete). */
  async clearField(): Promise<void> {
    const mod = navigator.userAgent.includes('Mac') ? 4 : 2; // Meta : Ctrl
    await this.cdp('Input.dispatchKeyEvent', {
      type: 'rawKeyDown',
      key: 'a',
      code: 'KeyA',
      windowsVirtualKeyCode: 65,
      modifiers: mod,
      ...(mod === 4 ? { commands: ['selectAll'] } : {}),
    });
    await this.cdp('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', modifiers: mod });
    await this.pressKey('Backspace');
  }

  async detach(): Promise<void> {
    if (!this.attached) return;
    this.attached = false;
    await chrome.debugger.detach({ tabId: this.tabId }).catch(() => {});
  }

  // -------------------------------------------------------------------------
  // Remote-drive surface (W9). SIGNATURE STUBS — bodies are wave EXT-1's
  // (docs/MCP-CONTRACTS.md); the remote drive-controller (EXT-2) compiles
  // against these. Local drive above is untouched by any of it.
  // -------------------------------------------------------------------------

  /** Like `attach`, but a refusal THROWS: a remote caller must get the real
   * error over MCP, not a silent downgrade to synthetic input. */
  static async attachOrThrow(tabId: number): Promise<CdpSession> {
    const session = await CdpSession.attach(tabId);
    if (!session) {
      throw new Error(
        'Chrome refused the debugger attach on this tab (user declined, or another debugger owns it)',
      );
    }
    return session;
  }

  /** Viewport-clipped base64 JPEG, longest edge ≤ 1568 px. */
  async screenshot(_quality?: number): Promise<{ data: string; w: number; h: number }> {
    throw new Error('EXT-1: not implemented yet');
  }

  /** `Runtime.evaluate` with returnByValue + userGesture. */
  async evaluate<T = unknown>(_expression: string, _awaitPromise?: boolean): Promise<T> {
    throw new Error('EXT-1: not implemented yet');
  }

  /** CSS viewport size of the driven tab. */
  async viewportSize(): Promise<{ w: number; h: number }> {
    throw new Error('EXT-1: not implemented yet');
  }

  /** `Page.navigate` + load-event wait. */
  async navigate(_url: string): Promise<void> {
    throw new Error('EXT-1: not implemented yet');
  }

  /** readyState poll raced against a hard timer — never hangs a driven op. */
  async awaitIdle(_timeoutMs?: number): Promise<void> {
    throw new Error('EXT-1: not implemented yet');
  }

  /** Key chord ("Meta+a", "Control+Shift+p") with macOS editing commands. */
  async pressChord(_chord: string): Promise<void> {
    throw new Error('EXT-1: not implemented yet');
  }

  /** Interpolated trusted drag (pointer down → 10 moves → up). */
  async drag(_fromX: number, _fromY: number, _toX: number, _toY: number): Promise<void> {
    throw new Error('EXT-1: not implemented yet');
  }

  /** Per-character trusted typing with real key codes; emoji/CJK fall back to
   * `Input.insertText`. Use for canvas/rich editors; `insertText` otherwise. */
  async typeText(_text: string): Promise<void> {
    throw new Error('EXT-1: not implemented yet');
  }

  /** Console + page exceptions ring buffer (cap 200, armed on attach). */
  consoleMessages(_pattern?: string, _limit?: number): Array<{ level: string; text: string; t: number }> {
    throw new Error('EXT-1: not implemented yet');
  }

  /** Network request ring buffer (cap 200, armed on attach). */
  networkRequests(
    _pattern?: string,
    _limit?: number,
  ): Array<{ method: string; url: string; status?: number; t: number }> {
    throw new Error('EXT-1: not implemented yet');
  }
}
