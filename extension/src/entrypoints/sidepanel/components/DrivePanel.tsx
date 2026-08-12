import { useEffect, useRef } from 'react';
import {
  Bot,
  Check,
  MousePointerClick,
  Pause,
  PartyPopper,
  Play,
  ShieldAlert,
  SkipForward,
  Square,
  TriangleAlert,
} from 'lucide-react';
import { t } from '../../../i18n';
import type { DriveState } from '../../../types';
import { sendBg, STEP_ICONS } from '../lib';

const SPEEDS = [0.5, 1, 2];

/** Drive mode: Stept performs the tour. Play/pause/stop, a speed dial, a live
 * per-step status list, and the error surface that offers Skip / Retry / Abort
 * when a step cannot be executed. */
export function DrivePanel({ drive }: { drive: DriveState }) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const running = drive.status === 'running' || drive.status === 'paused';

  useEffect(() => {
    const el = listRef.current?.querySelector('[data-current="true"]');
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [drive.index]);

  return (
    <div className="run-panel">
      <div className="run-head">
        <span className="run-badge accent">
          <Bot size={13} />
        </span>
        <span className="run-title-wrap">
          <span className="run-title truncate" title={drive.name}>
            {drive.name}
          </span>
          <span className="run-sub">
            {drive.status === 'completed'
              ? t('drive.finished')
              : drive.status === 'error'
                ? t('drive.stopped_on_error')
                : t('drive.driving_step', {
                    current: Math.min(drive.index + 1, drive.total),
                    total: drive.total,
                  })}
          </span>
        </span>
      </div>

      {drive.transport === 'synthetic' && running && (
        <div className="banner warn" role="status">
          <span className="banner-title">
            <ShieldAlert size={13} /> {t('drive.simulated_title')}
          </span>
          <span className="banner-body">{t('drive.simulated_body')}</span>
        </div>
      )}

      <div
        className="run-progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={drive.total}
        aria-valuenow={Math.min(drive.index, drive.total)}
      >
        <i style={{ width: `${Math.round((Math.min(drive.index, drive.total) / Math.max(1, drive.total)) * 100)}%` }} />
      </div>

      {drive.status === 'completed' && (
        <div className="banner ok" role="status">
          <span className="banner-title">
            <PartyPopper size={13} /> {t('drive.done')}
          </span>
          <span className="banner-actions">
            <button className="btn primary" onClick={() => void sendBg({ type: 'drive-stop' })}>
              {t('drive.close')}
            </button>
          </span>
        </div>
      )}

      {drive.error && (
        <div className="banner danger" role="alert">
          <span className="banner-title">
            <TriangleAlert size={13} />{' '}
            {t('drive.step_failed', { step: Math.min(drive.index + 1, drive.total) })}
          </span>
          <span className="banner-body">{drive.error}</span>
          <span className="banner-actions">
            {drive.awaitingDecision ? (
              <>
                <button className="btn" onClick={() => void sendBg({ type: 'drive-decide', decision: 'retry' })}>
                  {t('drive.retry')}
                </button>
                <button className="btn" onClick={() => void sendBg({ type: 'drive-decide', decision: 'skip' })}>
                  <SkipForward size={13} /> {t('drive.skip')}
                </button>
                <button
                  className="btn danger-outline"
                  onClick={() => void sendBg({ type: 'drive-decide', decision: 'abort' })}
                >
                  {t('drive.abort')}
                </button>
              </>
            ) : (
              <button className="btn" onClick={() => void sendBg({ type: 'drive-stop' })}>
                {t('drive.close')}
              </button>
            )}
          </span>
        </div>
      )}

      <div className="run-steps" ref={listRef}>
        {drive.steps.map((s, i) => {
          const Icon = STEP_ICONS[s.type] ?? MousePointerClick;
          const status = drive.stepStatus[i] ?? 'pending';
          const current = running && i === drive.index;
          return (
            <div
              key={s.id}
              className={`run-step ${status}${current ? ' current' : ''}`}
              data-current={current || undefined}
            >
              <span className="run-step-marker">{status === 'done' ? <Check size={11} /> : i + 1}</span>
              <span className="run-step-title truncate" title={s.title}>
                {s.title}
              </span>
              <span className="run-step-status">
                {status === 'pending' ? '' : t(`drive.status_${status}`)}
              </span>
              <Icon size={12} className="run-step-type" />
            </div>
          );
        })}
      </div>

      {running && (
        <div className="run-controls">
          <button
            className="btn"
            onClick={() => void sendBg({ type: 'drive-pause', paused: drive.status !== 'paused' })}
          >
            {drive.status === 'paused' ? <Play size={13} /> : <Pause size={13} />}
            {drive.status === 'paused' ? t('drive.resume') : t('drive.pause')}
          </button>
          <div className="speed" role="group" aria-label={t('drive.playback_speed')}>
            {SPEEDS.map((s) => (
              <button
                key={s}
                className={`speed-btn${drive.speed === s ? ' on' : ''}`}
                aria-pressed={drive.speed === s}
                onClick={() => void sendBg({ type: 'drive-speed', speed: s })}
              >
                {s}×
              </button>
            ))}
          </div>
          <span className="spacer" />
          <button className="btn danger-outline" onClick={() => void sendBg({ type: 'drive-stop' })}>
            <Square size={11} fill="currentColor" /> {t('drive.stop')}
          </button>
        </div>
      )}
    </div>
  );
}
