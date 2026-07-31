/** Create or edit a help-center collection (name, emoji icon, description). */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
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
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { currentWorkspaceId } from '@/stores/auth'

import { knowledgeApi, knowledgeKeys, type Collection } from '../api'

const EMOJI = ['📘', '🚀', '💳', '⚙️', '🔒', '💬', '📦', '❓', '🧩', '🎯']

export function CollectionDialog({
  collection,
  open,
  onOpenChange,
}: {
  collection?: Collection | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [icon, setIcon] = useState('📘')
  const [description, setDescription] = useState('')

  useEffect(() => {
    if (open) {
      setName(collection?.name ?? '')
      setIcon(collection?.icon ?? '📘')
      setDescription(collection?.description ?? '')
    }
  }, [open, collection])

  const mutation = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), icon, description: description.trim() || null }
      return collection
        ? knowledgeApi.updateCollection(collection.id, body)
        : knowledgeApi.createCollection(body)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.collections(workspaceId) })
      toast.success(collection ? 'Collection updated' : 'Collection created')
      onOpenChange(false)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not save collection'),
  })

  return (
    <Dialog open={open} onOpenChange={(next) => !mutation.isPending && onOpenChange(next)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{collection ? 'Edit collection' : 'New collection'}</DialogTitle>
          <DialogDescription>Group related help-center articles together.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="collection-name">Name</Label>
            <Input
              id="collection-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Getting started"
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Icon</Label>
            <div className="flex flex-wrap gap-1.5">
              {EMOJI.map((e) => (
                <button
                  key={e}
                  type="button"
                  aria-label={`Icon ${e}`}
                  aria-pressed={icon === e}
                  onClick={() => setIcon(e)}
                  className={cn(
                    'flex size-9 items-center justify-center rounded-md border text-lg transition-colors hover:bg-accent',
                    icon === e && 'border-primary bg-accent'
                  )}
                >
                  {e}
                </button>
              ))}
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="collection-desc">Description</Label>
            <Textarea
              id="collection-desc"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={!name.trim() || mutation.isPending}>
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
