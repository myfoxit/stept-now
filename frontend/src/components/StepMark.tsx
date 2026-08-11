/**
 * The Stept mark — two bars on a 24×24 grid, the leading one stepped right and
 * down. The step is the product: the thing that just moved forward.
 *
 * Geometry is the source of truth in `assets/brand/` — reproduced here, never
 * redrawn. Keep in sync with `landing/src/components/Wordmark.astro`, the
 * extension's `Logo()`, and `scripts/gen-brand-assets.mjs`.
 *
 * `mono` (default) inherits `currentColor`, so it is near-black on light and
 * white on dark wherever it sits beside a word — the sidebar, the switcher, a
 * menu. `split` paints the leading bar with `--brand`, the app's own indigo,
 * which already lifts on dark surfaces; that variant is reserved for the places
 * the logo is the subject rather than furniture (today: the sign-in card).
 */
export function StepMark({
  className,
  title,
  variant = 'mono',
}: {
  className?: string
  title?: string
  variant?: 'mono' | 'split'
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      role={title ? 'img' : 'presentation'}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      <rect x="3" y="3.5" width="15" height="7" rx="2.9" fill="currentColor" />
      <rect
        x="6"
        y="13.5"
        width="15"
        height="7"
        rx="2.9"
        fill={variant === 'split' ? 'var(--brand)' : 'currentColor'}
      />
    </svg>
  )
}
