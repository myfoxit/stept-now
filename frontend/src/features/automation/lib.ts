/**
 * Pure (de)serialization between the builder's editable rows and the wire
 * Condition/Action shapes. Kept dependency-free so it is trivially unit-tested.
 */

import type { components } from '@/api/schema'

import { ACTION_SPECS, LIST_OPS, VALUELESS_OPS } from './constants'

export type Condition = components['schemas']['Condition']
export type Action = components['schemas']['Action']

let counter = 0
/** Stable-enough local id for React keys on unsaved rows. */
export function localId(prefix = 'row'): string {
  counter += 1
  return `${prefix}-${counter}-${Math.random().toString(36).slice(2, 7)}`
}

export interface ConditionRow {
  id: string
  field: string
  op: string
  value: string
}

export interface ActionRow {
  id: string
  type: string
  params: Record<string, string>
}

export function emptyConditionRow(): ConditionRow {
  return { id: localId('cond'), field: 'status', op: 'eq', value: '' }
}

export function emptyActionRow(): ActionRow {
  return { id: localId('act'), type: 'set_status', params: {} }
}

/** Coerce a raw string operand to number / boolean where unambiguous. */
export function coerceValue(raw: string): string | number | boolean {
  const trimmed = raw.trim()
  if (trimmed === 'true') return true
  if (trimmed === 'false') return false
  if (trimmed !== '' && !Number.isNaN(Number(trimmed))) return Number(trimmed)
  return raw
}

export function serializeCondition(row: ConditionRow): Condition {
  if (VALUELESS_OPS.has(row.op)) {
    return { field: row.field.trim(), op: row.op as Condition['op'], value: null }
  }
  if (LIST_OPS.has(row.op)) {
    const value = row.value
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean)
    return { field: row.field.trim(), op: row.op as Condition['op'], value }
  }
  return { field: row.field.trim(), op: row.op as Condition['op'], value: coerceValue(row.value) }
}

export function deserializeCondition(condition: Condition): ConditionRow {
  const op = condition.op ?? 'eq'
  let value = ''
  if (Array.isArray(condition.value)) value = condition.value.join(', ')
  else if (condition.value !== null && condition.value !== undefined) value = String(condition.value)
  return { id: localId('cond'), field: condition.field ?? '', op, value }
}

export function serializeAction(row: ActionRow): Action {
  const spec = ACTION_SPECS[row.type]
  const params: Record<string, unknown> = {}
  if (spec) {
    for (const field of spec.params) {
      const raw = row.params[field.key]
      if (raw !== undefined && raw !== '') params[field.key] = raw
    }
  }
  return { type: row.type as Action['type'], params }
}

export function deserializeAction(action: Action): ActionRow {
  const params: Record<string, string> = {}
  for (const [key, value] of Object.entries(action.params ?? {})) {
    params[key] = value == null ? '' : String(value)
  }
  return { id: localId('act'), type: action.type ?? 'set_status', params }
}

/** Human summary of a condition, e.g. "status is open". */
export function describeCondition(condition: Condition): string {
  const value = Array.isArray(condition.value)
    ? condition.value.join(', ')
    : condition.value == null
      ? ''
      : String(condition.value)
  const opLabel =
    { eq: 'is', neq: 'is not', contains: 'contains', in: 'is any of', exists: 'exists' }[
      condition.op ?? 'eq'
    ] ?? condition.op
  return `${condition.field} ${opLabel}${value ? ` ${value}` : ''}`.trim()
}
