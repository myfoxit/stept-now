/** Left pane: saved views, status tabs w/ live counts, filters, search,
 * multi-select + bulk actions, infinite list. */

import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, Filter, Loader2, PenSquare, Search, X } from 'lucide-react'

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
import { BulkActionBar } from '@/features/inbox/components/BulkActionBar'
import { ConversationRow } from '@/features/inbox/components/ConversationRow'
import { NewConversationDialog } from '@/features/inbox/components/NewConversationDialog'
import { ViewsBar } from '@/features/inbox/components/ViewsBar'
import type { ConversationFilters, Counts, SavedView, Tag } from '@/features/inbox/api'
import {
  useConversationsList,
  useCounts,
  useInboxes,
  useMembers,
  usePresence,
  useTeams,
} from '@/features/inbox/hooks'
import { useTags } from '@/features/contacts/hooks'
import { useDrilldownStore } from '@/stores/drilldown'
import { t } from '@/i18n'

interface TabDef {
  key: string
  label: string
  countKey: keyof Counts
  filter: ConversationFilters
}

const TABS: TabDef[] = [
  { key: 'open', label: 'Open', countKey: 'open', filter: { status: ['open'] } },
  { key: 'mine', label: 'Mine', countKey: 'mine', filter: { status: ['open'], assignee: 'me' } },
  {
    key: 'unassigned',
    label: 'Unassigned',
    countKey: 'unassigned',
    filter: { status: ['open'], assignee: 'unassigned' },
  },
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
  const [view, setView] = useState<SavedView | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  // A report drill-down arrives as an unsaved filter document; it applies once
  // and shows as a dismissible chip rather than pretending to be a saved view.
  const takeDrilldown = useDrilldownStore((s) => s.take)
  const [drilldown, setDrilldown] = useState<ReturnType<typeof takeDrilldown>>(null)
  useEffect(() => {
    const pending = takeDrilldown()
    if (pending) {
      setDrilldown(pending)
      setView(null)
    }
  }, [takeDrilldown])

  const canWrite = useHasPerm('conversations:write')
  const canManage = useHasPerm('conversations:manage')
  const { data: counts } = useCounts()
  const { data: inboxes = [] } = useInboxes()
  const { data: tags = [] } = useTags()
  const { data: members = [] } = useMembers()
  const { data: teams = [] } = useTeams()
  const presence = usePresence()

  useEffect(() => {
    const t = setTimeout(() => setQ(searchInput.trim()), 300)
    return () => clearTimeout(t)
  }, [searchInput])

  const filters = useMemo<ConversationFilters>(() => {
    const base = TABS.find((t) => t.key === tab)?.filter ?? {}
    return {
      ...base,
      inbox_id: inboxId,
      priority,
      tag_id: tagId,
      q: q || undefined,
      view_id: view?.id,
    }
  }, [tab, inboxId, priority, tagId, q, view])

  const { data, isLoading, isError, refetch, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useConversationsList(filters, drilldown?.query ?? null)

  const items = useMemo(() => data?.pages.flatMap((p) => p.items) ?? [], [data])

  // Rows that scroll out of the filtered set must not stay selected — a bulk
  // action against a stale id would silently target the wrong conversation.
  useEffect(() => {
    setSelected((prev) => {
      const visible = new Set(items.map((item) => item.id))
      const next = prev.filter((id) => visible.has(id))
      return next.length === prev.length ? prev : next
    })
  }, [items])

  function toggleSelected(id: string) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((value) => value !== id) : [...prev, id]
    )
  }
  const tagsById = useMemo(() => new Map<string, Tag>(tags.map((t) => [t.id, t])), [tags])
  const activeFilterCount = [inboxId, priority, tagId].filter(Boolean).length

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <h2 className="text-sm font-semibold">{t('common.inbox')}</h2>
        {canWrite ? (
          <Button
            variant="ghost"
            size="icon-sm"
            className="ml-auto"
            onClick={() => setNewOpen(true)}
            aria-label={t('inbox.new_conversation')}
          >
            <PenSquare className="size-4" />
          </Button>
        ) : null}
      </div>

      {drilldown ? (
        <div className="flex items-center gap-2 border-b bg-accent/40 px-2 py-1.5 text-xs">
          <span className="font-medium">Drill-down:</span>
          <span className="truncate">{drilldown.label}</span>
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto size-6"
            aria-label={t('inbox.clear_drill_down')}
            onClick={() => setDrilldown(null)}
          >
            <X className="size-3.5" />
          </Button>
        </div>
      ) : (
        <ViewsBar
          activeViewId={view?.id ?? null}
          onSelect={(next) => {
            setView(next)
            setSelected([])
          }}
        />
      )}

      {canManage && selected.length > 0 ? (
        <BulkActionBar
          selected={selected}
          members={members}
          teams={teams}
          tags={tags}
          onDone={() => setSelected([])}
        />
      ) : null}

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
                tab === t.key
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-accent'
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
            placeholder={t('inbox.search_conversations')}
            aria-label={t('inbox.search_conversations')}
            className="h-8 pl-8"
          />
        </div>
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" aria-label={t('inbox.filters')} className="relative">
              <Filter className="size-4" />
              {activeFilterCount ? (
                <span className="absolute -right-1 -top-1 flex size-4 items-center justify-center rounded-full bg-brand text-[10px] text-brand-foreground">
                  {activeFilterCount}
                </span>
              ) : null}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-64 space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">{t('common.inbox')}</label>
              <Select
                value={inboxId ?? ALL}
                onValueChange={(v) => setInboxId(v === ALL ? undefined : v)}
              >
                <SelectTrigger className="w-full" size="sm">
                  <SelectValue placeholder={t('inbox.all_inboxes')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{t('inbox.all_inboxes')}</SelectItem>
                  {inboxes.map((i) => (
                    <SelectItem key={i.id} value={i.id}>
                      {i.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">{t('common.priority')}</label>
              <Select
                value={priority ?? ALL}
                onValueChange={(v) => setPriority(v === ALL ? undefined : v)}
              >
                <SelectTrigger className="w-full" size="sm">
                  <SelectValue placeholder={t('inbox.any_priority')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{t('inbox.any_priority')}</SelectItem>
                  {['urgent', 'high', 'medium', 'low', 'none'].map((p) => (
                    <SelectItem key={p} value={p} className="capitalize">
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">{t('common.tag')}</label>
              <Select
                value={tagId ?? ALL}
                onValueChange={(v) => setTagId(v === ALL ? undefined : v)}
              >
                <SelectTrigger className="w-full" size="sm">
                  <SelectValue placeholder={t('inbox.any_tag')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{t('inbox.any_tag')}</SelectItem>
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
            <p className="text-sm text-muted-foreground">{t('inbox.failed_to_load_conversations')}</p>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        ) : items.length === 0 ? (
          <Empty className="py-16">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Search className="size-5" />
              </EmptyMedia>
              <EmptyTitle>{t('inbox.no_conversations')}</EmptyTitle>
              <EmptyDescription>
                {q || activeFilterCount
                  ? 'Try adjusting your filters.'
                  : 'This inbox is all caught up.'}
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
                selectable={canManage}
                selected={selected.includes(item.id)}
                onToggleSelected={toggleSelected}
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
