import type { TourOfferAttachment } from '../api-extra'
import { t } from '../../i18n'

/**
 * A `tour_offer` attachment rendered as a card in the thread: what the tour is,
 * how big a commitment it is, and one button that actually starts it in the
 * host page (via the loader — the iframe cannot touch the host DOM).
 */
export function TourCard({
  offer,
  onStart,
}: {
  offer: TourOfferAttachment
  onStart: (tourId: string) => void
}) {
  const minutes = Math.max(1, Math.round(offer.est_seconds / 60))
  return (
    <div class="sw-tour-card" role="group" aria-label={t('tour.card.label')}>
      <div class="sw-tour-card-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
          <path d="M8 5v14l11-7z" />
        </svg>
      </div>
      <div class="sw-tour-card-main">
        <div class="sw-tour-card-title">{offer.title}</div>
        {offer.steps > 0 && (
          <div class="sw-tour-card-meta">
            {t('tour.card.steps', { count: offer.steps })}
            {offer.est_seconds > 0 && <> · {t('tour.card.min', { min: minutes })}</>}
          </div>
        )}
      </div>
      <button
        type="button"
        class="sw-btn sw-btn-primary sw-tour-card-start"
        onClick={() => onStart(offer.tour_id)}
      >
        {t('tour.card.start')}
      </button>
    </div>
  )
}
