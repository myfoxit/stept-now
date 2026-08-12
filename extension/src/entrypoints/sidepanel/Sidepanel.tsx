import { Component, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Circle,
  MousePointerClick,
  Pause,
  Play,
  RefreshCw,
  Settings,
  Square,
  Timer,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react';
import { compile } from '../../compiler';
import { t, tParts } from '../../i18n';
import type { BgToPanel, SimpleResult } from '../../messages';
import { emptyPanelState, type PanelState } from '../../types';
import { DrivePanel } from './components/DrivePanel';
import { EditPanel } from './components/EditPanel';
import { GuidePanel } from './components/GuidePanel';
import { Login } from './components/Login';
import { SavedBanner } from './components/SavedBanner';
import { SaveSheet } from './components/SaveSheet';
import { SettingsDrawer } from './components/SettingsDrawer';
import { StepRow } from './components/StepCards';
import { ToursPanel } from './components/ToursPanel';
import { fmtElapsed, Logo, sendBg, useNow } from './lib';

export function Sidepanel() {
  return (
    <PanelErrorBoundary>
      <PanelBody />
    </PanelErrorBoundary>
  );
}

/** Production seatbelt: a render crash shows a reload card instead of a
 * silently blank side panel. */
class PanelErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="panel">
        <div className="empty crash">
          <TriangleAlert size={20} />
          {t('sidepanel.crash')}
          <button className="btn" onClick={() => location.reload()}>
            <RefreshCw size={13} /> {t('sidepanel.reload_panel')}
          </button>
        </div>
      </div>
    );
  }
}

