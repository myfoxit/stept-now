import { useState } from 'react';
import { ArrowLeft, RefreshCw, TriangleAlert, UploadCloud } from 'lucide-react';
import type { SimpleResult } from '../../../messages';
import type { EditingTour, PanelState } from '../../../types';
import { sendBg } from '../lib';
import { StepRow } from './StepCards';

/** A tour pulled from the workspace, edited here and pushed back with
 * `base_version`. A 409 means the dashboard changed it first — we never
 * silently clobber someone else's edit, we ask the user to reload. */
export function EditPanel({
  editing,
  state,
  onError,
}: {
  editing: EditingTour;
  state: PanelState;
  onError: (m: string) => void;
}) {
  const [pushing, setPushing] = useState(false);

  const push = async () => {
    setPushing(true);
    const r = await sendBg<SimpleResult>({ type: 'push-tour' });
    setPushing(false);
    if (r && !r.ok && r.error) onError(r.error);
  };

  return (
    <div className="edit-panel">
      <div className="edit-head">
        <button className="icon-btn" aria-label="Back to tours" onClick={() => void sendBg({ type: 'close-editing' })}>
          <ArrowLeft size={15} />
        </button>
        <span className="edit-title truncate" title={editing.name}>
          {editing.name}
        </span>
        <span className="chip">v{editing.baseVersion}</span>
      </div>

      {editing.conflict && (
        <div className="banner warn" role="alert">
          <span className="banner-title">
            <TriangleAlert size={13} /> Changed in the dashboard
          </span>
          <span className="banner-body">
            Someone edited this tour while you had it open, so your push was rejected. Reload it to
            pick up their version — your local changes here will be replaced.
          </span>
          <span className="banner-actions">
            <button
              className="btn"
              onClick={() => void sendBg({ type: 'pull-tour', tourId: editing.tourId })}
            >
              <RefreshCw size={13} /> Reload tour
            </button>
          </span>
        </div>
      )}

      <div className="step-list">
        {editing.steps.length === 0 && <div className="empty">This tour has no steps.</div>}
        {editing.steps.map((step, i) => (
          <StepRow
            key={step.id}
            index={i}
            step={step}
            apiBase={state.auth.apiBase}
            workspaceId={state.auth.workspaceId}
            isFirst={i === 0}
            isLast={i === editing.steps.length - 1}
            onRename={(title) => void sendBg({ type: 'edit-step', stepId: step.id, patch: { title } })}
            onBody={(body) => void sendBg({ type: 'edit-step', stepId: step.id, patch: { body } })}
            onMove={(dir) => void sendBg({ type: 'edit-move-step', stepId: step.id, dir })}
            onDelete={() => void sendBg({ type: 'edit-delete-step', stepId: step.id })}
          />
        ))}
      </div>

      <button className="btn primary big" disabled={pushing} aria-busy={pushing} onClick={() => void push()}>
        {pushing ? <RefreshCw size={14} className="spin" /> : <UploadCloud size={14} />}
        {pushing ? 'Pushing…' : 'Push changes to Stept'}
      </button>
    </div>
  );
}
