/** Left pane: status tabs w/ live counts, filters, search, infinite list. */

import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, Filter, Loader2, PenSquare, Search } from 'lucide-react'

import { cn } from '@/lib/utils'
import { useHasPerm } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ConversationRow } from '@/features/inbox/components/ConversationRow'
import { NewConversationDialog } from '@/features/inbox/components/NewConversationDialog'
import type { ConversationFilters, Counts, Tag } from '@/features/inbox/api'
import {
  useConversationsList,
  useCounts,
  useInboxes,
  usePresence,
} from '@/features/inbox/hooks'
import { useTags } from '@/features/contacts/hooks'

interface TabDef {
  key: string
  label: string
  countKey: keyof Counts
  filter: ConversationFilters
}

const TABS: TabDef[] = [
  { key: 'open', label: 'Open', countKey: 'open', filter: { status: ['open'] } },
  { key: 'mine', label: 'Mine', countKey: 'mine', filter: { status: ['open'], assignee: 'me' } },
  { key: 'unassigned', label: 'Unassigned', countKey: 'unassigned', filter: { status: ['open'], assignee: 'unassigned' } },
  { key: 'pending', label: 'AI', countKey: 'pending', filter: { status: ['pending'] } },
  { key: 'snoozed', label: 'Snoozed', countKey: 'snoozed', filter: { status: ['snoozed'] } },
  { key: 'resolved', label: 'Resolved', countKey: 'resolved', filter: { status: ['resolved'] } },
]

const ALL = '__all__'

export function ConversationListPane({
  activeId,
  onSelect,
}: {
  activeId?: string
  onSelect: (id: string) => void
}) {
  const [tab, setTab] = useState('open')
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')
  const [inboxId, setInboxId] = useState<string | undefined>()
  const [priority, setPriority] = useState<string | undefined>()
  const [tagId, setTagId] = useState<string | undefined>()
  const [newOpen, setNewOpen] = useState(false)

  const canWrite = useHasPerm('conversations:write')
  const { data: counts } = useCounts()
  const { data: inboxes = [] } = useInboxes()
  const { data: tags = [] } = useTags()
  const presence = usePresence()

  useEffect(() => {
    const t = setTimeout(() => setQ(searchInput.trim()), 300)
    return () => clearTimeout(t)
  }, [searchInput])

  const filters = useMemo<ConversationFilters>(() => {
    const base = TABS.find((t) => t.key === tab)?.filter ?? {}
    return { ...base, inbox_id: inboxId, priority, tag_id: tagId, q: q || undefined }
  }, [tab, inboxId, priority, tagId, q])

  const { data, isLoading, isError, refetch, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useConversationsList(filters)

  const items = useMemo(() => data?.pages.flatMap((p) => p.items) ?? [], [data])
  const tagsById = useMemo(() => new Map<string, Tag>(tags.map((t) => [t.id, t])), [tags])
  const activeFilterCount = [inboxId, priority, tagId].filter(Boolean).length

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <h2 className="text-sm font-semibold">Inbox</h2>
        {canWrite ? (
          <Button
            variant="ghost"
            size="icon-sm"
            className="ml-auto"
            onClick={() => setNewOpen(true)}
            aria-label="New conversation"
          >
            <PenSquare className="size-4" />
          </Button>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-1 border-b p-2">
        {TABS.map((t) => {
          const count = counts?.[t.countKey] ?? 0
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={cn(
                'flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors',
                tab === t.key ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-accent'
              )}
            >
              {t.label}
              {count > 0 ? (
                <span
                  className={cn(
                    'rounded-full px-1.5 text-[10px]',
                    tab === t.key ? 'bg-primary-foreground/20' : 'bg-muted'
                  )}
                >
                  {count}
                </span>
              ) : null}
            </button>
          )
        })}
      </div>

      <div className="flex items-center gap-2 border-b p-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search conversations"
            aria-label="Search conversations"
            className="h-8 pl-8"
          />
        </div>
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" aria-label="Filters" className="relative">
              <Filter className="size-4" />
              {activeFilterCount ? (
                <span className="absolute -right-1 -top-1 flex size-4 items-center justify-center rounded-full bg-primary text-[10px] text-primary-foreground">
                  {activeFilterCount}
                </span>
              ) : null}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-64 space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">Inbox</label>
              <Select value={inboxId ?? ALL} onValueChange={(v) => setInboxId(v === ALL ? undefined : v)}>
                <SelectTrigger className="w-full" size="sm">
                  <SelectValue placeholder="All inboxes" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>All inboxes</SelectItem>
                  {inboxes.map((i) => (
                    <SelectItem key={i.id} value={i.id}>
                      {i.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">Priority</label>
              <Select value={priority ?? ALL} onValueChange={(v) => setPriority(v === ALL ? undefined : v)}>
                <SelectTrigger className="w-full" size="sm">
                  <SelectValue placeholder="Any priority" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Any priority</SelectItem>
                  {['urgent', 'high', 'medium', 'low', 'none'].map((p) => (
                    <SelectItem key={p} value={p} className="capitalize">
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">Tag</label>
              <Select value={tagId ?? ALL} onValueChange={(v) => setTagId(v === ALL ? undefined : v)}>
                <SelectTrigger className="w-full" size="sm">
                  <SelectValue placeholder="Any tag" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Any tag</SelectItem>
                  {tags.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </PopoverContent>
        </Popover>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-3 p-3" data-testid="list-loading">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="flex gap-3">
                <Skeleton className="size-8 rounded-full" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-3 w-1/2" />
                  <Skeleton className="h-3 w-3/4" />
                </div>
              </div>
            ))}
          </div>
        ) : isError ? (
          <div className="flex flex-col items-center gap-3 p-8 text-center">
            <AlertCircle className="size-8 text-destructive" />
            <p className="text-sm text-muted-foreground">Failed to load conversations.</p>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          </div>
        ) : items.length === 0 ? (
          <Empty className="py-16">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Search className="size-5" />
              </EmptyMedia>
              <EmptyTitle>No conversations</EmptyTitle>
              <EmptyDescription>
                {q || activeFilterCount ? 'Try adjusting your filters.' : 'This inbox is all caught up.'}
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <>
            {items.map((item) => (
              <ConversationRow
                key={item.id}
                item={item}
                active={item.id === activeId}
                assigneeOnline={!!item.assignee && presence.has(item.assignee.id)}
                tagsById={tagsById}
                onSelect={onSelect}
              />
            ))}
            {hasNextPage ? (
              <div className="p-3">
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full"
                  onClick={() => fetchNextPage()}
                  disabled={isFetchingNextPage}
                >
                  {isFetchingNextPage ? <Loader2 className="size-4 animate-spin" /> : null}
                  Load more
                </Button>
              </div>
            ) : null}
          </>
        )}
      </div>

      <NewConversationDialog
        open={newOpen}
        onOpenChange={setNewOpen}
        onCreated={(id) => {
          setNewOpen(false)
          onSelect(id)
        }}
      />
    </div>
  )
}
