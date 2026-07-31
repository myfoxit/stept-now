import { useEffect, useRef } from 'react';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  MousePointerClick,
  PartyPopper,
  Route,
  Square,
  TriangleAlert,
} from 'lucide-react';
import type { GuideState } from '../../../types';
import { sendBg, STEP_ICONS } from '../lib';

/** Live guide progress: the same numbered steps as the page overlay, plus the
 * panel-side controls. Mirrors the overlay so the user can follow from either
 * surface. Ported from the old `GuidePanel.tsx`. */
export function GuidePanel({ guide }: { guide: GuideState }) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const active = guide.status === 'active';

  useEffect(() => {
    const el = listRef.current?.querySelector('[data-current="true"]');
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [guide.index]);

  return (
    <div className="run-panel">
      <div className="run-head">
        <span className="run-badge">
          <Route size={13} />
        </span>
        <span className="run-title-wrap">
          <span className="run-title truncate" title={guide.name}>
            {guide.name}
          </span>
          <span className="run-sub">
            {guide.status === 'completed'
              ? 'All steps done'
              : guide.status === 'error'
                ? 'Guide interrupted'
                : `Step ${Math.min(guide.index + 1, guide.total)} of ${guide.total} — follow the highlight on the page`}
          </span>
        </span>
      </div>

      <div
        className="run-progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={guide.total}
        aria-valuenow={Math.min(guide.index, guide.total)}
      >
        <i style={{ width: `${Math.round((Math.min(guide.index, guide.total) / Math.max(1, guide.total)) * 100)}%` }} />
      </div>

      {guide.status === 'completed' && (
        <div className="banner ok" role="status">
          <span className="banner-title">
            <PartyPopper size={13} /> Guide finished
          </span>
          <span className="banner-body">You walked through all {guide.total} steps.</span>
          <span className="banner-actions">
            <button className="btn primary" onClick={() => void sendBg({ type: 'guide-stop' })}>
              Done
            </button>
          </span>
        </div>
      )}

      {guide.status === 'error' && (
        <div className="banner danger" role="alert">
          <span className="banner-body">{guide.error ?? 'The guide could not continue.'}</span>
          <span className="banner-actions">
            <button className="btn" onClick={() => void sendBg({ type: 'guide-stop' })}>
              Close
            </button>
          </span>
        </div>
      )}

      {active && guide.stuck && (
        <div className="banner warn" role="alert">
          <span className="banner-title">
            <TriangleAlert size={13} /> Element not found
          </span>
          <span className="banner-body">
            The page may have changed since this was recorded. Do the step by hand, or skip it — it
            keeps looking in the background.
          </span>
          <span className="banner-actions">
            <button className="btn" onClick={() => void sendBg({ type: 'guide-nav', dir: 1 })}>
              Skip step
            </button>
          </span>
        </div>
      )}

      <div className="run-steps" ref={listRef}>
        {guide.steps.map((s, i) => {
          const Icon = STEP_ICONS[s.type] ?? MousePointerClick;
          const done = i < guide.index || guide.status === 'completed';
          const current = active && i === guide.index;
          return (
            <div
              key={s.id}
              className={`run-step${done ? ' done' : ''}${current ? ' current' : ''}`}
              data-current={current || undefined}
            >
              <span className="run-step-marker">{done ? <Check size={11} /> : i + 1}</span>
              <span className="run-step-title truncate" title={s.title}>
                {s.title}
              </span>
              <Icon size={12} className="run-step-type" />
            </div>
          );
        })}
      </div>

      {active && (
        <div className="run-controls">
          <button className="btn" disabled={guide.index === 0} onClick={() => void sendBg({ type: 'guide-nav', dir: -1 })}>
            <ChevronLeft size={13} /> Back
          </button>
          <button className="btn" onClick={() => void sendBg({ type: 'guide-nav', dir: 1 })}>
            Skip <ChevronRight size={13} />
          </button>
          <span className="spacer" />
          <button className="btn danger-outline" onClick={() => void sendBg({ type: 'guide-stop' })}>
            <Square size={11} fill="currentColor" /> Stop
          </button>
        </div>
      )}
    </div>
  );
}
