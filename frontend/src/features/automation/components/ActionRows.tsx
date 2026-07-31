import { Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Textarea } from '@/components/ui/textarea'

import { ACTION_SPECS, ACTION_TYPES, type Option, type ParamField } from '../constants'
import { emptyActionRow, localId, type ActionRow } from '../lib'

export function ActionRows({
  rows,
  onChange,
  specs = ACTION_SPECS,
  types = ACTION_TYPES,
}: {
  rows: ActionRow[]
  onChange: (rows: ActionRow[]) => void
  /** Action catalog to build against — defaults to automation-rule actions. */
  specs?: Record<string, { label: string; params: ParamField[] }>
  types?: Option[]
}) {
  function update(id: string, patch: Partial<ActionRow>) {
    onChange(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)))
  }
  function setParam(id: string, key: string, value: string) {
    onChange(
      rows.map((row) => (row.id === id ? { ...row, params: { ...row.params, [key]: value } } : row))
    )
  }
  function remove(id: string) {
    onChange(rows.filter((row) => row.id !== id))
  }
  function add() {
    onChange([...rows, { ...emptyActionRow(), id: localId('act') }])
  }

  return (
    <div className="grid gap-3">
      {rows.map((row) => {
        const spec = specs[row.type]
        return (
          <div
            key={row.id}
            className="grid gap-2 rounded-md border border-border bg-muted/30 p-3"
            data-testid="action-row"
          >
            <div className="flex items-center gap-2">
              <NativeSelect
                aria-label="Action"
                className="w-48"
                value={row.type}
                onChange={(event) => update(row.id, { type: event.target.value, params: {} })}
              >
                {types.map((option) => (
                  <NativeSelectOption key={option.value} value={option.value}>
                    {option.label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
              <div className="flex-1" />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="Remove action"
                onClick={() => remove(row.id)}
              >
                <X className="size-4" />
              </Button>
            </div>
            {spec && spec.params.length > 0 ? (
              <div className="grid gap-2 sm:grid-cols-2">
                {spec.params.map((field) => (
                  <label key={field.key} className="grid gap-1 text-xs text-muted-foreground">
                    {field.label}
                    {field.kind === 'select' ? (
                      <NativeSelect
                        aria-label={field.label}
                        className="w-full"
                        value={row.params[field.key] ?? ''}
                        onChange={(event) => setParam(row.id, field.key, event.target.value)}
                      >
                        <NativeSelectOption value="">Select…</NativeSelectOption>
                        {field.options?.map((option) => (
                          <NativeSelectOption key={option.value} value={option.value}>
                            {option.label}
                          </NativeSelectOption>
                        ))}
                      </NativeSelect>
                    ) : field.kind === 'textarea' ? (
                      <Textarea
                        aria-label={field.label}
                        rows={2}
                        placeholder={field.placeholder}
                        value={row.params[field.key] ?? ''}
                        onChange={(event) => setParam(row.id, field.key, event.target.value)}
                      />
                    ) : (
                      <Input
                        aria-label={field.label}
                        placeholder={field.placeholder}
                        value={row.params[field.key] ?? ''}
                        onChange={(event) => setParam(row.id, field.key, event.target.value)}
                      />
                    )}
                  </label>
                ))}
              </div>
            ) : null}
          </div>
        )
      })}
      <div>
        <Button type="button" variant="outline" size="sm" onClick={add}>
          <Plus className="size-4" /> Add action
        </Button>
      </div>
    </div>
  )
}
