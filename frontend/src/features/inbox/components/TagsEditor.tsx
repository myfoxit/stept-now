/** Reusable tag editor: shows applied tags + a popover to add/create tags. */

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, X } from 'lucide-react'

import { useAuthStore } from '@/stores/auth'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { createTag, type Tag } from '@/features/contacts/api'
import { useTags } from '@/features/contacts/hooks'

export function TagsEditor({
  appliedTagIds,
  onAdd,
  onRemove,
  canManage,
}: {
  appliedTagIds: string[]
  onAdd: (tagId: string) => void
  onRemove: (tagId: string) => void
  canManage: boolean
}) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const qc = useQueryClient()
  const { data: tags = [] } = useTags()
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)

  const applied = appliedTagIds
    .map((id) => tags.find((t) => t.id === id))
    .filter((t): t is Tag => !!t)
  const available = tags.filter(
    (t) => !appliedTagIds.includes(t.id) && t.name.toLowerCase().includes(query.toLowerCase())
  )
  const exactExists = tags.some((t) => t.name.toLowerCase() === query.trim().toLowerCase())

  const create = useMutation({
    mutationFn: (name: string) => createTag({ name }),
    onSuccess: (tag) => {
      qc.invalidateQueries({ queryKey: ['tags', workspaceId] })
      onAdd(tag.id)
      setQuery('')
      setOpen(false)
    },
  })

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {applied.map((tag) => (
        <Badge
          key={tag.id}
          variant="outline"
          className="gap-1"
          style={{ borderColor: tag.color, color: tag.color }}
        >
          {tag.name}
          {canManage ? (
            <button
              type="button"
              aria-label={`Remove ${tag.name}`}
              onClick={() => onRemove(tag.id)}
              className="hover:text-foreground"
            >
              <X className="size-3" />
            </button>
          ) : null}
        </Badge>
      ))}

      {canManage ? (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" size="xs" aria-label="Add tag">
              <Plus className="size-3" />
              Tag
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-56 p-2">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search or create…"
              aria-label="Search tags"
              className="h-8"
            />
            <ul className="mt-2 max-h-48 space-y-0.5 overflow-y-auto">
              {available.map((tag) => (
                <li key={tag.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onAdd(tag.id)
                      setOpen(false)
                      setQuery('')
                    }}
                    className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-accent"
                  >
                    <span className="size-2.5 rounded-full" style={{ backgroundColor: tag.color }} />
                    {tag.name}
                  </button>
                </li>
              ))}
              {query.trim() && !exactExists ? (
                <li>
                  <button
                    type="button"
                    onClick={() => create.mutate(query.trim())}
                    disabled={create.isPending}
                    className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm text-primary hover:bg-accent"
                  >
                    <Plus className="size-3.5" />
                    Create “{query.trim()}”
                  </button>
                </li>
              ) : null}
              {!available.length && !query ? (
                <li className="px-2 py-1 text-xs text-muted-foreground">No more tags</li>
              ) : null}
            </ul>
          </PopoverContent>
        </Popover>
      ) : null}
    </div>
  )
}
