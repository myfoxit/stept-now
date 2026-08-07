/**
 * Conversation filter builder.
 *
 * Renders entirely from the server's field catalog (`GET /views/catalog`), so a
 * workspace's own conversation attributes appear as filterable fields without
 * anything hardcoded here. The catalog also carries each field's allowed
 * operators, which is what keeps the UI from offering a combination the API
 * would reject.
 */

import { Plus, X } from 'lucide-react'
import { useMemo } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { FilterCondition, FilterField, FilterQuery } from '@/features/inbox/api'

const OP_LABELS: Record<string, string> = {
  eq: 'is',
  neq: 'is not',
  in: 'is any of',
  not_in: 'is none of',
  contains: 'contains',
  starts_with: 'starts with',
  exists: 'is set',
  not_exists: 'is not set',
  gt: 'after',
  lt: 'before',
  within_days: 'in the last (days)',
  before_days: 'older than (days)',
}

/** Operators that take no value — the input is hidden for these. */
const VALUELESS = new Set(['exists', 'not_exists'])

/** Operators whose value is a list. */
const MULTI = new Set(['in', 'not_in'])

function inputTypeFor(field: FilterField | undefined, op: string): string {
  if (op === 'within_days' || op === 'before_days') return 'number'
  switch (field?.value_type) {
    case 'datetime':
    case 'date':
      return 'datetime-local'
    case 'number':
    case 'currency':
    case 'percent':
      return 'number'
    default:
      return 'text'
  }
}

function valueToInput(value: unknown): string {
  if (value == null) return ''
  if (Array.isArray(value)) return value.join(', ')
  return String(value)
}

function inputToValue(raw: string, op: string, field: FilterField | undefined): unknown {
  if (MULTI.has(op)) {
    return raw
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean)
  }
  if (inputTypeFor(field, op) === 'number') {
    const parsed = Number(raw)
    return Number.isFinite(parsed) ? parsed : raw
  }
  return raw
}

export function FilterBuilder({
  fields,
  query,
  onChange,
}: {
  fields: FilterField[]
  query: FilterQuery
  onChange: (next: FilterQuery) => void
}) {
  const byField = useMemo(() => new Map(fields.map((field) => [field.field, field])), [fields])

  function update(index: number, patch: Partial<FilterCondition>) {
    const conditions = query.conditions.map((condition, i) =>
      i === index ? { ...condition, ...patch } : condition
    )
    onChange({ ...query, conditions })
  }

  function addCondition() {
    const first = fields[0]
    if (!first) return
    onChange({
      ...query,
      conditions: [
        ...query.conditions,
        { field: first.field, op: first.ops[0] ?? 'eq', value: '' },
      ],
    })
  }

  function removeCondition(index: number) {
    onChange({ ...query, conditions: query.conditions.filter((_, i) => i !== index) })
  }

  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2">
        <Label htmlFor="filter-match" className="text-xs text-muted-foreground">
          Match
        </Label>
        <Select
          value={query.match}
          onValueChange={(match) => onChange({ ...query, match: match as 'all' | 'any' })}
        >
          <SelectTrigger id="filter-match" className="h-8 w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">all conditions</SelectItem>
            <SelectItem value="any">any condition</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {query.conditions.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No conditions yet — this view matches every conversation.
        </p>
      ) : null}

      {query.conditions.map((condition, index) => {
        const field = byField.get(condition.field)
        const ops = field?.ops ?? ['eq']
        const options = field?.options ?? []
        const showValue = !VALUELESS.has(condition.op)
        return (
          <div key={index} className="flex flex-wrap items-center gap-2">
            <Select
              value={condition.field}
              onValueChange={(next) => {
                const nextField = byField.get(next)
                update(index, {
                  field: next,
                  op: nextField?.ops[0] ?? 'eq',
                  value: '',
                })
              }}
            >
              <SelectTrigger className="h-8 w-44" aria-label="Field">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {fields.map((option) => (
                  <SelectItem key={option.field} value={option.field}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={condition.op} onValueChange={(op) => update(index, { op, value: '' })}>
              <SelectTrigger className="h-8 w-40" aria-label="Operator">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ops.map((op) => (
                  <SelectItem key={op} value={op}>
                    {OP_LABELS[op] ?? op}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {showValue && options.length > 0 && !MULTI.has(condition.op) ? (
              <Select
                value={valueToInput(condition.value)}
                onValueChange={(value) => update(index, { value })}
              >
                <SelectTrigger className="h-8 w-44" aria-label="Value">
                  <SelectValue placeholder="Choose…" />
                </SelectTrigger>
                <SelectContent>
                  {options.map((option) => (
                    <SelectItem key={String(option.value)} value={String(option.value)}>
                      {String(option.label ?? option.value)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}

            {showValue && (options.length === 0 || MULTI.has(condition.op)) ? (
              <Input
                className="h-8 w-44"
                aria-label="Value"
                type={inputTypeFor(field, condition.op)}
                placeholder={MULTI.has(condition.op) ? 'comma separated' : 'value'}
                value={valueToInput(condition.value)}
                onChange={(e) =>
                  update(index, { value: inputToValue(e.target.value, condition.op, field) })
                }
              />
            ) : null}

            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              aria-label="Remove condition"
              onClick={() => removeCondition(index)}
            >
              <X className="size-4" />
            </Button>
          </div>
        )
      })}

      <div>
        <Button variant="outline" size="sm" onClick={addCondition} disabled={fields.length === 0}>
          <Plus className="mr-1 size-4" />
          Add condition
        </Button>
      </div>
    </div>
  )
}
