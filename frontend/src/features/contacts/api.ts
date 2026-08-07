/**
 * Contact directory API — CRUD, notes, timeline events, tags, CSAT, segments.
 * Shared by the Contacts pages and the Inbox context pane.
 */

import { api, ws } from '@/api/client'
import type { components } from '@/api/schema'

export type Contact = components['schemas']['ContactOut']
export type ContactUpdate = components['schemas']['ContactUpdate']
export type Tag = components['schemas']['TagOut']
export type ContactNote = components['schemas']['ContactNoteOut']
export type ContactEvent = components['schemas']['ContactEventOut']
export type Csat = components['schemas']['CsatResponseOut']
export type Segment = components['schemas']['SegmentOut']

export interface CursorPage<T> {
  items: T[]
  next_cursor: string | null
}

/** URLSearchParams-based query string (supports repeated keys for arrays). */
export function qs(
  params: Record<string, string | string[] | number | boolean | undefined | null>
): string {
  const sp = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) value.forEach((item) => sp.append(key, String(item)))
    else sp.append(key, String(value))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export const contactsApi = {
  list: (params: { q?: string; segment_id?: string; cursor?: string; limit?: number }) =>
    api.get<CursorPage<Contact>>(ws('/contacts') + qs(params)),
  get: (id: string) => api.get<Contact>(ws(`/contacts/${id}`)),
  create: (body: {
    external_id?: string | null
    email?: string | null
    name?: string
    phone?: string | null
    attributes?: Record<string, unknown>
  }) => api.post<Contact>(ws('/contacts'), body),
  update: (id: string, body: ContactUpdate) => api.patch<Contact>(ws(`/contacts/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/contacts/${id}`)),
  notes: (id: string) => api.get<ContactNote[]>(ws(`/contacts/${id}/notes`)),
  addNote: (id: string, body: string) =>
    api.post<ContactNote>(ws(`/contacts/${id}/notes`), { body }),
  deleteNote: (id: string, noteId: string) =>
    api.delete<{ message: string }>(ws(`/contacts/${id}/notes/${noteId}`)),
  events: (id: string, limit?: number) =>
    api.get<ContactEvent[]>(ws(`/contacts/${id}/events`) + qs({ limit })),
  attachTag: (id: string, tagId: string) =>
    api.post<Tag[]>(ws(`/contacts/${id}/tags`), { tag_id: tagId }),
  detachTag: (id: string, tagId: string) =>
    api.delete<Tag[]>(ws(`/contacts/${id}/tags/${tagId}`)),
  csat: (id: string) => api.get<Csat[]>(ws(`/contacts/${id}/csat`)),
}

/** Shared lookups (tags catalog + segments) used across contacts and inbox. */
export const listSegments = () => api.get<Segment[]>(ws('/segments'))
export const listTags = () => api.get<Tag[]>(ws('/tags'))
export const createTag = (body: { name: string; color?: string }) =>
  api.post<Tag>(ws('/tags'), body)

// ---------------------------------------------------------------------------
// Merge, block, CSV import/export (docs/CHATWOOT-BACKLOG.md §1.7)
// ---------------------------------------------------------------------------

export type ContactImport = components['schemas']['ContactImportOut']
export type ContactImportPreview = components['schemas']['ContactImportPreview']

export const contactAdminApi = {
  merge: (winnerId: string, loserId: string) =>
    api.post<Contact>(ws(`/contacts/${winnerId}/merge`), { loser_id: loserId }),
  setBlocked: (id: string, blocked: boolean) =>
    api.post<Contact>(ws(`/contacts/${id}/block`), { blocked }),
  listImports: () => api.get<ContactImport[]>(ws('/contacts/imports')),
  getImport: (id: string) => api.get<ContactImport>(ws(`/contacts/imports/${id}`)),
  upload: (file: File) => api.upload<ContactImportPreview>(ws('/contacts/imports'), file),
  start: (id: string, mapping?: Record<string, string>) =>
    api.post<ContactImport>(ws(`/contacts/imports/${id}/start`), { mapping: mapping ?? null }),
  /** Absolute URL so the browser can download it directly. */
  exportUrl: () => ws('/contacts/export'),
}
