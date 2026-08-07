/** Contacts directory — searchable, segment-filterable table. */

import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { AlertCircle, Loader2, Plus, Search, Users } from 'lucide-react'

import { timeAgo } from '@/lib/format'
import { useHasPerm } from '@/stores/auth'
import { NewContactDialog } from '@/features/contacts/components/NewContactDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ContactAvatar } from '@/features/inbox/components/atoms'
import { useContactsList, useSegments } from '@/features/contacts/hooks'

const ALL = '__all__'

export function Component() {
  const navigate = useNavigate()
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')
  const [segmentId, setSegmentId] = useState<string | undefined>()
  const [creating, setCreating] = useState(false)
  const canWrite = useHasPerm('contacts:write')

  const { data: segments = [] } = useSegments()

  useEffect(() => {
    const t = setTimeout(() => setQ(searchInput.trim()), 300)
    return () => clearTimeout(t)
  }, [searchInput])

  const { data, isLoading, isError, refetch, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useContactsList({ q: q || undefined, segmentId })
  const contacts = useMemo(() => data?.pages.flatMap((p) => p.items) ?? [], [data])

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b px-6 py-4">
        <Users className="size-5" />
        <h1 className="text-lg font-semibold">Contacts</h1>
        {canWrite ? (
          <Button size="sm" className="ml-auto" onClick={() => setCreating(true)}>
            <Plus className="size-4" /> New contact
          </Button>
        ) : null}
      </div>

      <NewContactDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(c) => navigate(`/contacts/${c.id}`)}
      />

      <div className="flex flex-wrap items-center gap-2 border-b px-6 py-3">
        <div className="relative max-w-sm flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search by name, email, or ID"
            aria-label="Search contacts"
            className="pl-8"
          />
        </div>
        <Select value={segmentId ?? ALL} onValueChange={(v) => setSegmentId(v === ALL ? undefined : v)}>
          <SelectTrigger aria-label="Segment" className="w-52">
            <SelectValue placeholder="All contacts" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All contacts</SelectItem>
            {segments.map((s) => (
              <SelectItem key={s.id} value={s.id}>
                {s.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-3 p-6" data-testid="contacts-loading">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : isError ? (
          <div className="flex flex-col items-center gap-3 p-12 text-center">
            <AlertCircle className="size-8 text-destructive" />
            <p className="text-sm text-muted-foreground">Could not load contacts.</p>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          </div>
        ) : contacts.length === 0 ? (
          <Empty className="py-20">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Users className="size-5" />
              </EmptyMedia>
              <EmptyTitle>No contacts found</EmptyTitle>
              <EmptyDescription>
                {q || segmentId
                  ? 'Try a different search or segment.'
                  : 'Contacts appear here as people reach out — or add one yourself.'}
              </EmptyDescription>
            </EmptyHeader>
            {!q && !segmentId && canWrite ? (
              <Button size="sm" onClick={() => setCreating(true)}>
                <Plus className="size-4" /> New contact
              </Button>
            ) : null}
          </Empty>
        ) : (
          <div className="px-6">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Contact</TableHead>
                <TableHead>Tags</TableHead>
                <TableHead>Plan</TableHead>
                <TableHead className="text-right">Last seen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {contacts.map((c) => (
                <TableRow
                  key={c.id}
                  className="cursor-pointer focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
                  // The row was a bare click handler: no keyboard access and
                  // nothing announced to assistive tech.
                  role="link"
                  tabIndex={0}
                  aria-label={`Open contact ${c.name || c.email || 'Unnamed'}`}
                  onClick={() => navigate(`/contacts/${c.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      navigate(`/contacts/${c.id}`)
                    }
                  }}
                >
                  <TableCell>
                    <div className="flex items-center gap-3">
                      <ContactAvatar name={c.name} avatarUrl={c.avatar_url} />
                      <div className="min-w-0">
                        <p className="truncate font-medium">{c.name || 'Unnamed'}</p>
                        <p className="truncate text-xs text-muted-foreground">{c.email ?? '—'}</p>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {(c.tags ?? []).slice(0, 3).map((t) => (
                        <Badge
                          key={t.id}
                          variant="outline"
                          style={{ borderColor: t.color, color: t.color }}
                        >
                          {t.name}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-sm capitalize text-muted-foreground">
                    {String((c.attributes as Record<string, unknown>)?.plan ?? '—')}
                  </TableCell>
                  <TableCell className="text-right text-xs text-muted-foreground">
                    {c.last_seen_at ? timeAgo(c.last_seen_at) : '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
        )}

        {hasNextPage ? (
          <div className="p-4">
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
      </div>
    </div>
  )
}

export default Component
