/**
 * The Stept mark — building blocks with the last block set apart, taken from
 * the internal `stepped` app's post-login brand (its lucide `Blocks` lockup).
 * The detached block is filled: the step that just clicked into place.
 *
 * Inherits `currentColor` so it works on any surface, legible down to 16px.
 * Same geometry as the marketing site, the docs and the favicons — regenerate
 * those from this file, never redraw them.
 */
export function StepMark({ className, title }: { className?: string; title?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      role={title ? 'img' : 'presentation'}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      <path
        d="M10 21V8a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5a1 1 0 0 0-1-1H3"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <rect x="14" y="3" width="7" height="7" rx="1.4" fill="currentColor" />
    </svg>
  )
}
