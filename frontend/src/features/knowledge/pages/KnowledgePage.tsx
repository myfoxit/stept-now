import { useMutation, useQueryClient } from '@tanstack/react-query'
import { BookOpen, MoreVertical, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { currentWorkspaceId, useHasPerm } from '@/stores/auth'

import { knowledgeApi, knowledgeKeys } from '../api'
import { AddSourceDialog } from '../components/AddSourceDialog'
import {
  ErrorState,
  KnowledgeNav,
  ListSkeleton,
  PageHeader,
  PageShell,
  ScrollBody,
} from '../components/shell'
import { SourceStatusBadge, SourceTypeIcon } from '../components/status'
import { useSources } from '../hooks'

export function Component() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  const canWrite = useHasPerm('knowledge:write')
  const [dialogOpen, setDialogOpen] = useState(false)
  const { data: sources, isLoading, isError, refetch } = useSources()

  const syncMutation = useMutation({
    mutationFn: (id: string) => knowledgeApi.syncSource(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.sources(workspaceId) })
      toast.success('Sync started')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Sync failed'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => knowledgeApi.deleteSource(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.sources(workspaceId) })
      toast.success('Source deleted')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Delete failed'),
  })

  return (
    <PageShell>
      <PageHeader
        title="Knowledge"
        description="Content your AI agents retrieve and cite"
        actions={
          canWrite ? (
            <Button onClick={() => setDialogOpen(true)}>
              <Plus className="size-4" /> Add source
            </Button>
          ) : null
        }
      />
      <KnowledgeNav />
      <ScrollBody>
        {isLoading ? (
          <ListSkeleton />
        ) : isError ? (
          <ErrorState onRetry={() => refetch()} />
        ) : !sources || sources.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <BookOpen />
              </EmptyMedia>
              <EmptyTitle>No knowledge sources yet</EmptyTitle>
              <EmptyDescription>
                Upload files, crawl URLs or paste text so your AI agents have something to cite.
              </EmptyDescription>
            </EmptyHeader>
            {canWrite ? (
              <EmptyContent>
                <Button onClick={() => setDialogOpen(true)}>
                  <Plus className="size-4" /> Add your first source
                </Button>
              </EmptyContent>
            ) : null}
          </Empty>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {sources.map((source) => (
              <Card
                key={source.id}
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/knowledge/sources/${source.id}`)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') navigate(`/knowledge/sources/${source.id}`)
                }}
                className="cursor-pointer transition-colors hover:border-primary/40"
              >
                <CardContent className="flex flex-col gap-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex size-9 items-center justify-center rounded-md bg-muted">
                      <SourceTypeIcon type={source.type} />
                    </div>
                    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      {canWrite && (source.type === 'urls' || source.type === 'articles') ? (
                        <Button
                          size="icon"
                          variant="ghost"
                          className="size-8"
                          aria-label="Re-sync source"
                          disabled={syncMutation.isPending}
                          onClick={() => syncMutation.mutate(source.id)}
                        >
                          <RefreshCw className="size-4" />
                        </Button>
                      ) : null}
                      {canWrite ? (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="size-8"
                              aria-label="Source actions"
                            >
                              <MoreVertical className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={() => deleteMutation.mutate(source.id)}
                            >
                              <Trash2 className="size-4" /> Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      ) : null}
                    </div>
                  </div>
                  <div className="min-w-0">
                    <p className="truncate font-medium">{source.name}</p>
                    <p className="text-sm text-muted-foreground">
                      {source.document_count}{' '}
                      {source.document_count === 1 ? 'document' : 'documents'}
                      {' · '}
                      <span className="capitalize">{source.type}</span>
                    </p>
                  </div>
                  <SourceStatusBadge status={source.status} error={source.error} />
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </ScrollBody>
      <AddSourceDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onCreated={(source) => navigate(`/knowledge/sources/${source.id}`)}
      />
    </PageShell>
  )
}

export default Component
