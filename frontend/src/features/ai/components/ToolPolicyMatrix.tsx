/**
 * Per-tool policy matrix. Each row is a tool (builtin or custom action) with a
 * three-way segmented control: auto / require_approval / disabled. Controlled —
 * the parent owns the policy map keyed by tool key.
 */

import { cn } from '@/lib/utils'

import type { ToolPolicy } from '../api'
import { TOOL_POLICIES } from '../tools'

export interface PolicyRow {
  key: string
  label: string
  description?: string
}

export function ToolPolicyMatrix({
  rows,
  value,
  onChange,
  disabled,
}: {
  rows: PolicyRow[]
  value: Record<string, ToolPolicy>
  onChange: (key: string, policy: ToolPolicy) => void
  disabled?: boolean
}) {
  return (
    <div className="divide-y rounded-lg border">
      {rows.map((row) => {
        const current = value[row.key] ?? 'auto'
        return (
          <div
            key={row.key}
            role="group"
            aria-label={row.label}
            className="flex flex-wrap items-center gap-3 p-3"
          >
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{row.label}</p>
              {row.description ? (
                <p className="text-xs text-muted-foreground">{row.description}</p>
              ) : null}
            </div>
            <div className="inline-flex rounded-md border p-0.5">
              {TOOL_POLICIES.map((policy) => {
                const active = current === policy.value
                return (
                  <button
                    key={policy.value}
                    type="button"
                    disabled={disabled}
                    aria-pressed={active}
                    aria-label={`${row.label}: ${policy.label}`}
                    title={policy.hint}
                    onClick={() => onChange(row.key, policy.value)}
                    className={cn(
                      'rounded px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50',
                      active
                        ? policy.value === 'disabled'
                          ? 'bg-muted text-muted-foreground'
                          : policy.value === 'require_approval'
                            ? 'bg-amber-500 text-white'
                            : 'bg-primary text-primary-foreground'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {policy.label}
                  </button>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}
