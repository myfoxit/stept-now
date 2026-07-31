/**
 * Tiny dependency-free funnel sparkline (per-step viewed counts). Bars use the
 * de-emphasis brand token so it stays legible in light and dark.
 */
export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length === 0) return <span className="text-xs text-muted-foreground">—</span>
  const max = Math.max(1, ...values)
  return (
    <div
      className={className}
      style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 24 }}
      aria-hidden
    >
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
