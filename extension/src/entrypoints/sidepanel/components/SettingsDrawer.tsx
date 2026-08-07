import { Bot, Boxes, Crosshair, LogOut, X } from 'lucide-react';
import { formatPicked } from '../../../api/client';
import type { PanelState } from '../../../types';
import { sendBg, useCopy } from '../lib';

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Account + tools drawer: who you are signed in as, the selector picker, and
 * sign-out (which forgets the extension token). */
export function SettingsDrawer({
  open,
  state,
  onClose,
}: {
  open: boolean;
  state: PanelState;
  onClose: () => void;
}) {
  const [copied, copy] = useCopy();
  if (!open) return null;
  const picked = state.picked;

  return (
    <div className="drawer" role="dialog" aria-label="Settings">
      <div className="drawer-head">
        <span className="section-title">Settings</span>
        <button className="icon-btn" aria-label="Close settings" onClick={onClose}>
          <X size={15} />
        </button>
      </div>

      <div className="drawer-body">
        <div className="kv">
          <span>Signed in as</span>
          <b>{state.auth.userName || '—'}</b>
        </div>
        <div className="kv">
          <span>Workspace</span>
          <b>{state.auth.workspaceName || '—'}</b>
        </div>
        <div className="kv">
          <span>Stept URL</span>
          <b className="truncate">{state.auth.apiBase}</b>
        </div>

        <div className="drawer-section">
          <span className="section-title">Sandbox capture</span>
          <p className="hint">
            Also save a copy of each screen while recording, so the tour can be replayed as an
            interactive demo without anyone signing in to your app. Slower to record, and the
            copy is only as private as the data on screen.
          </p>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={state.sandbox}
              onChange={(e) => void sendBg({ type: 'set-sandbox', sandbox: e.target.checked })}
            />
            <span>
              <Boxes size={13} /> Capture screens for sandbox
            </span>
          </label>
          {state.sandboxStats.captured > 0 && (
            <div className="kv">
              <span>Captured</span>
              <b>
                {state.sandboxStats.captured} screen
                {state.sandboxStats.captured === 1 ? '' : 's'} ·{' '}
                {formatBytes(state.sandboxStats.bytes)}
              </b>
            </div>
          )}
        </div>

        <div className="drawer-section">
          <span className="section-title">Selector picker</span>
          <p className="hint">
            Click any element on the page to capture the selector Stept would record for it — handy
            for re-targeting a step by hand.
          </p>
          <button className="btn" onClick={() => void sendBg({ type: 'picker-start' })}>
            <Crosshair size={13} /> Pick an element
          </button>
          {picked && (
            <div className="picked">
              <code className="picked-sel">{picked.selector || '(no selector)'}</code>
              {picked.fallbacks.length > 0 && (
                <div className="picked-fallbacks">
                  {picked.fallbacks.map((f) => (
                    <code key={f}>{f}</code>
                  ))}
                </div>
              )}
              {picked.textHint && <div className="picked-text">“{picked.textHint}”</div>}
              <div className="row">
                <button className="btn" onClick={() => copy(formatPicked(picked))}>
                  {copied ? 'Copied' : 'Copy'}
                </button>
                <button className="btn ghost" onClick={() => void sendBg({ type: 'clear-picked' })}>
                  Clear
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="drawer-section">
          <span className="section-title">Automation</span>
          <p className="hint">
            Allow AI clients connected to your workspace (over MCP) to open a tab in this browser,
            drive it, and record tours. Turning this off disconnects immediately.
          </p>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={state.remoteControl}
              onChange={(e) => void sendBg({ type: 'set-remote-control', enabled: e.target.checked })}
            />
            <span>
              <Bot size={13} /> Let Stept control this browser
            </span>
          </label>
          <div className="kv">
            <span>Status</span>
            <b>{state.remoteConnected ? 'Connected' : 'Off'}</b>
          </div>
        </div>

        <div className="drawer-section">
          <button className="btn danger-outline" onClick={() => void sendBg({ type: 'sign-out' })}>
            <LogOut size={13} /> Sign out
          </button>
          <p className="hint">Forgets the stored extension token on this browser.</p>
        </div>
      </div>
    </div>
  );
}
