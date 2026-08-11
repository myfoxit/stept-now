/**
 * Re-edit an authored document.
 *
 * The detail endpoint returns the raw stored text on `content` for documents
 * the backend will let you PATCH (storage-backed text/markdown) and `null` for
 * everything else — so it, not the caller, decides whether editing is offered.
 * Saving re-writes the file and re-ingests it; an unchanged body is a no-op
 * server-side thanks to the content hash.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { RichTextEditor } from '@/components/editor/RichTextEditor'
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
import { Spinner } from '@/components/ui/spinner'
import { currentWorkspaceId } from '@/stores/auth'

import { knowledgeApi, knowledgeKeys } from '../api'
import { useDocument } from '../hooks'
import { t } from '@/i18n'

export function EditDocumentDialog({
  documentId,
  sourceId,
  onClose,
}: {
  documentId: string | null
  sourceId: string
  onClose: () => void
}) {
  return (
    <Dialog open={Boolean(documentId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        {documentId ? (
          <EditDocumentBody documentId={documentId} sourceId={sourceId} onClose={onClose} />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function EditDocumentBody({
  documentId,
  sourceId,
  onClose,
}: {
  documentId: string
  sourceId: string
  onClose: () => void
}) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const { data: document, isLoading } = useDocument(documentId)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')

  useEffect(() => {
    if (!document) return
    setTitle(document.title)
    setContent(document.content ?? '')
  }, [document])

  const mutation = useMutation({
    mutationFn: () => knowledgeApi.updateDocument(documentId, { title: title.trim(), content }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.document(workspaceId, documentId) })
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(workspaceId, sourceId) })
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.source(workspaceId, sourceId) })
      toast.success(t('knowledge.document_saved'))
      onClose()
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Save failed'),
  })

  if (isLoading || !document) {
    return (
      <>
        <DialogHeader>
          <DialogTitle>{t('knowledge.edit_document')}</DialogTitle>
          <DialogDescription>{t('knowledge.loading_the_stored_content')}</DialogDescription>
        </DialogHeader>
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      </>
    )
  }

  const editable = typeof document.content === 'string'

  return (
    <>
      <DialogHeader>
        <DialogTitle className="truncate">Edit “{document.title}”</DialogTitle>
        <DialogDescription>
          {editable
            ? 'Changes are re-indexed as soon as you save.'
            : 'This document comes from a connector or URL and can only be changed at the source.'}
        </DialogDescription>
      </DialogHeader>
      {editable ? (
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="edit-doc-title">{t('common.title')}</Label>
            <Input
              id="edit-doc-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={mutation.isPending}
            />
          </div>
          <div className="grid gap-1.5">
            <Label>{t('knowledge.content')}</Label>
            <RichTextEditor
              value={content}
              onChange={setContent}
              variant="full"
              disabled={mutation.isPending}
              ariaLabel="Document content"
            />
          </div>
        </div>
      ) : null}
      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
          {editable ? 'Cancel' : 'Close'}
        </Button>
        {editable ? (
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending || !title.trim() || !content.trim()}
          >
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            Save changes
          </Button>
        ) : null}
      </DialogFooter>
    </>
  )
}
