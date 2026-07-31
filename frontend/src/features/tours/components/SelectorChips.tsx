import { X } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import { addFallback, MAX_FALLBACK_SELECTORS } from '../lib'

/**
 * Ordered fallback selectors, entered one per Enter press. The player tries
 * them after the primary selector, so order matters and the list is capped at
 * five — beyond that the recorder's rich `target` is the better mechanism.
 */
export function SelectorChips({
  values,
  onChange,
  disabled = false,
  label,
  inputId,
}: {
  values: string[]
  onChange: (values: string[]) => void
  disabled?: boolean
  label: string
  inputId: string
}) {
  const [draft, setDraft] = useState('')
  const full = values.length >= MAX_FALLBACK_SELECTORS

  function commit() {
    const trimmed = draft.trim()
    if (!trimmed) return
    const next = addFallback(values, trimmed)
    if (next !== values) onChange(next)
    // Added, or already present — either way the input has served its purpose.
    if (next !== values || values.includes(trimmed)) setDraft('')
  }

  return (
    <div className="grid gap-1.5">
      {values.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {values.map((value, index) => (
            <Badge key={value} variant="secondary" className="max-w-full gap-1 font-mono">
              <span className="truncate">{value}</span>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="-mr-1 size-4"
                aria-label={`Remove fallback selector ${index + 1}`}
                disabled={disabled}
                onClick={() => onChange(values.filter((_, i) => i !== index))}
              >
                <X className="size-3" />
              </Button>
            </Badge>
          ))}
        </div>
      ) : null}
      <Input
        id={inputId}
        aria-label={label}
        className="font-mono text-xs"
        placeholder={full ? 'Maximum of 5 fallbacks' : 'Add a fallback, then press Enter'}
        value={draft}
        disabled={disabled || full}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit()
          }
        }}
        onBlur={commit}
      />
    </div>
  )
}