function PanelBody() {
  const [state, setState] = useState<PanelState>(() => emptyPanelState());
  const [error, setError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const now = useNow(state.recording && !state.paused);

  useEffect(() => {
    void sendBg<{ state: PanelState }>({ type: 'get-state' }).then((r) => r?.state && setState(r.state));
    const listener = (msg: BgToPanel) => {
      if (msg?.type === 'state') setState(msg.state);
      else if (msg?.type === 'error') setError(msg.message);
    };
    chrome.runtime.onMessage.addListener(listener);
    return () => chrome.runtime.onMessage.removeListener(listener);
  }, []);

  // Live compile: the panel runs the SAME pure compiler the save path does, so
  // what you see while recording is exactly what gets saved.
  const compiled = useMemo(
    () =>
      compile(state.events, {
        startUrl: state.startUrl,
        titleOverrides: state.titleOverrides,
        bodyOverrides: state.bodyOverrides,
        order: state.stepOrder,
      }),
    [state.events, state.startUrl, state.titleOverrides, state.bodyOverrides, state.stepOrder],
  );
  const steps = compiled.steps;
  const warnings = useMemo(
    () => new Map(compiled.warnings.map((w) => [w.stepId, w.reason])),
    [compiled.warnings],
  );

  // Keep the newest step in view while recording; jump back to the top when the
  // recording stops so review starts at the beginning.
  const listRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = state.recording ? el.scrollHeight : 0;
  }, [steps.length, state.recording]);

  if (!state.auth.signedIn) return <Login auth={state.auth} />;

  const move = (index: number, dir: -1 | 1) => {
    const next = [...steps];
    const j = index + dir;
    const a = next[index];
    const b = next[j];
    if (!a || !b) return;
    next[index] = b;
    next[j] = a;
    void sendBg({ type: 'reorder-steps', order: next.map((s) => s.id) });
  };

  const reviewing = !state.recording && steps.length > 0;
  const busyRunning = !!state.guide || !!state.drive;
  // "{{count}} steps" with the number lifted out, so it can render bold while
  // the words (and their order) stay the translator's.
  const [stepCountPre, stepCountPost] = tParts('sidepanel.step_count', 'count', {
    count: steps.length,
  });

  return (
    <div className="panel">
      <header className="panel-head">
        <div className="brand">
          <Logo /> Stept
        </div>
        <div className="head-actions">
          {state.recording && (
            <span className={`status-pill ${state.paused ? 'paused' : 'rec'}`}>
              <Circle size={9} fill="currentColor" />{' '}
              {state.paused ? t('sidepanel.paused') : t('sidepanel.recording')}
            </span>
          )}
          <button
            className="icon-btn"
            title={t('sidepanel.settings')}
            aria-label={t('sidepanel.open_settings')}
            onClick={() => setDrawerOpen(true)}
          >
            <Settings size={15} />
          </button>
        </div>
      </header>

      {(error || state.auth.error) && (
        <div className="banner danger" role="alert">
          <span className="banner-body">{error ?? state.auth.error}</span>
          <span className="banner-actions">
            <button
              className="btn ghost"
              aria-label={t('sidepanel.dismiss_error')}
              onClick={() => setError(null)}
            >
              <X size={13} /> {t('sidepanel.dismiss')}
            </button>
          </span>
        </div>
      )}

      {state.drive ? (
        <DrivePanel drive={state.drive} />
      ) : state.guide ? (
        <GuidePanel guide={state.guide} />
      ) : state.editing ? (
        <EditPanel editing={state.editing} state={state} onError={setError} />
      ) : (
        <>
          {state.recording && (
            <div className="stat-row">
              <span className="stat">
                {stepCountPre}
                <b>{steps.length}</b>
                {stepCountPost}
              </span>
              <span className="stat">
                <Timer size={12} /> {fmtElapsed(now - (state.startedAt ?? now))}
              </span>
            </div>
          )}

          {!state.recording ? (
            <button
              className="btn record big"
              disabled={state.saving || busyRunning}
              onClick={() => {
                setError(null);
                void sendBg({ type: 'start-recording' });
              }}
            >
              <Circle size={14} fill="currentColor" /> {t('sidepanel.start_recording')}
            </button>
          ) : (
            <div className="row">
              <button
                className="btn"
                onClick={() => void sendBg({ type: 'pause-recording', paused: !state.paused })}
              >
                {state.paused ? <Play size={14} /> : <Pause size={14} />}
                {state.paused ? t('sidepanel.resume') : t('sidepanel.pause')}
              </button>
              <button className="btn danger" onClick={() => void sendBg({ type: 'stop-recording' })}>
                <Square size={13} fill="currentColor" /> {t('sidepanel.stop')}
              </button>
            </div>
          )}

          {state.lastSave && <SavedBanner saved={state.lastSave} />}

          {(state.recording || steps.length > 0) && (
            <div className={`step-list${state.saving ? ' is-saving' : ''}`} ref={listRef} aria-busy={state.saving}>
              {steps.length === 0 && state.recording && (
                <div className="empty">
                  <MousePointerClick size={20} />
                  {t('sidepanel.record_empty')}
                </div>
              )}
              {steps.map((step, i) => (
                <StepRow
                  key={step.id}
                  index={i}
                  step={step}
                  apiBase={state.auth.apiBase}
                  workspaceId={state.auth.workspaceId}
                  isFirst={i === 0}
                  isLast={i === steps.length - 1}
                  warning={warnings.get(step.id)}
                  onRename={(title) => void sendBg({ type: 'retitle-step', stepId: step.id, title })}
                  onBody={(body) => void sendBg({ type: 'set-step-body', stepId: step.id, body })}
                  onMove={(dir) => move(i, dir)}
                  onDelete={() => {
                    const sources = compiled.sources[step.id] ?? [];
                    if (sources.length) void sendBg({ type: 'delete-events', indexes: sources });
                  }}
                />
              ))}
            </div>
          )}

          {reviewing && (
            <>
              <SaveSheet
                defaultName={compiled.suggestedName}
                defaultUrlPattern={compiled.suggestedUrlPattern}
                saving={state.saving}
                stepCount={steps.length}
                onSave={async (opts) => {
                  setError(null);
                  const r = await sendBg<SimpleResult>({ type: 'save-tour', ...opts });
                  if (r && !r.ok && r.error) setError(r.error);
                }}
              />
              <button className="btn ghost" onClick={() => void sendBg({ type: 'discard-recording' })}>
                <Trash2 size={13} /> {t('sidepanel.discard_recording')}
              </button>
            </>
          )}

          {!state.recording && steps.length === 0 && <ToursPanel state={state} onError={setError} />}
        </>
      )}

      <SettingsDrawer open={drawerOpen} state={state} onClose={() => setDrawerOpen(false)} />
    </div>
  );
}
