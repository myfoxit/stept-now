/** TanStack Query hooks for the contact directory. */

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import { useAuthStore } from '@/stores/auth'
import {
  contactsApi,
  listSegments,
  listTags,
  type Contact,
  type ContactUpdate,
} from '@/features/contacts/api'

const AREA = 'contacts'

export function useContactsList(filters: { q?: string; segmentId?: string }) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useInfiniteQuery({
    queryKey: [AREA, workspaceId, 'list', filters],
    enabled: !!workspaceId,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      contactsApi.list({ q: filters.q, segment_id: filters.segmentId, cursor: pageParam }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}

export function useContact(contactId: string | undefined) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'detail', contactId],
    enabled: !!workspaceId && !!contactId,
    queryFn: () => contactsApi.get(contactId!),
  })
}

export function useContactNotes(contactId: string | undefined) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'notes', contactId],
    enabled: !!workspaceId && !!contactId,
    queryFn: () => contactsApi.notes(contactId!),
  })
}

export function useContactEvents(contactId: string | undefined) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'events', contactId],
    enabled: !!workspaceId && !!contactId,
    queryFn: () => contactsApi.events(contactId!, 50),
  })
}

export function useSegments() {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'segments'],
    enabled: !!workspaceId,
    queryFn: listSegments,
  })
}

export function useTags() {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: ['tags', workspaceId],
    enabled: !!workspaceId,
    queryFn: listTags,
  })
}

export function useCreateContact() {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { name?: string; email?: string | null; phone?: string | null }) =>
      contactsApi.create(body),
    onSuccess: (created: Contact) => {
      qc.setQueryData([AREA, workspaceId, 'detail', created.id], created)
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'list'] })
    },
  })
}

export function useUpdateContact(contactId: string) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ContactUpdate) => contactsApi.update(contactId, body),
    onSuccess: (updated: Contact) => {
      qc.setQueryData([AREA, workspaceId, 'detail', contactId], updated)
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'list'] })
    },
  })
}

export function useContactTagMutations(contactId: string) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const qc = useQueryClient()
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'detail', contactId] })
    qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'list'] })
  }
  return {
    attach: useMutation({
      mutationFn: (tagId: string) => contactsApi.attachTag(contactId, tagId),
      onSuccess: invalidate,
    }),
    detach: useMutation({
      mutationFn: (tagId: string) => contactsApi.detachTag(contactId, tagId),
      onSuccess: invalidate,
    }),
  }
}

export function useContactNoteMutations(contactId: string) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const qc = useQueryClient()
  const invalidate = () =>
    qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'notes', contactId] })
  return {
    add: useMutation({
      mutationFn: (body: string) => contactsApi.addNote(contactId, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (noteId: string) => contactsApi.deleteNote(contactId, noteId),
      onSuccess: invalidate,
    }),
  }
}
