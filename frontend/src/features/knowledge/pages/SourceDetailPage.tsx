import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  FileText,
  Pencil,
  PencilLine,
  Plus,
  RefreshCw,
  RotateCcw,
  Trash2,
} from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Spinner } from '@/components/ui/spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fullDateTime, timeAgo } from '@/lib/format'
import { currentWorkspaceId, useHasPerm } from '@/stores/auth'

import { knowledgeApi, knowledgeKeys, REMOTE_SOURCE_TYPES } from '../api'
import { AddDocumentDialog } from '../components/AddDocumentDialog'
import { AddSourceDialog } from '../components/AddSourceDialog'
import { EditDocumentDialog } from '../components/EditDocumentDialog'
import { ErrorState, ListSkeleton, PageHeader, PageShell, ScrollBody } from '../components/shell'
import {
  AutoSyncBadge,
  CredentialsBadge,
  DocStatusBadge,
  SourceStatusBadge,
  SourceTypeIcon,
} from '../components/status'
import { useDocument, useDocuments, useSource } from '../hooks'
import { isEditableDocument, refreshMinutes, sourceTypeLabel } from '../lib'

export function Component() {
  const { sourceId = '' } = useParams()
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  const canWrite = useHasPerm('knowledge:write')
  const source = useSource(sourceId)
  const documents = useDocuments(sourceId)
  const [addOpen, setAddOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [editDocumentId, setEditDocumentId] = useState<string | null>(null)

  const syncMutation = useMutation({
    mutationFn: () => knowledgeApi.syncSource(sourceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.source(workspaceId, sourceId) })
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(workspaceId, sourceId) })
      toast.success('Sync started')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Sync failed'),
  })

  const retryMutation = useMutation({
    mutationFn: (id: string) => knowledgeApi.retryDocument(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(workspaceId, sourceId) })
      toast.success('Re-indexing document')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Retry failed'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => knowledgeApi.deleteDocument(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(workspaceId, sourceId) })
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.source(workspaceId, sourceId) })
      toast.success('Document deleted')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Delete failed'),
  })

  const docs = documents.data?.items ?? []
  const src = source.data
  const canSync = src && REMOTE_SOURCE_TYPES.includes(src.type)
  const canAdd = src && (src.type === 'files' || src.type === 'text')

  return (
    <PageShell>
      <PageHeader
        title={src?.name ?? 'Source'}
        description={src ? `${src.document_count} documents` : undefined}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" asChild>
              <Link to="/knowledge">
                <ArrowLeft className="size-4" /> Back
              </Link>
            </Button>
            {canWrite && canSync ? (
              <Button
                variant="outline"
                size="sm"
                disabled={syncMutation.isPending}
                onClick={() => syncMutation.mutate()}
              >
                <RefreshCw className="size-4" /> Re-sync
              </Button>
            ) : null}
            {canWrite && src ? (
              <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
                <Pencil className="size-4" /> Edit
              </Button>
            ) : null}
            {canWrite && canAdd ? (
              <Button size="sm" onClick={() => setAddOpen(true)}>
                <Plus className="size-4" /> Add document
              </Button>
            ) : null}
          </div>
        }
      />
      <ScrollBody className="space-y-4">
        {src ? (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border p-4">
            <div className="flex size-9 items-center justify-center rounded-md bg-muted">
              <SourceTypeIcon type={src.type} />
            </div>
            <div className="min-w-0 flex-1">
              <p className="font-medium">{src.name}</p>
              <p className="text-sm text-muted-foreground">
                {sourceTypeLabel(src.type)} source
                {src.last_synced_at ? (
                  <span title={fullDateTime(src.last_synced_at)}>
                    {' · '}last synced {timeAgo(src.last_synced_at)} ago
                  </span>
                ) : null}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <SourceStatusBadge status={src.status} error={src.error} />
              <AutoSyncBadge minutes={refreshMinutes(src.config)} />
              <CredentialsBadge hasSecrets={src.has_secrets} />
            </div>
          </div>
        ) : null}

        {documents.isLoading ? (
          <ListSkeleton />
        ) : documents.isError ? (
          <ErrorState onRetry={() => documents.refetch()} />
        ) : docs.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <FileText />
              </EmptyMedia>
              <EmptyTitle>No documents yet</EmptyTitle>
              <EmptyDescription>
                {canAdd
                  ? 'Add documents to this source to start indexing.'
                  : 'Re-sync this source to fetch its documents.'}
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Tokens</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="w-24" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {docs.map((doc) => (
                  <TableRow
                    key={doc.id}
                    className="cursor-pointer"
                    onClick={() => setPreviewId(doc.id)}
                  >
                    <TableCell className="max-w-xs">
                      <div className="truncate font-medium">{doc.title}</div>
                      {doc.uri ? (
                        <div className="truncate text-xs text-muted-foreground">{doc.uri}</div>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <DocStatusBadge status={doc.status} error={doc.error} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {doc.token_count.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-muted-foreground" title={fullDateTime(doc.updated_at)}>
                      {timeAgo(doc.updated_at)}
                    </TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      {canWrite ? (
                        <div className="flex justify-end gap-1">
                          {isEditableDocument(doc, src?.type) ? (
                            <Button
                              size="icon"
                              variant="ghost"
                              className="size-8"
                              aria-label={`Edit ${doc.title}`}
                              onClick={() => setEditDocumentId(doc.id)}
                            >
                              <PencilLine className="size-4" />
                            </Button>
                          ) : null}
                          {doc.status === 'failed' ? (
                            <Button
                              size="icon"
                              variant="ghost"
                              className="size-8"
                              aria-label="Retry document"
                              onClick={() => retryMutation.mutate(doc.id)}
                            >
                              <RotateCcw className="size-4" />
                            </Button>
                          ) : null}
                          <Button
                            size="icon"
                            variant="ghost"
                            className="size-8 text-destructive"
                            aria-label="Delete document"
                            onClick={() => deleteMutation.mutate(doc.id)}
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </div>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </ScrollBody>

      {src && canAdd ? (
        <AddDocumentDialog source={src} open={addOpen} onOpenChange={setAddOpen} />
      ) : null}
      {src ? <AddSourceDialog source={src} open={editOpen} onOpenChange={setEditOpen} /> : null}
      <DocumentPreviewDialog id={previewId} onClose={() => setPreviewId(null)} />
      <EditDocumentDialog
        documentId={editDocumentId}
        sourceId={sourceId}
        onClose={() => setEditDocumentId(null)}
      />
    </PageShell>
  )
}

function DocumentPreviewDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  return (
    <Dialog open={Boolean(id)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        {id ? <DocumentPreviewBody id={id} /> : null}
      </DialogContent>
    </Dialog>
  )
}

function DocumentPreviewBody({ id }: { id: string }) {
  const { data, isLoading } = useDocument(id)
  return (
    <>
      <DialogHeader>
        <DialogTitle className="truncate">{data?.title ?? 'Document'}</DialogTitle>
        <DialogDescription>
          {data
            ? `${data.token_count.toLocaleString()} tokens · ${data.chunks?.length ?? 0} chunk preview`
            : ''}
        </DialogDescription>
      </DialogHeader>
      {isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : (
        <div className="space-y-3">
          {(data?.chunks ?? []).map((chunk) => (
            <div key={chunk.id} className="rounded-md border p-3">
              <Badge variant="secondary" className="mb-2">
                Chunk {chunk.ord + 1}
              </Badge>
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">{chunk.content}</p>
            </div>
          ))}
          {data && (data.chunks?.length ?? 0) === 0 ? (
            <p className="text-sm text-muted-foreground">No indexed chunks yet.</p>
          ) : null}
        </div>
      )}
    </>
  )
}

export default Component
