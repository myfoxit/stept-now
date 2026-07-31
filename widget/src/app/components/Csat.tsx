import { useState } from 'preact/hooks'

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
    return <div class="sw-csat sw-csat-done">Thanks for your feedback!</div>
  }

  return (
    <div class="sw-csat">
      <div class="sw-csat-title">How did we do?</div>
      <div class="sw-stars" role="radiogroup" aria-label="Rating">
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            class={`sw-star ${n <= rating ? 'sw-star-on' : ''}`}
            aria-label={`${n} star${n > 1 ? 's' : ''}`}
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
            placeholder="Anything to add? (optional)"
            value={feedback}
            onInput={(e) => setFeedback((e.currentTarget as HTMLTextAreaElement).value)}
          />
          <button
            type="button"
            class="sw-btn sw-btn-primary"
            onClick={() => onSubmit(rating, feedback.trim() || undefined)}
          >
            Submit
          </button>
        </>
      )}
    </div>
  )
}
