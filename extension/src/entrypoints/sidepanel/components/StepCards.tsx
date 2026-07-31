import { useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronUp,
  MousePointerClick,
  Pencil,
  Trash2,
  TriangleAlert,
} from 'lucide-react';
import { screenshotUrl } from '../../../api/client';
import type { TourStep } from '../../../types';
import { STEP_ICONS } from '../lib';

/** A step's screenshot with the recorded target boxed and the click point
 * marked — the overlays are never burned into the image, so they stay crisp
 * and aligned at any panel width. Ported from the old `StepCards.tsx`; the
 * asset now loads straight from the PUBLIC media route (no bearer token
 * needed, which is why `<img src>` works at all). */
export function Shot({
  step,
  apiBase,
  workspaceId,
}: {
  step: TourStep;
  apiBase: string;
  workspaceId: string | null;
}) {
  const [failed, setFailed] = useState(false);
  if (!step.screenshot_key || !workspaceId || failed) return null;
  const url = screenshotUrl(apiBase, workspaceId, step.screenshot_key);
  const bbox = step.target?.bbox;
  const vw = bbox?.viewport?.w ?? 1280;
  const vh = bbox?.viewport?.h ?? 800;
  return (
    <a className="shot" href={url} target="_blank" rel="noreferrer" title="Open full screenshot">
      <img src={url} alt="" onError={() => setFailed(true)} />
      {bbox && (
        <span
          className="shot-box"
          style={{
            left: `${(bbox.x / vw) * 100}%`,
            top: `${(bbox.y / vh) * 100}%`,
            width: `${(bbox.w / vw) * 100}%`,
            height: `${(bbox.h / vh) * 100}%`,
          }}
        />
      )}
      {bbox && (
        <span
          className="shot-dot"
          style={{
            left: `${((bbox.x + bbox.w / 2) / vw) * 100}%`,
            top: `${((bbox.y + bbox.h / 2) / vh) * 100}%`,
          }}
        >
          <span className="halo" />
          <span className="ring" />
        </span>
      )}
    </a>
  );
}

export interface StepRowProps {
  index: number;
  step: TourStep;
  apiBase: string;
  workspaceId: string | null;
  isFirst: boolean;
  isLast: boolean;
  warning?: string;
  onRename: (title: string) => void;
  onBody: (body: string) => void;
  onMove: (dir: -1 | 1) => void;
  onDelete: () => void;
}

export function StepRow({
  index,
  step,
  apiBase,
  workspaceId,
  isFirst,
  isLast,
  warning,
  onRename,
  onBody,
  onMove,
  onDelete,
}: StepRowProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(step.title);
  const [bodyOpen, setBodyOpen] = useState(false);
  const Icon = STEP_ICONS[step.type] ?? MousePointerClick;
  const masked = step.type === 'tooltip' && step.advance.on === 'input' && !step.action;

  const commit = () => {
    onRename(draft);
    setEditing(false);
  };

  return (
    <div className="step-card">
      <div className="step-head">
        <span className="step-index">{index + 1}</span>
        {editing ? (
          <input
            autoFocus
            className="step-title-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commit();
              if (e.key === 'Escape') setEditing(false);
            }}
          />
        ) : (
          <span
            className="step-title"
            title={step.title}
            onDoubleClick={() => {
              setDraft(step.title);
              setEditing(true);
            }}
          >
            {step.title}
          </span>
        )}
        <div className="step-actions">
          {editing ? (
            <button className="icon-btn" aria-label="Confirm rename" onClick={commit}>
              <Check size={13} />
            </button>
          ) : (
            <>
              <button
                className="icon-btn"
                aria-label="Move step up"
                title="Move up"
                disabled={isFirst}
                onClick={() => onMove(-1)}
              >
                <ChevronUp size={13} />
              </button>
              <button
                className="icon-btn"
                aria-label="Move step down"
                title="Move down"
                disabled={isLast}
                onClick={() => onMove(1)}
              >
                <ChevronDown size={13} />
              </button>
              <button
                className="icon-btn"
                aria-label="Rename step"
                title="Rename"
                onClick={() => {
                  setDraft(step.title);
                  setEditing(true);
                }}
              >
                <Pencil size={12} />
              </button>
              <button className="icon-btn danger" aria-label="Delete step" title="Delete" onClick={onDelete}>
                <Trash2 size={13} />
              </button>
            </>
          )}
        </div>
      </div>

      <div className="step-body">
        <div className="step-sub">
          <span className="type-chip">
            <Icon size={11} /> {step.type}
          </span>
          <span className="chip" title={`Advances on ${step.advance.on}`}>
            {step.advance.on.replace('_', ' ')}
          </span>
          {masked && <span className="chip">private</span>}
          {step.target?.frame?.length ? <span className="chip">iframe</span> : null}
        </div>

        {warning && (
          <div className="step-warn">
            <TriangleAlert size={12} /> {warning}
          </div>
        )}

        <Shot step={step} apiBase={apiBase} workspaceId={workspaceId} />

        <button className="link-btn" onClick={() => setBodyOpen((v) => !v)}>
          {bodyOpen ? 'Hide description' : step.body ? 'Edit description' : 'Add description'}
        </button>
        {bodyOpen && (
          <textarea
            className="step-body-input"
            value={step.body}
            rows={3}
            placeholder="Markdown shown under the step title in the tour"
            onChange={(e) => onBody(e.target.value)}
          />
        )}

        <code className="step-selector" title={step.selector}>
          {step.selector || '—'}
        </code>
      </div>
    </div>
  );
}
