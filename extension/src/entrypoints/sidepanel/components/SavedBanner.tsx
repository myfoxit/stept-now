import { CheckCircle2, ExternalLink } from 'lucide-react';
import type { SavedInfo } from '../../../types';

/** The one thing a user wants after a save: the link to keep working in the
 * dashboard. Lives in background state so closing and reopening the panel
 * mid-save never loses it. */
export function SavedBanner({ saved }: { saved: SavedInfo }) {
  return (
    <div className="banner ok" role="status">
      <span className="banner-title">
        <CheckCircle2 size={13} /> Saved “{saved.name}” as a draft
      </span>
      <span className="banner-body">
        Edit the copy, set targeting and publish it from the Stept dashboard.
      </span>
      <span className="banner-actions">
        <a className="btn primary" href={saved.appUrl} target="_blank" rel="noreferrer">
          <ExternalLink size={13} /> Open in Stept
        </a>
      </span>
    </div>
  );
}
