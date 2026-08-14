/**
 * Should the MV3 keepalive alarm stay armed?
 *
 * Extracted from the background's `onAlarm` listener because getting this wrong
 * is unrecoverable rather than merely wasteful. The alarm is the only thing that
 * can revive a torn-down service worker — RunClient's reconnect is a
 * `setTimeout` and dies with the worker — so a false negative here clears the
 * alarm, and the extension stays offline until the user reloads it by hand.
 *
 * The subtle case is a worker the alarm itself just revived: module state is
 * blank (no runClient, every flag false) while `restore()` is still awaiting its
 * first storage read. Hence the decision keys off the restored *session*, not
 * off `runClient`, which `startRunClient()` installs asynchronously.
 */
export interface KeepaliveInput {
  /** a recording is capturing events */
  recording: boolean;
  /** a guided tour is being shown (null when idle) */
  guide: unknown;
  /** a driven "do-it-for-me" run is in flight (null when idle) */
  drive: unknown;
  /** the gateway client is installed (may still be connecting) */
  hasRunClient: boolean;
  /** a signed-in session was restored from storage */
  hasSession: boolean;
  /** "Let Stept control this browser" — false kills the transport entirely */
  remoteControl: boolean;
}

export function keepaliveNeeded(input: KeepaliveInput): boolean {
  if (input.recording || input.guide != null || input.drive != null) return true;
  if (input.hasRunClient) return true;
  // Signed in with remote control on: stay reachable even with nothing running,
  // otherwise an idle browser silently drops off the MCP `browser_list`.
  return input.hasSession && input.remoteControl;
}
