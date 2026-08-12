import { useState } from 'react';
import { Bot, Pencil, Play, RefreshCw, Route } from 'lucide-react';
import { t } from '../../../i18n';
import type { SimpleResult } from '../../../messages';
import type { PanelState } from '../../../types';
import { sendBg, timeAgo } from '../lib';

/** The workspace's tours: pull one into the panel for a quick step edit, walk
 * through it (guide), or let Stept drive it. */
export function ToursPanel({ state, onError }: { state: PanelState; onError: (m: string) => void }) {
  const [busy, setBusy] = useState<string | null>(null);

  const run = async (id: string, msg: Parameters<typeof sendBg>[0]) => {
    setBusy(id);
    const r = await sendBg<SimpleResult>(msg);
    setBusy(null);
    if (r && !r.ok && r.error) onError(r.error);
  };

  return (
    <div className="tours">
      <div className="tours-head">
        <span className="section-title">
          {t('tours.title', { workspace: state.auth.workspaceName || t('tours.this_workspace') })}
        </span>
        <button
          className="icon-btn"
          aria-label={t('tours.refresh_aria')}
          title={t('tours.refresh')}
          onClick={() => void sendBg({ type: 'refresh-tours' })}
        >
          <RefreshCw size={13} className={state.toursLoading ? 'spin' : undefined} />
        </button>
      </div>

      {state.tours.length === 0 && !state.toursLoading && (
        <div className="empty">
          <Route size={20} />
          {t('tours.empty')}
        </div>
      )}

      <div className="tour-list">
        {state.tours.map((tour) => (
          <div className="tour-row" key={tour.id}>
            <div className="tour-main">
              <span className="tour-name" title={tour.name}>
                {tour.name}
              </span>
              <span className="tour-meta">
                <span className={`dot ${tour.status}`} /> {tour.status} ·{' '}
                {t('tours.step_count', { count: tour.steps_count })} · {timeAgo(tour.updated_at)}
              </span>
            </div>
            <div className="tour-actions">
              <button
                className="icon-btn"
                aria-label={t('tours.edit_aria', { name: tour.name })}
                title={t('tours.edit_title')}
                disabled={busy === tour.id}
                onClick={() => void run(tour.id, { type: 'pull-tour', tourId: tour.id })}
              >
                <Pencil size={13} />
              </button>
              <button
                className="icon-btn"
                aria-label={t('tours.walk_aria', { name: tour.name })}
                title={t('tours.walk_title')}
                disabled={busy === tour.id || tour.steps_count === 0}
                onClick={() => void run(tour.id, { type: 'guide-start', tourId: tour.id })}
              >
                <Play size={13} />
              </button>
              <button
                className="icon-btn accent"
                aria-label={t('tours.drive_aria', { name: tour.name })}
                title={t('tours.drive_title')}
                disabled={busy === tour.id || tour.steps_count === 0}
                onClick={() => void run(tour.id, { type: 'drive-start', tourId: tour.id })}
              >
                <Bot size={13} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
