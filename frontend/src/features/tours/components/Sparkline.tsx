/**
 * Tiny dependency-free funnel sparkline (per-step viewed counts). Bars use the
 * de-emphasis brand token so it stays legible in light and dark. Only the
 * data-driven bar height is an inline style — everything static is a token.
 */
export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length === 0) return <span className="text-xs text-muted-foreground">—</span>
  const max = Math.max(1, ...values)
  return (
    <div className={`flex h-6 items-end gap-0.5 ${className ?? ''}`} aria-hidden>
      {values.map((value, index) => (
        <div
          key={index}
          className="w-1.5 rounded-[1px] bg-primary/60"
          style={{ height: `${Math.max(8, (value / max) * 100)}%` }}
        />
      ))}
    </div>
  )
}
