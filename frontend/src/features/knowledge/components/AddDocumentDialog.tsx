/**
 * Add documents to an existing source.
 *
 * File sources take a drag & drop batch (up to 20 files in one multipart
 * request, each file reporting its own queued → uploading → indexed/failed
 * state). Text sources get a proper authoring surface: a title plus the shared
 * rich text editor, which serialises back to the markdown we persist.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Loader2 } from 'lucide-react'
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
import { currentWorkspaceId } from '@/stores/auth'

import { knowledgeApi, knowledgeKeys, MAX_BATCH_FILES, type Document, type Source } from '../api'
import { FileDrop, type FileStatus } from './FileDrop'

export function AddDocumentDialog({
  source,
  open,
  onOpenChange,
}: {
  source: Source
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [statuses, setStatuses] = useState<Record<number, FileStatus>>({})
  const isFiles = source.type === 'files'

  const reset = () => {
    setTitle('')
    setContent('')
    setFiles([])
    setStatuses({})
  }

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(workspaceId, source.id) })
    queryClient.invalidateQueries({ queryKey: knowledgeKeys.source(workspaceId, source.id) })
  }

  const mutation = useMutation({
    mutationFn: async (): Promise<Document[]> => {
      if (!isFiles) {
        return [await knowledgeApi.addTextDocument(source.id, { title: title.trim(), content })]
      }
      setStatuses(Object.fromEntries(files.map((_, i) => [i, { state: 'uploading' as const }])))
      const created = await knowledgeApi.uploadDocuments(source.id, files)
      // The batch endpoint answers in request order, so index ↔ file lines up.
      setStatuses(
        Object.fromEntries(
          files.map((_, i) => {
            const document = created[i]
            if (!document) return [i, { state: 'failed' as const, error: 'No response' }]
            return document.status === 'failed'
              ? [i, { state: 'failed' as const, error: document.error ?? 'Could not be parsed' }]
              : [i, { state: 'indexed' as const }]
          })
        )
      )
      return created
    },
    onSuccess: (created) => {
      invalidate()
      const failed = created.filter((document) => document.status === 'failed').length
      if (failed > 0) {
        toast.warning(`${created.length - failed} added · ${failed} failed`)
        return // keep the dialog open so the per-file errors stay visible
      }
      toast.success(created.length > 1 ? `${created.length} documents added` : 'Document added')
      reset()
      onOpenChange(false)
    },
    onError: (e) => {
      setStatuses(
        Object.fromEntries(
          files.map((_, i) => [
            i,
            { state: 'failed' as const, error: e instanceof ApiError ? e.message : 'Upload failed' },
          ])
        )
      )
      toast.error(e instanceof ApiError ? e.message : 'Could not add document')
    },
  })

  const tooMany = files.length > MAX_BATCH_FILES
  const canSubmit = isFiles
    ? files.length > 0 && !tooMany
    : title.trim().length > 0 && content.trim().length > 0

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (mutation.isPending) return
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isFiles ? 'Upload documents' : 'Write a document'}</DialogTitle>
          <DialogDescription>
            {isFiles
              ? `Drop up to ${MAX_BATCH_FILES} files — each is parsed and indexed into “${source.name}”.`
              : `Write content to index into “${source.name}”. Saved as markdown.`}
          </DialogDescription>
        </DialogHeader>
        {isFiles ? (
          <>
            <FileDrop
              files={files}
              onChange={setFiles}
              statuses={statuses}
              disabled={mutation.isPending}
              max={MAX_BATCH_FILES}
            />
            {tooMany ? (
              <p className="text-sm text-destructive">
                Only {MAX_BATCH_FILES} files can be uploaded at a time.
              </p>
            ) : null}
          </>
        ) : (
          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="doc-title">Title</Label>
              <Input id="doc-title" value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label>Content</Label>
              <RichTextEditor
                value={content}
                onChange={setContent}
                variant="full"
                ariaLabel="Document content"
                placeholder="Write the content you want indexed…"
              />
            </div>
          </div>
        )}
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={!canSubmit || mutation.isPending}>
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            {isFiles ? 'Upload' : 'Add document'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
