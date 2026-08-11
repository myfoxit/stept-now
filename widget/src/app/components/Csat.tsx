import { useState } from 'preact/hooks'
import { t } from '../../i18n'

/** Post-resolution 1–5 rating with optional feedback. */
export function Csat({
  done,
  onSubmit,
}: {
  done: boolean
  onSubmit: (rating: number, feedback?: string) => void
}) {
  const [rating, setRating] = useState(0)
  const [feedback, setFeedback] = useState('')

  if (done) {
    return <div class="sw-csat sw-csat-done">{t('csat.done')}</div>
  }

  return (
    <div class="sw-csat">
      <div class="sw-csat-title">{t('csat.title')}</div>
      <div class="sw-stars" role="radiogroup" aria-label={t('csat.rating')}>
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            class={`sw-star ${n <= rating ? 'sw-star-on' : ''}`}
            aria-label={t('csat.star', { count: n })}
            aria-checked={n === rating}
            role="radio"
            onClick={() => setRating(n)}
          >
            ★
          </button>
        ))}
      </div>
      {rating > 0 && (
        <>
          <textarea
            class="sw-csat-feedback"
            placeholder={t('csat.feedback_placeholder')}
            value={feedback}
            onInput={(e) => setFeedback((e.currentTarget as HTMLTextAreaElement).value)}
          />
          <button
            type="button"
            class="sw-btn sw-btn-primary"
            onClick={() => onSubmit(rating, feedback.trim() || undefined)}
          >
            {t('csat.submit')}
          </button>
        </>
      )}
    </div>
  )
}
