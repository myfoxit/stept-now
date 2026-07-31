/** Contact detail — profile, editable attributes, tags, timeline, notes. */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import { ArrowLeft, BadgeCheck, MessageSquare, Plus, Trash2, Zap } from 'lucide-react'

import { fullDateTime, timeAgo } from '@/lib/format'
import { useAuthStore, useHasPerm } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ContactAvatar, StatusBadge } from '@/features/inbox/components/atoms'
import { TagsEditor } from '@/features/inbox/components/TagsEditor'
import { inboxApi } from '@/features/inbox/api'
import {
  useContact,
  useContactEvents,
  useContactNoteMutations,
  useContactNotes,
  useContactTagMutations,
  useUpdateContact,
} from '@/features/contacts/hooks'

export function Component() {
  const { contactId } = useParams()
  const id = contactId!
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const canWrite = useHasPerm('contacts:write')

  const contactQuery = useContact(id)
  const notesQuery = useContactNotes(id)
  const eventsQuery = useContactEvents(id)
  const update = useUpdateContact(id)
  const tagMutations = useContactTagMutations(id)
  const noteMutations = useContactNoteMutations(id)

  const { data: conversations } = useQuery({
    queryKey: ['contacts', workspaceId, 'conversations', id],
    enabled: !!workspaceId,
    queryFn: () => inboxApi.listConversations({ contact_id: id }),
  })

  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({ name: '', email: '', phone: '' })
  const [attrs, setAttrs] = useState<[string, string][]>([])
  const [noteBody, setNoteBody] = useState('')

  const contact = contactQuery.data

  function startEdit() {
    if (!contact) return
    setForm({ name: contact.name ?? '', email: contact.email ?? '', phone: contact.phone ?? '' })
    setAttrs(Object.entries(contact.attributes ?? {}).map(([k, v]) => [k, String(v)]))
    setEditing(true)
  }

  function save() {
    const attributes: Record<string, unknown> = {}
    for (const [k, v] of attrs) if (k.trim()) attributes[k.trim()] = v
    update.mutate(
      { name: form.name, email: form.email || null, phone: form.phone || null, attributes },
      { onSuccess: () => setEditing(false) }
    )
  }

  if (contactQuery.isLoading) {
    return (
      <div className="mx-auto max-w-4xl space-y-4 p-6">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  if (contactQuery.isError || !contact) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <p className="text-sm text-muted-foreground">Contact not found.</p>
        <Button variant="outline" asChild>
          <Link to="/contacts">Back to contacts</Link>
        </Button>
      </div>
    )
  }

  const timeline = [
    ...(eventsQuery.data ?? []).map((e) => ({
      kind: 'event' as const,
      date: e.created_at,
      id: e.id,
      label: e.name,
    })),
    ...(conversations?.items ?? []).map((c) => ({
      kind: 'conversation' as const,
      date: c.last_activity_at,
      id: c.id,
      label: c.subject || `Conversation #${c.number}`,
      status: c.status,
    })),
  ].sort((a, b) => (a.date < b.date ? 1 : -1))

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <Button variant="ghost" size="sm" asChild className="-ml-2">
          <Link to="/contacts">
            <ArrowLeft className="size-4" /> Contacts
          </Link>
        </Button>

        {/* Profile */}
        <Card>
          <CardHeader className="flex-row items-center gap-4 space-y-0">
            <ContactAvatar name={contact.name} avatarUrl={contact.avatar_url} size="lg" />
            <div className="min-w-0 flex-1">
              <CardTitle className="flex items-center gap-2">
                {contact.name || 'Unnamed contact'}
                {contact.verified ? <BadgeCheck className="size-4 text-blue-500" /> : null}
              </CardTitle>
              <p className="text-sm text-muted-foreground">{contact.email ?? 'No email'}</p>
            </div>
            {canWrite && !editing ? (
              <Button variant="outline" size="sm" onClick={startEdit}>
                Edit
              </Button>
            ) : null}
          </CardHeader>
          <CardContent className="space-y-4">
            {editing ? (
              <div className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="grid gap-1.5">
                    <Label htmlFor="name">Name</Label>
                    <Input id="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor="email">Email</Label>
                    <Input id="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor="phone">Phone</Label>
                    <Input id="phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>Attributes</Label>
                  {attrs.map(([k, v], i) => (
                    <div key={i} className="flex gap-2">
                      <Input
                        value={k}
                        placeholder="key"
                        aria-label={`Attribute ${i + 1} key`}
                        onChange={(e) =>
                          setAttrs(attrs.map((row, j) => (j === i ? [e.target.value, row[1]] : row)))
                        }
                      />
                      <Input
                        value={v}
                        placeholder="value"
                        aria-label={`Attribute ${i + 1} value`}
                        onChange={(e) =>
                          setAttrs(attrs.map((row, j) => (j === i ? [row[0], e.target.value] : row)))
                        }
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Remove attribute"
                        onClick={() => setAttrs(attrs.filter((_, j) => j !== i))}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  ))}
                  <Button variant="outline" size="sm" onClick={() => setAttrs([...attrs, ['', '']])}>
                    <Plus className="size-4" /> Add attribute
                  </Button>
                </div>
                <div className="flex gap-2">
                  <Button onClick={save} disabled={update.isPending}>
                    Save
                  </Button>
                  <Button variant="outline" onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <div className="grid gap-3 text-sm sm:grid-cols-2">
                  <Detail label="External ID" value={contact.external_id} />
                  <Detail label="Phone" value={contact.phone} />
                  <Detail label="First seen" value={contact.first_seen_at ? fullDateTime(contact.first_seen_at) : null} />
                  <Detail label="Last seen" value={contact.last_seen_at ? fullDateTime(contact.last_seen_at) : null} />
                </div>
                {Object.entries(contact.attributes ?? {}).length ? (
                  <>
                    <Separator />
                    <dl className="grid gap-2 text-sm sm:grid-cols-2">
                      {Object.entries(contact.attributes).map(([k, v]) => (
                        <div key={k} className="flex justify-between gap-2">
                          <dt className="text-muted-foreground">{k}</dt>
                          <dd className="font-medium">{String(v)}</dd>
                        </div>
                      ))}
                    </dl>
                  </>
                ) : null}
                <Separator />
                <div className="space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Tags</p>
                  <TagsEditor
                    appliedTagIds={(contact.tags ?? []).map((t) => t.id)}
                    canManage={canWrite}
                    onAdd={(tagId) => tagMutations.attach.mutate(tagId)}
                    onRemove={(tagId) => tagMutations.detach.mutate(tagId)}
                  />
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          {/* Timeline */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Timeline</CardTitle>
            </CardHeader>
            <CardContent>
              {timeline.length ? (
                <ul className="space-y-3">
                  {timeline.map((item) => (
                    <li key={`${item.kind}-${item.id}`} className="flex items-start gap-2 text-sm">
                      <span className="mt-0.5 text-muted-foreground">
                        {item.kind === 'conversation' ? (
                          <MessageSquare className="size-4" />
                        ) : (
                          <Zap className="size-4" />
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        {item.kind === 'conversation' ? (
                          <Link to={`/inbox/${item.id}`} className="font-medium hover:underline">
                            {item.label}
                          </Link>
                        ) : (
                          <span className="font-medium">{item.label}</span>
                        )}
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          {item.kind === 'conversation' ? <StatusBadge status={item.status} /> : null}
                          <span>{timeAgo(item.date)}</span>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">No activity yet.</p>
              )}
            </CardContent>
          </Card>

          {/* Notes */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Notes</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {canWrite ? (
                <div className="space-y-2">
                  <Textarea
                    value={noteBody}
                    onChange={(e) => setNoteBody(e.target.value)}
                    placeholder="Add an internal note about this contact…"
                    aria-label="New note"
                    className="min-h-16"
                  />
                  <Button
                    size="sm"
                    disabled={!noteBody.trim() || noteMutations.add.isPending}
                    onClick={() =>
                      noteMutations.add.mutate(noteBody.trim(), { onSuccess: () => setNoteBody('') })
                    }
                  >
                    Add note
                  </Button>
                </div>
              ) : null}
              {notesQuery.data?.length ? (
                <ul className="space-y-2">
                  {notesQuery.data.map((note) => (
                    <li key={note.id} className="rounded-md border p-2 text-sm">
                      <p className="whitespace-pre-wrap">{note.body}</p>
                      <div className="mt-1 flex items-center justify-between text-xs text-muted-foreground">
                        <span>{timeAgo(note.created_at)}</span>
                        {canWrite ? (
                          <button
                            type="button"
                            aria-label="Delete note"
                            onClick={() => noteMutations.remove.mutate(note.id)}
                            className="hover:text-destructive"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">No notes yet.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

function Detail({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value || '—'}</span>
    </div>
  )
}

export default Component
