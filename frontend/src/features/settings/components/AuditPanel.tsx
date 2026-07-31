import { useEffect, useState } from 'react'

import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { fullDateTime } from '@/lib/format'

import { useAuditLog } from '../hooks'

function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

export function AuditPanel() {
  const [actionInput, setActionInput] = useState('')
  const [actorInput, setActorInput] = useState('')
  const action = useDebounced(actionInput)
  const actorId = useDebounced(actorInput)

  const audit = useAuditLog({
    action: action.trim() || undefined,
    actor_id: actorId.trim() || undefined,
  })

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="audit-action" className="text-xs text-muted-foreground">
            Action
          </Label>
          <Input
            id="audit-action"
            className="w-56"
            placeholder="e.g. member.update"
            value={actionInput}
            onChange={(e) => setActionInput(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="audit-actor" className="text-xs text-muted-foreground">
            Actor ID
          </Label>
          <Input
            id="audit-actor"
            className="w-56"
            placeholder="user id"
            value={actorInput}
            onChange={(e) => setActorInput(e.target.value)}
          />
        </div>
      </div>

      <Card>
        <CardContent className="overflow-x-auto p-0">
          {audit.isLoading ? (
            <div className="p-4">
              <Skeleton className="h-40 w-full" />
            </div>
          ) : !audit.data || audit.data.items.length === 0 ? (
            <p className="p-8 text-center text-sm text-muted-foreground">
              No audit entries match these filters.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Actor</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Target</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {audit.data.items.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {fullDateTime(entry.created_at)}
                    </TableCell>
                    <TableCell className="text-sm">
                      {entry.actor_label ?? entry.actor_id ?? entry.actor_type}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{entry.action}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {entry.target_type ? `${entry.target_type}${entry.target_id ? ` · ${entry.target_id.slice(0, 8)}` : ''}` : '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {audit.data ? (
        <p className="text-xs text-muted-foreground">
          Showing {audit.data.items.length} of {audit.data.total} entries.
        </p>
      ) : null}
    </div>
  )
}
