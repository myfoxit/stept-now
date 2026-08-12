import { CheckCircle2, ExternalLink } from 'lucide-react';
import { t } from '../../../i18n';
import type { SavedInfo } from '../../../types';

/** The one thing a user wants after a save: the link to keep working in the
 * dashboard. Lives in background state so closing and reopening the panel
 * mid-save never loses it. */
export function SavedBanner({ saved }: { saved: SavedInfo }) {
  return (
    <div className="banner ok" role="status">
      <span className="banner-title">
        <CheckCircle2 size={13} /> {t('saved.title', { name: saved.name })}
      </span>
      <span className="banner-body">{t('saved.body')}</span>
      <span className="banner-actions">
        <a className="btn primary" href={saved.appUrl} target="_blank" rel="noreferrer">
          <ExternalLink size={13} /> {t('saved.open_in_stept')}
        </a>
      </span>
    </div>
  );
}
