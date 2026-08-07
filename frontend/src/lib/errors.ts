/**
 * Turn an ApiError into something a form can render.
 *
 * The backend wraps every failure as `{error: {code, message, details}}`. For a
 * 422 the `details` payload is FastAPI's `exc.errors()` — a list of
 * `{type, loc, msg, input}` where `loc` is a path like `["body", "email"]`.
 * Without this, forms only have a generic message and field-level feedback is
 * impossible, so validation failures end up looking like a dead button.
 */

import { ApiError } from '@/api/client'

export interface ParsedApiError {
  /** Human-readable summary, safe to drop into a toast. */
  message: string
  /** Field name -> first message for that field. Empty when not a 422. */
  fields: Record<string, string>
}

interface PydanticError {
  loc?: unknown
  msg?: unknown
}

/** `["body", "email"]` -> `email`; `["body", "attributes", 0, "key"]` -> `attributes.0.key`. */
function fieldNameFromLoc(loc: unknown): string | null {
  if (!Array.isArray(loc)) return null
  const parts = loc
    .filter((p) => p !== 'body' && p !== 'query' && p !== 'path')
    .map((p) => String(p))
  return parts.length ? parts.join('.') : null
}

export function parseApiError(error: unknown): ParsedApiError {
  if (!(error instanceof ApiError)) {
    return {
      message: error instanceof Error && error.message ? error.message : 'Something went wrong.',
      fields: {},
    }
  }

  const fields: Record<string, string> = {}
  if (Array.isArray(error.details)) {
    for (const raw of error.details as PydanticError[]) {
      const name = fieldNameFromLoc(raw?.loc)
      const msg = typeof raw?.msg === 'string' ? raw.msg : null
      // First error per field wins — showing one message per input is enough.
      if (name && msg && !(name in fields)) fields[name] = msg
    }
  }

  // A bare "Request validation failed" tells the user nothing; if we managed to
  // extract field detail, lead with that instead.
  const fieldMessages = Object.entries(fields)
  const message =
    error.status === 422 && fieldMessages.length
      ? fieldMessages.map(([name, msg]) => `${name}: ${msg}`).join(', ')
      : error.message

  return { message, fields }
}
