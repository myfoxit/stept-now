/**
 * The Stept mark: an ascending stepped path with a filled node at the top —
 * adoption climbing, and the agent's pointer arriving at the destination.
 *
 * Stroked with `currentColor` so it works on any surface, and legible down to
 * 16px. Same geometry as the marketing site and the favicon.
 */
export function StepMark({ className, title }: { className?: string; title?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      className={className}
      role={title ? 'img' : 'presentation'}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      <path
        d="M4 27V21.5H12.5V14.5H21V7.5H28"
        stroke="currentColor"
        strokeWidth={3.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="27.5" cy="7.5" r="4" fill="currentColor" />
    </svg>
  )
}
