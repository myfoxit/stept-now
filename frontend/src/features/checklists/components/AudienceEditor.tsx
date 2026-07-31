import { Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'

import type { FilterOp } from '../api'
import { emptyFilter, FILTER_FIELDS, FILTER_OPS, opNeedsValue, type FilterDraft } from '../lib'

/**
 * Audience targeting: everyone, or contacts matching AND-ed filter rows using
 * the shared segment filter DSL. Deliberately duplicated per DAP feature so
 * neither imports the other's internals.
 */
export function AudienceEditor({
  type,
  filters,
  onTypeChange,
  onFiltersChange,
  disabled = false,
}: {
  type: 'all' | 'filters'
  filters: FilterDraft[]
  onTypeChange: (type: 'all' | 'filters') => void
  onFiltersChange: (filters: FilterDraft[]) => void
  disabled?: boolean
}) {
  function update(index: number, patch: Partial<FilterDraft>) {
    onFiltersChange(filters.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  return (
    <div className="grid gap-3">
      <div className="grid gap-1.5">
        <Label htmlFor="audience-type">Who sees it</Label>
        <NativeSelect
          id="audience-type"
          className="w-full"
          value={type}
          disabled={disabled}
          onChange={(e) => onTypeChange(e.target.value as 'all' | 'filters')}
        >
          <NativeSelectOption value="all">Everyone</NativeSelectOption>
          <NativeSelectOption value="filters">Contacts matching filters</NativeSelectOption>
        </NativeSelect>
      </div>

      {type === 'filters' ? (
        <div className="grid gap-2">
          {filters.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No filters yet — every identified contact matches.
            </p>
          ) : null}

          {filters.map((row, index) => (
            <div key={row.key} className="grid gap-2 rounded-md border p-2" data-testid="filter-row">
              <div className="flex items-center gap-2">
                <NativeSelect
                  className="w-full"
                  aria-label={`Filter ${index + 1} field`}
                  value={row.field}
                  disabled={disabled}
                  onChange={(e) => update(index, { field: e.target.value })}
                >
                  {FILTER_FIELDS.map((field) => (
                    <NativeSelectOption key={field.value} value={field.value}>
                      {field.label}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 shrink-0"
                  aria-label={`Remove filter ${index + 1}`}
                  disabled={disabled}
                  onClick={() => onFiltersChange(filters.filter((_, i) => i !== index))}
                >
                  <X className="size-4" />
                </Button>
              </div>

              {row.field === 'attributes' ? (
                <Input
                  className="font-mono text-xs"
                  placeholder="plan"
                  aria-label={`Filter ${index + 1} attribute key`}
                  value={row.attrKey}
                  disabled={disabled}
                  onChange={(e) => update(index, { attrKey: e.target.value })}
                />
              ) : null}

              <div className="flex items-center gap-2">
                <NativeSelect
                  className="w-full"
                  aria-label={`Filter ${index + 1} operator`}
                  value={row.op}
                  disabled={disabled}
                  onChange={(e) => update(index, { op: e.target.value as FilterOp })}
                >
                  {FILTER_OPS.map((op) => (
                    <NativeSelectOption key={op.value} value={op.value}>
                      {op.label}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
                {opNeedsValue(row.op) ? (
                  <Input
                    aria-label={`Filter ${index + 1} value`}
                    placeholder="value"
                    value={row.value}
                    disabled={disabled}
                    onChange={(e) => update(index, { value: e.target.value })}
                  />
                ) : null}
              </div>
            </div>
          ))}

          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => onFiltersChange([...filters, emptyFilter()])}
            >
              <Plus className="size-4" /> Add filter
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
