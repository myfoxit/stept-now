import { Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'

import {
  CHANNEL_OPTIONS,
  CONDITION_FIELDS,
  CONDITION_OPS,
  LIST_OPS,
  PRIORITY_OPTIONS,
  STATUS_OPTIONS,
  VALUELESS_OPS,
  type Option,
} from '../constants'
import { emptyConditionRow, localId, type ConditionRow } from '../lib'

const CUSTOM = '__custom'

function isKnownField(field: string): boolean {
  return CONDITION_FIELDS.some((f) => f.value === field)
}

function valueOptionsFor(field: string): Option[] | null {
  if (field === 'status') return STATUS_OPTIONS
  if (field === 'priority') return PRIORITY_OPTIONS
  if (field === 'channel_type') return CHANNEL_OPTIONS
  return null
}

export function ConditionRows({
  rows,
  onChange,
}: {
  rows: ConditionRow[]
  onChange: (rows: ConditionRow[]) => void
}) {
  function update(id: string, patch: Partial<ConditionRow>) {
    onChange(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)))
  }
  function remove(id: string) {
    onChange(rows.filter((row) => row.id !== id))
  }
  function add() {
    onChange([...rows, { ...emptyConditionRow(), id: localId('cond') }])
  }

  return (
    <div className="grid gap-2">
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No conditions — this rule runs on every matching event.
        </p>
      ) : null}
      {rows.map((row) => {
        const custom = !isKnownField(row.field)
        const valueOptions = valueOptionsFor(row.field)
        const hideValue = VALUELESS_OPS.has(row.op)
        return (
          <div key={row.id} className="flex flex-wrap items-center gap-2" data-testid="condition-row">
            <NativeSelect
              aria-label="Field"
              className="w-40"
              value={custom ? CUSTOM : row.field}
              onChange={(event) => {
                const next = event.target.value
                update(row.id, { field: next === CUSTOM ? '' : next })
              }}
            >
              {CONDITION_FIELDS.map((option) => (
                <NativeSelectOption key={option.value} value={option.value}>
                  {option.label}
                </NativeSelectOption>
              ))}
              <NativeSelectOption value={CUSTOM}>Custom field…</NativeSelectOption>
            </NativeSelect>

            {custom ? (
              <Input
                aria-label="Custom field"
                className="w-44"
                placeholder="contact.attributes.plan"
                value={row.field}
                onChange={(event) => update(row.id, { field: event.target.value })}
              />
            ) : null}

            <NativeSelect
              aria-label="Operator"
              className="w-32"
              value={row.op}
              onChange={(event) => update(row.id, { op: event.target.value })}
            >
              {CONDITION_OPS.map((option) => (
                <NativeSelectOption key={option.value} value={option.value}>
                  {option.label}
                </NativeSelectOption>
              ))}
            </NativeSelect>

            {hideValue ? (
              <span className="text-sm text-muted-foreground">—</span>
            ) : valueOptions ? (
              <NativeSelect
                aria-label="Value"
                className="w-40"
                value={row.value}
                onChange={(event) => update(row.id, { value: event.target.value })}
              >
                <NativeSelectOption value="">Select…</NativeSelectOption>
                {valueOptions.map((option) => (
                  <NativeSelectOption key={option.value} value={option.value}>
                    {option.label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            ) : (
              <Input
                aria-label="Value"
                className="w-48 flex-1"
                placeholder={LIST_OPS.has(row.op) ? 'value a, value b' : 'value'}
                value={row.value}
                onChange={(event) => update(row.id, { value: event.target.value })}
              />
            )}

            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Remove condition"
              onClick={() => remove(row.id)}
            >
              <X className="size-4" />
            </Button>
          </div>
        )
      })}
      <div>
        <Button type="button" variant="outline" size="sm" onClick={add}>
          <Plus className="size-4" /> Add condition
        </Button>
      </div>
    </div>
  )
}
