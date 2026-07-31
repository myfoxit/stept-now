/** Add a document to an existing source (text paste or file upload). */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
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
import { Textarea } from '@/components/ui/textarea'
import { currentWorkspaceId } from '@/stores/auth'

import { knowledgeApi, knowledgeKeys, type Source } from '../api'

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
  const [file, setFile] = useState<File | null>(null)
  const isFiles = source.type === 'files'

  const mutation = useMutation({
    mutationFn: async () => {
      if (isFiles) {
        if (!file) throw new Error('Choose a file')
        return knowledgeApi.uploadDocument(source.id, file)
      }
      return knowledgeApi.addTextDocument(source.id, { title: title.trim(), content })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(workspaceId, source.id) })
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.source(workspaceId, source.id) })
      toast.success('Document added')
      setTitle('')
      setContent('')
      setFile(null)
      onOpenChange(false)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not add document'),
  })

  const canSubmit = isFiles ? Boolean(file) : title.trim().length > 0 && content.trim().length > 0

  return (
    <Dialog open={open} onOpenChange={(next) => !mutation.isPending && onOpenChange(next)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add document</DialogTitle>
          <DialogDescription>Add content to “{source.name}”.</DialogDescription>
        </DialogHeader>
        {isFiles ? (
          <div className="grid gap-1.5">
            <Label htmlFor="doc-file">File</Label>
            <Input
              id="doc-file"
              type="file"
              aria-label="Document file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>
        ) : (
          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="doc-title">Title</Label>
              <Input id="doc-title" value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="doc-content">Content (markdown)</Label>
              <Textarea
                id="doc-content"
                rows={8}
                value={content}
                onChange={(e) => setContent(e.target.value)}
              />
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={!canSubmit || mutation.isPending}>
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            Add document
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
