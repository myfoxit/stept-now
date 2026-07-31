/** Create-source dialog with three modes: upload files, add URLs, paste text. */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { FileText, Globe, Loader2, Type, Upload, X } from 'lucide-react'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { currentWorkspaceId } from '@/stores/auth'
import { formatBytes } from '@/lib/format'

import { knowledgeApi, knowledgeKeys, type Source, type SourceType } from '../api'

export function AddSourceDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated?: (source: Source) => void
}) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<SourceType>('files')
  const [name, setName] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [urls, setUrls] = useState('')
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')

  function reset() {
    setName('')
    setFiles([])
    setUrls('')
    setTitle('')
    setContent('')
    setMode('files')
  }

  const mutation = useMutation({
    mutationFn: async (): Promise<Source> => {
      const trimmed = name.trim()
      if (mode === 'files') {
        const source = await knowledgeApi.createSource({
          type: 'files',
          name: trimmed || 'File upload',
        })
        for (const file of files) {
          await knowledgeApi.uploadDocument(source.id, file)
        }
        return source
      }
      if (mode === 'urls') {
        const list = urls
          .split('\n')
          .map((u) => u.trim())
          .filter(Boolean)
        const source = await knowledgeApi.createSource({
          type: 'urls',
          name: trimmed || 'Web pages',
          config: { urls: list },
        })
        await knowledgeApi.syncSource(source.id)
        return source
      }
      const source = await knowledgeApi.createSource({ type: 'text', name: trimmed || 'Pasted text' })
      await knowledgeApi.addTextDocument(source.id, { title: title.trim(), content })
      return source
    },
    onSuccess: (source) => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.sources(workspaceId) })
      toast.success('Source created')
      reset()
      onOpenChange(false)
      onCreated?.(source)
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.message : 'Could not create source')
    },
  })

  const canSubmit =
    (mode === 'files' && files.length > 0) ||
    (mode === 'urls' && urls.trim().length > 0) ||
    (mode === 'text' && title.trim().length > 0 && content.trim().length > 0)

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!mutation.isPending) onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a knowledge source</DialogTitle>
          <DialogDescription>
            Import content your AI agents and help center can search over.
          </DialogDescription>
        </DialogHeader>

        <Tabs value={mode} onValueChange={(v) => setMode(v as SourceType)}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="files">
              <Upload className="size-4" /> Files
            </TabsTrigger>
            <TabsTrigger value="urls">
              <Globe className="size-4" /> URLs
            </TabsTrigger>
            <TabsTrigger value="text">
              <Type className="size-4" /> Text
            </TabsTrigger>
          </TabsList>

          <div className="mt-4 grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="source-name">Source name</Label>
              <Input
                id="source-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Product documentation"
              />
            </div>

            <TabsContent value="files" className="mt-0">
              <FileDrop files={files} onChange={setFiles} />
            </TabsContent>

            <TabsContent value="urls" className="mt-0 grid gap-1.5">
              <Label htmlFor="source-urls">URLs (one per line)</Label>
              <Textarea
                id="source-urls"
                value={urls}
                onChange={(e) => setUrls(e.target.value)}
                rows={5}
                placeholder={'https://docs.example.com/getting-started\nhttps://example.com/faq'}
                className="font-mono text-xs"
              />
              <p className="text-xs text-muted-foreground">
                Each page is fetched, parsed and indexed after you save.
              </p>
            </TabsContent>

            <TabsContent value="text" className="mt-0 grid gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="source-title">Document title</Label>
                <Input
                  id="source-title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Refund policy"
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="source-content">Content (markdown)</Label>
                <Textarea
                  id="source-content"
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  rows={8}
                  placeholder="Paste or write the content you want indexed…"
                />
              </div>
            </TabsContent>
          </div>
        </Tabs>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={!canSubmit || mutation.isPending}>
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            Create source
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function FileDrop({ files, onChange }: { files: File[]; onChange: (files: File[]) => void }) {
  const [dragging, setDragging] = useState(false)
  return (
    <div className="grid gap-2">
      <label
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          onChange([...files, ...Array.from(e.dataTransfer.files)])
        }}
        className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-8 text-center text-sm transition-colors hover:bg-accent/50 ${
          dragging ? 'border-primary bg-accent/50' : ''
        }`}
      >
        <FileText className="size-6 text-muted-foreground" />
        <span className="font-medium">Drop files or click to browse</span>
        <span className="text-xs text-muted-foreground">PDF, DOCX, HTML, Markdown, CSV or TXT</span>
        <input
          type="file"
          multiple
          className="sr-only"
          aria-label="Upload files"
          onChange={(e) => onChange([...files, ...Array.from(e.target.files ?? [])])}
        />
      </label>
      {files.length > 0 ? (
        <ul className="grid gap-1">
          {files.map((file, i) => (
            <li
              key={`${file.name}-${i}`}
              className="flex items-center justify-between rounded-md border px-3 py-1.5 text-sm"
            >
              <span className="truncate">{file.name}</span>
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                {formatBytes(file.size)}
                <button
                  type="button"
                  aria-label={`Remove ${file.name}`}
                  onClick={() => onChange(files.filter((_, idx) => idx !== i))}
                  className="rounded p-0.5 hover:bg-muted"
                >
                  <X className="size-3.5" />
                </button>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
