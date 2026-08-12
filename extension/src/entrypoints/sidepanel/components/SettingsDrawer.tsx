import { Bot, Boxes, Crosshair, LogOut, X } from 'lucide-react';
import { formatPicked } from '../../../api/client';
import { t } from '../../../i18n';
import type { PanelState } from '../../../types';
import { sendBg, useCopy } from '../lib';

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return t('settings.kilobytes', { value: Math.round(bytes / 1024) });
  return t('settings.megabytes', { value: (bytes / 1024 / 1024).toFixed(1) });
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
    <div className="drawer" role="dialog" aria-label={t('settings.title')}>
      <div className="drawer-head">
        <span className="section-title">{t('settings.title')}</span>
        <button className="icon-btn" aria-label={t('settings.close')} onClick={onClose}>
          <X size={15} />
        </button>
      </div>

      <div className="drawer-body">
        <div className="kv">
          <span>{t('settings.signed_in_as')}</span>
          <b>{state.auth.userName || '—'}</b>
        </div>
        <div className="kv">
          <span>{t('settings.workspace')}</span>
          <b>{state.auth.workspaceName || '—'}</b>
        </div>
        <div className="kv">
          <span>{t('settings.stept_url')}</span>
          <b className="truncate">{state.auth.apiBase}</b>
        </div>

        <div className="drawer-section">
          <span className="section-title">{t('settings.sandbox_title')}</span>
          <p className="hint">{t('settings.sandbox_hint')}</p>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={state.sandbox}
              onChange={(e) => void sendBg({ type: 'set-sandbox', sandbox: e.target.checked })}
            />
            <span>
              <Boxes size={13} /> {t('settings.sandbox_toggle')}
            </span>
          </label>
          {state.sandboxStats.captured > 0 && (
            <div className="kv">
              <span>{t('settings.captured')}</span>
              <b>
                {t('settings.captured_value', {
                  count: state.sandboxStats.captured,
                  size: formatBytes(state.sandboxStats.bytes),
                })}
              </b>
            </div>
          )}
        </div>

        <div className="drawer-section">
          <span className="section-title">{t('settings.picker_title')}</span>
          <p className="hint">{t('settings.picker_hint')}</p>
          <button className="btn" onClick={() => void sendBg({ type: 'picker-start' })}>
            <Crosshair size={13} /> {t('settings.pick_element')}
          </button>
          {picked && (
            <div className="picked">
              <code className="picked-sel">{picked.selector || t('settings.no_selector')}</code>
              {picked.fallbacks.length > 0 && (
                <div className="picked-fallbacks">
                  {picked.fallbacks.map((f) => (
                    <code key={f}>{f}</code>
                  ))}
                </div>
              )}
              {picked.textHint && (
                <div className="picked-text">{t('settings.picked_text', { text: picked.textHint })}</div>
              )}
              <div className="row">
                <button className="btn" onClick={() => copy(formatPicked(picked))}>
                  {copied ? t('settings.copied') : t('settings.copy')}
                </button>
                <button className="btn ghost" onClick={() => void sendBg({ type: 'clear-picked' })}>
                  {t('settings.clear')}
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="drawer-section">
          <span className="section-title">{t('settings.automation_title')}</span>
          <p className="hint">{t('settings.automation_hint')}</p>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={state.remoteControl}
              onChange={(e) => void sendBg({ type: 'set-remote-control', enabled: e.target.checked })}
            />
            <span>
              <Bot size={13} /> {t('settings.remote_toggle')}
            </span>
          </label>
          <div className="kv">
            <span>{t('settings.status')}</span>
            <b>{state.remoteConnected ? t('settings.connected') : t('settings.off')}</b>
          </div>
        </div>

        <div className="drawer-section">
          <button className="btn danger-outline" onClick={() => void sendBg({ type: 'sign-out' })}>
            <LogOut size={13} /> {t('settings.sign_out')}
          </button>
          <p className="hint">{t('settings.sign_out_hint')}</p>
        </div>
      </div>
    </div>
  );
}
