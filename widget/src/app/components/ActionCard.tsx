/**
 * The confirm gate for a client action: what the assistant wants to run, with
 * the exact arguments, and the visitor's decision. Rendered in the thread like
 * a message — the run stays parked server-side until Run or Not now (or the
 * server-side timeout says nobody answered).
 */

import { t, tParts } from '../../i18n'
import type { PendingActionCard } from '../controller'

export function ActionCard({
  action,
  onRun,
  onDismiss,
}: {
  action: PendingActionCard
  onRun: () => void
  onDismiss: () => void
}) {
  const params = Object.entries(action.params || {})
  // Split around the emphasised verb rather than hardcoding "prefix + <strong>":
  // Japanese and Turkish put the verb last, and a fixed order would read as
  // machine translation in exactly the moment we are asking for consent.
  const [before, after] = tParts('action.wants_to', 'action')
  return (
    <div class="sw-action-card" role="group" aria-label={t('action.label')}>
      <div class="sw-action-card-title">
        {before}
        <strong>{humanize(action.name)}</strong>
        {after}
      </div>
      {action.description && <div class="sw-action-card-desc">{action.description}</div>}
      {params.length > 0 && (
        <dl class="sw-action-card-params">
          {params.map(([key, value]) => (
            <div key={key} class="sw-action-card-param">
              <dt>{key}</dt>
              <dd>{show(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      <div class="sw-action-card-buttons">
        <button type="button" class="sw-btn sw-btn-primary" onClick={onRun}>
          {t('action.run')}
        </button>
        <button type="button" class="sw-btn" onClick={onDismiss}>
          {t('action.dismiss')}
        </button>
      </div>
    </div>
  )
}

function humanize(name: string): string {
  return name.replace(/_/g, ' ')
}

function show(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
