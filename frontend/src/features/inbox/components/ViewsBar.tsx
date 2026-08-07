/**
 * Saved-view chips + the "save this filter" dialog.
 *
 * A view is just a stored filter document, so the bar sits above the status
 * tabs and swaps the active query rather than adding another filter dimension.
 */

import { Bookmark, BookmarkPlus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

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
import { cn } from '@/lib/utils'
import { useHasPerm } from '@/stores/auth'
import { EMPTY_QUERY, type FilterQuery, type SavedView } from '@/features/inbox/api'
import { FilterBuilder } from '@/features/inbox/components/FilterBuilder'
import {
  useCreateView,
  useDeleteView,
  useFilterCatalog,
  useFilterPreview,
  useSavedViews,
} from '@/features/inbox/hooks'

export function ViewsBar({
  activeViewId,
  onSelect,
}: {
  activeViewId: string | null
  onSelect: (view: SavedView | null) => void
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const { data: views = [] } = useSavedViews()
  const remove = useDeleteView()

  return (
    <div className="flex flex-wrap items-center gap-1 border-b px-2 py-1.5">
      <button
        type="button"
        onClick={() => onSelect(null)}
        className={cn(
          'rounded-md px-2 py-1 text-xs font-medium transition-colors',
          activeViewId === null
            ? 'bg-accent text-accent-foreground'
            : 'text-muted-foreground hover:bg-accent'
        )}
      >
        All
      </button>
      {views.map((view) => (
        <span key={view.id} className="group relative flex items-center">
          <button
            type="button"
            onClick={() => onSelect(view)}
            className={cn(
              'flex items-center gap-1 rounded-md py-1 pl-2 pr-6 text-xs font-medium transition-colors',
              activeViewId === view.id
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-accent'
            )}
          >
            {view.icon ? <span aria-hidden>{view.icon}</span> : <Bookmark className="size-3" />}
            {view.name}
          </button>
          <button
            type="button"
            aria-label={`Delete view ${view.name}`}
            onClick={() => {
              if (activeViewId === view.id) onSelect(null)
              remove.mutate(view.id)
            }}
            className="absolute right-1 hidden rounded p-0.5 hover:bg-black/10 group-hover:block"
          >
            <Trash2 className="size-3" />
          </button>
        </span>
      ))}
      <Button
        variant="ghost"
        size="sm"
        className="h-6 px-1.5 text-xs"
        onClick={() => setDialogOpen(true)}
      >
        <BookmarkPlus className="mr-1 size-3.5" />
        New view
      </Button>

      <SaveViewDialog open={dialogOpen} onOpenChange={setDialogOpen} onSaved={onSelect} />
    </div>
  )
}

export function SaveViewDialog({
  open,
  onOpenChange,
  onSaved,
  initialQuery,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved?: (view: SavedView) => void
  initialQuery?: FilterQuery
}) {
  const [name, setName] = useState('')
  const [shared, setShared] = useState(false)
  const [query, setQuery] = useState<FilterQuery>(initialQuery ?? EMPTY_QUERY)
  const canShare = useHasPerm('conversations:manage')
  const { data: catalog } = useFilterCatalog()
  const create = useCreateView()
  const preview = useFilterPreview(query, open)

  useEffect(() => {
    if (!open) return
    setName('')
    setShared(false)
    setQuery(initialQuery ?? EMPTY_QUERY)
  }, [open, initialQuery])

  async function save() {
    try {
      const view = await create.mutateAsync({
        name: name.trim(),
        query,
        visibility: shared ? 'shared' : 'personal',
      })
      onSaved?.(view)
      onOpenChange(false)
    } catch {
      /* toast handled in the hook */
    }
  }

  const matchCount = preview.data?.items.length ?? 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New view</DialogTitle>
          <DialogDescription>
            Save a filter so you can come back to this queue in one click.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor="view-name">Name</Label>
            <Input
              id="view-name"
              placeholder="e.g. Unassigned VIP"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <FilterBuilder fields={catalog?.fields ?? []} query={query} onChange={setQuery} />

          {canShare ? (
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={shared}
                onChange={(e) => setShared(e.target.checked)}
              />
              Share with the whole workspace
            </label>
          ) : null}

          <p className="text-xs text-muted-foreground" aria-live="polite">
            {preview.isError
              ? 'That filter is not valid yet.'
              : preview.isLoading
                ? 'Checking…'
                : `Matches ${matchCount}${preview.data?.next_cursor ? '+' : ''} conversation${
                    matchCount === 1 ? '' : 's'
                  }`}
          </p>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!name.trim() || create.isPending}>
            Save view
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
