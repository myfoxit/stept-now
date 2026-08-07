/** Start an outbound conversation: pick a contact + inbox, write the first message. */

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, Plus } from 'lucide-react'

import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { contactsApi, type Contact } from '@/features/contacts/api'
import { NewContactDialog } from '@/features/contacts/components/NewContactDialog'
import { useInboxes } from '@/features/inbox/hooks'
import { useCreateConversation } from '@/features/inbox/hooks'

export function NewConversationDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (id: string) => void
}) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const [search, setSearch] = useState('')
  const [contact, setContact] = useState<Contact | null>(null)
  const [inboxId, setInboxId] = useState<string>('')
  const [subject, setSubject] = useState('')
  const [content, setContent] = useState('')
  const [creating, setCreating] = useState(false)

  const { data: inboxes = [] } = useInboxes()
  const create = useCreateConversation()

  // Debounced so typing "test" fires one request, not one per keystroke.
  const [debouncedSearch, setDebouncedSearch] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim()), 300)
    return () => clearTimeout(t)
  }, [search])

  const { data: results, isFetching } = useQuery({
    queryKey: ['contacts', workspaceId, 'picker', debouncedSearch],
    enabled: open && !contact,
    queryFn: () => contactsApi.list({ q: debouncedSearch || undefined, limit: 8 }),
  })

  useEffect(() => {
    if (!open) {
      setSearch('')
      setDebouncedSearch('')
      setContact(null)
      setInboxId('')
      setSubject('')
      setContent('')
    }
  }, [open])

  useEffect(() => {
    if (!inboxId && inboxes.length) setInboxId(inboxes[0].id)
  }, [inboxes, inboxId])

  function start() {
    if (!contact || !inboxId || !content.trim()) return
    create.mutate(
      { contact_id: contact.id, inbox_id: inboxId, content: content.trim(), subject: subject || undefined },
      { onSuccess: (conv) => onCreated(conv.id) }
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <NewContactDialog
        open={creating}
        onOpenChange={setCreating}
        // Selecting it straight away keeps the outbound flow unbroken.
        onCreated={(c) => setContact(c)}
      />
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New conversation</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          {contact ? (
            <div className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
              <span>
                <span className="font-medium">{contact.name || 'Unnamed'}</span>
                {contact.email ? <span className="text-muted-foreground"> · {contact.email}</span> : null}
              </span>
              <Button variant="ghost" size="sm" onClick={() => setContact(null)}>
                Change
              </Button>
            </div>
          ) : (
            <div>
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search contacts by name or email"
                aria-label="Search contacts"
                autoFocus
              />
              {results?.items.length ? (
                <ul className="mt-1 max-h-40 overflow-y-auto rounded-md border">
                  {results.items.map((c) => (
                    <li key={c.id}>
                      <button
                        type="button"
                        onClick={() => setContact(c)}
                        className="flex w-full items-center justify-between px-3 py-1.5 text-left text-sm hover:bg-accent"
                      >
                        <span>
                          {c.name || 'Unnamed'}
                          {c.email ? <span className="text-muted-foreground"> · {c.email}</span> : null}
                        </span>
                        <Check className="size-4 opacity-0" />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                // Previously this branch rendered nothing at all, so an empty
                // workspace gave no feedback and no way forward.
                <div className="mt-1 rounded-md border px-3 py-3 text-sm text-muted-foreground">
                  {isFetching ? (
                    'Searching…'
                  ) : (
                    <div className="flex flex-col items-start gap-2">
                      <span>
                        {debouncedSearch
                          ? `No contacts match “${debouncedSearch}”.`
                          : 'No contacts yet.'}
                      </span>
                      <Button variant="outline" size="sm" onClick={() => setCreating(true)}>
                        <Plus className="size-4" /> New contact
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          <Select value={inboxId} onValueChange={setInboxId}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select an inbox" />
            </SelectTrigger>
            <SelectContent>
              {inboxes.map((i) => (
                <SelectItem key={i.id} value={i.id}>
                  {i.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="Subject (optional)"
            aria-label="Subject"
          />
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="Write the first message…"
            aria-label="Message"
            className={cn('min-h-24')}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={start} disabled={!contact || !inboxId || !content.trim() || create.isPending}>
            Start conversation
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
