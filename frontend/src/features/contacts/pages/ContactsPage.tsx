/** Contacts directory — searchable, segment-filterable table. */

import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { AlertCircle, Download, Loader2, Search, Upload, Users } from 'lucide-react'

import { timeAgo } from '@/lib/format'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useHasPerm } from '@/stores/auth'
import { authHeaders } from '@/api/client'
import { ContactAvatar } from '@/features/inbox/components/atoms'
import { ImportDialog } from '@/features/contacts/components/ImportDialog'
import { contactAdminApi } from '@/features/contacts/api'
import { useContactsList, useSegments } from '@/features/contacts/hooks'

/** Fetch the CSV with the session's auth header, then hand it to the browser. */
async function downloadExport() {
  const response = await fetch(contactAdminApi.exportUrl(), { headers: authHeaders() })
  if (!response.ok) return
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = 'contacts.csv'
  link.click()
  URL.revokeObjectURL(url)
}

const ALL = '__all__'

export function Component() {
  const navigate = useNavigate()
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')
  const [segmentId, setSegmentId] = useState<string | undefined>()

  const { data: segments = [] } = useSegments()
  const canWrite = useHasPerm('contacts:write')
  const [importOpen, setImportOpen] = useState(false)

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
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            onClick={() => setImportOpen(true)}
          >
            <Upload className="mr-1 size-4" />
            Import
          </Button>
        ) : null}
        <Button
          variant="outline"
          size="sm"
          className={canWrite ? undefined : 'ml-auto'}
          onClick={() => downloadExport()}
        >
          <Download className="mr-1 size-4" />
          Export
        </Button>
      </div>

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
        <Select
          value={segmentId ?? ALL}
          onValueChange={(v) => setSegmentId(v === ALL ? undefined : v)}
        >
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
                  : 'Contacts appear here as people reach out.'}
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
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
                  className="cursor-pointer"
                  onClick={() => navigate(`/contacts/${c.id}`)}
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

      <ImportDialog open={importOpen} onOpenChange={setImportOpen} />
    </div>
  )
}

export default Component
