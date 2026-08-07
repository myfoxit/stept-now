/**
 * Block / unblock and merge-duplicate actions on a contact.
 *
 * Merge folds another contact *into* this one: this contact keeps its own
 * name/email/phone and inherits whatever it was missing, and everything the
 * other one owned (conversations, notes, events, tags, channel identities) is
 * reparented here.
 */

import { Ban, Merge, ShieldCheck } from 'lucide-react'
import { useState } from 'react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { Contact } from '@/features/contacts/api'
import { useContactsList, useMergeContacts, useSetContactBlocked } from '@/features/contacts/hooks'

export function ContactAdminActions({ contact }: { contact: Contact }) {
  const [confirmBlock, setConfirmBlock] = useState(false)
  const [mergeOpen, setMergeOpen] = useState(false)
  const setBlocked = useSetContactBlocked()

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setMergeOpen(true)}>
        <Merge className="mr-1 size-4" />
        Merge
      </Button>
      <Button
        variant={contact.blocked ? 'default' : 'outline'}
        size="sm"
        onClick={() =>
          contact.blocked
            ? setBlocked.mutate({ id: contact.id, blocked: false })
            : setConfirmBlock(true)
        }
      >
        {contact.blocked ? (
          <>
            <ShieldCheck className="mr-1 size-4" />
            Unblock
          </>
        ) : (
          <>
            <Ban className="mr-1 size-4" />
            Block
          </>
        )}
      </Button>

      <AlertDialog open={confirmBlock} onOpenChange={setConfirmBlock}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Block this contact?</AlertDialogTitle>
            <AlertDialogDescription>
              They will not be able to start conversations, and inbound messages from them are
              dropped at the channel. Existing conversations stay put.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => setBlocked.mutate({ id: contact.id, blocked: true })}>
              Block
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <MergeDialog contact={contact} open={mergeOpen} onOpenChange={setMergeOpen} />
    </>
  )
}

function MergeDialog({
  contact,
  open,
  onOpenChange,
}: {
  contact: Contact
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Contact | null>(null)
  const merge = useMergeContacts()
  const { data } = useContactsList({ q: search || undefined })
  const candidates = (data?.pages.flatMap((page) => page.items) ?? [])
    .filter((row) => row.id !== contact.id && !row.merged_into_id)
    .slice(0, 8)

  async function run() {
    if (!selected) return
    try {
      await merge.mutateAsync({ winnerId: contact.id, loserId: selected.id })
      onOpenChange(false)
      setSelected(null)
      setSearch('')
    } catch {
      /* toast handled in the hook */
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Merge a duplicate into this contact</DialogTitle>
          <DialogDescription>
            {contact.name || 'This contact'} is kept. The one you pick is folded in — its
            conversations, notes and channel identities move here, and it stays as a tombstone so
            old links keep working.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor="merge-search">Find the duplicate</Label>
            <Input
              id="merge-search"
              placeholder="Search by name, email or ID"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value)
                setSelected(null)
              }}
            />
          </div>
          <ul className="max-h-52 space-y-1 overflow-y-auto">
            {candidates.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => setSelected(row)}
                  className={
                    selected?.id === row.id
                      ? 'w-full rounded-md bg-primary px-2 py-1.5 text-left text-sm text-primary-foreground'
                      : 'w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent'
                  }
                >
                  <span className="font-medium">{row.name || 'Unnamed'}</span>
                  <span className="ml-2 text-xs opacity-80">
                    {row.email ?? row.phone ?? row.id}
                  </span>
                </button>
              </li>
            ))}
            {candidates.length === 0 ? (
              <li className="px-2 py-1.5 text-sm text-muted-foreground">
                No other contacts found.
              </li>
            ) : null}
          </ul>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={run} disabled={!selected || merge.isPending}>
            Merge into this contact
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
